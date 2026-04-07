"""
Qdrant vector store integration.
"""

from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from config import get_logger, settings


logger = get_logger(__name__)


@dataclass
class SearchResult:
    page_id: str
    page_title: str
    section_title: str | None
    breadcrumb_path: str
    text: str
    text_preview: str
    url: str
    chunk_index: int
    score: float


class QdrantStore:
    """Wrapper around Qdrant operations."""

    def __init__(self, host: str = None, port: int = None, collection_name: str = None):
        self._client = QdrantClient(
            host=host or settings.qdrant_host,
            port=port or settings.qdrant_port,
        )
        self._collection_name = collection_name or settings.collection_name

    def create_collection(self, recreate: bool = True) -> None:
        collections = [collection.name for collection in self._client.get_collections().collections]

        if self._collection_name in collections:
            if recreate:
                self._client.delete_collection(collection_name=self._collection_name)
                logger.info("Deleted existing collection: %s", self._collection_name)
            else:
                logger.info("Collection already exists: %s", self._collection_name)
                return

        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
        )
        logger.info("Created collection: %s", self._collection_name)
        self._create_indexes()

    def _create_indexes(self) -> None:
        try:
            self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="page_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="breadcrumb_path",
                field_schema=PayloadSchemaType.TEXT,
            )
            self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="source",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            logger.info("Created payload indexes for collection: %s", self._collection_name)
        except Exception as exc:
            logger.warning("Payload index creation warning for %s: %s", self._collection_name, exc)

    def ensure_collection(self) -> None:
        collections = [collection.name for collection in self._client.get_collections().collections]
        if self._collection_name in collections:
            logger.info("Collection already available: %s", self._collection_name)
            return

        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
        )
        logger.info("Created collection: %s", self._collection_name)
        self._create_indexes()

    def delete_by_source(self, source: str) -> int:
        try:
            self._client.delete(
                collection_name=self._collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(key="source", match=MatchValue(value=source)),
                        ]
                    )
                ),
            )
            logger.info("Deleted data by source: %s", source)
            return 1
        except Exception:
            logger.exception("Failed to delete data by source: %s", source)
            return 0

    def count_by_source(self, source: str) -> int:
        try:
            result = self._client.count(
                collection_name=self._collection_name,
                count_filter=Filter(
                    must=[
                        FieldCondition(key="source", match=MatchValue(value=source)),
                    ]
                ),
            )
            return result.count
        except Exception:
            return 0

    def upsert_points(self, points: list[dict], vectors: list[list[float]], batch_size: int = 100) -> int:
        if len(points) != len(vectors):
            raise ValueError(f"points({len(points)}) and vectors({len(vectors)}) count mismatch")

        qdrant_points = [
            PointStruct(id=index, vector=vector, payload=point)
            for index, (point, vector) in enumerate(zip(points, vectors))
        ]

        for index in range(0, len(qdrant_points), batch_size):
            batch = qdrant_points[index:index + batch_size]
            self._client.upsert(collection_name=self._collection_name, points=batch)

        return len(qdrant_points)

    def upsert_points_with_ids(
        self,
        points: list[dict],
        vectors: list[list[float]],
        point_ids: list[str],
        batch_size: int = 100,
    ) -> int:
        if len(points) != len(vectors) or len(points) != len(point_ids):
            raise ValueError("points, vectors, and point_ids count mismatch")

        qdrant_points = [
            PointStruct(id=point_id, vector=vector, payload=point)
            for point_id, point, vector in zip(point_ids, points, vectors)
        ]

        for index in range(0, len(qdrant_points), batch_size):
            batch = qdrant_points[index:index + batch_size]
            self._client.upsert(collection_name=self._collection_name, points=batch)

        return len(qdrant_points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = None,
        score_threshold: float = None,
        page_id_filter: str = None,
        breadcrumb_filter: str = None,
    ) -> list[SearchResult]:
        top_k = top_k or settings.search_top_k
        score_threshold = score_threshold or settings.score_threshold

        query_filter = None
        if page_id_filter or breadcrumb_filter:
            must_conditions = []
            if page_id_filter:
                must_conditions.append({"key": "page_id", "match": {"value": page_id_filter}})
            if breadcrumb_filter:
                must_conditions.append({"key": "breadcrumb_path", "match": {"text": breadcrumb_filter}})
            query_filter = {"must": must_conditions}

        response = self._client.query_points(
            collection_name=self._collection_name,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            SearchResult(
                page_id=point.payload.get("page_id", ""),
                page_title=point.payload.get("page_title", "Untitled"),
                section_title=point.payload.get("section_title"),
                breadcrumb_path=point.payload.get("breadcrumb_path", ""),
                text=point.payload.get("text", ""),
                text_preview=point.payload.get("text_preview", ""),
                url=point.payload.get("url", ""),
                chunk_index=point.payload.get("chunk_index", 0),
                score=point.score,
            )
            for point in response.points
        ]

    def get_collection_info(self) -> dict:
        info = self._client.get_collection(collection_name=self._collection_name)
        return {
            "name": self._collection_name,
            "points_count": info.points_count,
            "indexed_vectors_count": getattr(info, "indexed_vectors_count", None),
        }
