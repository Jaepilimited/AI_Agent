"""
Query Expander - LLM을 사용하여 검색 쿼리 확장
"""

import json
from openai import OpenAI

from config import settings


EXPANSION_PROMPT = """당신은 검색 쿼리를 확장하는 전문가입니다.
사용자의 검색 질문을 받아 동일한 의도를 가진 다양한 검색 쿼리를 생성해주세요.

규칙:
1. 원본 쿼리를 첫 번째로 포함
2. 유사한 표현, 동의어, 관련 키워드를 사용한 변형 생성
3. 한국어/영어 변형 포함 (약어 확장 등)
4. 구체적인 표현과 일반적인 표현 모두 포함
5. 검색에 효과적인 핵심 키워드 조합 포함

예시:
- 입력: "east 업무"
- 출력: ["east 업무", "EAST팀 업무 가이드", "동부 마케팅 업무", "EAST 담당 업무", "이스트 팀 역할"]

JSON 배열 형식으로만 응답하세요. 설명 없이 배열만 출력하세요."""


class QueryExpander:
    """LLM 기반 쿼리 확장기"""

    def __init__(self, model: str = None):
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.llm_model

    def expand(self, query: str, num_queries: int = None) -> list[str]:
        """
        사용자 질문을 여러 검색 쿼리로 확장

        Args:
            query: 원본 사용자 질문
            num_queries: 생성할 쿼리 수 (기본값: settings에서 가져옴)

        Returns:
            확장된 쿼리 리스트 (원본 포함)
        """
        num_queries = num_queries or settings.multi_query_count

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": EXPANSION_PROMPT},
                    {"role": "user", "content": f"검색 쿼리 {num_queries}개 생성:\n{query}"}
                ],
                temperature=1,
                max_completion_tokens=1000
            )

            content = response.choices[0].message.content
            if not content:
                return [query]

            queries = self._parse_response(content.strip())

            if query not in queries:
                queries.insert(0, query)

            return queries[:num_queries]

        except Exception:
            # 실패 시 원본 쿼리만 반환
            return [query]

    def _parse_response(self, content: str) -> list[str]:
        """LLM 응답을 파싱하여 쿼리 리스트 추출"""
        # JSON 배열 추출 시도
        try:
            # ```json ... ``` 형식 처리
            if "```" in content:
                start = content.find("[")
                end = content.rfind("]") + 1
                content = content[start:end]

            queries = json.loads(content)
            if isinstance(queries, list):
                return [q.strip() for q in queries if isinstance(q, str) and q.strip()]
        except json.JSONDecodeError:
            pass

        # 줄바꿈으로 구분된 형식 처리
        lines = content.split("\n")
        queries = []
        for line in lines:
            line = line.strip()
            # 번호 제거 (1. 2. 등)
            if line and line[0].isdigit() and "." in line:
                line = line.split(".", 1)[1].strip()
            # 따옴표 제거
            line = line.strip('"').strip("'").strip()
            if line:
                queries.append(line)

        return queries
