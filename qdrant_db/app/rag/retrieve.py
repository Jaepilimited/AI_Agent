"""
검색 retrieval - 멀티 쿼리 확장 + Qdrant 검색 + 결과 병합
"""

from app.embeddings.client import EmbeddingClient
from app.qdrant.store import QdrantStore, SearchResult
from app.rag.query_expander import expand_query
from app.core.config import settings
from app.core.logging import logger


def retrieve(
    query: str,
    top_k: int = None,
    team_filter: str = None,
    hub_id_filter: str = None,
    embedder: EmbeddingClient = None,
    store: QdrantStore = None,
) -> list[SearchResult]:
    """
    쿼리를 LLM으로 확장 후 각 쿼리로 Qdrant 검색, 결과 병합.
    같은 chunk는 score 높은 것 우선으로 dedupe.
    """
    if embedder is None:
        embedder = EmbeddingClient()
    if store is None:
        store = QdrantStore()

    top_k = top_k or settings.search_top_k

    # 쿼리 확장 (구어체/약어 → 다양한 표현)
    queries = expand_query(query, num_queries=4)
    logger.info(f"확장된 쿼리 {len(queries)}개: {queries}")

    # 각 쿼리로 검색 후 병합
    seen: dict[str, SearchResult] = {}  # key: "page_id:chunk_index"

    for q in queries:
        vec = embedder.embed_single(q)
        results = store.search(
            query_vector=vec,
            top_k=top_k * 2,
            score_threshold=0.3,  # 확장 쿼리는 임계값 낮게
            team_filter=team_filter,
            hub_id_filter=hub_id_filter,
        )
        for r in results:
            key = f"{r.page_id}:{r.chunk_index}"
            if key not in seen or r.score > seen[key].score:
                seen[key] = r

    # score 기준 정렬
    merged = sorted(seen.values(), key=lambda r: r.score, reverse=True)

    # 같은 page에서 최대 3개 chunk만 유지
    page_counts: dict[str, int] = {}
    deduped: list[SearchResult] = []
    for r in merged:
        count = page_counts.get(r.page_id, 0)
        if count < 3:
            deduped.append(r)
            page_counts[r.page_id] = count + 1

    return deduped[:top_k]
