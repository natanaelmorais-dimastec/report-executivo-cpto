"""
Jira Cloud deliveries extractor for the monthly Products & Technology report (Dimastec).

Pulls issues marked Done within a month, groups them by their parent EPIC, maps each
epic to a product, and emits rows matching the `entregas` tab of the report spreadsheet:

    competencia, produto, titulo, impacto, status

Approach (confirmed with the team):
  - Base = issues with status Done/Concluído whose resolution date falls in the month.
  - Group by parent epic (the epic is the "delivery" the CEO/CFO understands).
  - Product is resolved from the issue's PROJECT KEY via PROJECT_PRODUCT_MAP
    (Faceum spans several projects; Mydhas is a single project).
  - `titulo` = epic summary. `impacto` = epic's own summary/description snippet, or a
    placeholder to be filled. `status` = Entregue if the epic itself is Done, else Em progresso.

Usage:
    export JIRA_BASE_URL="https://dimastec.atlassian.net"
    export JIRA_EMAIL="you@dimastec.com.br"
    export JIRA_API_TOKEN="xxxx"            # from id.atlassian.com -> API tokens
    python jira_extractor.py --month 2026-05 --output entregas_2026-05.csv

Requirements: requests. Read-only (only GET/POST search against the Jira REST API).
NOTE: All log messages are intentionally in English.
"""

import argparse
import base64
import csv
import logging
import os
import sys
from collections import defaultdict
from datetime import date

import requests
from dotenv import load_dotenv

# --- Configuration -----------------------------------------------------------

# Map each Jira PROJECT KEY to a report product.
# Faceum spans several projects; Mydhas is a single project.
# Fill with the real project keys (the ticket prefixes, e.g. "FAC", "MYD").
PROJECT_PRODUCT_MAP = {
    # --- AI (todos os projetos AI*) ---
    "AIEPI":     "AI",       # AI Detector de EPI
    "AIMCP":     "AI",       # AI MCP
    "AIE":       "AI",       # AI Predição estoque
    "AIRF":      "AI",       # AI Reconhecimento facial
    # --- Faceum (projetos DTF* / FACEUM*) ---
    "FACEUMAPP": "Faceum",   # DTF App
    "DTCOLAB":   "Faceum",   # DTF App Colaborador
    "FLA":       "Faceum",   # DTF App Lite
    "FACEUMBK":  "Faceum",   # DTF Backoffice
    "FACEUMCL":  "Faceum",   # DTF Client
    "DAU":       "Faceum",   # DTF MS Authentication
    "FMC":       "Faceum",   # DTF MS Cronjob
    "DTDOC":     "Faceum",   # DTF MS Documents
    "FMR":       "Faceum",   # DTF MS Reports
    # --- Mydhas (projeto único) ---
    "MD":        "Mydhas",   # Mydhas
    # NOTE: Integração (NiFi/Airflow) não entra nas entregas — as demandas são geridas
    # pela operação, não pela área de infra. Integração aparece apenas no custo de cloud.
    # Se aparecer "Project key X is not mapped" ao rodar, adicione a key aqui (provável Faceum).
}

# Statuses that count as "delivered" (case-insensitive match).
DONE_STATUSES = {"done", "concluído", "concluido", "finalizado", "closed", "resolved"}

# Issue types treated as epics (Jira Cloud uses "Epic"; adjust if localized).
EPIC_TYPE_NAMES = {"epic", "épico", "epico"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jira_extractor")


# --- Date helpers ------------------------------------------------------------

def month_bounds(month: str) -> tuple[str, str]:
    """Return (start, end_inclusive) ISO dates for 'YYYY-MM' for a JQL range."""
    year, mon = (int(p) for p in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    # JQL resolved <= end-1day; use the exclusive end date and '<' in JQL instead.
    return start.isoformat(), end.isoformat()


# --- Jira client -------------------------------------------------------------

class JiraClient:
    """Minimal read-only Jira Cloud REST client (API v3)."""

    def __init__(self, base_url: str, email: str, token: str):
        self.base_url = base_url.rstrip("/")
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def search(self, jql: str, fields: list[str], page_size: int = 100) -> list[dict]:
        """Run a JQL search, paginating through all results (uses /rest/api/3/search/jql)."""
        url = f"{self.base_url}/rest/api/3/search/jql"
        issues: list[dict] = []
        next_page_token: str | None = None
        while True:
            payload: dict = {"jql": jql, "fields": fields, "maxResults": page_size}
            if next_page_token:
                payload["nextPageToken"] = next_page_token
            resp = requests.post(url, headers=self.headers, json=payload, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(f"Jira API error {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            batch = data.get("issues", [])
            issues.extend(batch)
            total = data.get("total", len(issues))
            log.info("Fetched %d/%d issues", len(issues), total)
            next_page_token = data.get("nextPageToken")
            if not next_page_token or not batch:
                break
        return issues


# --- Mapping helpers ---------------------------------------------------------

def project_to_product(project_key: str) -> str:
    """Resolve a project key to a report product label."""
    if project_key not in PROJECT_PRODUCT_MAP:
        log.warning("Project key '%s' is not mapped -- add it to PROJECT_PRODUCT_MAP.", project_key)
    return PROJECT_PRODUCT_MAP.get(project_key, f"({project_key})")


def is_done(status_name: str) -> bool:
    return status_name.strip().lower() in DONE_STATUSES


# --- Core --------------------------------------------------------------------

def fetch_done_issues(client: JiraClient, month: str) -> list[dict]:
    """Fetch issues resolved/done within the month, across mapped projects."""
    start, end = month_bounds(month)
    keys = list(PROJECT_PRODUCT_MAP.keys())
    if not keys:
        raise SystemExit("PROJECT_PRODUCT_MAP is empty -- add your Jira project keys first.")
    projects = ", ".join(f'"{k}"' for k in keys)
    # status changed to Done within the month; covers issues completed in the period.
    jql = (
        f'project in ({projects}) '
        f'AND status changed to ("Done","Concluído","Finalizado") '
        f'DURING ("{start}", "{end}") '
        f'ORDER BY updated DESC'
    )
    log.info("JQL: %s", jql)
    fields = ["summary", "status", "issuetype", "project", "parent", "resolutiondate"]
    return client.search(jql, fields)


def fetch_epics(client: JiraClient, epic_keys: set[str]) -> dict[str, dict]:
    """Fetch epic details (summary, status) for the given epic keys."""
    if not epic_keys:
        return {}
    keys = ", ".join(f'"{k}"' for k in epic_keys)
    jql = f'issuekey in ({keys})'
    fields = ["summary", "status", "project"]
    epics = client.search(jql, fields)
    return {e["key"]: e for e in epics}


def group_by_epic(issues: list[dict], epics: dict[str, dict]) -> list[dict]:
    """Group done issues under their parent epic and build delivery rows."""
    by_epic: dict[str, list[dict]] = defaultdict(list)
    no_epic: list[dict] = []

    for issue in issues:
        fields = issue.get("fields", {})
        parent = fields.get("parent")
        if parent and parent.get("fields", {}).get("issuetype", {}).get("name", "").lower() in EPIC_TYPE_NAMES:
            by_epic[parent["key"]].append(issue)
        elif parent:
            # parent exists but is not an epic (e.g. a story under a story) -> still group by it
            by_epic[parent["key"]].append(issue)
        else:
            no_epic.append(issue)

    rows: list[dict] = []
    for epic_key, child_issues in by_epic.items():
        epic = epics.get(epic_key)
        if epic:
            epic_summary = epic["fields"]["summary"]
            epic_status = epic["fields"]["status"]["name"]
            project_key = epic["fields"]["project"]["key"]
        else:
            # epic details not fetched (parent might not be an epic) -> use first child's project
            epic_summary = f"[{epic_key}]"
            epic_status = ""
            project_key = child_issues[0]["fields"]["project"]["key"]

        produto = project_to_product(project_key)
        status = "Entregue" if is_done(epic_status) else "Em progresso"
        rows.append({
            "epic_key": epic_key,
            "produto": produto,
            "titulo": epic_summary,
            "impacto": f"{len(child_issues)} issue(s) concluída(s) no mês",  # placeholder, edit later
            "status": status,
            "n_issues": len(child_issues),
        })

    # issues with no epic -> grouped as a single "Avulsas" line per product
    if no_epic:
        per_product: dict[str, int] = defaultdict(int)
        for issue in no_epic:
            pk = issue["fields"]["project"]["key"]
            per_product[project_to_product(pk)] += 1
        for produto, count in per_product.items():
            rows.append({
                "epic_key": "",
                "produto": produto,
                "titulo": "Entregas avulsas (sem épico)",
                "impacto": f"{count} issue(s) concluída(s) sem épico associado",
                "status": "Entregue",
                "n_issues": count,
            })

    rows.sort(key=lambda r: (r["produto"], -r["n_issues"]))
    return rows


def write_csv(rows: list[dict], month: str, output_path: str) -> None:
    """Write rows in the exact column order of the `entregas` tab."""
    fields = ["competencia", "produto", "titulo", "impacto", "status"]
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "competencia": month,
                "produto": r["produto"],
                "titulo": r["titulo"],
                "impacto": r["impacto"],
                "status": r["status"],
            })
    log.info("Wrote %d delivery rows to %s", len(rows), output_path)


def print_summary(rows: list[dict]) -> None:
    by_product: dict[str, int] = defaultdict(int)
    for r in rows:
        by_product[r["produto"]] += 1
    log.info("--- Deliveries by product (epics/groups) ---")
    for produto, count in sorted(by_product.items(), key=lambda x: -x[1]):
        log.info("  %-16s %3d epic(s)/grupo(s)", produto, count)
    log.info("Total delivery rows: %d", len(rows))


# --- Entry point -------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Jira deliveries grouped by epic.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM")
    parser.add_argument("--output", default="entregas.csv", help="Output CSV path")
    parser.add_argument("--bq-project", default=None,
                        help="GCP project id to load into BigQuery (optional)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    args = parser.parse_args()

    load_dotenv()
    base_url = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not all([base_url, email, token]):
        log.error("Set JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN environment variables.")
        return 1

    client = JiraClient(base_url, email, token)
    try:
        issues = fetch_done_issues(client, args.month)
        log.info("Found %d done issues in %s", len(issues), args.month)

        # Diagnostic: if no issues found, list recent issues to identify actual statuses
        if not issues:
            log.warning("No done issues found. Running diagnostic query to list recent issues...")
            start, end = month_bounds(args.month)
            keys = list(PROJECT_PRODUCT_MAP.keys())
            projects = ", ".join(f'"{k}"' for k in keys)
            diag_jql = (
                f'project in ({projects}) '
                f'AND updated >= "{start}" '
                f'ORDER BY updated DESC'
            )
            diag_issues = client.search(diag_jql, ["summary", "status", "project", "updated"], page_size=20)
            if diag_issues:
                log.info("--- Diagnostic: %d recent issues found (showing up to 20) ---", len(diag_issues))
                statuses_seen: set[str] = set()
                for di in diag_issues[:20]:
                    f = di.get("fields", {})
                    st = f.get("status", {}).get("name", "?")
                    pk = f.get("project", {}).get("key", "?")
                    statuses_seen.add(st)
                    log.info("  %-12s %-20s %s", pk, st, f.get("summary", "")[:60])
                log.info("Statuses seen: %s", sorted(statuses_seen))
                log.info("If the correct 'done' status is missing from the JQL, add it to DONE_STATUSES and the JQL in fetch_done_issues().")
            else:
                log.warning("No issues at all found for these projects in %s. Check PROJECT_PRODUCT_MAP keys.", args.month)

        # collect parent epic keys
        epic_keys = set()
        for issue in issues:
            parent = issue.get("fields", {}).get("parent")
            if parent:
                epic_keys.add(parent["key"])
        epics = fetch_epics(client, epic_keys)

        rows = group_by_epic(issues, epics)
    except Exception as exc:
        log.error("Failed to extract deliveries: %s", exc)
        return 1

    write_csv(rows, args.month, args.output)
    print_summary(rows)
    log.info("Review the CSV: refine the 'impacto' column (placeholders) before sharing.")

    # --- Optional: load into BigQuery ---
    if args.bq_project:
        try:
            from bq_loader import BigQueryLoader
        except ImportError:
            log.error("bq_loader.py not found. Copy it next to this script or set PYTHONPATH.")
            return 1
        loader = BigQueryLoader(project_id=args.bq_project, dataset=args.bq_dataset)
        bq_rows = []
        for r in rows:
            bq_rows.append({
                "competencia": args.month,
                "produto": r["produto"],
                "titulo": r["titulo"],
                "impacto": r.get("impacto", ""),
                "status": r["status"],
                "n_issues": int(r.get("n_issues", 0)),
            })
        loader.replace_month("entregas", competencia=args.month, rows=bq_rows)
        log.info("Loaded Jira deliveries into BigQuery (%s.entregas).", args.bq_dataset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
