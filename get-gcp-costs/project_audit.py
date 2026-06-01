"""
GCP project / label auditor for the Dimastec cost report tagging effort.

READ-ONLY. Inspects the GCP Cloud Billing BigQuery export to understand:
  - which projects exist and how much each costs
  - what `product` labels are applied (line-item and project-level)
  - what fraction of each project's cost is labeled vs unlabeled
  - derived per-project weights (cost-share of identified products), useful as a
    proxy when attributing the *unlabeled* portion in extractor.py

This script never writes to BigQuery or modifies any GCP resource. It produces:
  - human-readable log of the landscape
  - project_audit.csv to feed extractor.py's weighted-split fallback

Usage:
    python project_audit.py \\
        --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_XXXX \\
        --month 2026-05 \\
        --output project_audit.csv

Requirements: google-cloud-bigquery, BigQuery Data Viewer on the export dataset.
NOTE: All log messages are intentionally in English.
"""

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict

from google.cloud import bigquery

# Label values we recognize as known products. Keys are lowercased label values
# as they appear in GCP; values are the canonical product name used in the report.
# Keep in sync with extractor.py's PRODUCT_LABEL_MAP.
PRODUCT_LABEL_MAP = {
    "faceum": "Faceum",
    "dtfaceum": "Faceum",
    "dt-faceum": "Faceum",
    "mydhas": "Mydhas",
    "ai": "AI",
    "integracao": "Integração",
    "integracao-faceum": "Integração",
    "compartilhado": "Compartilhado",
}

PRODUCT_LABEL_KEY = "product"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gcp_project_audit")


def month_to_invoice(month: str) -> str:
    """Map report competencia ('YYYY-MM') to the GCP invoice.month paid in that
    competencia (previous calendar month). Mirrors extractor.py — the report is
    cash-basis, so competencia=2026-05 audits invoice.month=202604."""
    year, mon = month.split("-")
    y, m = int(year), int(mon)
    if m == 1:
        return f"{y - 1}12"
    return f"{y}{m - 1:02d}"


def normalize_product(raw: str) -> str | None:
    """Map a raw label value to the canonical product name, or None if unknown."""
    if not raw:
        return None
    return PRODUCT_LABEL_MAP.get(raw.strip().lower())


def fetch_billing_rows(client: bigquery.Client, table: str, invoice_month: str) -> list[bigquery.Row]:
    """One row per (project, line-item product label, project product label) for the month."""
    sql = f"""
        SELECT
          project.id AS project_id,
          project.name AS project_name,
          (SELECT value FROM UNNEST(labels) WHERE key = @product_key LIMIT 1) AS line_label,
          (SELECT value FROM UNNEST(project.labels) WHERE key = @product_key LIMIT 1) AS project_label,
          SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS cost_native,
          ANY_VALUE(currency) AS currency
        FROM `{table}`
        WHERE invoice.month = @invoice_month
        GROUP BY project_id, project_name, line_label, project_label
        HAVING cost_native != 0
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("product_key", "STRING", PRODUCT_LABEL_KEY),
            bigquery.ScalarQueryParameter("invoice_month", "STRING", invoice_month),
        ]
    )
    log.info("Querying billing export for invoice month %s ...", invoice_month)
    rows = list(client.query(sql, job_config=job_config).result())
    log.info("Fetched %d (project x label) groupings.", len(rows))
    return rows


def to_brl(amount: float, currency: str | None, usd_brl_rate: float) -> float:
    """Convert to BRL using the rate when currency is USD; pass-through for BRL."""
    if currency == "BRL" or not currency:
        return amount
    if currency == "USD":
        return amount * usd_brl_rate
    log.warning("Unhandled currency '%s' — treating as BRL (audit only, not authoritative).", currency)
    return amount


def summarize(rows: list[bigquery.Row], usd_brl_rate: float) -> list[dict]:
    """For each project, compute total cost, labeled-cost share, and derived per-product weights."""
    by_project: dict[str, dict] = defaultdict(lambda: {
        "project_name": "",
        "total_brl": 0.0,
        "labeled_brl": 0.0,
        "by_product_brl": defaultdict(float),
        "unknown_labels": set(),
    })

    for row in rows:
        cost_brl = to_brl(float(row.cost_native), row.currency, usd_brl_rate)
        pid = row.project_id or "(no-project)"
        bucket = by_project[pid]
        if row.project_name:
            bucket["project_name"] = row.project_name
        bucket["total_brl"] += cost_brl

        # Cascade for the AUDIT view: line label wins over project label
        raw_label = row.line_label or row.project_label
        product = normalize_product(raw_label)
        if product:
            bucket["labeled_brl"] += cost_brl
            bucket["by_product_brl"][product] += cost_brl
        elif raw_label:
            bucket["unknown_labels"].add(raw_label)

    out: list[dict] = []
    for pid, b in sorted(by_project.items(), key=lambda kv: -kv[1]["total_brl"]):
        total = b["total_brl"]
        coverage = (b["labeled_brl"] / total) if total else 0.0
        weights = {
            p: round(v / b["labeled_brl"], 4)
            for p, v in b["by_product_brl"].items()
        } if b["labeled_brl"] else {}
        out.append({
            "project_id": pid,
            "project_name": b["project_name"],
            "total_brl": round(total, 2),
            "labeled_brl": round(b["labeled_brl"], 2),
            "label_coverage_pct": round(coverage * 100, 1),
            "derived_weights_json": json.dumps(weights, ensure_ascii=False, sort_keys=True),
            "unknown_label_values": "; ".join(sorted(b["unknown_labels"])),
        })
    return out


def print_summary(rows: list[dict]) -> None:
    log.info("--- Per-project audit (sorted by cost desc) ---")
    log.info("  %-40s %12s  %6s  %s", "project_id", "total_brl", "label%", "weights")
    for r in rows:
        log.info(
            "  %-40s %12.2f  %5.1f%%  %s",
            (r["project_id"][:40]),
            r["total_brl"],
            r["label_coverage_pct"],
            r["derived_weights_json"],
        )
    log.info("  %-40s %12.2f", "TOTAL", sum(r["total_brl"] for r in rows))


def write_csv(rows: list[dict], output_path: str) -> None:
    fields = [
        "project_id", "project_name", "total_brl", "labeled_brl",
        "label_coverage_pct", "derived_weights_json", "unknown_label_values",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %d rows to %s", len(rows), output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit GCP billing export for product-label coverage (read-only).")
    parser.add_argument("--billing-export-table", required=True,
                        help="Fully-qualified billing export table: project.dataset.gcp_billing_export_v1_XXXX")
    parser.add_argument("--month", required=True, help="Month to audit, format YYYY-MM")
    parser.add_argument("--bq-project", default="executive-reports-cpto",
                        help="GCP project for the BigQuery client (billing JobUser).")
    parser.add_argument("--usd-brl-rate", type=float, default=1.0,
                        help="USD->BRL rate when billing currency is USD (default 1.0 = no conversion).")
    parser.add_argument("--output", default="project_audit.csv", help="Output CSV path")
    args = parser.parse_args()

    invoice_month = month_to_invoice(args.month)
    client = bigquery.Client(project=args.bq_project)

    try:
        raw = fetch_billing_rows(client, args.billing_export_table, invoice_month)
    except Exception as exc:
        log.error("Failed to query billing export: %s", exc)
        log.error("Check that the export is enabled and the table name is correct (see SETUP_GCP_BILLING_EXPORT.md).")
        return 1

    summary = summarize(raw, args.usd_brl_rate)
    if not summary:
        log.warning("No cost found for %s — the export may not be populated yet for this month.", invoice_month)
        return 0

    print_summary(summary)
    write_csv(summary, args.output)
    log.info("Done. This was read-only — nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
