"""
LLM 답변 생성 프롬프트
"""

from app.qdrant.store import SearchResult


def build_context(results: list[SearchResult]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[{i}] 팀: {r.team} | 페이지: {r.page_title} | 섹션: {r.section_path}\n"
            f"{r.text}"
        )
    return "\n\n---\n\n".join(parts)


def build_answer_prompt(query: str, results: list[SearchResult]) -> str:
    context = build_context(results)
    return f"""당신은 사내 Notion 문서 기반 AI 어시스턴트입니다.

아래 참고 문서들을 바탕으로 질문에 답하세요.

규칙:
- 링크만 나열하지 말고, 먼저 내용을 설명하세요.
- 참고 문서에 근거가 없는 내용은 "문서에서 확인할 수 없습니다"라고 명시하세요.
- 답변 마지막에 출처 목록을 [번호] 형태로 붙이세요.
- 한국어로 답하세요.

===참고 문서===
{context}

===질문===
{query}

===답변==="""


def format_sources(results: list[SearchResult]) -> list[dict]:
    seen_pages: set[str] = set()
    sources = []
    for r in results:
        if r.page_id not in seen_pages:
            seen_pages.add(r.page_id)
            sources.append({
                "page_title": r.page_title,
                "page_url": r.page_url,
                "team": r.team,
                "section_path": r.section_path,
            })
    return sources
