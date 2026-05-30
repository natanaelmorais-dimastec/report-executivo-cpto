"""
Shared BigQuery loader for the monthly Products & Technology report (Dimastec).

Both the AWS cost extractor and the Jira deliveries extractor import this module to
write their rows into BigQuery instead of (or in addition to) writing CSV files.

Design choice: each load REPLACES the rows for the given competencia (month) in the
target table, so re-running an extractor for the same month is idempotent (no
duplicates). This is the "delete-then-insert by partition" pattern.

Usage (from an extractor):
    from bq_loader import BigQueryLoader
    loader = BigQueryLoader(project_id="seu-projeto", dataset="relatorio_pt")
    loader.replace_month("custos", competencia="2026-05", rows=rows)

Requirements: google-cloud-bigquery
Auth: Application Default Credentials (run `gcloud auth application-default login`)
      or a service account key via GOOGLE_APPLICATION_CREDENTIALS.
NOTE: All log messages are intentionally in English.
"""

import logging

from google.cloud import bigquery

log = logging.getLogger("bq_loader")


class BigQueryLoader:
    """Thin wrapper around the BigQuery client for idempotent monthly loads."""

    def __init__(self, project_id: str, dataset: str = "relatorio_pt"):
        self.project_id = project_id
        self.dataset = dataset
        self.client = bigquery.Client(project=project_id)
        log.info("BigQuery client ready (project=%s, dataset=%s)", project_id, dataset)

    def _table_ref(self, table: str) -> str:
        return f"{self.project_id}.{self.dataset}.{table}"

    def replace_month(
        self,
        table: str,
        competencia: str,
        rows: list[dict],
        fonte: str | None = None,
    ) -> None:
        """
        Replace all rows for `competencia` (and optionally `fonte`) in `table`.

        Idempotent: deletes existing rows, then inserts the new ones. Re-running the
        same (month, fonte) does not create duplicates.

        Pass `fonte` for tables shared by multiple sources (e.g. `custos`, where one
        month has aws + gcp + azure rows) so each extractor only replaces its own
        rows. Omit it for single-source tables like `entregas`.
        """
        table_ref = self._table_ref(table)

        # 1. Delete existing rows for this (month [, fonte])
        if fonte is None:
            delete_sql = f"DELETE FROM `{table_ref}` WHERE competencia = @competencia"
            params = [bigquery.ScalarQueryParameter("competencia", "STRING", competencia)]
            log.info("Deleting existing rows for %s in %s ...", competencia, table)
        else:
            delete_sql = (
                f"DELETE FROM `{table_ref}` "
                f"WHERE competencia = @competencia AND fonte = @fonte"
            )
            params = [
                bigquery.ScalarQueryParameter("competencia", "STRING", competencia),
                bigquery.ScalarQueryParameter("fonte", "STRING", fonte),
            ]
            log.info("Deleting existing rows for %s/fonte=%s in %s ...", competencia, fonte, table)
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.client.query(delete_sql, job_config=job_config).result()

        if not rows:
            log.warning("No rows to insert for %s in %s.", competencia, table)
            return

        # 2. Insert via a load job (not streaming inserts). Streaming-buffer rows
        # block DELETE/UPDATE for up to ~90 min; load jobs write directly to
        # managed storage so the next replace_month call works immediately.
        self._load_json(table_ref, rows)
        log.info("Loaded %d rows into %s for %s.", len(rows), table, competencia)

    def append(self, table: str, rows: list[dict]) -> None:
        """Append rows without deleting (use when not month-scoped). Rarely needed here."""
        if not rows:
            log.warning("No rows to append to %s.", table)
            return
        self._load_json(self._table_ref(table), rows)
        log.info("Appended %d rows into %s.", len(rows), table)

    def _load_json(self, table_ref: str, rows: list[dict]) -> None:
        """Load rows via a load job, reusing the destination table's schema."""
        log.info("Loading %d rows into %s ...", len(rows), table_ref)
        table_obj = self.client.get_table(table_ref)
        load_config = bigquery.LoadJobConfig(
            schema=table_obj.schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        self.client.load_table_from_json(rows, table_ref, job_config=load_config).result()
