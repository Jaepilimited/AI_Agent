"""
Qdrant 벡터 저장소

- ensure_collection(): 없으면 생성, 있으면 유지
- upsert_chunks(): chunk 배열 + 벡터 적재
- delete_by_page_id(): 특정 page 전체 chunk 삭제 (재색인 전 호출)
- search(): 벡터 검색
"""

from dataclasses import dataclass
from typing import Any

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

from app.core.config import settings
from app.core.logging import logger


@dataclass
class SearchResult:
    score: float
    page_id: str
    page_title: str
    page_url: str
    section_path: str
    breadcrumb: str
    text: str
    chunk_index: int
    team: str
    hub_id: str


class QdrantStore:
    def __init__(self):
        self._client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=60,
        )
        self._collection = settings.qdrant_collection

    def ensure_collection(self) -> None:
        """컬렉션이 없으면 생성, 있으면 유지. 인덱스는 항상 보장."""
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=settings.embedding_dim,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(f"컬렉션 '{self._collection}' 생성 완료")
        else:
            logger.info(f"컬렉션 '{self._collection}' 존재 확인")

        self._create_indexes()

    def collection_exists(self) -> bool:
        """Qdrant 컬렉션 존재 여부 확인."""
        existing = [c.name for c in self._client.get_collections().collections]
        return self._collection in existing

    def recreate_collection(self) -> None:
        """컬렉션을 삭제하고 새로 생성 (벡터 설정 초기화 목적)"""
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection in existing:
            self._client.delete_collection(collection_name=self._collection)
            logger.info(f"컬렉션 '{self._collection}' 삭제 완료")

        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(
                size=settings.embedding_dim,
                distance=Distance.COSINE,
            ),
        )
        logger.info(f"컬렉션 '{self._collection}' 재생성 완료")
        self._create_indexes()

    def _create_indexes(self) -> None:
        index_fields = {
            "source": PayloadSchemaType.KEYWORD,
            "hub_id": PayloadSchemaType.KEYWORD,
            "team": PayloadSchemaType.KEYWORD,
            "page_id": PayloadSchemaType.KEYWORD,
            "status": PayloadSchemaType.KEYWORD,
        }
        for field, schema in index_fields.items():
            try:
                self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception as e:
                logger.warning(f"인덱스 생성 경고 ({field}): {e}")
        logger.info(f"payload 인덱스 생성 완료: {list(index_fields)}")

    def delete_by_page_id(self, page_id: str) -> None:
        """특정 page의 모든 chunk 삭제"""
        self._client.delete(
            collection_name=self._collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="page_id", match=MatchValue(value=page_id))]
                )
            ),
        )
        logger.info(f"page_id='{page_id}' chunk 삭제 완료")

    def upsert_chunks(
        self,
        payloads: list[dict],
        vectors: list[list[float]],
        point_ids: list[str],
        batch_size: int = 50,
    ) -> int:
        """chunk 배열을 Qdrant에 upsert"""
        if len(payloads) != len(vectors) or len(payloads) != len(point_ids):
            raise ValueError("payloads / vectors / point_ids 길이 불일치")

        points = [
            PointStruct(id=pid, vector=vec, payload=payload)
            for pid, vec, payload in zip(point_ids, vectors, payloads)
        ]

        for i in range(0, len(points), batch_size):
            self._client.upsert(
                collection_name=self._collection,
                points=points[i : i + batch_size],
            )

        return len(points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = None,
        score_threshold: float = None,
        team_filter: str = None,
        hub_id_filter: str = None,
    ) -> list[SearchResult]:
        top_k = top_k or settings.search_top_k
        score_threshold = score_threshold or settings.score_threshold

        must = []
        if team_filter:
            must.append(FieldCondition(key="team", match=MatchValue(value=team_filter)))
        if hub_id_filter:
            must.append(FieldCondition(key="hub_id", match=MatchValue(value=hub_id_filter)))
        must.append(FieldCondition(key="status", match=MatchValue(value="active")))

        query_filter = Filter(must=must) if must else None

        response = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            SearchResult(
                score=r.score,
                page_id=r.payload.get("page_id", ""),
                page_title=r.payload.get("page_title", ""),
                page_url=r.payload.get("page_url", ""),
                section_path=r.payload.get("section_path", ""),
                breadcrumb=r.payload.get("breadcrumb", ""),
                text=r.payload.get("text", ""),
                chunk_index=r.payload.get("chunk_index", 0),
                team=r.payload.get("team", ""),
                hub_id=r.payload.get("hub_id", ""),
            )
            for r in response.points
        ]

    def list_page_chunk_stats(
        self,
        hub_id_filter: str = None,
        team_filter: str = None,
        batch_size: int = 256,
    ) -> list[dict[str, Any]]:
        """활성 chunk를 page_id 기준으로 집계해 페이지 단위 현황을 반환."""
        if not self.collection_exists():
            return []

        must = [FieldCondition(key="status", match=MatchValue(value="active"))]
        if hub_id_filter:
            must.append(FieldCondition(key="hub_id", match=MatchValue(value=hub_id_filter)))
        if team_filter:
            must.append(FieldCondition(key="team", match=MatchValue(value=team_filter)))

        query_filter = Filter(must=must)
        offset = None
        pages: dict[str, dict[str, Any]] = {}
        payload_fields = [
            "page_id",
            "page_title",
            "page_url",
            "team",
            "hub_id",
            "source",
            "last_edited_time",
            "status",
        ]

        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=query_filter,
                with_payload=payload_fields,
                with_vectors=False,
                limit=batch_size,
                offset=offset,
            )

            for point in points:
                payload = point.payload or {}
                page_id = payload.get("page_id")
                if not page_id:
                    continue

                page = pages.setdefault(
                    page_id,
                    {
                        "page_id": page_id,
                        "page_title": payload.get("page_title", ""),
                        "page_url": payload.get("page_url", ""),
                        "team": payload.get("team", ""),
                        "hub_id": payload.get("hub_id", ""),
                        "source": payload.get("source", ""),
                        "status": payload.get("status", ""),
                        "last_edited_time": payload.get("last_edited_time", ""),
                        "chunk_count": 0,
                    },
                )

                page["chunk_count"] += 1

                if not page["page_title"] and payload.get("page_title"):
                    page["page_title"] = payload["page_title"]
                if not page["page_url"] and payload.get("page_url"):
                    page["page_url"] = payload["page_url"]
                if not page["last_edited_time"] and payload.get("last_edited_time"):
                    page["last_edited_time"] = payload["last_edited_time"]

            if offset is None:
                break

        return sorted(
            pages.values(),
            key=lambda item: (item.get("team", ""), item.get("page_title", ""), item["page_id"]),
        )

    def get_indexed_pages(self, hub_id_filter: str = None) -> dict[str, str]:
        """활성 page_id → last_edited_time 맵 반환 (증분 처리용).

        Returns:
            {page_id: last_edited_time}  last_edited_time이 없으면 ""
        """
        if not self.collection_exists():
            return {}

        must = [FieldCondition(key="status", match=MatchValue(value="active"))]
        if hub_id_filter:
            must.append(FieldCondition(key="hub_id", match=MatchValue(value=hub_id_filter)))

        query_filter = Filter(must=must)
        result: dict[str, str] = {}
        offset = None

        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=query_filter,
                with_payload=["page_id", "last_edited_time"],
                with_vectors=False,
                limit=256,
                offset=offset,
            )

            for point in points:
                payload = point.payload or {}
                page_id = payload.get("page_id")
                if page_id and page_id not in result:
                    result[page_id] = payload.get("last_edited_time", "")

            if offset is None:
                break

        return result

    def get_collection_info(self) -> dict:
        info = self._client.get_collection(collection_name=self._collection)
        return {
            "name": self._collection,
            "points_count": info.points_count,
        }
