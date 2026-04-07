"""
LLM 기반 쿼리 확장 - 구어체/약어를 다양한 검색 쿼리로 변형
"""

import json
from openai import OpenAI

from app.core.config import settings
from app.core.logging import logger


_EXPANSION_PROMPT = """당신은 검색 쿼리를 확장하는 전문가입니다.
사용자의 검색 질문을 받아 동일한 의도를 가진 다양한 검색 쿼리를 생성해주세요.

규칙:
1. 원본 쿼리를 첫 번째로 포함
2. 유사한 표현, 동의어, 관련 키워드를 사용한 변형 생성
3. 한국어/영어 변형 포함 (약어 확장 포함 - 예: 클코 → 클로드 코드)
4. 구체적인 표현과 일반적인 표현 모두 포함
5. 검색에 효과적인 핵심 키워드 조합 포함

JSON 배열 형식으로만 응답하세요. 설명 없이 배열만 출력하세요."""


def expand_query(query: str, num_queries: int = 4) -> list[str]:
    """
    사용자 질문을 여러 검색 쿼리로 확장.
    실패 시 원본 쿼리만 반환 (silent failure 없음).
    """
    client = OpenAI(api_key=settings.openai_api_key)

    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": _EXPANSION_PROMPT},
                {"role": "user", "content": f"검색 쿼리 {num_queries}개 생성:\n{query}"},
            ],
            temperature=1.0,
            max_completion_tokens=500,
        )

        content = response.choices[0].message.content
        if not content:
            return [query]

        queries = _parse_queries(content.strip())

        if query not in queries:
            queries.insert(0, query)

        logger.debug(f"쿼리 확장: '{query}' → {queries}")
        return queries[:num_queries]

    except Exception as e:
        logger.warning(f"쿼리 확장 실패 (원본 사용): {e}")
        return [query]


def _parse_queries(content: str) -> list[str]:
    """LLM 응답을 파싱하여 쿼리 리스트 추출"""
    try:
        if "```" in content:
            start = content.find("[")
            end = content.rfind("]") + 1
            content = content[start:end]

        queries = json.loads(content)
        if isinstance(queries, list):
            return [q.strip() for q in queries if isinstance(q, str) and q.strip()]
    except json.JSONDecodeError:
        pass

    # JSON 파싱 실패 시 줄바꿈 파싱
    lines = content.split("\n")
    queries = []
    for line in lines:
        line = line.strip()
        if line and line[0].isdigit() and "." in line:
            line = line.split(".", 1)[1].strip()
        line = line.strip('"').strip("'").strip()
        if line:
            queries.append(line)

    return queries
