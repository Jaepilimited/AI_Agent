"""Orchestrator Agent (v3.0 core).

v2.0: Query Analyzer -> route_type -> single Agent call
v3.0: Orchestrator -> specialized Sub Agent delegation
v3.1: Conversation context continuity (messages passthrough)
v3.2: Dual model support (Gemini 2.5 Pro / Sonnet 4.5)
v3.3: Google Search grounding + multi-source analysis (internal + external)
v3.4: CS DB route — customer service Q&A from Google Spreadsheet
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional

import structlog

from app.core.llm import MODEL_CLAUDE, MODEL_GEMINI, get_flash_client, get_llm_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE
from app.core.response_formatter import ensure_formatting

# Existing agent
from app.agents.sql_agent import run_sql_agent

# v3.0 new agents
from app.agents.query_verifier import QueryVerifierAgent
from app.agents.notion_agent import NotionAgent
from app.agents.gws_agent import GWSAgent

logger = structlog.get_logger(__name__)


def _content_to_text(content) -> str:
    """Extract plain text from content (str or multimodal list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return " ".join(parts).strip()
    return str(content)


def _build_conversation_context(messages: List[Dict[str, str]]) -> str:
    """Build conversation context from all previous messages — no truncation.

    Gemini 2.5 Flash supports 1M token context, so we keep everything.
    """
    if not messages or len(messages) <= 1:
        return ""

    history = messages[:-1]
    if not history:
        return ""

    lines = []
    for msg in history:
        role = msg.get("role", "user")
        content = _content_to_text(msg.get("content", ""))
        if role == "user":
            lines.append(f"사용자: {content}")
        elif role in ("assistant", "model"):
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

    # Source name → route mapping (matches frontend DATA_SOURCE_KEYS)
    _SOURCE_ROUTE_MAP = {
        "BigQuery 매출": "bigquery", "BigQuery 제품": "bigquery",
        "BQ 광고데이터": "bigquery", "BQ 마케팅비용": "bigquery",
        "BQ Shopify": "bigquery", "BQ 플랫폼": "bigquery",
        "BQ 인플루언서": "bigquery", "BQ 아마존검색": "bigquery",
        "BQ 아마존리뷰": "bigquery", "BQ 큐텐리뷰": "bigquery",
        "BQ 쇼피리뷰": "bigquery", "BQ 스마트스토어": "bigquery",
        "BQ 메타광고": "bigquery",
        "Notion 문서": "notion",
        "CS Q&A": "cs",
        "Google Workspace": "gws",
    }

    def _allowed_routes(self, enabled_sources: Optional[List[str]]) -> Optional[set]:
        """Derive the set of allowed routes from enabled_sources.

        Returns None if no filtering should be applied (param not provided).
        Returns {"direct"} if explicitly empty list (all sources disabled).
        """
        if enabled_sources is None:
            return None
        routes = {"direct"}  # direct is always allowed
        for src in enabled_sources:
            route = self._SOURCE_ROUTE_MAP.get(src)
            if route:
                routes.add(route)
        # Allow multi if bigquery is enabled
        if "bigquery" in routes:
            routes.add("multi")
        return routes

    async def route_and_execute(
        self,
        query: str,
        messages: Optional[List[Dict[str, str]]] = None,
        model_type: str = MODEL_GEMINI,
        user_email: str = "",
        images: Optional[List[dict]] = None,
        brand_filter: Optional[str] = None,
        enabled_sources: Optional[List[str]] = None,
        stream_callback=None,
    ) -> dict:
        """Main entry point: analyze query -> delegate to Sub Agent -> return result.

        Args:
            query: User's natural language question (latest message).
            messages: Full conversation history for context continuity.
            model_type: "gemini" or "claude" — which LLM to use.
            user_email: User's email for GWS OAuth authentication.
            images: Extracted images [{"data": bytes, "mime_type": str}].
            brand_filter: Comma-separated brand codes (e.g. "SK,CL,CBT" or "UM").
            enabled_sources: List of enabled source keys from frontend checkboxes.
            stream_callback: Optional async callable(chunk: str) for real-time streaming.

        Returns:
            {"source": str, "answer": str, ...}
        """
        messages = messages or []
        images = images or []
        conversation_context = _build_conversation_context(messages)

        # Image present → force direct route (vision LLM)
        if images:
            logger.info("orchestrator_image_route_forced", image_count=len(images), query=query[:100])
            result = await self._handle_direct(
                query, messages, conversation_context, model_type, user_email, images=images
            )
            if "answer" in result:
                result["answer"] = ensure_formatting(result["answer"], domain="direct")
            return result

        # Step 1: Classify query intent
        # Fast path: keyword match first, LLM fallback only for short ambiguous queries
        route = self._keyword_classify(query)
        is_system_task = query.strip().startswith("### Task:")
        _DIRECT_LOCK_KW = ["회사", "뭐하는", "소개", "누가 만들", "주인", "재밌", "안녕", "하이", "hello", "hi", "부동산", "주식", "투자", "아파트", "전세", "월세", "대출", "연봉", "이직"]
        _is_direct_locked = any(kw in query.lower() for kw in _DIRECT_LOCK_KW)
        if route == "direct" and conversation_context and not is_system_task and not _is_direct_locked:
            if len(query.strip()) <= 30:
                flash = get_flash_client()
                route = await self._classify_with_llm(query, conversation_context, flash)
        # Apply enabled_sources filter — redirect to direct if route is disabled
        allowed = self._allowed_routes(enabled_sources)
        if allowed is not None and route not in allowed:
            logger.info("route_filtered_by_sources", original_route=route, allowed=list(allowed))
            route = "direct"

        logger.info(
            "orchestrator_routed",
            query=query[:100],
            route=route,
            model_type=model_type,
            has_context=bool(conversation_context),
            enabled_sources=enabled_sources,
        )

        # Step 2: Execute via Sub Agent with context
        handlers = {
            "bigquery": self._handle_bigquery,
            "notion": self._handle_notion,
            "gws": self._handle_gws,
            "cs": self._handle_cs,
            "multi": self._handle_multi,
        }
        handler = handlers.get(route, self._handle_direct)
        if route in ("bigquery", "multi"):
            result = await handler(query, messages, conversation_context, model_type, user_email, brand_filter=brand_filter, enabled_sources=enabled_sources)
        elif route == "direct" or handler == self._handle_direct:
            result = await self._handle_direct(query, messages, conversation_context, model_type, user_email, images=images, stream_callback=stream_callback)
        else:
            result = await handler(query, messages, conversation_context, model_type, user_email)

        # Step 3: Coherence check — fire-and-forget background (was blocking 2-3s)
        # Only log mismatches; don't delay the response
        if "answer" in result and route not in ("direct", "multi", "cs"):
            import threading
            _q, _a, _r = query, result["answer"], route
            def _bg_coherence():
                try:
                    import asyncio as _aio
                    _aio.run(self._verify_coherence(_q, _a, _r))
                except Exception:
                    pass
            threading.Thread(target=_bg_coherence, daemon=True).start()

        # Step 4: Post-process response for consistent markdown formatting
        if "answer" in result:
            result["answer"] = ensure_formatting(result["answer"], domain=route)

        return result

    async def route_and_stream(
        self,
        query: str,
        messages=None,
        model_type: str = MODEL_GEMINI,
        user_email: str = "",
        images=None,
        brand_filter=None,
        enabled_sources=None,
    ):
        """Async generator: yields (type, data) tuples for real-time streaming.

        Yields:
            ("source", source_name) — route source tag
            ("chunk", text) — streamed text chunk
            ("done", full_answer) — final complete answer (for non-streaming routes)
        """
        import asyncio

        messages = messages or []
        images = images or []
        conversation_context = _build_conversation_context(messages)

        # Image → non-streaming direct
        if images:
            result = await self._handle_direct(
                query, messages, conversation_context, model_type, user_email, images=images
            )
            yield ("source", "direct")
            yield ("done", ensure_formatting(result.get("answer", ""), domain="direct"))
            return

        route = self._keyword_classify(query)
        is_system_task = query.strip().startswith("### Task:")
        # Re-classify short ambiguous queries with LLM (only if no strong direct signal)
        _DIRECT_LOCK = ["회사", "뭐하는", "소개", "누가 만들", "주인", "재밌", "안녕", "하이", "hello", "hi"]
        _is_direct_locked = any(kw in query.lower() for kw in _DIRECT_LOCK)
        if route == "direct" and conversation_context and not is_system_task and not _is_direct_locked:
            if len(query.strip()) <= 30:
                flash = get_flash_client()
                route = await self._classify_with_llm(query, conversation_context, flash)

        # Direct route → real-time streaming
        if route == "direct" and not is_system_task:
            yield ("source", "direct")

            llm = get_llm_client(model_type)
            today = datetime.now().strftime("%Y년 %m월 %d일 (%A)")
            system = self._build_direct_system_prompt(today, model_type)

            _needs_search = self._needs_web_search(query)
            final_system = system
            if _needs_search:
                search_context = self._gather_search_context(query)
                if search_context:
                    final_system = system + f"\n\n## 참고할 최신 검색 정보 (Google 검색 결과)\n{search_context}"

            # Stream via thread + queue
            _q: asyncio.Queue = asyncio.Queue()
            _loop = asyncio.get_running_loop()

            def _worker():
                try:
                    if messages and len(messages) > 1 and hasattr(llm, 'generate_with_history_stream'):
                        gen = llm.generate_with_history_stream(
                            messages=messages, system_instruction=final_system, temperature=0.5,
                        )
                    else:
                        gen = llm.generate_stream(
                            query, system_instruction=final_system, temperature=0.5,
                        )
                    for chunk in gen:
                        _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", chunk))
                except Exception as e:
                    _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", f"오류: {e}"))
                _loop.call_soon_threadsafe(_q.put_nowait, ("end", None))

            _loop.run_in_executor(None, _worker)

            full_answer = ""
            while True:
                msg_type, data = await _q.get()
                if msg_type == "end":
                    break
                full_answer += data
                yield ("chunk", data)

            # Streaming complete — signal done (content already sent via chunks)
            yield ("done", "")
            return

        # BQ route → streaming format_answer
        if route == "bigquery":
            import asyncio as _aio
            from app.agents.sql_agent import run_sql_agent_stream

            yield ("source", "bigquery")

            _q: _aio.Queue = _aio.Queue()
            _loop = _aio.get_running_loop()

            def _bq_worker():
                try:
                    for chunk in run_sql_agent_stream(
                        query,
                        conversation_context=conversation_context,
                        model_type=model_type,
                        brand_filter=brand_filter,
                        enabled_sources=enabled_sources,
                    ):
                        _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", chunk))
                except Exception as e:
                    _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", f"오류: {e}"))
                _loop.call_soon_threadsafe(_q.put_nowait, ("end", None))

            _loop.run_in_executor(None, _bq_worker)

            while True:
                msg_type, data = await _q.get()
                if msg_type == "end":
                    break
                yield ("chunk", data)

            yield ("done", "")
            return

        # Non-streaming routes (CS, Notion, GWS, Multi)
        handlers = {
            "notion": self._handle_notion,
            "gws": self._handle_gws,
            "cs": self._handle_cs,
            "multi": self._handle_multi,
        }
        handler = handlers.get(route, self._handle_direct)
        if route == "multi":
            result = await handler(query, messages, conversation_context, model_type, user_email, brand_filter=brand_filter, enabled_sources=enabled_sources)
        else:
            result = await handler(query, messages, conversation_context, model_type, user_email)

        if "answer" in result:
            result["answer"] = ensure_formatting(result["answer"], domain=route)

        yield ("source", result.get("source", route))
        yield ("done", result.get("answer", ""))

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
- notion: 사내 문서, 정책, 매뉴얼, 프로세스 관련
- gws: Google Drive 파일, Gmail 메일, Calendar 일정 관련
- cs: 제품 CS 상담 (성분, 사용법, 비건인증, 피부 관련 질문, 제품 문의)
- multi: 내부 데이터 + 외부 정보가 모두 필요한 복합 분석 질문
  예시: "날씨가 매출에 영향?", "매출 하락 원인", "시장 트렌드와 매출 비교", "인도네시아 경제 상황이 판매에 미치는 영향"
- direct: 일반 지식, 용어 설명, 간단한 질문, 실시간 정보 (날씨, 뉴스 등)

판단 기준:
- 데이터 조회만 → bigquery
- 제품 성분/사용법/CS 문의 → cs
- SKIN1004 데이터 + 외부맥락(날씨/시장/경쟁/원인/영향/트렌드) → multi
- 외부 정보만 → direct
- ⚠️ SKIN1004/매출/제품과 무관한 질문(부동산, 주식, 일반상식, 개인질문 등)은 이전 대화가 BQ였어도 반드시 direct!
- 이전 대화 맥락을 참고하여 "그거", "아까", "다시" 같은 참조를 이해하세요.
{context_section}현재 질문: {query}

경로 하나만 답변 (bigquery/notion/gws/cs/multi/direct):"""

        try:
            response = llm.generate(prompt, temperature=0.0)
            route = response.strip().lower().split()[0] if response.strip() else "direct"

            valid_routes = {"bigquery", "notion", "gws", "cs", "multi", "direct"}
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
        "몰별", "채널별", "브랜드별", "제품별", "카테고리별", "카테고리", "SKU",
        "라인", "차트", "그래프", "그려",
        "재고", "판매", "거래", "실적", "성과",
        "데이터", "조회", "집계", "합계", "평균",
        "분석", "추이", "증감", "성장률",
        "top", "순위", "랭킹",
        # Product listing queries → BigQuery Product table
        "제품 리스트", "제품 목록", "제품 종류", "전체 제품",
        "어떤 제품", "제품이 뭐", "제품 수", "몇 개 제품",
        "제품 현황", "제품 카테고리",
        # Marketing / Advertising (마케팅 테이블)
        "광고", "광고비", "광고 비용", "마케팅", "마케팅비", "마케팅 비용",
        "ROAS", "roas", "ROI", "roi", "CTR", "ctr",
        "노출", "노출수", "impression", "클릭", "클릭수", "click",
        "전환", "전환율", "conversion", "구매전환",
        "페이스북", "facebook", "메타", "meta", "publisher_platform",
        "구글 광고", "google ads", "네이버 광고", "네이버 검색광고",
        "카카오모먼츠", "kakao",
        "GMV", "gmv",
        # Influencer (인플루언서 테이블)
        "인플루언서", "influencer", "팔로워", "좋아요",
        "조회수", "공유수", "댓글수", "저장수",
        "콘텐츠", "캠페인", "에이전시", "티어",
        # Review (리뷰 테이블)
        "리뷰", "review", "평점", "별점",
        "리뷰 분석", "고객 리뷰", "제품 리뷰",
        "스마트스토어 리뷰", "아마존 리뷰", "쇼피 리뷰", "큐텐 리뷰",
        # Shopify
        "shopify", "쇼피파이", "자사몰 매출", "반품", "환불",
        # Platform metrics
        "플랫폼 순위", "제품 순위", "랭킹 데이터", "할인가",
        # Region / Continent / Team / Account
        "cis", "동남아", "유럽", "북미", "남미", "중동", "대륙",
        "신규", "업체", "거래처", "바이어", "b2b", "b2c",
        "세일즈", "매상", "비중", "비율", "갯수", "개수",
        "판매량", "전년 대비", "전년대비",
        "베스트셀러", "인기 제품", "가장 많이 팔",
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
        "정책", "매뉴얼", "프로세스", "가이드", "반품 정책", "반품정책",
        "사내 문서", "위키", "제품 정보",
    ]

    _CS_KEYWORDS = [
        "cs", "고객 상담", "고객상담", "faq",
        "성분", "비건", "peta", "동물실험",
        "사용법", "사용 방법", "사용방법", "루틴", "스킨케어",
        "제품 문의", "제품문의",
        "센텔라", "히알루", "톤브라이트닝", "포어마이징",
        "티트리카", "프로바이오", "랩인네이처",
        "commonlabs", "zombie beauty", "좀비뷰티", "커먼랩스",
        "자극", "알레르기", "보관", "유통기한", "개봉 후", "개봉후",
        "임산부", "수유", "아토피", "민감", "트러블",
        "피부 타입", "피부타입", "건성", "지성", "복합성",
        "사용 순서", "사용순서", "바르는 순서",
        "세럼", "앰플", "토너", "클렌저", "선크림", "크림", "마스크",
        "레티놀", "pha", "bha", "aha",
        "영유아", "어린이", "아기", "아이 피부", "아이에게", "아이가 써", "아이한테",
        "붉어", "따가", "가려", "피부 반응",
        "예민", "홍조", "건조", "좁쌀", "뾰루지",
        "불량", "교환", "환불", "이물질",
        "피부과", "시술", "직사광선",
        "병풀", "패치 테스트", "패치테스트",
        "skin1004", "스킨1004",
        "방부제", "향료", "인공색소", "파라벤", "sls", "글루텐",
        "직구", "매장",
        "기름지", "피부 관리", "피부관리",
    ]

    # How-to / guide keywords — when combined with platform/tool names, route to Notion
    # e.g. "틱톡샵 접속 방법 알려줘" → Notion (documented process), NOT sales data
    _HOWTO_KEYWORDS = [
        "접속 방법", "접속방법", "접속법", "로그인 방법", "로그인방법",
        "설정 방법", "설정방법", "설정법", "세팅 방법", "세팅방법",
        "등록 방법", "등록방법", "등록법",
        "연동 방법", "연동방법", "연동법",
        "어떻게 접속", "어떻게 들어가",
        "어떻게 로그인", "어떻게 설정", "어떻게 등록", "어떻게 연동",
        "접속하는 법", "들어가는 법",
        "접속하는 방법", "들어가는 방법",
        "접속해", "접속하", "어디서 접속", "어디로 접속",
    ]

    # Broader how-to keywords — only routed to Notion when a platform/tool name is also present
    # (avoids stealing CS queries like "센텔라 사용법")
    _HOWTO_BROAD_KEYWORDS = [
        "사용 방법", "사용방법", "사용법", "이용 방법", "이용방법", "이용법",
        "어떻게 사용", "어떻게 이용",
        "사용하는 법", "사용하는 방법",
        "가이드", "튜토리얼", "매뉴얼",
        "방법 알려", "방법알려", "방법 좀", "방법좀",
        "링크", "url", "주소",
    ]

    # Platform/tool names that, combined with how-to keywords, indicate a Notion doc question
    _PLATFORM_TOOL_NAMES = [
        "틱톡", "tiktok", "쇼피", "shopee", "라자다", "lazada", "아마존", "amazon",
        "쇼피파이", "shopify", "큐텐", "qoo10",
        "스마트스토어", "smartstore", "네이버",
        "셀러센터", "seller center", "셀러 센터",
        "노션", "notion", "지라", "jira", "슬랙", "slack",
        "빅쿼리", "bigquery", "구글 애널리틱스", "ga4",
        "erp", "sap", "crm",
    ]

    # Capability question patterns ("이미지 분석 가능해?", "차트 그릴 수 있어?") → direct
    # NOTE: "되나", "돼?" excluded — too broad (matches CS: "임산부가 써도 되나요")
    _CAPABILITY_PATTERNS = ["가능해", "가능한가", "가능하나", "수 있어", "뭐할 수", "뭐 할 수"]

    # Compound notion keywords that take priority over _DATA_KEYWORDS exclusion
    # e.g. "반품 정책 알려줘" → notion (not blocked by "반품" in data keywords)
    _COMPOUND_NOTION = ["반품 정책", "반품정책"]

    def _keyword_classify(self, query: str) -> str:
        """Keyword-based query classification.

        Priority: System tasks > Full data request > How-to (Notion) > Notion (explicit) > GWS > CS > Data > External > Direct
        """
        # Open WebUI system tasks (title/tag/follow-up) → direct, skip BQ false routing
        if query.strip().startswith("### Task:"):
            return "direct"

        q = query.lower()

        # Capability questions ("이미지 분석 가능해?", "차트 그릴 수 있어?") → direct
        if any(p in q for p in self._CAPABILITY_PATTERNS):
            return "direct"

        # Full data request → always bigquery (handled by _handle_bigquery → _handle_fulldata_request)
        if any(kw in q for kw in self._FULLDATA_KEYWORDS):
            return "bigquery"

        # How-to / guide questions about platforms → Notion (not BigQuery)
        # "틱톡샵 접속 방법 알려줘" = how to access TikTok Shop (documented in Notion)
        # vs "틱톡 2월 매출" = sales data query (BigQuery)
        # Narrow how-to keywords (접속방법, 로그인방법 etc.) → always Notion
        if any(kw in q for kw in self._HOWTO_KEYWORDS):
            return "notion"
        # Broad how-to keywords (사용법, 가이드 etc.) → Notion only with platform/tool names
        # This avoids stealing CS queries like "센텔라 사용법" (skincare product)
        if any(kw in q for kw in self._HOWTO_BROAD_KEYWORDS):
            if any(p in q for p in self._PLATFORM_TOOL_NAMES):
                return "notion"

        # Pre-compute data keyword match (used in Notion guard + later routing)
        has_data = any(kw in q for kw in self._DATA_KEYWORDS)

        # Notion check — but defer to bigquery when strong data keywords present
        if any(kw in q for kw in self._NOTION_KEYWORDS):
            # Compound notion keywords (e.g. "반품 정책") take priority over data exclusion
            if any(kw in q for kw in self._COMPOUND_NOTION):
                return "notion"
            # Don't steal data queries: "Shopify 반품 추이" → bigquery, not notion
            if not has_data:
                return "notion"

        # GWS check — highest priority for personal workspace queries
        if any(kw in q for kw in self._GWS_KEYWORDS):
            return "gws"

        # Web search guard: if search keywords match but NO SKIN1004 business context → direct
        # "올해 한국 GDP 성장률" → direct (general knowledge)
        # "올해 미국 매출" → bigquery (매출 = SKIN1004 data)
        if has_data and any(kw in q for kw in self._SEARCH_KEYWORDS):
            _SKIN1004_TERMS = [
                "skin1004", "스킨", "센텔라", "히알루", "커먼랩스", "좀비뷰티", "랩인네이처", "크레이버",
                "매출", "수량", "주문", "판매", "재고", "실적", "매상", "세일즈",
                "쇼피", "아마존", "틱톡", "라자다", "큐텐", "shopify", "쇼피파이",
                "광고비", "광고", "메타", "roas", "ctr", "마케팅비", "노출수", "클릭수",
                "인플루언서", "리뷰", "반품", "환불",
                "b2b", "b2c", "거래처", "바이어", "업체",
            ]
            if not any(t in q for t in _SKIN1004_TERMS):
                return "direct"

        # CS check — product Q&A, ingredients, usage, skincare
        # When both CS + DATA keywords present, only prefer BQ for strong analytics keywords
        # (매출, 수량, 주문 etc.), not ambiguous ones like "라인", "제품 목록"
        _STRONG_DATA = [
            "매출", "수량", "주문", "sales", "revenue",
            "판매량", "판매 수량", "세일즈", "매상", "갯수", "개수",
            "국가별", "월별", "분기별", "대륙별", "플랫폼별", "연도별", "채널별", "카테고리별", "카테고리",
            "재고", "집계", "합계", "통계", "데이터", "조회",
            "차트", "그래프", "그려",
            "top", "순위", "랭킹", "성장률", "증감", "추이",
            "비교", "비중", "비율", "전년 대비", "전년대비", "대비",
            "베스트셀러", "인기 제품", "가장 많이 팔",
            # Marketing strong data keywords
            "광고비", "광고 비용", "마케팅비", "ROAS", "CTR",
            "노출수", "클릭수", "전환율", "전환수",
            "인플루언서", "리뷰", "GMV",
            "신규", "업체", "거래처", "바이어", "b2b", "b2c",
            # Meta ads — override CS even when "skin1004" present
            "광고", "메타 광고", "메타광고", "활성 광고", "비활성 광고",
            "분포", "현황", "건수",
        ]
        has_strong_data = any(kw in q for kw in _STRONG_DATA)
        if any(kw in q for kw in self._CS_KEYWORDS) and not has_strong_data:
            return "cs"
        has_external = any(kw in q for kw in self._EXTERNAL_KEYWORDS)

        # Both data + external context needed → multi-source analysis
        if has_data and has_external:
            # "매출 트렌드" = pure data trend, not multi-source
            # Only override when the ONLY external keyword is "트렌드"
            # and it's adjacent to a data word (매출/판매/실적)
            external_hits = [kw for kw in self._EXTERNAL_KEYWORDS if kw in q]
            if external_hits == ["트렌드"]:
                data_trend = ["매출 트렌드", "매출트렌드", "판매 트렌드", "실적 트렌드", "주문 트렌드"]
                if any(p in q for p in data_trend):
                    return "bigquery"
            return "multi"

        if has_data:
            # Guard: data keywords present but NO SKIN1004 business context → direct
            # e.g. "육룡이 나르샤 평점" → "평점" matches data but not about our products
            _BIZ_CONTEXT = [
                "skin1004", "스킨", "센텔라", "히알루", "커먼랩스", "좀비뷰티", "랩인네이처", "크레이버",
                "매출", "수량", "주문", "판매", "재고", "실적", "매상", "세일즈",
                "쇼피", "아마존", "틱톡", "라자다", "큐텐", "shopify", "쇼피파이", "올리브영",
                "광고비", "광고", "메타", "roas", "ctr", "마케팅비", "노출수", "클릭수",
                "인플루언서", "반품", "환불", "b2b", "b2c", "거래처", "업체",
                "국가별", "월별", "팀별", "채널별", "제품별", "브랜드별", "사업부",
            ]
            if any(t in q for t in _BIZ_CONTEXT):
                return "bigquery"
            return "direct"
        return "direct"

    # Keywords that indicate user wants full/unlimited data from previous query
    _FULLDATA_KEYWORDS = [
        "전체 데이터 줘", "전체 데이터", "전체데이터", "다 줘", "다줘",
        "전부 줘", "전부줘", "전부 다 줘", "전부다줘",
        "제한 없이", "제한없이", "리밋 없이", "리밋없이",
        "가져가겠", "가져갈게", "그래도 줘", "그래도줘",
        "전체 보여", "전체보여", "다 보여", "다보여",
        "전부 가져", "전부가져", "모두 줘", "모두줘",
        "full data", "no limit", "all data",
    ]

    def _is_fulldata_request(self, query: str, conversation_context: str) -> bool:
        """Check if user is requesting full data after a truncation warning."""
        q = query.lower().strip()
        has_keyword = any(kw in q for kw in self._FULLDATA_KEYWORDS)
        has_truncation_context = "10,000행 제한" in conversation_context or "LIMIT에 도달" in conversation_context
        return has_keyword and has_truncation_context

    async def _handle_bigquery(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
        brand_filter: Optional[str] = None,
        enabled_sources: Optional[List[str]] = None,
    ) -> dict:
        """BigQuery Agent with conversation context.

        Falls back to a helpful data-error message if SQL generation fails,
        preserving context that this was a SKIN1004 internal data query.
        """
        # Check for "full data" follow-up request
        if self._is_fulldata_request(query, conversation_context):
            return await self._handle_fulldata_request(query, messages, conversation_context, model_type)

        # Maintenance check: warn but don't block (production-ready)
        _maintenance_warning = ""
        from app.core.safety import get_maintenance_manager
        mm = get_maintenance_manager()
        if mm.active and mm.manual:
            # Manual maintenance = hard block (admin explicitly requested)
            return {
                "source": "bigquery",
                "answer": (
                    "**데이터 점검 중입니다** — "
                    "관리자가 수동으로 점검을 활성화했습니다. "
                    "잠시 후 다시 시도해 주세요.\n\n"
                    f"*사유: {mm.reason}*"
                ),
            }
        elif mm.active:
            # Auto-detected update = soft warning, still execute query
            _maintenance_warning = f"\n\n> ⚠️ 참고: 데이터 테이블이 업데이트 중일 수 있습니다. 수치가 부정확하면 잠시 후 다시 조회해주세요."
            logger.info("maintenance_soft_warning", reason=mm.reason)
        try:
            answer = await run_sql_agent(
                query,
                conversation_context=conversation_context,
                model_type=model_type,
                brand_filter=brand_filter,
                enabled_sources=enabled_sources,
            )
            # Check if SQL agent returned an error (it returns error as string, not exception)
            if "오류" in answer and ("SQL" in answer or "생성되지" in answer):
                # Retry once before falling back
                logger.warning("bigquery_sql_failed_retry", query=query[:100])
                answer = await run_sql_agent(
                    query,
                    conversation_context=conversation_context,
                    model_type=model_type,
                    brand_filter=brand_filter,
                    enabled_sources=enabled_sources,
                )
                if "오류" in answer and ("SQL" in answer or "생성되지" in answer):
                    logger.warning("bigquery_sql_failed_fallback_to_direct", query=query[:100])
                    return await self._handle_bigquery_fallback(
                        query, messages, conversation_context, model_type, user_email
                    )
            return {"source": "bigquery", "answer": answer + _maintenance_warning}
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
5. 한국어로 답변하세요.
6. "오류가 발생" 같은 표현 대신 "데이터를 조회하지 못했습니다" 등 부드러운 표현을 쓰세요."""

        try:
            answer = llm.generate(fallback_prompt, temperature=0.3)
            return {"source": "bigquery_fallback", "answer": answer}
        except Exception:
            return {
                "source": "bigquery_fallback",
                "answer": (
                    "### 📊 데이터 조회 안내\n\n"
                    "요청하신 데이터를 조회하지 못했습니다. "
                    "질문을 좀 더 구체적으로 해주시면 다시 시도해보겠습니다.\n\n"
                    "---\n\n"
                    "> 💡 **이런 식으로 질문해 보세요**\n"
                    "> - \"2024년 미국 아마존 월별 매출 알려줘\"\n"
                    "> - \"2024년 미국 채널별 매출 top5 비교해줘\"\n"
                    "> - \"센텔라 앰플 120ml 미국 매출 추이 알려줘\""
                ),
            }

    async def _handle_fulldata_request(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
    ) -> dict:
        """Re-run previous BigQuery SQL without LIMIT when user requests full data."""
        from app.agents.sql_agent import _extract_previous_sql, run_sql_agent_unlimited

        # Extract previous SQL from conversation history
        previous_sql = ""
        for msg in reversed(messages):
            content = msg.get("content", "")
            if msg.get("role") == "assistant" and "```sql" in content:
                previous_sql = _extract_previous_sql(content)
                break

        if not previous_sql:
            # Try extracting from conversation context
            previous_sql = _extract_previous_sql(conversation_context)

        if not previous_sql:
            return {
                "source": "bigquery",
                "answer": "이전 쿼리를 찾을 수 없습니다. 원래 질문을 다시 해주세요.",
            }

        # Find the original question for context
        original_query = query
        for msg in reversed(messages):
            content = msg.get("content", "")
            if msg.get("role") == "user" and content != query:
                # This was the previous user question (the actual data query)
                if any(kw in content.lower() for kw in ["매출", "수량", "데이터", "조회"]):
                    original_query = content
                    break

        logger.info("fulldata_request", original_query=original_query[:100], sql=previous_sql[:200])

        try:
            answer = await run_sql_agent_unlimited(
                previous_sql=previous_sql,
                query=original_query,
                model_type=model_type,
            )
            return {"source": "bigquery", "answer": answer}
        except Exception as e:
            logger.error("fulldata_request_failed", error=str(e))
            return {
                "source": "bigquery",
                "answer": f"죄송합니다. 데이터 조회 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
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

    async def _handle_cs(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
    ) -> dict:
        """CS DB Sub Agent execution — customer service Q&A lookup."""
        from app.agents.cs_agent import run as run_cs_agent

        contextualized_query = query
        if conversation_context:
            contextualized_query = f"[이전 대화]\n{conversation_context}\n\n[현재 질문]\n{query}"
        try:
            result = await run_cs_agent(contextualized_query, model_type=model_type)
            return {"source": "cs", "answer": result}
        except Exception as e:
            logger.error("orchestrator_cs_failed", error=str(e))
            return {"source": "cs", "answer": f"죄송합니다. CS 데이터 조회 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}

    async def _handle_multi(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
        brand_filter: Optional[str] = None,
        enabled_sources: Optional[List[str]] = None,
    ) -> dict:
        """Multi-source analysis: internal data (BigQuery) + external info (Google Search).

        v6.4: Steps 1+2 run in parallel via asyncio.to_thread, synthesis uses Flash.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        sub_results = {}

        # --- Prepare prompts ---
        search_prompt = f"""질문과 관련된 최신 외부 정보를 검색하여 핵심만 간결히 정리하세요.
내부 매출 데이터는 제외. 시장 동향, 뉴스, 경쟁 환경 위주.
오늘: {today}
질문: {query}
항목별로 간결하게 정리:"""

        data_query_prompt = f"""사용자의 복합 질문에서 BigQuery 매출/주문 데이터 조회에 필요한 부분만 추출하세요.
외부 분석(날씨, 시장, 원인 등)은 제외하고, 순수 데이터 조회 질문으로 변환하세요.

원래 질문: {query}

예시:
- "날씨가 인도네시아 매출에 영향?" → "인도네시아 최근 매출 데이터 조회"
- "경쟁사 대비 태국 쇼피 매출 분석" → "태국 쇼피 매출 데이터 조회"
- "환율 변동으로 베트남 매출 하락 원인" → "베트남 최근 월별 매출 추이"

데이터 조회 질문만 한 줄로 작성:"""

        # --- Steps 1+2: Google Search + BigQuery in parallel (v6.4) ---
        # v6.5: Use Flash for search (was Pro — 60-80s → 30-40s)
        def _web_search_sync():
            flash = get_flash_client()
            return flash.generate_with_search(search_prompt, temperature=0.2, max_output_tokens=4096)

        def _bq_query_sync():
            # Maintenance: only hard-block on manual maintenance
            from app.core.safety import get_maintenance_manager
            mm = get_maintenance_manager()
            if mm.active and mm.manual:
                return "", "데이터 점검 중으로 매출 데이터 조회가 일시 중단되었습니다."

            flash = get_flash_client()
            data_query = flash.generate(data_query_prompt, temperature=0.0).strip()
            logger.info("multi_data_query_rewritten", original=query[:100], rewritten=data_query[:100])
            from app.agents.sql_agent import sql_agent as _graph
            state = {
                "query": data_query,
                "route_type": "text_to_sql",
                "generated_sql": None, "sql_valid": None, "sql_result": None,
                "retrieved_docs": None, "doc_relevance": None, "web_search_results": None,
                "answer": "", "needs_retry": False, "retry_count": 0, "error": None,
                "messages": None,
                "conversation_context": conversation_context,
                "model_type": model_type,
                "brand_filter": brand_filter,
            }
            result = _graph.invoke(state)
            return data_query, result.get("answer", "")

        web_context = ""
        bq_answer = ""

        try:
            gathered = await asyncio.gather(
                asyncio.to_thread(_web_search_sync),
                asyncio.to_thread(_bq_query_sync),
                return_exceptions=True,
            )

            # Web search result
            if isinstance(gathered[0], Exception):
                logger.warning("multi_web_search_failed", error=str(gathered[0]))
                sub_results["web_search"] = {"error": str(gathered[0])}
            else:
                web_context = gathered[0] or ""
                sub_results["web_search"] = {"answer": web_context}
                logger.info("multi_web_search_done", length=len(web_context))

            # BQ result
            if isinstance(gathered[1], Exception):
                logger.warning("multi_bigquery_failed", error=str(gathered[1]))
                sub_results["bigquery"] = {"error": str(gathered[1])}
            else:
                _, bq_answer = gathered[1]
                if "오류" in bq_answer and "SQL" in bq_answer:
                    logger.warning("multi_bigquery_sql_failed", answer=bq_answer[:100])
                    bq_answer = ""
                    sub_results["bigquery"] = {"error": "데이터 조회 실패"}
                else:
                    sub_results["bigquery"] = {"answer": bq_answer}
                    logger.info("multi_bigquery_done", length=len(bq_answer))
        except Exception as e:
            logger.error("multi_parallel_failed", error=str(e))

        # --- Step 3: Synthesize with Flash for speed (v6.4) ---
        flash = get_flash_client()

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
- [데이터 기반의 실행 가능한 제안 1-3개. 구체적 행동 포함]

---
*분석 기준: SKIN1004 내부 데이터 + Google 검색 ({today})*

> 💡 **이런 것도 물어보세요**
> - [관련 데이터 심화 분석 질문]
> - [다른 시장/국가/기간 비교 질문]
> - [관련 외부 요인 추가 분석 질문]

## 작성 규칙
1. 금액: 1억 이상은 "약 OO.O억원", 1억 미만은 천 단위 쉼표. 퍼센트는 소수점 1자리까지.
2. 내부 데이터 **사실**과 외부 맥락 **분석**을 명확히 구분하세요. (데이터 = 팩트, 외부 = 맥락)
3. 핵심 수치는 **굵게** 표시하세요.
4. 전문적이면서 친근한 톤으로 — 비즈니스 분석 보고서 품질로 작성하세요.
5. 후속 질문은 우리 시스템이 답변 가능한 구체적 질문만 제안하세요.
"""

        try:
            answer = flash.generate(synthesis_prompt, temperature=0.3)
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

    async def _handle_system_task(
        self,
        query: str,
        messages: List[Dict[str, str]],
    ) -> dict:
        """Handle Open WebUI system tasks (title/tag/follow-up) with Flash.

        These are auto-generated requests from Open WebUI, not user queries.
        Using Flash for speed since these are lightweight formatting tasks.
        """
        flash = get_flash_client()
        q_start = query[:200].lower()

        # Follow-up suggestion — custom prompt for quality
        if "follow" in q_start or "suggest" in q_start:
            return await self._handle_followup_task(messages, flash)

        # Title / Tag generation — include conversation context
        try:
            # Build conversation snippet for title/tag context
            conv_parts = []
            for msg in messages:
                content = msg.get("content", "")
                if msg.get("role") == "user" and not content.strip().startswith("### Task:"):
                    conv_parts.append(f"사용자: {content[:200]}")
                elif msg.get("role") == "assistant":
                    conv_parts.append(f"AI: {content[:200]}")
            conv_snippet = "\n".join(conv_parts[-6:])  # Last 3 turns

            prompt_with_context = f"""{query}

### Chat History:
{conv_snippet}"""
            answer = flash.generate(prompt_with_context, temperature=0.3)
            return {"source": "direct", "answer": answer}
        except Exception as e:
            logger.warning("system_task_failed", error=str(e))
            return {"source": "direct", "answer": ""}

    async def _handle_followup_task(
        self,
        messages: List[Dict[str, str]],
        flash,
    ) -> dict:
        """Generate high-quality follow-up suggestions for Open WebUI chips.

        Only suggests questions that our system can clearly answer:
        - BigQuery data queries (specific country/period/product)
        - CS product questions (ingredients, usage, certifications)
        - Notion document queries
        """
        # Extract previous user question and assistant answer
        prev_user = ""
        prev_assistant = ""
        for msg in reversed(messages):
            content = msg.get("content", "")
            if msg.get("role") == "assistant" and not prev_assistant:
                prev_assistant = content[:800]
            elif msg.get("role") == "user" and not prev_user:
                if not content.strip().startswith("### Task:"):
                    prev_user = content
            if prev_user and prev_assistant:
                break

        if not prev_user:
            return {"source": "direct", "answer": '{"follow_ups": []}'}

        prompt = f"""이전 대화를 기반으로, 사용자가 바로 물어볼 수 있는 후속 질문 3개를 JSON으로 생성하세요.

이전 질문: {prev_user}
이전 답변: {prev_assistant[:500]}

## 필수 규칙
1. 우리 시스템이 **명확하게 답변할 수 있는** 질문만 제안
   - ✅ 매출/판매 데이터: "2024년 미국 아마존 월별 매출 보여줘"
   - ✅ 마케팅/광고 데이터: "2025년 TikTok 광고비 총액 알려줘", "Facebook ROAS 분석해줘", "인플루언서 팀별 비용 비교"
   - ✅ 리뷰 데이터: "아마존 최근 리뷰 보여줘", "쇼피 제품별 리뷰 분석"
   - ✅ 제품 성분/사용법/CS: "센텔라 앰플 사용법 알려줘"
   - ✅ 노션 문서/업무 가이드: "틱톡샵 접속 방법 알려줘", "해외 출장 가이드북", "스마트스토어 운영 방법", "광고 입력 업무 절차"
   - ✅ 제품 비교/순위: "태국 쇼피 top5 제품 비교해줘"
   - ❌ 모호한 질문: "~일까요?", "~궁금해요", "~있을까?"
   - ❌ 예측/의견: "~할 것 같아?", "~전망은?"
2. 구체적 조건 포함 (국가명, 기간, 제품명, 플랫폼, 광고매체 등)
3. "~알려줘", "~보여줘", "~비교해줘" 형태의 직접적인 요청문
4. 이전 답변의 데이터를 확장하는 방향 (다른 기간, 다른 국가, 다른 광고매체, 상세 분석)
5. 매출 질문 후속 → 마케팅/광고 데이터도 제안, 마케팅 질문 후속 → 매출 연계 제안

JSON만 반환:
{{"follow_ups": ["질문1", "질문2", "질문3"]}}"""

        try:
            answer = flash.generate(prompt, temperature=0.3)
            return {"source": "direct", "answer": answer}
        except Exception as e:
            logger.warning("followup_generation_failed", error=str(e))
            return {"source": "direct", "answer": '{"follow_ups": []}'}

    def _build_direct_system_prompt(self, today: str, model_type: str = MODEL_CLAUDE) -> str:
        """Build system prompt for direct LLM route (shared by _handle_direct and route_and_stream)."""
        model_name = "Claude Sonnet 4 (Anthropic) — 빠른 대화. SQL 생성/차트에는 Gemini Flash 사용"
        # Import the full system prompt from _handle_direct inline (it's too long to duplicate)
        # We reference the same structure
        return f"""당신은 SKIN1004의 AI 어시스턴트입니다. ({model_name} 기반)
이 시스템은 **임재필(Jeffrey Im)**이 기획·개발하여 운영하고 있습니다.
오늘 날짜는 {today}입니다.

{LANGUAGE_DETECTION_RULE}

## 회사 소개
(주)크레이버코퍼레이션(Craver Corporation) — "WHAT DO YOU CRAVE?"
공동대표: 전항일/천주혁. 설립 2014년 8월. 서울 강남구 테헤란로 129.
브랜드: SKIN1004(메인), CommonLabs, Zombie Beauty. 마다가스카르 센텔라 기반 클린 뷰티(Cruelty-Free & Vegan).
글로벌 K-뷰티 리더. Shopee/YesStyle/StyleKorean 카테고리 1위. 리테일: Costco, ULTA, H&M, 올리브영.
진출: 한국, 북미, 유럽, 동남아, 일본, 중국, 중남미, 중동.
"우리 회사" = Craver Corporation / SKIN1004. 회사 질문은 이 정보로 답변(웹검색 불필요).

## 시스템 기능
- BigQuery SQL 실행 (매출/수량/순위)
- Notion 사내 문서 검색
- Google Workspace 연동 (Gmail/Calendar/Drive)
- CS 제품 Q&A
- Google 실시간 웹검색
- 이미지 분석, 차트 생성

## 핵심 원칙
- 전문적이면서 친근한 톤. 바로 답변 시작.
- 질문한 내용만 답변. 모르면 솔직하게.
- 복잡한 주제는 헤더/표/bullet으로 구조화.
- 핵심 수치는 **굵게**. 인사이트는 > 인용으로.
- 후속 질문 제안 (💡 이런 것도 물어보세요).
- 지식/설명형 답변 끝에 *AI 생성 답변 · {today}*
- ⚠️ 이전 대화 맥락과 무관한 일반 질문(잡담, 취미, 상식 등)이 오면 이전 맥락을 무시하고 해당 질문에만 집중하여 자연스럽게 답변하세요. 매번 같은 질문에는 일관된 톤과 분량으로 답변하세요.
- ⛔ 절대로 내부 사고 과정(thinking)을 사용자에게 노출하지 마세요. "The user is asking...", "I should...", "Let me check..." 같은 영어 사고 과정을 출력하면 안 됩니다. 바로 답변만 출력하세요."""

    # Keywords that indicate the query needs real-time web search
    _SEARCH_KEYWORDS = [
        "날씨", "뉴스", "오늘", "현재", "실시간", "최신", "지금",
        "환율", "주가", "코스피", "나스닥", "다우",
        "검색", "찾아봐", "알아봐",
        "경쟁사", "시장", "트렌드", "업계",
        "정책", "법률", "규정",
        "이벤트", "행사",
        "대통령", "총리", "선거", "국회", "정부",
        "올해", "이번 달", "이번달", "최근",
        # 엔터테인먼트/외부 정보
        "넷플릭스", "netflix", "영화", "드라마", "인기작", "순위",
        "유튜브", "youtube", "스포츠", "축구", "야구",
        "주식", "비트코인", "코인", "부동산",
        "맛집", "여행", "관광",
    ]

    def _needs_web_search(self, query: str) -> bool:
        """Check if query needs real-time web search or can be answered directly."""
        q = query.lower().strip()
        # Skip search for company/product questions (answered from system prompt)
        _NO_SEARCH = ["회사", "소개", "뭐하는", "크레이버", "skin1004", "센텔라", "재밌", "원피스"]
        if any(kw in q for kw in _NO_SEARCH):
            return False
        # Check search keywords FIRST — even short queries like "현재 대통령" need search
        if any(kw in q for kw in self._SEARCH_KEYWORDS):
            return True
        # Very short queries (greetings, single words) → no search
        if len(q) <= 10:
            return False
        # Year/date reference in query → likely needs current info
        import re
        if re.search(r'202[4-9]년|202[4-9]\s', q):
            return True
        # Questions about external topics
        if len(q) > 30 and "?" in query:
            return True
        return False

    async def _handle_direct(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
        images: Optional[List[dict]] = None,
        stream_callback=None,
    ) -> dict:
        """General question: uses full conversation history for natural dialogue.

        Uses Google Search grounding only when the query needs real-time info.
        Simple questions (greetings, SKIN1004 Q&A) skip search for faster response.
        When images are provided, uses vision LLM directly.
        """
        # Handle Open WebUI system tasks (follow-up/title/tag generation)
        if query.strip().startswith("### Task:"):
            return await self._handle_system_task(query, messages)

        images = images or []
        llm = get_llm_client(model_type)
        today = datetime.now().strftime("%Y년 %m월 %d일 (%A)")

        model_name = "Claude Sonnet 4 (Anthropic) — 빠른 대화. SQL 생성/차트에는 Gemini Flash 사용"

        system = f"""당신은 SKIN1004의 AI 어시스턴트입니다. ({model_name} 기반)
이 시스템은 **임재필(Jeffrey Im)**이 기획·개발하여 운영하고 있습니다.
오늘 날짜는 {today}입니다.

{LANGUAGE_DETECTION_RULE}

## 회사 소개 (공식 정보 — 웹검색 불필요, 이 정보만으로 답변)
- **기업명**: (주)크레이버코퍼레이션 (Craver Corporation)
- **슬로건**: "WHAT DO YOU CRAVE?"
- **대표자**: 전항일 / 천주혁 (공동대표)
- **설립일**: 2014년 8월
- **소재지**: 서울 강남구 테헤란로 129, 11층·12층
- **업종**: 패션·명품·뷰티 > 뷰티 > 화장품
- **기업유형**: 스타트업
- **브랜드**: SKIN1004(메인), CommonLabs(커먼랩스), Zombie Beauty(좀비뷰티)
- **브랜드 철학**: "Clean Beauty from Madagascar Centella Asiatica" — 마다가스카르 센텔라 아시아티카 기반 클린 뷰티, Cruelty-Free & Vegan
- **주요 제품**: 센텔라 앰플, 크림, 토너, 선크림, 클렌징 오일 등
- **글로벌 포지션**: K-뷰티 가장 빠르게 성장하는 기업. Shopee, YesStyle, StyleKorean, Stylevana 등 주요 글로벌 플랫폼에서 카테고리 1위
- **글로벌 리테일**: Costco(코스트코), ULTA(얼타), H&M, 올리브영 등
- **진출 시장**: 한국, 북미, 유럽, 동남아, 일본, 중국, 중남미, 중동
- **주요 온라인 채널**: 올리브영, 아마존, 쇼피, 라자다, 틱톡샵, 큐텐, 자사몰(skin1004.com)
- **성장 전략**: 카테고리 확장·신제품 고도화, 글로벌 리테일 채널 확장, 국가별 현지화 전략 강화, 인재 투자
"우리 회사" = Craver Corporation / SKIN1004. 회사 소개 질문에는 위 정보만으로 답변하세요 (웹검색 절대 불필요).

## 시스템 기능 (사용자에게 정확히 안내할 것)
- **Google 실시간 웹검색** 연동: 날씨, 뉴스, 환율, 인물, 시사 등 최신 정보를 검색하여 제공합니다.
- **BigQuery SQL 실행**: 매출, 수량, 순위, 국가별/제품별 데이터를 직접 조회합니다.
- **Notion 사내 문서 검색**: 사내 정책, 매뉴얼, 프로세스 문서를 검색합니다.
- **Google Workspace 연동**: Gmail 메일, Google Calendar 일정, Google Drive 파일을 조회합니다.
- **CS 제품 Q&A**: 제품 성분, 사용법, 비건인증 등 고객상담 데이터베이스를 검색합니다.
- **이미지 분석**: 업로드된 이미지를 분석하여 설명하거나 질문에 답변합니다.
- **차트 생성**: 매출/데이터 질문 시 자동으로 차트(막대, 라인, 파이 등)를 생성합니다.
- "뭐 할 수 있어?", "기능이 뭐야?" 등의 질문에는 위 기능들을 안내하세요.

## 핵심 원칙
- 사용자의 질문에 **전문적이면서도 친근한 톤**으로 답변하세요. 비즈니스 전문가가 동료에게 설명하듯 자연스럽게.
- 질문한 내용만 답변하세요. 질문과 무관한 부가 정보나 홍보성 안내를 덧붙이지 마세요.
- 실시간 정보가 제공된 경우, 최신 정보를 있는 그대로 전달하세요.
- 모르는 것은 모른다고 솔직하게 답변하세요. 추측하거나 지어내지 마세요.
- 자기소개를 길게 하지 마세요. 바로 답변 내용으로 시작하세요.
- "누가 만들었어?", "주인이 누구야?" 등의 질문에는 임재필(Jeffrey Im)이 만들고 운영한다고 답변하세요.
- ⚠️ 이전 대화가 업무 관련이어도, 일반 질문(잡담, 취미, 상식)이 오면 이전 맥락을 무시하고 해당 질문에만 자연스럽게 답변하세요. 같은 질문에는 항상 일관된 톤과 분량으로 답변하세요.
- ⛔ 절대로 내부 사고 과정을 사용자에게 노출하지 마세요. "The user is asking...", "I should...", "Let me check..." 같은 텍스트를 출력하면 안 됩니다.

## 답변 형식 표준
- **구조화된 답변**: 복잡한 주제는 반드시 섹션(헤더)으로 나누어 정리하세요.
  - 개념 설명: 정의 → 핵심 포인트 → 활용 예시
  - 비교: 마크다운 표 활용
  - 절차/방법: 번호 목록 (1. → 2. → 3.) — 각 단계에 구체적 설명
  - 목록: bullet 포인트로 깔끔하게
- 핵심 용어, 수치, 결론은 **굵게** 표시하세요.
- 주목할 인사이트나 핵심 결론은 `> ` 인용 형식으로 강조하세요.
- 3개 이상 비교 항목은 반드시 **마크다운 표**를 사용하세요.
- 간단한 인사에는 인사 + "매출 조회, 사내 문서 검색, CS 제품 문의, 일정·메일 확인, 이미지 분석 등을 도와드릴 수 있습니다." 한 줄을 포함하세요.
- 의미 없는 입력(특수문자, 숫자, 자음/모음, 이모지만)에는 "입력하신 내용을 이해하기 어렵습니다. 매출 조회, 사내 문서 검색, 일정 확인 등 궁금한 점을 문장으로 질문해 주세요." 라고 안내하세요.
- 실시간 검색 정보를 포함할 때는 출처를 간략히 명시하세요.
- **후속 질문 제안**: 실질적인 답변 뒤에 관련 후속 질문 2-3개를 아래 형식으로 제안하세요 (단순 인사/잡담에는 생략):
  > 💡 **이런 것도 물어보세요**
  > - [후속 질문 1]
  > - [후속 질문 2]
  > - [후속 질문 3]
- **출처 표시**: 지식/설명형 답변(5줄 이상)에는 답변 끝에 `---` 구분선 후 *AI 생성 답변 · {today}* 형태로 날짜를 표기하세요. 인사/짧은 답변에는 생략."""

        try:
            # Vision mode: images present → use generate_with_images
            if images:
                vision_text = query or "이 이미지에 대해 설명해주세요."
                answer = llm.generate_with_images(
                    vision_text,
                    images,
                    system_instruction=system,
                    temperature=0.5,
                )
                return {"source": "direct", "answer": answer}

            # Search grounding: use Tavily/Google search if needed
            _needs_search = self._needs_web_search(query)
            final_system = system
            if _needs_search:
                search_context = self._gather_search_context(query)
                if search_context:
                    final_system = system + f"\n\n## 참고할 최신 검색 정보 (Google 검색 결과)\n{search_context}"

            # Claude streaming for all direct queries (TTFB 1.7s vs Gemini 7s)
            import asyncio as _aio

            if stream_callback:
                # Real-time streaming via thread + async queue
                if messages and len(messages) > 1:
                    # Multi-turn: use history stream
                    _q: _aio.Queue = _aio.Queue()
                    _loop = _aio.get_running_loop()

                    def _stream_worker():
                        for chunk in llm.generate_with_history_stream(
                            messages=messages, system_instruction=final_system, temperature=0.5,
                        ):
                            _loop.call_soon_threadsafe(_q.put_nowait, chunk)
                        _loop.call_soon_threadsafe(_q.put_nowait, None)

                    _loop.run_in_executor(None, _stream_worker)
                    answer = ""
                    while True:
                        chunk = await _q.get()
                        if chunk is None:
                            break
                        answer += chunk
                        await stream_callback(chunk)
                else:
                    # Single-turn stream
                    _q: _aio.Queue = _aio.Queue()
                    _loop = _aio.get_running_loop()

                    def _stream_worker():
                        for chunk in llm.generate_stream(
                            query, system_instruction=final_system, temperature=0.5,
                        ):
                            _loop.call_soon_threadsafe(_q.put_nowait, chunk)
                        _loop.call_soon_threadsafe(_q.put_nowait, None)

                    _loop.run_in_executor(None, _stream_worker)
                    answer = ""
                    while True:
                        chunk = await _q.get()
                        if chunk is None:
                            break
                        answer += chunk
                        await stream_callback(chunk)
            else:
                # Non-streaming fallback
                if messages and len(messages) > 1:
                    answer = llm.generate_with_history(
                        messages=messages, system_instruction=final_system, temperature=0.5,
                    )
                else:
                    answer = llm.generate(
                        query, system_instruction=final_system, temperature=0.5,
                    )

            return {"source": "direct", "answer": answer}
        except Exception as e:
            logger.error("direct_llm_failed", error=str(e))
            return {"source": "direct", "answer": f"죄송합니다. 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n> 기술 참고: {str(e)[:100]}"}

    async def _verify_coherence(self, query: str, answer: str, route: str) -> str:
        """Verify the answer actually addresses the user's question.

        Uses Flash for a lightweight check. Only flags CRITICAL mismatches:
        - Asked about product A, answered about product B
        - Asked about country X, answered about country Y
        - Asked about 2026 full year, answered only 1 month WITHOUT acknowledging it

        Does NOT flag (these are normal):
        - Partial data (agent already explains limitations)
        - CS DB not having specific info (expected behavior)
        - SKIN1004-specific answers (this IS a SKIN1004 system)
        - Answer already contains its own caveats/warnings

        Skips: direct route, multi route, short answers, answers with existing warnings.
        """
        if len(answer) < 30:
            return answer

        # Skip if answer already acknowledges limitations
        limitation_phrases = [
            "데이터가 없", "조회되지 않", "찾을 수 없", "찾지 못했",
            "정보가 없", "제공하지 못", "확인되지 않",
            "부분적", "일부만", "까지의 데이터",
            "⚠️", "⚠",
        ]
        answer_start = answer[:500]
        if any(phrase in answer_start for phrase in limitation_phrases):
            return answer

        # Skip for CS route — CS DB has inherent limitations, agent handles "not found" gracefully
        if route == "cs":
            return answer

        try:
            flash = get_flash_client()
            today = datetime.now().strftime("%Y년 %m월 %d일")
            check_prompt = f"""이것은 SKIN1004 화장품 회사의 내부 AI 시스템입니다.
모든 답변은 SKIN1004 자체 데이터(매출, 제품, 문서)에 기반합니다.
오늘: {today}

사용자 질문: {query}
AI 답변 (앞부분): {answer[:600]}

## 판단 기준
다음 경우에만 scope_match=false로 판단하세요:
1. 질문한 제품과 완전히 다른 제품을 답변함 (예: 센텔라를 물었는데 히알루 답변)
2. 질문한 국가/채널과 완전히 다른 국가/채널을 답변함 (예: 미국을 물었는데 일본 답변)

## 이것은 정상이므로 scope_match=true로 판단하세요:
- 데이터가 부분적이어서 일부 기간/항목만 답변한 경우 (정상 — 있는 데이터만 답변)
- "정보가 없습니다", "찾을 수 없습니다" 등 솔직한 답변 (정상 — 올바른 대응)
- SKIN1004 자사 제품/매출로 답변한 경우 (정상 — 이 시스템의 목적)
- 답변이 질문 주제를 다루지만 완전하지 않은 경우 (정상 — 데이터 한계)

JSON만 반환:
{{"scope_match": true/false, "issue": "불일치 설명 또는 빈문자열"}}"""

            result = flash.generate(check_prompt, temperature=0.0)
            import json as _json
            clean = result.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = _json.loads(clean)

            if not parsed.get("scope_match", True) and parsed.get("issue"):
                issue = parsed["issue"]
                logger.warning("coherence_issue_detected", query=query[:80], issue=issue)
                warning = f"> ⚠️ **참고**: {issue} (오늘 기준: {today})\n\n"
                return warning + answer

        except Exception as e:
            logger.debug("coherence_check_skipped", error=str(e))

        return answer

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
            return "(실시간 검색에 실패했습니다. 최신 정보가 반영되지 않을 수 있습니다.)"
