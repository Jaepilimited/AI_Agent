"""
RAG 답변 생성
"""

from openai import OpenAI

from app.core.config import settings
from app.core.logging import logger
from app.qdrant.store import SearchResult
from app.rag.prompt import build_answer_prompt, format_sources


def generate_answer(
    query: str,
    results: list[SearchResult],
) -> dict:
    """
    검색 결과를 context로 LLM 답변 생성.

    Returns:
        {"answer": "...", "sources": [...]}
    """
    if not results:
        return {
            "answer": "관련 문서를 찾지 못했습니다. 질문을 다시 구체적으로 입력해 주세요.",
            "sources": [],
        }

    prompt = build_answer_prompt(query, results)
    client = OpenAI(api_key=settings.openai_api_key)

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )

    answer = response.choices[0].message.content.strip()
    sources = format_sources(results)

    logger.info(f"답변 생성 완료 (출처 {len(sources)}개)")

    return {"answer": answer, "sources": sources}
