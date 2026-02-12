"""Vector search retriever with relevance grading and reranking."""

import json
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.core.bigquery import get_bigquery_client
from app.core.embeddings import get_embedding_model
from app.core.llm import get_llm_client

logger = structlog.get_logger(__name__)


class VectorRetriever:
    """Retrieve relevant documents using BigQuery vector search."""

    def __init__(
        self,
        top_k: int = 5,
        relevance_threshold: float = 0.7,
    ) -> None:
        """Initialize the retriever.

        Args:
            top_k: Number of documents to retrieve.
            relevance_threshold: Minimum similarity score to consider relevant.
        """
        self.top_k = top_k
        self.relevance_threshold = relevance_threshold
        logger.info(
            "retriever_initialized",
            top_k=top_k,
            threshold=relevance_threshold,
        )

    def retrieve(self, query: str) -> List[Dict[str, Any]]:
        """Retrieve relevant documents for a query.

        Args:
            query: Search query string.

        Returns:
            List of document dicts with content, metadata, and score.
        """
        logger.info("retrieving_documents", query=query[:100])

        # Generate query embedding
        embedding_model = get_embedding_model()
        query_embedding = embedding_model.embed_query(query)

        # Vector search
        bq = get_bigquery_client()
        results = bq.vector_search(
            query_embedding=query_embedding,
            top_k=self.top_k,
            distance_type="COSINE",
        )

        documents = []
        for row in results:
            metadata = row.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}

            doc = {
                "id": row.get("id", ""),
                "content": row.get("content", ""),
                "metadata": metadata,
                "source_type": row.get("source_type", ""),
                "score": 1.0 - row.get("distance", 1.0),  # Convert distance to similarity
            }
            documents.append(doc)

        logger.info("documents_retrieved", count=len(documents))
        return documents

    def grade_documents(
        self,
        query: str,
        documents: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Grade retrieved documents for relevance.

        Args:
            query: The original query.
            documents: Retrieved documents.

        Returns:
            Tuple of (relevant_documents, relevance_verdict).
            relevance_verdict is "yes" or "no".
        """
        if not documents:
            return [], "no"

        llm = get_llm_client()
        relevant_docs = []

        for doc in documents:
            prompt = f"""다음 문서가 사용자의 질문에 관련이 있는지 판단하세요.

질문: {query}

문서:
{doc['content'][:500]}

관련 있으면 "yes", 없으면 "no"로만 답변하세요."""

            try:
                result = llm.generate(prompt, temperature=0.0).strip().lower()
                if "yes" in result:
                    relevant_docs.append(doc)
            except Exception as e:
                logger.warning("grading_failed", doc_id=doc.get("id"), error=str(e))
                # Include document if grading fails (fail-open for retrieval)
                relevant_docs.append(doc)

        relevance = "yes" if relevant_docs else "no"
        logger.info(
            "documents_graded",
            total=len(documents),
            relevant=len(relevant_docs),
            verdict=relevance,
        )
        return relevant_docs, relevance

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Rerank documents using cross-encoder or LLM scoring.

        Args:
            query: The original query.
            documents: Documents to rerank.

        Returns:
            Reranked list of documents (most relevant first).
        """
        if len(documents) <= 1:
            return documents

        llm = get_llm_client()
        prompt = f"""다음 문서들을 질문과의 관련성 순으로 정렬하세요.

질문: {query}

문서 목록:
"""
        for i, doc in enumerate(documents):
            prompt += f"\n[{i}] {doc['content'][:200]}"

        prompt += "\n\n가장 관련 있는 문서 번호부터 순서대로 나열하세요 (예: 2, 0, 1, 3). 번호만 출력하세요."

        try:
            result = llm.generate(prompt, temperature=0.0)
            # Parse the ordering
            import re
            indices = [int(x) for x in re.findall(r'\d+', result)]
            indices = [i for i in indices if i < len(documents)]

            reranked = [documents[i] for i in indices]
            # Append any documents not in the reranked list
            seen = set(indices)
            for i, doc in enumerate(documents):
                if i not in seen:
                    reranked.append(doc)

            logger.info("documents_reranked", order=indices[:5])
            return reranked

        except Exception as e:
            logger.warning("reranking_failed", error=str(e))
            return documents


def get_retriever(top_k: int = 5, relevance_threshold: float = 0.7) -> VectorRetriever:
    """Create a new retriever instance."""
    return VectorRetriever(top_k=top_k, relevance_threshold=relevance_threshold)
