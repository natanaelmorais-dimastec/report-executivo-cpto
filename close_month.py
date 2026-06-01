#!/usr/bin/env python3
"""
Monthly close orchestrator for the P&T executive report (Dimastec).

Runs every cost extractor (AWS, GCP, MongoDB Atlas, GitHub Copilot, SendGrid),
then the manual YAML ingest, then the Jira deliveries extractor — all on the
cash-basis convention (competencia=YYYY-MM means R$ paid in that month).

Each step is independent: if one fails the others continue. A summary at the
end shows OK/FAIL per source plus a final BigQuery total for the month.

Usage:
    python3 close_month.py --month 2026-05
    python3 close_month.py --month 2026-05 --usd-brl-rate 5.12
    python3 close_month.py --month 2026-05 --skip copilot,sendgrid
    python3 close_month.py --month 2026-05 --only manuais,jira

Pre-requisites (see MONTHLY_CLOSE.md):
- gcloud ADC: `gcloud auth application-default login`
- AWS profile `dimastec-mgmt` configured (~/.aws/credentials)
- `.env` files filled in each get-*-costs/ folder where applicable
- manual-invoices/<YYYY-MM>/manifest.yaml prepared for the month

NOTE: All log messages are intentionally in English to match the rest of
the codebase. The runbook MONTHLY_CLOSE.md is in PT-BR.
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent
BQ_PROJECT = "executive-reports-cpto"
GCP_BILLING_EXPORT_TABLE = (
    "executive-reports-cpto.billing_export."
    "gcp_billing_export_v1_01ED44_5AEFA1_EE5D4B"
)


def aws_args(month: str, rate: str) -> list[str]:
    return [
        "python3", "extractor.py",
        "--month", month,
        "--profile", "dimastec-mgmt",
        "--usd-brl-rate", rate,
        "--audit-sa", "tag_audit.csv",
        "--audit-ue", "tag_audit_useast.csv",
        "--bq-project", BQ_PROJECT,
    ]


def gcp_args(month: str, rate: str) -> list[str]:
    return [
        "python3", "extractor.py",
        "--month", month,
        "--usd-brl-rate", rate,
        "--billing-export-table", GCP_BILLING_EXPORT_TABLE,
        "--audit", "project_audit.csv",
        "--bq-project", BQ_PROJECT,
    ]


def atlas_args(month: str, rate: str) -> list[str]:
    return [
        "python3", "extractor.py",
        "--month", month,
        "--usd-brl-rate", rate,
        "--bq-project", BQ_PROJECT,
    ]


def copilot_args(month: str, rate: str) -> list[str]:
    return [
        "python3", "extractor.py",
        "--month", month,
        "--usd-brl-rate", rate,
        "--bq-project", BQ_PROJECT,
    ]


def sendgrid_args(month: str, rate: str) -> list[str]:
    return [
        "python3", "extractor.py",
        "--month", month,
        "--usd-brl-rate", rate,
        "--bq-project", BQ_PROJECT,
    ]


def manuais_args(month: str, rate: str) -> list[str]:
    return [
        "python3", "ingest_manual.py",
        "--month", month,
        "--invoices-dir", "../manual-invoices",
        "--bq-project", BQ_PROJECT,
    ]


def jira_args(month: str, rate: str) -> list[str]:
    return [
        "python3", "jira_extractor.py",
        "--month", month,
        "--bq-project", BQ_PROJECT,
    ]


# Step definition: (slug, label, cwd_relative, args-builder, known-flaky-explanation)
# `slug` is what the user passes to --skip / --only. `flaky` is an optional
# note shown when the step fails, explaining a known-acceptable failure mode.
STEPS: list[dict] = [
    {
        "slug": "aws",
        "label": "AWS Cost Explorer",
        "cwd": "get-aws-costs",
        "args": aws_args,
    },
    {
        "slug": "gcp",
        "label": "GCP Billing Export",
        "cwd": "get-gcp-costs",
        "args": gcp_args,
    },
    {
        "slug": "atlas",
        "label": "MongoDB Atlas",
        "cwd": "get-mongodb-atlas-costs",
        "args": atlas_args,
    },
    {
        "slug": "copilot",
        "label": "GitHub Copilot",
        "cwd": "get-github-copilot-costs",
        "args": copilot_args,
        "flaky": "Expected 404 if Enhanced Billing not enabled. "
                 "Fallback: manual-github-copilot in the YAML manifest.",
    },
    {
        "slug": "sendgrid",
        "label": "SendGrid (Twilio)",
        "cwd": "get-sendgrid-costs",
        "args": sendgrid_args,
        "flaky": "Standalone SendGrid accounts return 0 rows (not via Twilio billing). "
                 "Fallback: manual-sendgrid in the YAML manifest.",
    },
    {
        "slug": "manuais",
        "label": "Manual ingest (YAML)",
        "cwd": "ingest-manual-costs",
        "args": manuais_args,
    },
    {
        "slug": "jira",
        "label": "Jira deliveries",
        "cwd": "jira-deliveries-extractor",
        "args": jira_args,
    },
]


def run_step(step: dict, month: str, rate: str) -> int:
    cwd = REPO_ROOT / step["cwd"]
    cmd = step["args"](month, rate)
    print(f"\n{'=' * 70}")
    print(f"  [{step['slug']}] {step['label']}")
    print(f"  $ cd {step['cwd']} && {' '.join(cmd)}")
    print(f"{'=' * 70}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0 and step.get("flaky"):
        print(f"  ⚠  Known acceptable failure: {step['flaky']}")
    return result.returncode


def query_summary(month: str) -> None:
    try:
        from google.cloud import bigquery
    except ImportError:
        print("  (google-cloud-bigquery not installed, skipping BQ summary)")
        return
    c = bigquery.Client(project=BQ_PROJECT)
    custos = list(c.query(
        f"SELECT categoria, ROUND(SUM(valor_brl), 2) total "
        f"FROM `{BQ_PROJECT}.relatorio_pt.custos` "
        f"WHERE competencia='{month}' GROUP BY categoria ORDER BY total DESC"
    ).result())
    custos_total = list(c.query(
        f"SELECT COUNT(*) n, ROUND(SUM(valor_brl), 2) total "
        f"FROM `{BQ_PROJECT}.relatorio_pt.custos` WHERE competencia='{month}'"
    ).result())[0]
    entregas = list(c.query(
        f"SELECT COUNT(*) n, SUM(n_issues) issues "
        f"FROM `{BQ_PROJECT}.relatorio_pt.entregas` WHERE competencia='{month}'"
    ).result())[0]

    print(f"\n  --- BigQuery state for {month} ---")
    print(f"  custos:   {custos_total['n']} linhas, R$ {float(custos_total['total'] or 0):,.2f}")
    for r in custos:
        print(f"    {r['categoria']:25s}  R$ {float(r['total']):>13,.2f}")
    print(f"  entregas: {entregas['n']} entregas, {entregas['issues'] or 0} issues")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full monthly close: every cost extractor + manual ingest + Jira deliveries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Steps: " + ", ".join(s["slug"] for s in STEPS),
    )
    parser.add_argument("--month", required=True, help="Report month, format YYYY-MM")
    parser.add_argument("--usd-brl-rate", default="5.04", help="USD->BRL rate for the month (default 5.04)")
    parser.add_argument("--skip", default="", help="Comma-separated step slugs to skip")
    parser.add_argument("--only", default="", help="Comma-separated step slugs — run ONLY these")
    args = parser.parse_args()

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    selected = [
        s for s in STEPS
        if s["slug"] not in skip and (not only or s["slug"] in only)
    ]
    if not selected:
        print("No steps selected. Use --only/--skip with one of:",
              ", ".join(s["slug"] for s in STEPS))
        return 1

    print(f"Monthly close for {args.month} (USD/BRL = {args.usd_brl_rate})")
    print(f"Running {len(selected)} step(s): " + ", ".join(s["slug"] for s in selected))

    results: list[tuple[str, int]] = []
    for step in selected:
        rc = run_step(step, args.month, args.usd_brl_rate)
        results.append((step["slug"], rc))

    print(f"\n{'=' * 70}")
    print("  SUMMARY")
    print(f"{'=' * 70}")
    for slug, rc in results:
        status = "OK" if rc == 0 else f"FAIL (exit {rc})"
        print(f"  {slug:12s}  {status}")

    query_summary(args.month)

    failures = [slug for slug, rc in results if rc != 0]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
