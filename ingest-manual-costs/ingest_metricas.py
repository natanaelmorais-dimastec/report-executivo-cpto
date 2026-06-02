"""
Manual ingest for business metrics (usuarios_ativos, contratos_ativos) per produto.

Reads a YAML at manual-invoices/<YYYY-MM>/metricas.yaml as a LIST of entries —
one per produto — and upserts each row in relatorio_pt.metricas_negocio.
Idempotent: MERGE on (competencia, produto) — re-running updates each row in place.

YAML format (list of mappings):

    - produto: Faceum
      usuarios_ativos: 349772
      contratos_ativos: 816
    - produto: Mydhas
      usuarios_ativos: 12345
      contratos_ativos: 67

If the file does not exist for a month, the script exits 0 (no-op) — useful when
called from close_month.py so the close doesn't fail when the numbers aren't ready.

Usage:
    python3 ingest_metricas.py --month 2026-05 --bq-project executive-reports-cpto
    python3 ingest_metricas.py --month 2026-05 --dry-run

Requirements: pyyaml, google-cloud-bigquery
NOTE: All log messages are intentionally in English.
"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

REQUIRED_KEYS = ("produto", "usuarios_ativos", "contratos_ativos")
KNOWN_PRODUTOS = {"Faceum", "Mydhas", "AI", "Integração", "Compartilhado", "Saturno"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest_metricas")


def load_manifest(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or []
    if not isinstance(data, list):
        raise ValueError(
            f"metricas manifest must be a YAML list of entries (one per produto), "
            f"got {type(data).__name__}"
        )
    return data


def validate(entries: list[dict]) -> list[dict]:
    seen_produtos: set[str] = set()
    rows: list[dict] = []
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry #{i}: must be a mapping, got {type(entry).__name__}")
        for key in REQUIRED_KEYS:
            if key not in entry or entry[key] in ("", None):
                raise ValueError(f"Entry #{i}: missing required key '{key}'")
        produto = entry["produto"]
        if produto in seen_produtos:
            raise ValueError(f"Entry #{i}: duplicate produto '{produto}' in manifest")
        seen_produtos.add(produto)
        if produto not in KNOWN_PRODUTOS:
            log.warning("Entry #%d: unknown produto '%s' (known: %s)",
                        i, produto, ", ".join(sorted(KNOWN_PRODUTOS)))
        for numeric in ("usuarios_ativos", "contratos_ativos"):
            v = entry[numeric]
            if not isinstance(v, int):
                raise ValueError(
                    f"Entry #{i}: '{numeric}' must be an integer, "
                    f"got {type(v).__name__}: {v!r}"
                )
            if v < 0:
                raise ValueError(f"Entry #{i}: '{numeric}' must be >= 0, got {v}")
        rows.append({
            "produto": produto,
            "usuarios_ativos": entry["usuarios_ativos"],
            "contratos_ativos": entry["contratos_ativos"],
        })
    return rows


def upsert(project: str, dataset: str, competencia: str, rows: list[dict]) -> None:
    from google.cloud import bigquery
    c = bigquery.Client(project=project)
    for row in rows:
        sql = f"""
            MERGE `{project}.{dataset}.metricas_negocio` T
            USING (SELECT @comp AS competencia,
                          @prod AS produto,
                          @usu AS usuarios_ativos,
                          @con AS contratos_ativos) S
            ON T.competencia = S.competencia AND IFNULL(T.produto,'') = IFNULL(S.produto,'')
            WHEN MATCHED THEN UPDATE SET usuarios_ativos = S.usuarios_ativos,
                                         contratos_ativos = S.contratos_ativos,
                                         carregado_em = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (competencia, produto, usuarios_ativos, contratos_ativos, carregado_em)
                                  VALUES (S.competencia, S.produto, S.usuarios_ativos, S.contratos_ativos, CURRENT_TIMESTAMP())
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("comp", "STRING", competencia),
            bigquery.ScalarQueryParameter("prod", "STRING", row["produto"]),
            bigquery.ScalarQueryParameter("usu", "INT64", row["usuarios_ativos"]),
            bigquery.ScalarQueryParameter("con", "INT64", row["contratos_ativos"]),
        ])
        c.query(sql, job_config=job_config).result()
        log.info("Upserted metricas_negocio %s/%s: usuarios=%d, contratos=%d",
                 competencia, row["produto"],
                 row["usuarios_ativos"], row["contratos_ativos"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest per-produto business metrics from a YAML manifest.")
    parser.add_argument("--month", required=True, help="Month, format YYYY-MM")
    parser.add_argument("--invoices-dir", default="manual-invoices",
                        help="Root folder for per-month manifests")
    parser.add_argument("--manifest", default=None,
                        help="Explicit path; overrides --invoices-dir/<month>/metricas.yaml")
    parser.add_argument("--bq-project", default=None, help="GCP project for BigQuery")
    parser.add_argument("--bq-dataset", default="relatorio_pt", help="BigQuery dataset")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no BigQuery write")
    args = parser.parse_args()

    manifest_path = (
        Path(args.manifest) if args.manifest
        else Path(args.invoices_dir) / args.month / "metricas.yaml"
    )
    if not manifest_path.exists():
        log.info("No metricas manifest at %s — skipping (OK if numbers aren't ready yet).",
                 manifest_path)
        return 0

    log.info("Reading metricas manifest %s", manifest_path)
    try:
        entries = load_manifest(manifest_path)
        rows = validate(entries)
    except ValueError as exc:
        log.error("Validation failed: %s", exc)
        return 1

    log.info("Validated %d produto entries.", len(rows))
    for r in rows:
        log.info("  %-15s usuarios=%d  contratos=%d", r["produto"],
                 r["usuarios_ativos"], r["contratos_ativos"])

    if args.dry_run:
        log.info("Dry-run — skipping BigQuery upsert.")
        return 0
    if not args.bq_project:
        log.info("No --bq-project given — validate-only mode (no BigQuery write).")
        return 0

    upsert(args.bq_project, args.bq_dataset, args.month, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
