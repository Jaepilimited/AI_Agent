"""
Notion RAG chatbot entrypoint.
"""

import sys
from pathlib import Path

from openai import OpenAI

# Add project root to import path.
sys.path.insert(0, str(Path(__file__).parent))

from config import get_logger, settings, setup_logging
from retriever import MultiQueryRetriever
from vector_store import SearchResult


setup_logging()
logger = get_logger(__name__)


SYSTEM_PROMPT = """당신은 Notion에 저장된 정보를 기반으로 답변하는 AI 비서입니다.

규칙:
1. 제공된 컨텍스트 정보만 바탕으로 답변합니다.
2. 컨텍스트에 없는 정보는 찾을 수 없다고 명확히 말합니다.
3. 답변은 명확하고 간결하게 작성합니다.
4. 가능하면 출처(페이지 제목, 경로)를 함께 언급합니다.
5. 여러 문서에서 관련 정보를 찾은 경우 종합해서 답변합니다."""


def build_context(results: list[SearchResult]) -> str:
    """Convert retrieved chunks into a prompt context."""
    context_parts = []

    for index, result in enumerate(results, 1):
        section_info = f" > {result.section_title}" if result.section_title else ""
        path_info = f"[경로: {result.breadcrumb_path}{section_info}]"
        context_parts.append(
            f"[문서 {index}] {result.page_title}\n"
            f"{path_info}\n"
            f"{result.text}"
        )

    return "\n\n---\n\n".join(context_parts)


def generate_answer(query: str, context: str) -> str:
    """Generate an answer from retrieved context."""
    client = OpenAI(api_key=settings.openai_api_key)

    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"컨텍스트:\n{context}\n\n질문: {query}"},
            ],
            temperature=0.7,
            max_completion_tokens=1000,
        )
        return response.choices[0].message.content or "(답변 생성 실패)"
    except Exception as exc:
        logger.exception("Answer generation failed")
        return f"(오류: {exc})"


def main():
    print("=" * 50)
    print("Notion RAG 챗봇 (Multi-Query)")
    print(f"모델: {settings.llm_model}")
    print(f"검색 상위 K: {settings.search_top_k}")
    print(f"Multi-Query 활성화: {settings.multi_query_enabled}")
    print(f"쿼리 확장 수: {settings.multi_query_count}")
    print("종료: quit / q / exit")
    print("=" * 50)
    print()

    logger.info(
        "Chatbot started | model=%s top_k=%s multi_query=%s query_count=%s",
        settings.llm_model,
        settings.search_top_k,
        settings.multi_query_enabled,
        settings.multi_query_count,
    )

    retriever = MultiQueryRetriever()

    while True:
        query = input("질문: ").strip()

        if query.lower() in ["quit", "exit", "q"]:
            logger.info("Chatbot terminated by user")
            print("종료합니다.")
            break

        if not query:
            continue

        logger.info("Received query: %s", query)

        retrieval_result = retriever.retrieve(query)
        results = retrieval_result.results

        if not results:
            logger.info("No results found for query: %s", query)
            print("\n관련 문서를 찾지 못했습니다.\n")
            continue

        context = build_context(results)
        logger.info(
            "Generating answer | query=%s results=%s expanded_queries=%s total_candidates=%s",
            query,
            len(results),
            retrieval_result.expanded_queries,
            retrieval_result.total_candidates,
        )
        print("\n답변 생성 중입니다...")
        answer = generate_answer(query, context)

        print(f"\n{'=' * 50}")
        print(f"답변:\n{answer}")
        print(f"{'=' * 50}")

        logger.info("Answer generated successfully for query: %s", query)

        print("\n참고 문서:")
        for index, result in enumerate(results[:3], 1):
            score_pct = result.score * 100
            section = f" > {result.section_title}" if result.section_title else ""
            print(f"  [{index}] {result.page_title}{section} (유사도: {score_pct:.1f}%)")
            print(f"      경로: {result.breadcrumb_path}")
            if result.url:
                print(f"      URL: {result.url}")
        print()


if __name__ == "__main__":
    main()
