"""
Multi-Query Retriever - 다중 쿼리 검색 및 결과 병합
"""

from dataclasses import dataclass

from config import settings
from embedding import BatchEmbedder
from vector_store import QdrantStore, SearchResult
from .query_expander import QueryExpander


@dataclass
class RetrievalResult:
    """검색 결과 (메타데이터 포함)"""
    results: list[SearchResult]
    expanded_queries: list[str]
    total_candidates: int


class MultiQueryRetriever:
    """다중 쿼리 검색기"""

    def __init__(
        self,
        embedder: BatchEmbedder = None,
        store: QdrantStore = None,
        expander: QueryExpander = None
    ):
        self._embedder = embedder or BatchEmbedder()
        self._store = store or QdrantStore()
        self._expander = expander or QueryExpander()

    def retrieve(
        self,
        query: str,
        top_k: int = None,
        enable_expansion: bool = None
    ) -> RetrievalResult:
        """
        쿼리 확장 후 다중 검색 수행

        Args:
            query: 사용자 질문
            top_k: 최종 반환할 결과 수
            enable_expansion: 쿼리 확장 활성화 여부

        Returns:
            RetrievalResult (결과 + 메타데이터)
        """
        top_k = top_k or settings.search_top_k
        enable_expansion = enable_expansion if enable_expansion is not None else settings.multi_query_enabled

        # 쿼리 확장
        if enable_expansion:
            queries = self._expander.expand(query)
        else:
            queries = [query]

        # 각 쿼리로 검색
        all_results: list[SearchResult] = []
        seen_keys: set[str] = set()

        for q in queries:
            vector = self._embedder.embed_single(q)
            results = self._store.search(
                query_vector=vector,
                top_k=top_k,
                score_threshold=settings.multi_query_threshold
            )

            for result in results:
                # 중복 제거 (page_id + chunk_index 기준)
                key = f"{result.page_id}:{result.chunk_index}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_results.append(result)

        total_candidates = len(all_results)

        # 점수 기준 정렬 후 상위 K개 선택
        all_results.sort(key=lambda x: x.score, reverse=True)
        final_results = all_results[:top_k]

        return RetrievalResult(
            results=final_results,
            expanded_queries=queries,
            total_candidates=total_candidates
        )

    def retrieve_simple(
        self,
        query: str,
        top_k: int = None
    ) -> list[SearchResult]:
        """
        간단한 인터페이스 - 결과만 반환

        Args:
            query: 사용자 질문
            top_k: 반환할 결과 수

        Returns:
            SearchResult 리스트
        """
        result = self.retrieve(query, top_k)
        return result.results
