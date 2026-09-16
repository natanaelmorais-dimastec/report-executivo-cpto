"""
AWS Cost Explorer extractor for the monthly Products & Technology report (Dimastec).

Produces rows matching the `custos_cloud` tab of the report spreadsheet:

    competencia, produto, ambiente, valor_brl

Environment comes from the AWS account (accounts are separated by environment).
Product attribution is deliberately CONSERVATIVE — only two steps:

    1. Explicit `product` cost allocation tag  (dtfaceum -> Faceum, mydhas -> Mydhas,
       integracao/integração/integracao-dtfaceum -> Integração, ai -> AI, etc.).
       Tag values are normalized: case, accents and separators are ignored, so
       `Integração`, `integracao` and `INTEGRACAO` all resolve to the same product.
    2. Everything with no usable `product` tag -> 'Compartilhado'.

    Products in the report: Faceum, Mydhas, Integração, AI, Compartilhado.

There is NO estimated split of the untagged cost, on purpose. A previous version
weighted untagged cost by the FRACTION OF RESOURCES per product taken from a tag
audit CSV, which is not defensible: resource count has no relationship to cost (a
backup recovery point and an RDS instance count the same), and the Resource Groups
Tagging API only returns resources that ALREADY have tags — so it cannot see the
untagged resources that generate the untagged cost. Untagged cost therefore stays
in 'Compartilhado', which the report already rateia downstream in
`relatorio_pt.vw_eficiencia`. Rateio happens in exactly one place.

To make the untagged bucket actionable instead of opaque, the extractor also emits
a per-service diagnostic (--untagged-report) splitting untagged cost into:
    - TAGGABLE        (ECS, ELB, RDS, CloudWatch, ... -> belongs to a product;
                       apply the `product` tag to move it out of Compartilhado)
    - GENUINELY SHARED (Tax, Support, VPC/NAT, Route 53, ... -> no owner by nature)

Usage:
    python extractor.py --month 2026-05 --usd-brl-rate 5.04 \
        --output custos_cloud_2026-05.csv

Requirements: boto3, ce:GetCostAndUsage. Run from the management/payer account.
NOTE: All log messages are intentionally in English.
"""

import argparse
import csv
import logging
import sys
import unicodedata
from collections import defaultdict
from datetime import date

import boto3

# --- Configuration -----------------------------------------------------------

ACCOUNTS = {
    "295574221328": "Produção",
    "058264268348": "QA",
}

PRODUCT_TAG_KEY = "product"

# Keys MUST be written in normalized form (see normalize_tag_value): lowercase,
# no accents, and '_' / '.' / ' ' collapsed to '-'. The live account mixes
# spellings ('integração' with an accent vs 'integracao' without), so normalizing
# is what keeps both resolving to the same product instead of silently falling
# through to 'Compartilhado'.
PRODUCT_TAG_MAP = {
    "dtfaceum": "Faceum",
    "faceum": "Faceum",
    "integracao-dtfaceum": "Integração",
    "integracao": "Integração",
    "mydhas": "Mydhas",
    "ai": "AI",
    "compartilhado": "Compartilhado",
    "finance-report": "Compartilhado",  # adjust if finance-report becomes its own product
    # Values naming more than one product: the resource genuinely serves both, so
    # it belongs in the shared bucket rather than being arbitrarily assigned or
    # split 50/50. vw_eficiencia rateia Compartilhado between the products anyway.
    "mydhas-faceum": "Compartilhado",
    "faceum-mydhas": "Compartilhado",
}

SHARED_LABEL = "Compartilhado"

# Untagged-cost diagnostic. Services whose cost belongs to a product once the
# `product` tag is applied vs. services with no natural owner. Matched by
# case-insensitive substring against the Cost Explorer SERVICE name; anything
# unmatched is reported as UNCLASSIFIED so new services surface instead of
# being silently bucketed.
TAGGABLE_SERVICE_HINTS = [
    "elastic container service", "elastic kubernetes", "container registry",
    "elastic load balancing", "relational database service", "elasticache",
    "cloudwatch", "simple storage service", "elastic compute cloud", "ec2 - other",
    "elastic file system", "dynamodb", "lambda", "sagemaker", "opensearch",
    "simple queue service", "simple notification service", "waf",
    "step functions", "amplify", "simple email service",
]
GENUINELY_SHARED_SERVICE_HINTS = [
    "tax", "support", "virtual private cloud", "route 53", "cloudfront",
    "guardduty", "key management service", "secrets manager", "config",
    "cloudtrail", "backup", "data transfer", "cost explorer", "organizations",
    # CI/CD and ops tooling: serves every product, no natural owner.
    "codebuild", "codepipeline", "codeartifact", "codecommit",
    "systems manager", "cloud map", "cloudshell", "cloudformation",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aws_cost_extractor")


def month_to_period(month: str) -> tuple[str, str]:
    """Convert 'YYYY-MM' into a Cost Explorer period (start inclusive, end exclusive)."""
    year, mon = (int(p) for p in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start.isoformat(), end.isoformat()


def prev_month(month: str) -> str:
    """Return the previous 'YYYY-MM' (e.g. '2026-05' -> '2026-04', '2026-01' -> '2025-12')."""
    year, mon = (int(p) for p in month.split("-"))
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


def resolve_environment(account_id: str) -> str:
    if account_id not in ACCOUNTS:
        log.warning("Unmapped account id '%s' -- add it to ACCOUNTS.", account_id)
    return ACCOUNTS.get(account_id, account_id)


def normalize_tag_value(raw_value: str) -> str:
    """Fold a raw `product` tag value into the form used as a PRODUCT_TAG_MAP key.

    Lowercases, strips accents (so 'Integração' == 'integracao') and collapses
    '_', '.', '/' and whitespace to '-'. Without this, 'integração' misses the map
    and its cost silently lands in 'Compartilhado'."""
    folded = unicodedata.normalize("NFKD", raw_value.strip().lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    for sep in ("_", ".", "/", " "):
        folded = folded.replace(sep, "-")
    while "--" in folded:
        folded = folded.replace("--", "-")
    return folded.strip("-")


def classify_product_tag(raw_value: str) -> str | None:
    """Explicit `product` tag value -> report product label (None if unusable)."""
    if not raw_value:
        return None
    return PRODUCT_TAG_MAP.get(normalize_tag_value(raw_value))


def classify_service(service: str) -> str:
    """Bucket an untagged service as TAGGABLE, SHARED or UNCLASSIFIED (diagnostic only)."""
    blob = service.lower()
    for hint in GENUINELY_SHARED_SERVICE_HINTS:
        if hint in blob:
            return "SHARED"
    for hint in TAGGABLE_SERVICE_HINTS:
        if hint in blob:
            return "TAGGABLE"
    return "UNCLASSIFIED"


def fetch_cost_by_account_and_tag(client, start: str, end: str) -> list[dict]:
    """Fetch monthly cost grouped by account (environment) and the `product` tag."""
    rows: list[dict] = []
    next_token = None
    while True:
        params = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [
                {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
                {"Type": "TAG", "Key": PRODUCT_TAG_KEY},
            ],
        }
        if next_token:
            params["NextPageToken"] = next_token
        log.info("Requesting cost for %s -> %s", start, end)
        resp = client.get_cost_and_usage(**params)
        for bucket in resp["ResultsByTime"]:
            competencia = bucket["TimePeriod"]["Start"][:7]
            for group in bucket["Groups"]:
                account_id, product_raw = group["Keys"]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                unit = group["Metrics"]["UnblendedCost"]["Unit"]
                if amount == 0:
                    continue
                raw_value = product_raw.split("$", 1)[1] if "$" in product_raw else ""
                rows.append({
                    "competencia": competencia,
                    "account_id": account_id,
                    "product_raw": raw_value,
                    "amount": amount,
                    "currency": unit,
                })
        next_token = resp.get("NextPageToken")
        if not next_token:
            break
    log.info("Fetched %d non-zero cost rows from Cost Explorer.", len(rows))
    return rows


def attribute(rows: list[dict]) -> list[dict]:
    """Map tagged cost to its product; everything untagged lands in Compartilhado.

    No estimated split — see the module docstring for why. `unmapped` collects the
    tag values that were present but not in PRODUCT_TAG_MAP, so a new spelling in
    the account shows up as a warning instead of quietly inflating Compartilhado."""
    out: dict[tuple, dict] = {}

    def add(comp, produto, ambiente, amount, currency):
        key = (comp, produto, ambiente)
        if key not in out:
            out[key] = {"competencia": comp, "produto": produto,
                        "ambiente": ambiente, "amount": 0.0, "currency": currency}
        out[key]["amount"] += amount

    unmapped: dict[str, float] = defaultdict(float)
    for row in rows:
        env = resolve_environment(row["account_id"])
        mapped = classify_product_tag(row["product_raw"])
        if mapped:
            add(row["competencia"], mapped, env, row["amount"], row["currency"])
        else:
            if row["product_raw"]:
                unmapped[row["product_raw"]] += row["amount"]
            add(row["competencia"], SHARED_LABEL, env, row["amount"], row["currency"])

    if unmapped:
        log.warning("%d `product` tag value(s) present in the account but NOT in "
                    "PRODUCT_TAG_MAP -- their cost went to '%s':",
                    len(unmapped), SHARED_LABEL)
        for value, amount in sorted(unmapped.items(), key=lambda kv: -kv[1]):
            log.warning("    product=%r -> normalized %r  %.2f",
                        value, normalize_tag_value(value), amount)
        log.warning("Add the normalized key to PRODUCT_TAG_MAP to attribute it.")
    return list(out.values())


def fetch_untagged_by_service(client, start: str, end: str) -> list[dict]:
    """Cost with NO `product` tag, grouped by account and service.

    Drives the tagging backlog: it says which services are worth tagging (and how
    much moves out of Compartilhado when they are) versus which are shared by
    nature. Diagnostic only — never loaded into BigQuery."""
    rows: list[dict] = []
    next_token = None
    while True:
        params = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [
                {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
                {"Type": "DIMENSION", "Key": "SERVICE"},
            ],
            "Filter": {"Tags": {"Key": PRODUCT_TAG_KEY, "MatchOptions": ["ABSENT"]}},
        }
        if next_token:
            params["NextPageToken"] = next_token
        resp = client.get_cost_and_usage(**params)
        for bucket in resp["ResultsByTime"]:
            for group in bucket["Groups"]:
                account_id, service = group["Keys"]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                if amount <= 0:
                    continue
                rows.append({
                    "ambiente": resolve_environment(account_id),
                    "servico": service,
                    "bucket": classify_service(service),
                    "amount": amount,
                    "currency": group["Metrics"]["UnblendedCost"]["Unit"],
                })
        next_token = resp.get("NextPageToken")
        if not next_token:
            break
    return rows


def report_untagged(rows: list[dict], month: str, usd_brl_rate: float | None,
                    output_path: str | None) -> None:
    """Log the untagged cost split by bucket and optionally write the detail CSV."""
    if not rows:
        log.info("No untagged cost found -- every cost line carries a `product` tag.")
        return
    rate = usd_brl_rate or 1.0
    by_bucket: dict[str, float] = defaultdict(float)
    for r in rows:
        r["competencia"] = month
        r["valor_brl"] = round(r["amount"] * rate, 2) if r["currency"] == "USD" else round(r["amount"], 2)
        by_bucket[r["bucket"]] += r["valor_brl"]
    total = sum(by_bucket.values())

    log.info("--- Untagged cost (no `product` tag) = R$ %.2f ---", total)
    for bucket in ("TAGGABLE", "SHARED", "UNCLASSIFIED"):
        if bucket not in by_bucket:
            continue
        value = by_bucket[bucket]
        log.info("  %-14s R$ %12.2f  (%4.1f%% of untagged)", bucket, value, value / total * 100)
    log.info("  TAGGABLE is what leaves 'Compartilhado' once the `product` tag is applied.")
    top = sorted(rows, key=lambda r: -r["valor_brl"])[:8]
    log.info("  Biggest untagged services:")
    for r in top:
        log.info("    %-10s %-44s R$ %10.2f  [%s]",
                 r["ambiente"], r["servico"][:44], r["valor_brl"], r["bucket"])
    if by_bucket.get("UNCLASSIFIED"):
        log.warning("  UNCLASSIFIED services are not in either hint list -- add them to "
                    "TAGGABLE_SERVICE_HINTS or GENUINELY_SHARED_SERVICE_HINTS.")

    if output_path:
        fields = ["competencia", "ambiente", "servico", "bucket", "valor_brl"]
        with open(output_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: -r["valor_brl"]))
        log.info("Wrote %d untagged-cost rows to %s", len(rows), output_path)


def convert_currency(rows: list[dict], usd_brl_rate: float | None) -> list[dict]:
    for row in rows:
        if row["currency"] == "USD" and usd_brl_rate:
            row["valor_brl"] = round(row["amount"] * usd_brl_rate, 2)
        else:
            row["valor_brl"] = round(row["amount"], 2)
    return rows


def write_csv(rows: list[dict], output_path: str) -> None:
    fields = ["competencia", "produto", "ambiente", "valor_brl"]
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %d rows to %s", len(rows), output_path)


def print_summary(rows: list[dict]) -> None:
    total = sum(r["valor_brl"] for r in rows)
    by_product: dict[str, float] = defaultdict(float)
    for r in rows:
        by_product[r["produto"]] += r["valor_brl"]
    log.info("--- Summary (R$) ---")
    for product, value in sorted(by_product.items(), key=lambda x: -x[1]):
        share = (value / total * 100) if total else 0
        log.info("  %-16s %12.2f  (%4.1f%%)", product, value, share)
    log.info("  %-16s %12.2f", "TOTAL", total)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract AWS cost with product cascade attribution.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM")
    parser.add_argument("--output", default="custos_cloud.csv", help="Output CSV path")
    parser.add_argument("--usd-brl-rate", type=float, default=None, help="USD->BRL rate (e.g. 5.04)")
    parser.add_argument("--profile", default=None, help="AWS named profile")
    parser.add_argument("--untagged-report", default="untagged_by_service.csv",
                        help="CSV with untagged cost per service (tagging backlog); "
                             "pass an empty string to skip")
    parser.add_argument("--audit-sa", "--audit-ue", "--audit-env-sa", "--audit-env-ue",
                        dest="audit_legacy", default=None,
                        help=argparse.SUPPRESS)  # accepted and ignored; see below
    parser.add_argument("--bq-project", default=None,
                        help="GCP project id to load into BigQuery (optional)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    args = parser.parse_args()

    # Cash-basis convention (mirrors get-gcp-costs): competencia=YYYY-MM is the
    # AWS bill paid in that month, which covers the PREVIOUS month's usage.
    usage_month = prev_month(args.month)
    start, end = month_to_period(usage_month)
    log.info("Report month=%s -> querying AWS usage period %s to %s.", args.month, start, end)

    if args.audit_legacy:
        log.warning("--audit-* flags are ignored: the resource-count weighting they fed "
                    "was removed (resource count does not track cost, and the tag audit "
                    "cannot see untagged resources). Untagged cost stays in '%s' and is "
                    "rateado downstream by relatorio_pt.vw_eficiencia.", SHARED_LABEL)

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    client = session.client("ce", region_name="us-east-1")

    try:
        raw_rows = fetch_cost_by_account_and_tag(client, start, end)
    except Exception as exc:
        log.error("Failed to fetch cost data: %s", exc)
        return 1

    # Label rows with the REPORT month (not the usage month they came from).
    for r in raw_rows:
        r["competencia"] = args.month

    attributed = attribute(raw_rows)
    attributed = convert_currency(attributed, args.usd_brl_rate)
    attributed.sort(key=lambda r: (r["competencia"], r["ambiente"], r["produto"]))
    write_csv(attributed, args.output)
    print_summary(attributed)

    # Diagnostic: how much of 'Compartilhado' is only there for lack of a tag.
    try:
        untagged = fetch_untagged_by_service(client, start, end)
        report_untagged(untagged, args.month, args.usd_brl_rate,
                        args.untagged_report or None)
    except Exception as exc:
        # Never fail the close over a diagnostic — the cost rows above are already good.
        log.warning("Untagged-cost diagnostic failed (cost rows are unaffected): %s", exc)

    # --- Optional: load into BigQuery ---
    if args.bq_project:
        try:
            from bq_loader import BigQueryLoader
        except ImportError:
            log.error("bq_loader.py not found. Copy it next to this script or set PYTHONPATH.")
            return 1
        loader = BigQueryLoader(project_id=args.bq_project, dataset=args.bq_dataset)
        bq_rows = []
        for r in attributed:
            ambiente = r["ambiente"]
            provedor = "Azure" if ambiente == "Azure" else ("GCP" if ambiente == "GCP" else "AWS")
            is_usd = r.get("currency") == "USD" and args.usd_brl_rate
            bq_rows.append({
                "competencia": r["competencia"],
                "categoria": "Cloud",
                "produto": r["produto"],
                "cloud_provedor": provedor,
                "item": ambiente,
                "valor_brl": float(r["valor_brl"]),
                "fonte": "aws",
                "valor_usd": round(float(r["amount"]), 4) if is_usd else None,
                "taxa_usd_brl": round(args.usd_brl_rate, 4) if is_usd else None,
            })
        # idempotent per (month, fonte) — only replaces AWS rows, leaves gcp/azure intact
        for mes in sorted({r["competencia"] for r in bq_rows}):
            mes_rows = [r for r in bq_rows if r["competencia"] == mes]
            loader.replace_month("custos", competencia=mes, rows=mes_rows, fonte="aws")
        log.info("Loaded AWS cloud cost into BigQuery (%s.custos).", args.bq_dataset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
