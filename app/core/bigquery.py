"""BigQuery client for query execution and vector search."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import structlog
from google.cloud import bigquery
from google.cloud.bigquery import QueryJobConfig

from app.config import get_settings

logger = structlog.get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)


class BigQueryClient:
    """BigQuery client wrapper with safety controls."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = bigquery.Client(project=self.settings.gcp_project_id)
        logger.info("bigquery_client_initialized", project=self.settings.gcp_project_id)

    def execute_query(
        self,
        sql: str,
        timeout: float = 45.0,
        max_rows: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Execute a SQL query with timeout and row limit.

        Args:
            sql: The SQL query to execute.
            timeout: Query timeout in seconds.
            max_rows: Maximum number of rows to return.

        Returns:
            List of row dictionaries.

        Raises:
            TimeoutError: If query exceeds timeout.
            Exception: If query execution fails.
        """
        logger.info("executing_query", sql=sql[:200])

        # Circuit breaker: block calls if BigQuery service is in OPEN state
        from app.core.safety import get_circuit
        cb = get_circuit("bigquery")
        if not cb.is_available():
            raise RuntimeError("BigQuery circuit breaker OPEN \u2014 \uc77c\uc2dc\uc801\uc73c\ub85c \uc694\uccad\uc774 \ucc28\ub2e8\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

        job_config = QueryJobConfig()
        job_config.maximum_bytes_billed = 10 * 1024 * 1024 * 1024  # 10 GB limit

        try:
            query_job = self.client.query(sql, job_config=job_config)
            results = query_job.result(timeout=timeout)

            rows = []
            for i, row in enumerate(results):
                if i >= max_rows:
                    break
                rows.append(dict(row))

            logger.info("query_completed", row_count=len(rows))
            cb.record_success()
            return rows

        except Exception as e:
            logger.error("query_failed", error=str(e), sql=sql[:200])
            cb.record_failure()
            raise

    def get_table_schema(self, table_id: str) -> List[Dict[str, str]]:
        """Get schema information for a BigQuery table.

        Args:
            table_id: Full table ID (project.dataset.table).

        Returns:
            List of column definitions with name, type, and description.
        """
        try:
            table = self.client.get_table(table_id)
            schema = []
            for field in table.schema:
                schema.append({
                    "name": field.name,
                    "type": field.field_type,
                    "mode": field.mode,
                    "description": field.description or "",
                })
            logger.info("schema_retrieved", table=table_id, columns=len(schema))
            return schema
        except Exception as e:
            logger.error("schema_retrieval_failed", table=table_id, error=str(e))
            raise

    def vector_search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        distance_type: str = "COSINE",
    ) -> List[Dict[str, Any]]:
        """Perform vector similarity search on the embeddings table.

        Args:
            query_embedding: The query embedding vector.
            top_k: Number of results to return.
            distance_type: Distance metric (COSINE, EUCLIDEAN, DOT_PRODUCT).

        Returns:
            List of matching documents with scores.
        """
        settings = self.settings
        embedding_str = ", ".join(str(v) for v in query_embedding)

        sql = f"""
        SELECT
            base.id,
            base.content,
            base.metadata,
            base.source_type,
            distance
        FROM
            VECTOR_SEARCH(
                TABLE `{settings.embeddings_table_full_path}`,
                'embedding',
                (SELECT [{embedding_str}] AS embedding),
                top_k => {top_k},
                distance_type => '{distance_type}'
            )
        ORDER BY distance ASC
        """

        logger.info("vector_search", top_k=top_k, distance_type=distance_type)
        return self.execute_query(sql, timeout=30.0, max_rows=top_k)

    def insert_embeddings(self, rows: List[Dict[str, Any]]) -> None:
        """Insert embedding rows into the embeddings table.

        Args:
            rows: List of dicts with id, content, metadata, embedding, source_type.
        """
        table_ref = self.settings.embeddings_table_full_path
        errors = self.client.insert_rows_json(table_ref, rows)
        if errors:
            logger.error("embedding_insert_failed", errors=errors)
            raise RuntimeError(f"BigQuery insert errors: {errors}")
        logger.info("embeddings_inserted", count=len(rows))

    def insert_qa_log(self, log_entry: Dict[str, Any]) -> None:
        """Insert a QA log entry.

        Args:
            log_entry: Dict with query, route_type, answer, etc.
        """
        if "id" not in log_entry:
            log_entry["id"] = str(uuid.uuid4())

        table_ref = self.settings.qa_logs_table_full_path
        errors = self.client.insert_rows_json(table_ref, [log_entry])
        if errors:
            logger.error("qa_log_insert_failed", errors=errors)
        else:
            logger.info("qa_log_inserted", id=log_entry["id"])

    def test_connection(self) -> bool:
        """Test BigQuery connection.

        Returns:
            True if connection is successful.
        """
        try:
            query = "SELECT 1 AS test"
            results = self.execute_query(query, timeout=10.0)
            logger.info("connection_test_passed")
            return len(results) > 0
        except Exception as e:
            logger.error("connection_test_failed", error=str(e))
            return False


# Singleton instance
_client: Optional[BigQueryClient] = None


def get_bigquery_client() -> BigQueryClient:
    """Get or create the BigQuery client singleton."""
    global _client
    if _client is None:
        _client = BigQueryClient()
    return _client
