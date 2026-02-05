"""Query Verifier Agent (v3.0).

Additional LLM-based verification layer on top of existing
sql_agent.py validate_sql(). Separate agent because SQL accuracy
is critical - wrong queries have high risk.
"""

import json

from langchain_anthropic import ChatAnthropic

from app.models.agent_models import AgentModel


class QueryVerifierAgent:
    def __init__(self):
        self.llm = ChatAnthropic(
            model=AgentModel.QUERY_VERIFIER.value,
            temperature=0,
            max_tokens=2048,
        )

    async def verify(self, sql: str, schema_info: str) -> dict:
        """Verify generated SQL.

        Args:
            sql: SQL query to verify.
            schema_info: Table schema information.

        Returns:
            {"valid": bool, "errors": list, "corrected_sql": str|None}
        """
        prompt = f"""당신은 BigQuery SQL 검증 전문가입니다.
아래 SQL을 검증하고 문제가 있으면 수정해주세요.

검증 항목:
1. SQL 문법 정확성 (BigQuery Standard SQL)
2. 스키마 정합성 (존재하는 컬럼/테이블인지)
3. 보안 검사 (SELECT만 허용, INSERT/UPDATE/DELETE/DROP 차단)
4. 컬럼 매핑 규칙 준수:
   - 매출: Sales1_R 우선 (Sales2_R 보조)
   - 수량: Total_Qty (Quantity 사용 금지)
   - 제품명: `SET` (Product_Name 금지, 백틱 필수)
   - 대륙: Continent1 우선
   - 팀: Team_NEW (Team 금지)
5. Date >= '2000-01-01' 필터 포함 여부
6. SET 컬럼 백틱 이스케이프 여부

스키마 정보:
{schema_info}

검증 대상 SQL:
{sql}

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{"valid": true, "errors": [], "corrected_sql": null}}
또는
{{"valid": false, "errors": ["에러 설명"], "corrected_sql": "수정된 SQL"}}"""

        response = await self.llm.ainvoke(prompt)
        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> dict:
        """Parse LLM response."""
        try:
            text = content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            return json.loads(text)
        except (json.JSONDecodeError, IndexError):
            return {
                "valid": False,
                "errors": ["검증 응답 파싱 실패 - 수동 확인 필요"],
                "corrected_sql": None,
            }
