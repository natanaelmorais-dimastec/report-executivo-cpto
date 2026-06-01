"""
Manual cost ingestor for the monthly Products & Technology report (Dimastec).

Covers the cost sources that don't have a usable API (Jira/Atlassian billing,
Excalidraw, dev folha, partners like Gryfo/Beonup/Danysoft, etc.). Reads a
per-month YAML manifest where each entry becomes one row in
`relatorio_pt.custos`.

Convention:

    manual-invoices/
        2026-05/
            manifest.yaml              <-- the source of truth for BQ
            gryfo-fatura-abril.pdf     <-- evidence/audit trail (not parsed)
            danysoft-nf-122.pdf
            ...

Idempotent per (competencia, fonte): every entry must declare its `fonte`
(e.g. `manual-gryfo`, `manual-folha-clt`). Re-running with the manifest edited
only replaces rows for the fontes present IN the manifest — other fontes
(including AWS/GCP loaded by their own extractors) are untouched.

Usage:
    python ingest_manual.py --month 2026-05 --bq-project executive-reports-cpto
    python ingest_manual.py --month 2026-05 --dry-run            # validate only
    python ingest_manual.py --month 2026-05 --manifest path.yaml --bq-project ...

Requirements: pyyaml, google-cloud-bigquery
NOTE: All log messages are intentionally in English.
"""

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

import yaml

# Known values — used for WARNINGS only. A new value is not an error (new
# categorias/produtos may appear as the report evolves).
KNOWN_CATEGORIAS = {
    "Time", "Cloud", "Ferramentas", "Parceiros/Operação", "Fornecedor de Produto",
}
KNOWN_PRODUTOS = {
    "Faceum", "Mydhas", "AI", "Integração", "Compartilhado", "Saturno",
}
REQUIRED_FIELDS = ("categoria", "produto", "item", "valor_brl", "fonte")

# Encargo multipliers applied at ingest time, keyed by `fonte`.
# Manifest values are RAW (salário base / NF bruta). Encargo is a business
# rule that converts to "custo onerado" for the executive report:
#   - CLT:     1.70  (provisão de férias, 13º, INSS patronal, FGTS — estimado)
#   - Estágio: 1.05  (vale transporte, alimentação)
# Any fonte not listed = no encargo (multiplier 1.0).
# To change rates, update this dict and re-run the month (idempotent).
ENCARGO_BY_FONTE: dict[str, float] = {
    "manual-folha-clt":     1.70,
    "manual-folha-estagio": 1.05,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest_manual")


def load_manifest(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or []
    if not isinstance(data, list):
        raise ValueError(
            f"Manifest must be a YAML list of entries, got {type(data).__name__}"
        )
    return data


def validate(entries: list[dict], competencia: str) -> list[dict]:
    """Validate entries and return BigQuery-ready row dicts."""
    rows: list[dict] = []
    seen_keys: set[tuple] = set()

    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry #{i}: must be a mapping, got {type(entry).__name__}")

        for field in REQUIRED_FIELDS:
            if field not in entry or entry[field] in ("", None):
                raise ValueError(f"Entry #{i}: missing required field '{field}'")

        try:
            valor = float(entry["valor_brl"])
        except (TypeError, ValueError):
            raise ValueError(f"Entry #{i}: valor_brl must be numeric, got {entry['valor_brl']!r}")
        if valor <= 0:
            raise ValueError(f"Entry #{i}: valor_brl must be > 0, got {valor}")

        if entry["categoria"] not in KNOWN_CATEGORIAS:
            log.warning("Entry #%d: unknown categoria '%s' (known: %s)",
                        i, entry["categoria"], ", ".join(sorted(KNOWN_CATEGORIAS)))
        if entry["produto"] not in KNOWN_PRODUTOS:
            log.warning("Entry #%d: unknown produto '%s' (known: %s)",
                        i, entry["produto"], ", ".join(sorted(KNOWN_PRODUTOS)))

        # Catch within-file double-counting: same (categoria, produto, item, fonte) twice
        key = (entry["categoria"], entry["produto"], entry["item"], entry["fonte"])
        if key in seen_keys:
            raise ValueError(
                f"Entry #{i}: duplicate (categoria, produto, item, fonte)={key} "
                "— would double-count in the report"
            )
        seen_keys.add(key)

        encargo = ENCARGO_BY_FONTE.get(entry["fonte"], 1.0)
        rows.append({
            "competencia": competencia,
            "categoria": entry["categoria"],
            "produto": entry["produto"],
            "cloud_provedor": entry.get("cloud_provedor"),  # nullable in schema
            "item": entry["item"],
            "valor_brl": round(valor * encargo, 2),
            "fonte": entry["fonte"],
        })

    return rows


def print_summary(rows: list[dict]) -> None:
    by_fonte: dict[str, float] = defaultdict(float)
    by_categoria: dict[str, float] = defaultdict(float)
    for r in rows:
        by_fonte[r["fonte"]] += r["valor_brl"]
        by_categoria[r["categoria"]] += r["valor_brl"]
    total = sum(r["valor_brl"] for r in rows)
    log.info("--- Summary (R$, encargo applied where applicable) ---")
    log.info("  By fonte:")
    for fonte, value in sorted(by_fonte.items(), key=lambda kv: -kv[1]):
        mult = ENCARGO_BY_FONTE.get(fonte)
        tag = f" (encargo ×{mult})" if mult else ""
        log.info("    %-30s %12.2f%s", fonte, value, tag)
    log.info("  By categoria:")
    for cat, value in sorted(by_categoria.items(), key=lambda kv: -kv[1]):
        log.info("    %-30s %12.2f", cat, value)
    log.info("  %-30s %12.2f", "TOTAL", total)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest manual cost entries from a YAML manifest into BigQuery.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM")
    parser.add_argument("--invoices-dir", default="manual-invoices",
                        help="Root folder for per-month manifests (default: manual-invoices)")
    parser.add_argument("--manifest", default=None,
                        help="Explicit manifest path; overrides --invoices-dir/<month>/manifest.yaml")
    parser.add_argument("--bq-project", default=None,
                        help="GCP project for BigQuery load (omit to validate-only)")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate + summarize, do not load into BigQuery")
    args = parser.parse_args()

    manifest_path = (
        Path(args.manifest) if args.manifest
        else Path(args.invoices_dir) / args.month / "manifest.yaml"
    )
    if not manifest_path.exists():
        log.error("Manifest not found: %s", manifest_path)
        log.error("Expected layout: %s/<YYYY-MM>/manifest.yaml", args.invoices_dir)
        return 1
    log.info("Reading manifest %s", manifest_path)

    entries = load_manifest(manifest_path)
    if not entries:
        log.warning("Manifest is empty — nothing to ingest.")
        return 0

    try:
        rows = validate(entries, args.month)
    except ValueError as exc:
        log.error("Validation failed: %s", exc)
        return 1

    log.info("Validated %d entries.", len(rows))
    print_summary(rows)

    if args.dry_run:
        log.info("Dry-run — skipping BigQuery load.")
        return 0
    if not args.bq_project:
        log.info("No --bq-project given — validate-only mode (no BigQuery load).")
        return 0

    try:
        from bq_loader import BigQueryLoader
    except ImportError:
        log.error("bq_loader.py not found. Copy it next to this script or set PYTHONPATH.")
        return 1
    loader = BigQueryLoader(project_id=args.bq_project, dataset=args.bq_dataset)

    # Group by fonte and replace_month per fonte — keeps each manual source
    # independently idempotent, and never wipes rows from other extractors.
    by_fonte: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_fonte[r["fonte"]].append(r)
    for fonte, fonte_rows in sorted(by_fonte.items()):
        loader.replace_month("custos", competencia=args.month, rows=fonte_rows, fonte=fonte)
    log.info("Loaded %d rows across %d fonte(s) into BigQuery (%s.custos).",
             len(rows), len(by_fonte), args.bq_dataset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
