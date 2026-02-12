"""Orchestrator Agent (v3.0 core).

v2.0: Query Analyzer -> route_type -> single Agent call
v3.0: Orchestrator -> specialized Sub Agent delegation
v3.1: Conversation context continuity (messages passthrough)
v3.2: Dual model support (Gemini 2.5 Pro / Sonnet 4.5)
v3.3: Google Search grounding + multi-source analysis (internal + external)
"""

import json
from datetime import datetime
from typing import Dict, List, Optional

import structlog

from app.core.llm import MODEL_CLAUDE, MODEL_GEMINI, get_flash_client, get_llm_client
from app.core.response_formatter import ensure_formatting

# Existing agent
from app.agents.sql_agent import run_sql_agent

# v3.0 new agents
from app.agents.query_verifier import QueryVerifierAgent
from app.agents.notion_agent import NotionAgent
from app.agents.gws_agent import GWSAgent

logger = structlog.get_logger(__name__)


def _build_conversation_context(messages: List[Dict[str, str]], max_turns: int = 5) -> str:
    """Build a conversation context string from recent messages.

    Extracts the last N turns (user+assistant pairs) excluding the final user message,
    so agents can understand references like "아까 그 데이터", "그거 다시", "2월은?" etc.

    Args:
        messages: Full conversation history [{"role": ..., "content": ...}].
        max_turns: Maximum number of previous turns to include.

    Returns:
        Context string, or empty string if no history.
    """
    if not messages or len(messages) <= 1:
        return ""

    # Exclude the last message (current query) — take previous messages
    history = messages[:-1]

    # Take only the last N messages (max_turns * 2 for user+assistant pairs)
    history = history[-(max_turns * 2):]

    if not history:
        return ""

    lines = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lines.append(f"사용자: {content}")
        elif role in ("assistant", "model"):
            # Truncate long assistant responses
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"AI: {content}")

    return "\n".join(lines)


class OrchestratorAgent:
    """Orchestrator-Worker pattern conductor.

    Analyzes query intent and delegates to appropriate Sub Agent.
    Supports both Gemini 2.5 Pro and Claude Sonnet 4.5 based on user selection.
    """

    def __init__(self):
        logger.info("orchestrator_initialized")

        # v3.0 new agents (lazy init)
        self._query_verifier = None
        self._notion_agent = None
        self._gws_agent = None

    @property
    def query_verifier(self):
        if self._query_verifier is None:
            self._query_verifier = QueryVerifierAgent()
        return self._query_verifier

    @property
    def notion_agent(self):
        if self._notion_agent is None:
            self._notion_agent = NotionAgent()
        return self._notion_agent

    @property
    def gws_agent(self):
        if self._gws_agent is None:
            self._gws_agent = GWSAgent()
        return self._gws_agent

    async def route_and_execute(
        self,
        query: str,
        messages: Optional[List[Dict[str, str]]] = None,
        model_type: str = MODEL_GEMINI,
        user_email: str = "",
    ) -> dict:
        """Main entry point: analyze query -> delegate to Sub Agent -> return result.

        Args:
            query: User's natural language question (latest message).
            messages: Full conversation history for context continuity.
            model_type: "gemini" or "claude" — which LLM to use.
            user_email: User's email for GWS OAuth authentication.

        Returns:
            {"source": str, "answer": str, ...}
        """
        messages = messages or []
        conversation_context = _build_conversation_context(messages)

        # Step 1: Classify query intent
        # Fast path: keyword match first, LLM fallback only when ambiguous
        route = self._keyword_classify(query)
        if route == "direct" and conversation_context:
            # Ambiguous query with context — use Flash for fast classification
            flash = get_flash_client()
            route = await self._classify_with_llm(query, conversation_context, flash)
        logger.info(
            "orchestrator_routed",
            query=query[:100],
            route=route,
            model_type=model_type,
            has_context=bool(conversation_context),
        )

        # Step 2: Execute via Sub Agent with context
        handlers = {
            "bigquery": self._handle_bigquery,
            "notion": self._handle_notion,
            "gws": self._handle_gws,
            "multi": self._handle_multi,
        }
        handler = handlers.get(route, self._handle_direct)
        result = await handler(query, messages, conversation_context, model_type, user_email)

        # Step 3: Post-process response for consistent markdown formatting
        if "answer" in result:
            result["answer"] = ensure_formatting(result["answer"], domain=route)

        return result

    async def _classify_with_llm(self, query: str, conversation_context: str, llm) -> str:
        """LLM-based classification (used only when keyword match is ambiguous).

        Uses Flash model for speed. Only called when there's conversation context
        and keyword matching returned 'direct'.
        """
        context_section = ""
        if conversation_context:
            context_section = f"""
## 이전 대화 (참고용)
{conversation_context}

"""

        prompt = f"""사용자 질문을 분석하여 적절한 처리 경로를 결정하세요.

경로 옵션:
- bigquery: 순수 데이터 조회 (매출, 수량, 주문, 재고 등 숫자 조회/집계만 필요)
- notion: 사내 문서, 정책, 매뉴얼, 제품 정보, 프로세스 관련
- gws: Google Drive 파일, Gmail 메일, Calendar 일정 관련
- multi: 내부 데이터 + 외부 정보가 모두 필요한 복합 분석 질문
  예시: "날씨가 매출에 영향?", "매출 하락 원인", "시장 트렌드와 매출 비교", "인도네시아 경제 상황이 판매에 미치는 영향"
- direct: 일반 지식, 용어 설명, 간단한 질문, 실시간 정보 (날씨, 뉴스 등)

판단 기준:
- 데이터 조회만 → bigquery
- 데이터 + 외부맥락(날씨/시장/경쟁/원인/영향/트렌드) → multi
- 외부 정보만 → direct
- 이전 대화 맥락을 참고하여 "그거", "아까", "다시" 같은 참조를 이해하세요.
{context_section}현재 질문: {query}

경로 하나만 답변 (bigquery/notion/gws/multi/direct):"""

        try:
            response = llm.generate(prompt, temperature=0.0)
            route = response.strip().lower().split()[0] if response.strip() else "direct"

            valid_routes = {"bigquery", "notion", "gws", "multi", "direct"}
            if route in valid_routes:
                return route
        except Exception as e:
            logger.warning("llm_classify_failed", error=str(e))

        # Keyword-based fallback
        return self._keyword_classify(query)

    # Data-related keywords (triggers BigQuery)
    _DATA_KEYWORDS = [
        "매출", "수량", "주문", "sales", "revenue",
        "쇼피", "아마존", "틱톡", "국가별", "월별",
        "대륙별", "플랫폼별", "연도별", "분기별",
        "몰별", "채널별", "브랜드별", "제품별", "SKU",
        "라인", "차트", "그래프", "그려",
        "재고", "판매", "거래", "실적", "성과",
        "데이터", "조회", "집계", "합계", "평균",
        "분석", "추이", "증감", "성장률",
        "top", "순위", "랭킹",
        # Product listing queries → BigQuery Product table
        "제품 리스트", "제품 목록", "제품 종류", "전체 제품",
        "어떤 제품", "제품이 뭐", "제품 수", "몇 개 제품",
        "제품 현황", "제품 카테고리",
    ]

    # External-only keywords (triggers web search when combined with data keywords → multi)
    # These are ONLY external context — "분석", "데이터" etc. belong in _DATA_KEYWORDS
    _EXTERNAL_KEYWORDS = [
        "날씨", "영향", "원인", "이유", "왜",
        "트렌드", "경쟁", "뉴스",
        "환율", "전망", "예측",
        "연관", "상관",
        "경제", "물가", "인플레이션", "정책변화",
        "소비자", "인구",
        "시즌", "계절", "명절", "할인행사",
        # NOTE: "시장" removed — too ambiguous, causes false multi-routing
        # for pure data queries like "인도네시아 시장 매출". Other external
        # keywords (트렌드, 영향 etc.) still catch true multi-intent queries.
    ]

    _GWS_KEYWORDS = [
        "드라이브", "drive", "메일", "gmail", "캘린더", "calendar",
        "회의록", "회의", "미팅", "일정", "스케줄", "구글시트", "스프레드시트",
        "내 메일", "내 드라이브", "내 캘린더", "내 일정",
        "파일 찾아", "파일 검색", "시트 찾아", "시트 열어",
        "메일 보여", "메일 찾아", "메일 요약", "메일 정리",
        "이번주 일정", "오늘 일정", "이번달 일정",
        "받은 메일", "보낸 메일", "읽지 않은 메일",
    ]

    _NOTION_KEYWORDS = [
        "노션", "notion",
        "정책", "매뉴얼", "프로세스", "가이드", "반품",
        "사내 문서", "위키", "제품 정보",
    ]

    def _keyword_classify(self, query: str) -> str:
        """Keyword-based query classification.

        Priority: Notion (explicit) > GWS > Data > External > Direct
        """
        q = query.lower()

        # Notion check — "노션" explicitly mentioned → always Notion
        if any(kw in q for kw in self._NOTION_KEYWORDS):
            return "notion"

        # GWS check — highest priority for personal workspace queries
        if any(kw in q for kw in self._GWS_KEYWORDS):
            return "gws"

        has_data = any(kw in q for kw in self._DATA_KEYWORDS)
        has_external = any(kw in q for kw in self._EXTERNAL_KEYWORDS)

        # Both data + external context needed → multi-source analysis
        if has_data and has_external:
            return "multi"

        if has_data:
            return "bigquery"
        return "direct"

    async def _handle_bigquery(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
    ) -> dict:
        """BigQuery Agent with conversation context.

        Falls back to a helpful data-error message if SQL generation fails,
        preserving context that this was a SKIN1004 internal data query.
        """
        try:
            answer = await run_sql_agent(
                query,
                conversation_context=conversation_context,
                model_type=model_type,
            )
            # Check if SQL agent returned an error (it returns error as string, not exception)
            if "오류" in answer and ("SQL" in answer or "생성되지" in answer):
                logger.warning("bigquery_sql_failed_fallback_to_direct", query=query[:100])
                return await self._handle_bigquery_fallback(
                    query, messages, conversation_context, model_type, user_email
                )
            return {"source": "bigquery", "answer": answer}
        except Exception as e:
            logger.error("orchestrator_bigquery_failed", error=str(e))
            return await self._handle_bigquery_fallback(
                query, messages, conversation_context, model_type, user_email
            )

    async def _handle_bigquery_fallback(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
    ) -> dict:
        """Fallback when BigQuery SQL generation fails.

        Instead of generic direct LLM (which may answer with unrelated general knowledge),
        we give the LLM context that this was a SKIN1004 internal data query so it provides
        a helpful "data unavailable" response with suggestions.
        """
        llm = get_llm_client(model_type)
        fallback_prompt = f"""사용자가 SKIN1004 내부 매출/판매 데이터를 조회하려 했으나, 데이터베이스에서 조회에 실패했습니다.

사용자 질문: {query}

다음 규칙에 따라 답변하세요:
1. 요청한 데이터를 조회할 수 없었다는 점을 간결하게 안내하세요.
2. 질문을 좀 더 구체적으로 바꿔보라고 제안하세요 (예: 기간, 국가, 채널, 제품명 등을 명시).
3. 가능한 질문 예시를 2-3개 제시하세요.
4. 일반적인 인터넷 정보로 답변하지 마세요. 이것은 SKIN1004 내부 데이터 질문입니다.
5. 한국어로 답변하세요."""

        try:
            answer = llm.generate(fallback_prompt, temperature=0.3)
            return {"source": "bigquery_fallback", "answer": answer}
        except Exception:
            return {
                "source": "bigquery_fallback",
                "answer": "죄송합니다. 요청하신 데이터를 조회할 수 없었습니다. "
                "질문을 좀 더 구체적으로 해주시면 다시 시도해보겠습니다.\n\n"
                "예시:\n"
                "- \"2024년 미국 아마존 월별 매출 알려줘\"\n"
                "- \"2024년 미국 채널별 매출 top5 비교해줘\"\n"
                "- \"센텔라 앰플 120ml 미국 매출 추이 알려줘\"",
            }

    async def _handle_notion(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
    ) -> dict:
        """Notion Sub Agent execution with context."""
        contextualized_query = query
        if conversation_context:
            contextualized_query = f"[이전 대화]\n{conversation_context}\n\n[현재 질문]\n{query}"
        result = await self.notion_agent.run(contextualized_query, model_type=model_type)
        return {"source": "notion", "answer": result}

    async def _handle_gws(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
    ) -> dict:
        """Google Workspace Sub Agent execution with context and per-user auth."""
        contextualized_query = query
        if conversation_context:
            contextualized_query = f"[이전 대화]\n{conversation_context}\n\n[현재 질문]\n{query}"
        result = await self.gws_agent.run(contextualized_query, user_email=user_email)
        return {"source": "gws", "answer": result}

    async def _handle_multi(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
    ) -> dict:
        """Multi-source analysis: combines internal data (BigQuery) + external info (Google Search).

        Steps:
          1. Google Search — gather external context (market, weather, news, etc.)
          2. BigQuery — query internal sales/data
          3. Synthesize — combine both with LLM (with search grounding for Gemini)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        sub_results = {}

        # --- Step 1: Google Search for external context (always via Gemini) ---
        web_context = ""
        try:
            gemini = get_llm_client(MODEL_GEMINI)
            search_prompt = f"""다음 질문과 관련된 최신 외부 정보를 검색하여 정리하세요.
내부 매출/주문 데이터는 제외하고, 다음과 같은 외부 맥락만 수집하세요:
- 해당 국가/지역의 현재 상황 (날씨, 경제, 정책 등)
- 시장 동향, 소비자 트렌드
- 관련 뉴스, 이벤트
- 경쟁 환경, 업계 동향

오늘 날짜: {today}
질문: {query}

관련 외부 정보를 항목별로 정리하세요:"""
            web_context = gemini.generate_with_search(search_prompt, temperature=0.2)
            sub_results["web_search"] = {"answer": web_context}
            logger.info("multi_web_search_done", length=len(web_context))
        except Exception as e:
            logger.warning("multi_web_search_failed", error=str(e))
            sub_results["web_search"] = {"error": str(e)}

        # --- Step 2: BigQuery for internal data ---
        # Rewrite the query to focus on data extraction only
        # (the original query may be too broad for SQL generation)
        bq_answer = ""
        try:
            flash = get_flash_client()
            data_query_prompt = f"""사용자의 복합 질문에서 BigQuery 매출/주문 데이터 조회에 필요한 부분만 추출하세요.
외부 분석(날씨, 시장, 원인 등)은 제외하고, 순수 데이터 조회 질문으로 변환하세요.

원래 질문: {query}

예시:
- "날씨가 인도네시아 매출에 영향?" → "인도네시아 최근 매출 데이터 조회"
- "경쟁사 대비 태국 쇼피 매출 분석" → "태국 쇼피 매출 데이터 조회"
- "환율 변동으로 베트남 매출 하락 원인" → "베트남 최근 월별 매출 추이"

데이터 조회 질문만 한 줄로 작성:"""
            data_query = flash.generate(data_query_prompt, temperature=0.0).strip()
            logger.info("multi_data_query_rewritten", original=query[:100], rewritten=data_query[:100])

            bq_answer = await run_sql_agent(
                data_query,
                conversation_context=conversation_context,
                model_type=model_type,
            )
            # Check if bq_answer contains an error message
            if "오류" in bq_answer and "SQL" in bq_answer:
                logger.warning("multi_bigquery_sql_failed", answer=bq_answer[:100])
                bq_answer = ""
                sub_results["bigquery"] = {"error": "데이터 조회 실패"}
            else:
                sub_results["bigquery"] = {"answer": bq_answer}
                logger.info("multi_bigquery_done", length=len(bq_answer))
        except Exception as e:
            logger.warning("multi_bigquery_failed", error=str(e))
            sub_results["bigquery"] = {"error": str(e)}

        # --- Step 3: Synthesize internal + external ---
        llm = get_llm_client(model_type)

        synthesis_prompt = f"""당신은 SKIN1004의 데이터 분석 전문 AI입니다.
내부 데이터와 외부 정보를 종합하여 **분석 보고서 형식**으로 답변하세요.

## 사용자 질문
{query}

## 내부 데이터 (BigQuery 매출/주문 데이터)
{bq_answer if bq_answer else "데이터 조회 결과 없음"}

## 외부 정보 (Google 검색)
{web_context if web_context else "외부 정보 수집 실패"}

## 답변 형식 (반드시 아래 구조를 따르세요)

### 📈 [질문 주제] 분석

#### 요약
[3-4문장 핵심 결론. 가장 중요한 수치는 **굵게**]

#### 내부 데이터 분석
[BigQuery 매출/수량 데이터 기반. 핵심 수치를 표로 정리. 추이나 변화를 수치로 제시]

#### 외부 맥락
[Google 검색 기반 시장/경제/날씨 정보. 관련 외부 요인 정리]

#### 종합 인사이트
[내부 데이터 + 외부 맥락을 연결한 분석]
> [핵심 시사점 1-2개를 인용 형식으로 강조]

#### 제안 사항
- [실행 가능한 제안 1-3개]

---
*분석 기준: SKIN1004 내부 데이터 + Google 검색 ({today})*

## 작성 규칙
1. 금액: 1억 이상은 "약 OO.O억원", 1억 미만은 천 단위 쉼표.
2. 내부 데이터 사실과 외부 맥락 분석을 명확히 구분하세요.
3. 핵심 수치는 **굵게** 표시하세요.
"""

        try:
            # Gemini: use search grounding for even richer synthesis
            if model_type == MODEL_GEMINI:
                answer = llm.generate_with_search(synthesis_prompt, temperature=0.3)
            else:
                answer = llm.generate(synthesis_prompt, temperature=0.3)
        except Exception as e:
            logger.warning("multi_synthesize_failed", error=str(e))
            # Fallback: just concatenate the parts
            parts = []
            if bq_answer:
                parts.append(f"## 내부 데이터\n{bq_answer}")
            if web_context:
                parts.append(f"## 외부 정보\n{web_context}")
            answer = "\n\n".join(parts) if parts else "분석에 필요한 정보를 수집하지 못했습니다."

        return {
            "source": "multi",
            "answer": answer,
            "sub_results": sub_results,
        }

    async def _handle_direct(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
    ) -> dict:
        """General question: uses full conversation history for natural dialogue.

        Both Gemini and Claude get real-time info via Google Search.
        - Gemini: native Google Search grounding
        - Claude: Gemini Search gathers info → passed to Claude for final answer
        """
        llm = get_llm_client(model_type)
        today = datetime.now().strftime("%Y년 %m월 %d일 (%A)")

        # Model display name for self-identification
        if model_type == MODEL_CLAUDE:
            model_name = "Claude Opus (Anthropic) — 복잡한 판단/분석. 내부 경량 작업에는 Claude Sonnet을 사용합니다"
        else:
            model_name = "Gemini 2.5 Pro (Google) — 대화용. 내부적으로 SQL 생성/차트 등 빠른 작업에는 Gemini 2.5 Flash를 사용합니다"

        system = f"""당신은 SKIN1004의 AI 어시스턴트입니다. ({model_name} 기반)
오늘 날짜는 {today}입니다.

## 핵심 원칙
- 사용자의 질문에 친절하고 정확하게 한국어로 답변하세요.
- 질문한 내용만 답변하세요. 질문과 무관한 부가 정보나 홍보성 안내를 덧붙이지 마세요.
- 실시간 정보가 제공된 경우, 최신 정보를 있는 그대로 전달하세요.
- 모르는 것은 모른다고 솔직하게 답변하세요. 추측하거나 지어내지 마세요.
- 자기소개를 길게 하지 마세요. 바로 답변 내용으로 시작하세요.

## 답변 형식
- 복잡한 주제는 구조화하세요: 개념 설명(정의 → 핵심 포인트 → 예시), 비교(표 활용), 목록(번호 목록).
- 핵심 용어나 수치는 **굵게** 표시하세요.
- 간단한 인사나 한두 줄 답변에는 불필요한 구조를 넣지 마세요.
- 실시간 검색 정보를 포함할 때는 출처를 간략히 명시하세요."""

        try:
            if model_type == MODEL_GEMINI:
                # Gemini: native Google Search grounding
                if messages and len(messages) > 1:
                    answer = llm.generate_with_history_and_search(
                        messages=messages,
                        system_instruction=system,
                        temperature=0.5,
                    )
                else:
                    answer = llm.generate_with_search(
                        query,
                        system_instruction=system,
                        temperature=0.5,
                    )
            else:
                # Claude: gather real-time info via Gemini Search, then answer with Claude
                search_context = self._gather_search_context(query)

                if search_context:
                    # Inject search results into Claude's prompt
                    search_system = system + f"\n\n## 참고할 최신 검색 정보 (Google 검색 결과)\n{search_context}"
                else:
                    search_system = system

                if messages and len(messages) > 1:
                    answer = llm.generate_with_history(
                        messages=messages,
                        system_instruction=search_system,
                        temperature=0.5,
                    )
                else:
                    answer = llm.generate(
                        query,
                        system_instruction=search_system,
                        temperature=0.5,
                    )
            return {"source": "direct", "answer": answer}
        except Exception as e:
            logger.error("direct_llm_failed", error=str(e))
            return {"source": "direct", "answer": f"답변 생성 중 오류가 발생했습니다: {str(e)}"}

    def _gather_search_context(self, query: str) -> str:
        """Gather real-time info via Gemini Search for non-Gemini models.

        Returns search context string, or empty string if not needed / failed.
        """
        try:
            gemini = get_llm_client(MODEL_GEMINI)
            search_result = gemini.generate_with_search(
                f"다음 질문에 답하기 위해 필요한 최신 정보를 검색하여 핵심만 정리하세요. "
                f"길게 설명하지 말고 사실 위주로 간결하게 정리하세요.\n\n질문: {query}",
                temperature=0.1,
            )
            logger.info("search_context_gathered", length=len(search_result))
            return search_result
        except Exception as e:
            logger.warning("search_context_failed", error=str(e))
            return ""
