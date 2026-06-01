"""
MongoDB Atlas cost extractor for the monthly Products & Technology report (Dimastec).

Pulls a month's invoice from the Atlas Billing API and loads its line items
into `relatorio_pt.custos`:

    competencia, categoria=Ferramentas, produto, cloud_provedor=NULL,
    item=<cluster_or_group> (<service>), valor_brl, fonte=mongodb-atlas

Atlas creates ONE invoice per organization per billing period (monthly). The
extractor lists invoices, picks the one whose `startDate` falls in the target
month, fetches its details, and groups lineItems by cluster (or group/project,
when the line item is not cluster-bound — e.g. backup storage).

Product attribution cascade:
  1. CLUSTER_PRODUCT_MAP[clusterName]
  2. GROUP_PRODUCT_MAP[groupName]   (Atlas projects)
  3. SHARED_LABEL ("Compartilhado")

Auth: HTTP Digest using a Programmatic API Key generated under
  Atlas -> Organization Access Manager -> API Keys
  (role "Organization Billing Viewer" is enough)

Usage:
    cp .env.example .env       # then fill in keys
    python extractor.py --month 2026-05 --usd-brl-rate 5.04 \\
        --org-id <ATLAS_ORG_ID> --bq-project executive-reports-cpto

Requirements: requests, python-dotenv, google-cloud-bigquery.
NOTE: All log messages are intentionally in English.
"""

import argparse
import csv
import logging
import os
import sys
from collections import defaultdict

import requests
from dotenv import load_dotenv
from requests.auth import HTTPDigestAuth

# --- Configuration -----------------------------------------------------------

# Atlas clusterName -> report product. Extend after first run logs the unmapped clusters.
CLUSTER_PRODUCT_MAP: dict[str, str] = {
    # "faceum-prod":   "Faceum",
    # "mydhas-prd":    "Mydhas",
    # "ai-sandbox":    "AI",
}

# Atlas project (group) name -> product. Used as fallback when a line item has no cluster
# (e.g. organization-level backup storage, support, data transfer).
GROUP_PRODUCT_MAP: dict[str, str] = {
    # "Production":    "Compartilhado",
    # "Faceum":        "Faceum",
}

SHARED_LABEL = "Compartilhado"
ATLAS_API_VERSION = "application/vnd.atlas.2023-01-01+json"
ATLAS_BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mongodb_atlas_extractor")


def atlas_get(path: str, public_key: str, private_key: str, params: dict | None = None) -> dict:
    """GET helper with the Atlas API version header and Digest auth."""
    url = f"{ATLAS_BASE_URL}{path}"
    resp = requests.get(
        url,
        auth=HTTPDigestAuth(public_key, private_key),
        headers={"Accept": ATLAS_API_VERSION},
        params=params,
        timeout=60,
    )
    if resp.status_code == 401:
        raise RuntimeError("Atlas API 401 — check MONGODB_ATLAS_PUBLIC_KEY / PRIVATE_KEY and that the key has 'Organization Billing Viewer' role.")
    resp.raise_for_status()
    return resp.json()


def list_invoices(org_id: str, public_key: str, private_key: str) -> list[dict]:
    """Paginated list of invoices for the org (most recent first)."""
    invoices: list[dict] = []
    page = 1
    while True:
        data = atlas_get(
            f"/orgs/{org_id}/invoices",
            public_key, private_key,
            params={"pageNum": page, "itemsPerPage": 100},
        )
        batch = data.get("results", [])
        invoices.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    log.info("Found %d invoices in org %s.", len(invoices), org_id)
    return invoices


def pick_invoice_for_month(invoices: list[dict], month: str) -> dict | None:
    """Return the invoice whose startDate falls in `month` (YYYY-MM), or None."""
    for inv in invoices:
        start = (inv.get("startDate") or "")[:7]   # 'YYYY-MM' from ISO8601
        if start == month:
            return inv
    return None


def fetch_invoice_details(org_id: str, invoice_id: str, public_key: str, private_key: str) -> dict:
    return atlas_get(f"/orgs/{org_id}/invoices/{invoice_id}", public_key, private_key)


def attribute_line(line: dict) -> tuple[str, str]:
    """Return (produto, item_label) for a single lineItem."""
    cluster = (line.get("clusterName") or "").strip()
    group = (line.get("groupName") or "").strip()
    service = (line.get("service") or "Atlas").strip()

    if cluster and cluster in CLUSTER_PRODUCT_MAP:
        return CLUSTER_PRODUCT_MAP[cluster], f"{cluster} ({service})"
    if group and group in GROUP_PRODUCT_MAP:
        return GROUP_PRODUCT_MAP[group], f"{group} / {service}"
    label = cluster or group or "(no-cluster)"
    return SHARED_LABEL, f"{label} ({service})"


def aggregate(invoice: dict, competencia: str, usd_brl_rate: float) -> list[dict]:
    """Sum lineItems by (produto, item) and convert to BRL rows."""
    sums: dict[tuple, dict] = {}
    unmapped_clusters: set[str] = set()

    for line in invoice.get("lineItems", []):
        cents = int(line.get("totalPriceCents") or 0)
        if cents == 0:
            continue
        produto, item = attribute_line(line)
        cluster = (line.get("clusterName") or "").strip()
        if produto == SHARED_LABEL and cluster and cluster not in CLUSTER_PRODUCT_MAP:
            unmapped_clusters.add(cluster)
        key = (produto, item)
        if key not in sums:
            sums[key] = {
                "competencia": competencia,
                "produto": produto,
                "item": item,
                "valor_usd": 0.0,
            }
        sums[key]["valor_usd"] += cents / 100.0

    if unmapped_clusters:
        log.warning("Unmapped clusters fell back to '%s': %s — add to CLUSTER_PRODUCT_MAP.",
                    SHARED_LABEL, ", ".join(sorted(unmapped_clusters)))

    rows: list[dict] = []
    for s in sums.values():
        rows.append({
            "competencia": s["competencia"],
            "produto": s["produto"],
            "item": s["item"],
            "valor_brl": round(s["valor_usd"] * usd_brl_rate, 2),
        })
    return rows


def write_csv(rows: list[dict], output_path: str) -> None:
    fields = ["competencia", "produto", "item", "valor_brl"]
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
    parser = argparse.ArgumentParser(description="Extract MongoDB Atlas monthly invoice into custos.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM (matches invoice startDate)")
    parser.add_argument("--org-id", default=None,
                        help="Atlas Organization ID (or set MONGODB_ATLAS_ORG_ID in .env)")
    parser.add_argument("--usd-brl-rate", type=float, default=1.0,
                        help="USD->BRL rate (Atlas bills in USD). Default 1.0 = no conversion.")
    parser.add_argument("--output", default="custos_mongodb_atlas.csv", help="Output CSV path")
    parser.add_argument("--bq-project", default=None,
                        help="GCP project id to load into BigQuery (optional)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    args = parser.parse_args()

    load_dotenv()
    public_key = os.environ.get("MONGODB_ATLAS_PUBLIC_KEY")
    private_key = os.environ.get("MONGODB_ATLAS_PRIVATE_KEY")
    org_id = args.org_id or os.environ.get("MONGODB_ATLAS_ORG_ID")
    if not (public_key and private_key and org_id):
        log.error("Missing credentials. Set MONGODB_ATLAS_PUBLIC_KEY, MONGODB_ATLAS_PRIVATE_KEY, "
                  "and either --org-id or MONGODB_ATLAS_ORG_ID in .env.")
        return 1

    try:
        invoices = list_invoices(org_id, public_key, private_key)
    except Exception as exc:
        log.error("Failed to list invoices: %s", exc)
        return 1

    # Cash-basis convention (mirrors AWS/GCP): competencia=YYYY-MM is what was
    # paid in that month, i.e. the previous calendar month's Atlas invoice.
    year_i, mon_i = (int(p) for p in args.month.split("-"))
    if mon_i == 1:
        usage_month = f"{year_i - 1}-12"
    else:
        usage_month = f"{year_i}-{mon_i - 1:02d}"
    log.info("Report month=%s -> picking Atlas invoice with startDate in %s.",
             args.month, usage_month)

    invoice = pick_invoice_for_month(invoices, usage_month)
    if not invoice:
        log.warning("No invoice with startDate in %s. Invoices visible: %s",
                    usage_month,
                    sorted({(i.get('startDate') or '')[:7] for i in invoices}, reverse=True)[:6])
        return 0

    log.info("Selected invoice %s (startDate=%s)", invoice.get("id"), invoice.get("startDate"))
    try:
        detail = fetch_invoice_details(org_id, invoice["id"], public_key, private_key)
    except Exception as exc:
        log.error("Failed to fetch invoice detail: %s", exc)
        return 1

    rows = aggregate(detail, args.month, args.usd_brl_rate)
    rows.sort(key=lambda r: (r["produto"], r["item"]))
    write_csv(rows, args.output)
    print_summary(rows)

    if args.bq_project:
        try:
            from bq_loader import BigQueryLoader
        except ImportError:
            log.error("bq_loader.py not found. Copy it next to this script or set PYTHONPATH.")
            return 1
        loader = BigQueryLoader(project_id=args.bq_project, dataset=args.bq_dataset)
        bq_rows = [
            {
                "competencia": r["competencia"],
                "categoria": "Ferramentas",
                "produto": r["produto"],
                "cloud_provedor": None,
                "item": r["item"],
                "valor_brl": float(r["valor_brl"]),
                "fonte": "mongodb-atlas",
            }
            for r in rows
        ]
        loader.replace_month("custos", competencia=args.month, rows=bq_rows, fonte="mongodb-atlas")
        log.info("Loaded MongoDB Atlas cost into BigQuery (%s.custos).", args.bq_dataset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
