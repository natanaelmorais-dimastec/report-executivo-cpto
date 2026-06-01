"""
GCP Cost extractor for the monthly Products & Technology report (Dimastec).

Reads the Cloud Billing → BigQuery export (Standard) and produces rows for the
`custos` table of the report dataset (relatorio_pt), in the same shape the AWS
extractor uses:

    competencia, categoria=Cloud, produto, cloud_provedor=GCP, item=<project_id>,
    valor_brl, fonte=gcp

Product attribution uses the same CASCADE shape as the AWS extractor, but adapted
to GCP primitives:

    1. line-item `labels.product`   (resource-level label, normalized by PRODUCT_LABEL_MAP)
    2. project-level `project.labels.product`  (project-wide default label)
    3. PROJECT_PRODUCT_MAP          (hardcoded fallback: project_id -> product)
    4. weighted split from project_audit.csv  (when a project mixes products and is
       unlabeled — split unattributed cost using the cost-shares observed in the audit)
    5. fallback -> 'Compartilhado'

Step 4 mirrors AWS's tag-audit weights; produce the CSV first with project_audit.py.

Usage:
    python extractor.py --month 2026-05 \\
        --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_XXXX \\
        --usd-brl-rate 5.04 \\
        --audit project_audit.csv \\
        --bq-project executive-reports-cpto

Requirements: google-cloud-bigquery, ADC (gcloud auth application-default login).
NOTE: All log messages are intentionally in English.
"""

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict

from google.cloud import bigquery

# --- Configuration -----------------------------------------------------------

# Project id -> product (canonical name as used in the report).
# Seed with what we know; extend after the first project_audit.py run.
PROJECT_PRODUCT_MAP: dict[str, str] = {
    "executive-reports-cpto": "Compartilhado",
    "monitoratec-59fe3": "Faceum",
    "my-project-91598-1687878101443": "Faceum",
}

PRODUCT_LABEL_KEY = "product"

# Lowercased label value -> canonical product name. Keep in sync with project_audit.py.
PRODUCT_LABEL_MAP: dict[str, str] = {
    "faceum": "Faceum",
    "dtfaceum": "Faceum",
    "dt-faceum": "Faceum",
    "mydhas": "Mydhas",
    "ai": "AI",
    "integracao": "Integração",
    "integracao-faceum": "Integração",
    "compartilhado": "Compartilhado",
}

SHARED_LABEL = "Compartilhado"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gcp_cost_extractor")


def month_to_invoice(month: str) -> str:
    """Map report competencia ('YYYY-MM') to the GCP invoice.month paid in that
    competencia, i.e. the PREVIOUS calendar month's usage. The Dimastec report is
    cash-basis: competencia=2026-05 reflects the invoice paid in May, which covers
    April usage (GCP invoice.month=202604)."""
    year, mon = month.split("-")
    y, m = int(year), int(mon)
    if m == 1:
        return f"{y - 1}12"
    return f"{y}{m - 1:02d}"


def normalize_product(raw: str | None) -> str | None:
    """Map a raw label value to the canonical product name, or None if unknown."""
    if not raw:
        return None
    return PRODUCT_LABEL_MAP.get(raw.strip().lower())


def load_audit_weights(audit_path: str | None) -> dict[str, dict[str, float]]:
    """Read project_audit.csv and return {project_id: {product: weight}}."""
    if not audit_path:
        return {}
    weights: dict[str, dict[str, float]] = {}
    try:
        with open(audit_path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                pid = row.get("project_id", "")
                raw = row.get("derived_weights_json", "")
                if not pid or not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Bad weights JSON for project %s — skipping.", pid)
                    continue
                if parsed:
                    weights[pid] = {p: float(w) for p, w in parsed.items()}
    except FileNotFoundError:
        log.warning("Audit file not found: %s — unlabeled cost will fall through to '%s'.",
                    audit_path, SHARED_LABEL)
        return {}
    if weights:
        log.info("Loaded weighted-split fallback for %d project(s) from audit.", len(weights))
    return weights


def fetch_billing_rows(client: bigquery.Client, table: str, invoice_month: str) -> list[bigquery.Row]:
    """One row per (project, line-item label, project label, currency) for the month."""
    sql = f"""
        SELECT
          project.id AS project_id,
          project.name AS project_name,
          (SELECT value FROM UNNEST(labels) WHERE key = @product_key LIMIT 1) AS line_label,
          (SELECT value FROM UNNEST(project.labels) WHERE key = @product_key LIMIT 1) AS project_label,
          currency,
          SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS cost_native
        FROM `{table}`
        WHERE invoice.month = @invoice_month
        GROUP BY project_id, project_name, line_label, project_label, currency
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
    log.info("Fetched %d billing rows from %s.", len(rows), table)
    return rows


def to_brl(amount: float, currency: str | None, usd_brl_rate: float) -> float:
    """Convert to BRL: pass-through for BRL, multiply for USD; warn for anything else."""
    if currency == "BRL" or not currency:
        return amount
    if currency == "USD":
        return amount * usd_brl_rate
    log.warning("Unhandled currency '%s' — treating as BRL (review the export).", currency)
    return amount


def attribute(
    rows: list[bigquery.Row],
    competencia: str,
    usd_brl_rate: float,
    audit_weights: dict[str, dict[str, float]],
) -> list[dict]:
    """Apply the cascade and aggregate to (competencia, produto, project_id)."""
    out: dict[tuple, dict] = {}

    def add(produto: str, project_id: str, project_name: str, amount_brl: float):
        key = (competencia, produto, project_id)
        if key not in out:
            out[key] = {
                "competencia": competencia,
                "produto": produto,
                "project_id": project_id,
                "project_name": project_name,
                "valor_brl": 0.0,
            }
        out[key]["valor_brl"] += amount_brl

    for row in rows:
        pid = row.project_id or "(no-project)"
        pname = row.project_name or ""
        amount_brl = to_brl(float(row.cost_native), row.currency, usd_brl_rate)

        # 1) line-item label
        mapped = normalize_product(row.line_label)
        if mapped:
            add(mapped, pid, pname, amount_brl)
            continue

        # 2) project-level label
        mapped = normalize_product(row.project_label)
        if mapped:
            add(mapped, pid, pname, amount_brl)
            continue

        # 3) hardcoded project map
        if pid in PROJECT_PRODUCT_MAP:
            add(PROJECT_PRODUCT_MAP[pid], pid, pname, amount_brl)
            continue

        # 4) audit-derived weights for this project
        weights = audit_weights.get(pid)
        if weights:
            for produto, weight in weights.items():
                add(produto, pid, pname, amount_brl * weight)
            continue

        # 5) fallback
        add(SHARED_LABEL, pid, pname, amount_brl)

    for row in out.values():
        row["valor_brl"] = round(row["valor_brl"], 2)
    return list(out.values())


def write_csv(rows: list[dict], output_path: str) -> None:
    fields = ["competencia", "produto", "project_id", "project_name", "valor_brl"]
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
    parser = argparse.ArgumentParser(description="Extract GCP cost from the Billing BigQuery export, with product cascade attribution.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM")
    parser.add_argument("--billing-export-table", required=True,
                        help="Fully-qualified billing export table: project.dataset.gcp_billing_export_v1_XXXX")
    parser.add_argument("--usd-brl-rate", type=float, default=1.0,
                        help="USD->BRL rate when billing currency is USD (default 1.0 = no conversion).")
    parser.add_argument("--audit", default=None,
                        help="project_audit.csv with derived per-project weights (optional)")
    parser.add_argument("--output", default="custos_cloud_gcp.csv", help="Output CSV path")
    parser.add_argument("--bq-project", default=None,
                        help="GCP project id to load into BigQuery (optional; without it, CSV only)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset for the load")
    parser.add_argument("--query-project", default="executive-reports-cpto",
                        help="GCP project used by the BigQuery client for queries (billing JobUser)")
    args = parser.parse_args()

    competencia = args.month
    invoice_month = month_to_invoice(args.month)

    audit_weights = load_audit_weights(args.audit)

    client = bigquery.Client(project=args.query_project)
    try:
        raw_rows = fetch_billing_rows(client, args.billing_export_table, invoice_month)
    except Exception as exc:
        log.error("Failed to query billing export: %s", exc)
        log.error("Check the table name and IAM (see SETUP_GCP_BILLING_EXPORT.md).")
        return 1

    if not raw_rows:
        log.warning("No cost found for invoice month %s — export may not be populated for this month yet.",
                    invoice_month)
        return 0

    attributed = attribute(raw_rows, competencia, args.usd_brl_rate, audit_weights)
    attributed.sort(key=lambda r: (r["competencia"], r["project_id"], r["produto"]))
    write_csv(attributed, args.output)
    print_summary(attributed)

    # --- Optional: load into BigQuery ---
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
                "categoria": "Cloud",
                "produto": r["produto"],
                "cloud_provedor": "GCP",
                "item": r["project_id"],
                "valor_brl": float(r["valor_brl"]),
                "fonte": "gcp",
            }
            for r in attributed
        ]
        # idempotent per (month, fonte) — only replaces GCP rows, leaves aws/azure intact
        for mes in sorted({r["competencia"] for r in bq_rows}):
            mes_rows = [r for r in bq_rows if r["competencia"] == mes]
            loader.replace_month("custos", competencia=mes, rows=mes_rows, fonte="gcp")
        log.info("Loaded GCP cloud cost into BigQuery (%s.custos).", args.bq_dataset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
