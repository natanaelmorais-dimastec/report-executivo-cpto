"""
GitHub Copilot cost extractor for the monthly Products & Technology report (Dimastec).

Queries the GitHub Enhanced Billing Platform usage endpoint for the target month
and aggregates Copilot line items into `relatorio_pt.custos`:

    competencia, categoria=Ferramentas, produto=Compartilhado, cloud_provedor=NULL,
    item="GitHub Copilot - <sku>", valor_brl, fonte=github-copilot

All Copilot cost goes to produto=Compartilhado because seats are per-developer and
the report's product attribution operates at app/service level, not per-engineer.
If Business and Enterprise SKUs coexist, they show as separate `item` rows so the
breakdown is preserved.

Auth: a fine-grained or classic PAT with org-level billing read.
  Classic PAT scope: `manage_billing:copilot` (read-only billing)
  Fine-grained: org permission "Plan" -> "Read"

Usage:
    cp .env.example .env       # then fill in GITHUB_TOKEN
    python extractor.py --month 2026-05 --usd-brl-rate 5.04 \\
        --org <github-org-slug> --bq-project executive-reports-cpto

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

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
COPILOT_PRODUCT_NAMES = {"copilot", "github copilot"}  # case-insensitive match

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_copilot_extractor")


def fetch_usage(org: str, token: str, year: int, month: int) -> list[dict]:
    """Call the Enhanced Billing usage endpoint and return raw usageItems."""
    url = f"{GITHUB_API_BASE}/organizations/{org}/settings/billing/usage"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    params = {"year": year, "month": month}
    log.info("Querying %s for org=%s year=%d month=%d", url, org, year, month)
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    if resp.status_code == 401:
        raise RuntimeError("GitHub 401 — check GITHUB_TOKEN and that it has billing scope.")
    if resp.status_code == 403:
        raise RuntimeError(
            "GitHub 403 — token lacks org billing access. For a classic PAT, "
            "ensure scope 'manage_billing:copilot' is enabled."
        )
    if resp.status_code == 404:
        raise RuntimeError(f"GitHub 404 — org '{org}' not found or not visible to this token.")
    resp.raise_for_status()
    data = resp.json()
    items = data.get("usageItems", [])
    log.info("Fetched %d usage items total.", len(items))
    return items


def aggregate_copilot(items: list[dict]) -> dict[str, float]:
    """Sum netAmount (USD) per SKU for Copilot items only."""
    by_sku: dict[str, float] = defaultdict(float)
    for item in items:
        product = (item.get("product") or "").strip().lower()
        if product not in COPILOT_PRODUCT_NAMES:
            continue
        sku = (item.get("sku") or "Copilot").strip()
        net = float(item.get("netAmount") or 0)
        by_sku[sku] += net
    return dict(by_sku)


def to_rows(by_sku: dict[str, float], competencia: str, usd_brl_rate: float) -> list[dict]:
    rows: list[dict] = []
    for sku, usd in sorted(by_sku.items()):
        if usd == 0:
            continue
        rows.append({
            "competencia": competencia,
            "produto": "Compartilhado",
            "item": f"GitHub Copilot - {sku}",
            "valor_brl": round(usd * usd_brl_rate, 2),
            "valor_usd": round(usd, 4),
            "taxa_usd_brl": round(usd_brl_rate, 4),
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
    log.info("--- Summary (R$) ---")
    for r in rows:
        log.info("  %-40s %12.2f", r["item"], r["valor_brl"])
    log.info("  %-40s %12.2f", "TOTAL", total)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract GitHub Copilot monthly cost into custos.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM")
    parser.add_argument("--org", default=None,
                        help="GitHub organization slug (or set GITHUB_ORG in .env)")
    parser.add_argument("--usd-brl-rate", type=float, default=1.0,
                        help="USD->BRL rate (GitHub bills in USD). Default 1.0.")
    parser.add_argument("--output", default="custos_github_copilot.csv", help="Output CSV path")
    parser.add_argument("--bq-project", default=None,
                        help="GCP project id to load into BigQuery (optional)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    org = args.org or os.environ.get("GITHUB_ORG")
    if not (token and org):
        log.error("Missing credentials. Set GITHUB_TOKEN in .env and either --org or GITHUB_ORG.")
        return 1

    try:
        year, mon = args.month.split("-")
        year_i, mon_i = int(year), int(mon)
    except ValueError:
        log.error("Invalid --month '%s', expected YYYY-MM.", args.month)
        return 1

    # Cash-basis convention (mirrors AWS/GCP): competencia=YYYY-MM is what was
    # paid in that month, i.e. the previous calendar month's usage.
    usage_year, usage_mon = (year_i - 1, 12) if mon_i == 1 else (year_i, mon_i - 1)
    log.info("Report month=%s -> querying Copilot usage for %04d-%02d.",
             args.month, usage_year, usage_mon)

    try:
        items = fetch_usage(org, token, usage_year, usage_mon)
    except Exception as exc:
        log.error("Failed to fetch usage: %s", exc)
        return 1

    by_sku = aggregate_copilot(items)
    if not by_sku:
        log.warning("No Copilot usage items found for %s. Total items returned: %d", args.month, len(items))
        return 0

    rows = to_rows(by_sku, args.month, args.usd_brl_rate)
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
                "fonte": "github-copilot",
                "valor_usd": float(r["valor_usd"]),
                "taxa_usd_brl": float(r["taxa_usd_brl"]),
            }
            for r in rows
        ]
        loader.replace_month("custos", competencia=args.month, rows=bq_rows, fonte="github-copilot")
        log.info("Loaded GitHub Copilot cost into BigQuery (%s.custos).", args.bq_dataset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
