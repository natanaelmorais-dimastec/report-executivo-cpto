"""
AWS Cost Explorer extractor for the monthly Products & Technology report (Dimastec).

Produces rows matching the `custos_cloud` tab of the report spreadsheet:

    competencia, produto, ambiente, valor_brl

Environment comes from the AWS account (accounts are separated by environment).
Product is resolved by a 3-step CASCADE, because the account already has a usable
(if partial) tagging convention:

    1. Explicit `product` cost allocation tag  (dtfaceum -> Faceum, mydhas -> Mydhas,
       integracao/integracao-dtfaceum -> Integração, ai -> AI, etc.)
    2. Name-based inference  (resource Name/Cluster contains 'faceum', 'mydhas',
       'airflow'/'nifi' -> Integração, 'brain' -> AI, 'cl-dimastec-prod' -> Faceum)
    3. Fallback -> 'Compartilhado'  (shared infra: NAT, Jenkins, networking, backups)

    Products in the report: Faceum, Mydhas, Integração, AI, Compartilhado.

Because step 2 works on names that already exist, the product split is available
RETROACTIVELY (e.g. for May) without applying any new tags.

Cost Explorer cannot return per-resource names, so this extractor uses two inputs:
    A) Cost Explorer  -> authoritative monthly cost per (account, `product` tag)
    B) tag audit CSVs -> name->product weights, used to attribute the untagged cost.

Usage:
    python extractor.py --month 2026-05 --usd-brl-rate 5.04 \
        --audit-sa tag_audit.csv --audit-ue tag_audit_useast.csv \
        --output custos_cloud_2026-05.csv

Requirements: boto3, ce:GetCostAndUsage. Run from the management/payer account.
NOTE: All log messages are intentionally in English.
"""

import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import date

import boto3

# --- Configuration -----------------------------------------------------------

ACCOUNTS = {
    "295574221328": "Produção",
    "058264268348": "QA",
}

PRODUCT_TAG_KEY = "product"

PRODUCT_TAG_MAP = {
    "dtfaceum": "Faceum",
    "integracao-dtfaceum": "Integração",
    "integracao": "Integração",
    "mydhas": "Mydhas",
    "ai": "AI",
    "compartilhado": "Compartilhado",
    "finance-report": "Compartilhado",  # adjust if finance-report becomes its own product
}

# Name-inference rules, applied in order. First match wins.
NAME_RULES = [
    ("faceum", "Faceum"),
    ("mydhas", "Mydhas"),
    ("airflow", "Integração"),
    ("nifi", "Integração"),
    ("brain", "AI"),
    ("epi-monitoring", "Faceum"),
    ("cl-dimastec-prod", "Faceum"),
]

SHARED_LABEL = "Compartilhado"

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


def classify_product_tag(raw_value: str) -> str | None:
    """Step 1: explicit `product` tag value -> report product label."""
    if not raw_value:
        return None
    return PRODUCT_TAG_MAP.get(raw_value.lower())


def classify_by_name(*texts: str) -> str | None:
    """Step 2: infer product from any resource name/cluster/arn text."""
    blob = " ".join(t for t in texts if t).lower()
    for substring, product in NAME_RULES:
        if substring in blob:
            return product
    return None


def parse_tags_field(tags_field: str) -> dict[str, str]:
    """Parse the 'k1=v1; k2=v2' tags column from the audit CSV."""
    result: dict[str, str] = {}
    for pair in tags_field.split("; "):
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k] = v
    return result


def build_product_weights(audit_paths: dict[str, str]) -> dict[str, dict[str, float]]:
    """Per environment, fraction of resources mapped to each product (by the cascade)."""
    weights: dict[str, dict[str, float]] = {}
    for env, path in audit_paths.items():
        counts: dict[str, int] = defaultdict(int)
        try:
            with open(path, newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    tags = parse_tags_field(row.get("tags", ""))
                    product = (
                        classify_product_tag(tags.get(PRODUCT_TAG_KEY, ""))
                        or classify_by_name(tags.get("Name", ""), tags.get("Cluster", ""), row.get("resource", ""))
                        or SHARED_LABEL
                    )
                    counts[product] += 1
        except FileNotFoundError:
            log.warning("Audit file for %s not found: %s -- weights skipped.", env, path)
            continue
        total = sum(counts.values())
        if total:
            weights[env] = {p: c / total for p, c in counts.items()}
            log.info("Weights for %s: %s", env, {p: round(w, 3) for p, w in weights[env].items()})
    return weights


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


def attribute(rows: list[dict], weights: dict[str, dict[str, float]]) -> list[dict]:
    """Map tagged cost directly; split untagged cost by audit-derived name weights."""
    out: dict[tuple, dict] = {}

    def add(comp, produto, ambiente, amount, currency):
        key = (comp, produto, ambiente)
        if key not in out:
            out[key] = {"competencia": comp, "produto": produto,
                        "ambiente": ambiente, "amount": 0.0, "currency": currency}
        out[key]["amount"] += amount

    for row in rows:
        env = resolve_environment(row["account_id"])
        mapped = classify_product_tag(row["product_raw"])
        if mapped:
            add(row["competencia"], mapped, env, row["amount"], row["currency"])
        else:
            env_weights = weights.get(env, {SHARED_LABEL: 1.0})
            for produto, weight in env_weights.items():
                add(row["competencia"], produto, env, row["amount"] * weight, row["currency"])
    return list(out.values())


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
    parser.add_argument("--audit-sa", default=None, help="tag_audit.csv for the prod/sa-east-1 environment")
    parser.add_argument("--audit-ue", default=None, help="tag_audit CSV for the us-east-1 environment")
    parser.add_argument("--audit-env-sa", default="Produção", help="Environment label for --audit-sa")
    parser.add_argument("--audit-env-ue", default="QA", help="Environment label for --audit-ue")
    parser.add_argument("--bq-project", default=None,
                        help="GCP project id to load into BigQuery (optional)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    args = parser.parse_args()

    # Cash-basis convention (mirrors get-gcp-costs): competencia=YYYY-MM is the
    # AWS bill paid in that month, which covers the PREVIOUS month's usage.
    usage_month = prev_month(args.month)
    start, end = month_to_period(usage_month)
    log.info("Report month=%s -> querying AWS usage period %s to %s.", args.month, start, end)

    audit_paths = {}
    if args.audit_sa:
        audit_paths[args.audit_env_sa] = args.audit_sa
    if args.audit_ue:
        audit_paths[args.audit_env_ue] = args.audit_ue
    weights = build_product_weights(audit_paths) if audit_paths else {}
    if not weights:
        log.warning("No audit weights loaded; untagged cost will all go to '%s'.", SHARED_LABEL)

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

    attributed = attribute(raw_rows, weights)
    attributed = convert_currency(attributed, args.usd_brl_rate)
    attributed.sort(key=lambda r: (r["competencia"], r["ambiente"], r["produto"]))
    write_csv(attributed, args.output)
    print_summary(attributed)
    log.info("Note: untagged cost was split by name-based weights from the tag audit. "
             "Apply the `product` tag to make the split exact.")

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
