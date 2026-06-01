"""
SendGrid (via Twilio) cost extractor for the monthly P&T report (Dimastec).

Twilio acquired SendGrid and unified billing — SendGrid email charges appear in
the Twilio account's monthly Usage Records under email-related categories. This
extractor queries Twilio's Usage Records API for the target month, filters for
records whose category mentions email/sendgrid, and loads them into
`relatorio_pt.custos`:

    competencia, categoria=Ferramentas, produto=Compartilhado, cloud_provedor=NULL,
    item="SendGrid (Twilio) - <category>", valor_brl, fonte=sendgrid

CAVEAT: not all SendGrid accounts surface email charges through Twilio's Usage
Records — legacy/standalone SendGrid plans don't. If this script returns zero
rows for a month you KNOW had SendGrid usage, the API path is the wrong one for
your account; fall back to `ingest-manual-costs` with `fonte: manual-sendgrid`.

Usage:
    cp .env.example .env       # then fill in Twilio creds
    python extractor.py --month 2026-05 --usd-brl-rate 5.04 \\
        --bq-project executive-reports-cpto

Requirements: requests, python-dotenv, google-cloud-bigquery.
NOTE: All log messages are intentionally in English.
"""

import argparse
import calendar
import csv
import logging
import os
import sys
from collections import defaultdict
from datetime import date

import requests
from dotenv import load_dotenv

TWILIO_API_BASE = "https://api.twilio.com"
# Match category names containing any of these tokens (case-insensitive).
EMAIL_CATEGORY_HINTS = ("email", "sendgrid")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sendgrid_twilio_extractor")


def month_bounds(month: str) -> tuple[str, str]:
    """Return (YYYY-MM-DD, YYYY-MM-DD) for the first and last day of `month`."""
    year, mon = (int(p) for p in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return date(year, mon, 1).isoformat(), date(year, mon, last).isoformat()


def fetch_monthly_records(account_sid: str, auth_token: str, start: str, end: str) -> list[dict]:
    """Page through Usage Records Monthly endpoint for the date range."""
    url = (
        f"{TWILIO_API_BASE}/2010-04-01/Accounts/{account_sid}/Usage/Records/Monthly.json"
        f"?StartDate={start}&EndDate={end}&PageSize=200"
    )
    records: list[dict] = []
    while url:
        resp = requests.get(url, auth=(account_sid, auth_token), timeout=60)
        if resp.status_code == 401:
            raise RuntimeError("Twilio 401 — check TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN.")
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("usage_records", []))
        next_uri = data.get("next_page_uri")
        url = f"{TWILIO_API_BASE}{next_uri}" if next_uri else None
    log.info("Fetched %d total Twilio usage records for %s..%s.", len(records), start, end)
    return records


def filter_email_records(records: list[dict]) -> list[dict]:
    out = []
    seen_categories: set[str] = set()
    for r in records:
        cat = (r.get("category") or "").lower()
        seen_categories.add(cat)
        if any(hint in cat for hint in EMAIL_CATEGORY_HINTS):
            out.append(r)
    log.info("Email-related categories matched: %d records out of %d total.", len(out), len(records))
    if not out and records:
        log.warning("No email/sendgrid categories found. Categories seen this month:")
        for c in sorted(seen_categories):
            log.warning("    - %s", c or "(empty)")
        log.warning("If you expected SendGrid charges, your Twilio account may not surface "
                    "them via Usage Records. Fall back to ingest-manual-costs (fonte: manual-sendgrid).")
    return out


def aggregate(records: list[dict], competencia: str, usd_brl_rate: float) -> list[dict]:
    by_category: dict[str, float] = defaultdict(float)
    for r in records:
        try:
            price = float(r.get("price") or 0)
        except ValueError:
            continue
        # Twilio reports `price` as a negative or zero number for cost (signed convention varies);
        # use abs so we always store a positive R$ value.
        by_category[r.get("category") or "unknown"] += abs(price)
    rows = []
    for cat, usd in sorted(by_category.items()):
        if usd == 0:
            continue
        rows.append({
            "competencia": competencia,
            "produto": "Compartilhado",
            "item": f"SendGrid (Twilio) - {cat}",
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
    parser = argparse.ArgumentParser(description="Extract SendGrid (via Twilio) monthly cost into custos.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM")
    parser.add_argument("--usd-brl-rate", type=float, default=1.0,
                        help="USD->BRL rate (Twilio bills in USD). Default 1.0.")
    parser.add_argument("--output", default="custos_sendgrid.csv", help="Output CSV path")
    parser.add_argument("--bq-project", default=None,
                        help="GCP project id to load into BigQuery (optional)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    args = parser.parse_args()

    load_dotenv()
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and token):
        log.error("Missing credentials. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env.")
        return 1

    # Cash-basis convention (mirrors AWS/GCP): competencia=YYYY-MM is what was
    # paid in that month, i.e. the previous calendar month's Twilio/SendGrid usage.
    year_i, mon_i = (int(p) for p in args.month.split("-"))
    if mon_i == 1:
        usage_month = f"{year_i - 1}-12"
    else:
        usage_month = f"{year_i}-{mon_i - 1:02d}"
    log.info("Report month=%s -> querying Twilio usage for %s.", args.month, usage_month)

    start, end = month_bounds(usage_month)
    try:
        records = fetch_monthly_records(sid, token, start, end)
    except Exception as exc:
        log.error("Failed to fetch Twilio usage records: %s", exc)
        return 1

    email_records = filter_email_records(records)
    if not email_records:
        log.info("No email records to load for %s. Exiting cleanly.", args.month)
        return 0

    rows = aggregate(email_records, args.month, args.usd_brl_rate)
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
                "fonte": "sendgrid",
                "valor_usd": float(r["valor_usd"]),
                "taxa_usd_brl": float(r["taxa_usd_brl"]),
            }
            for r in rows
        ]
        loader.replace_month("custos", competencia=args.month, rows=bq_rows, fonte="sendgrid")
        log.info("Loaded SendGrid cost into BigQuery (%s.custos).", args.bq_dataset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
