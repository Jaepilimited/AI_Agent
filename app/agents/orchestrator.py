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
import re
from datetime import datetime
from typing import Dict, List, Optional

import structlog

from app.core.llm import MODEL_CLAUDE, MODEL_GEMINI, get_flash_client, get_llm_client
from app.core.prompt_fragments import FOLLOWUP_INSTRUCTION, LANGUAGE_DETECTION_RULE
from app.core.response_formatter import ensure_formatting
from app.core.security import FI_ACCESS_DENIED_MESSAGE

# Existing agent
from app.agents.sql_agent import run_sql_agent

# v3.0 new agents
from app.agents.notion_agent import NotionAgent
from app.agents.gws_agent import GWSAgent

logger = structlog.get_logger(__name__)


def _model_display_name() -> str:
    """답변에 노출되는 모델 이름 — **설정에서 만든다.**

    손으로 적어 두면 모델을 올려도 프롬프트에 옛 이름이 남는다. 실제로
    `claude-opus-5` 로 올린 직후에도 "Claude Opus 4.8 기반"이라고 답했다
    (2026-08-13). 값은 기동 시 고정이라 프롬프트 캐시도 그대로 유지된다.
    """
    from app.config import get_settings
    parts = (get_settings().anthropic_opus_model or "").replace("claude-", "").split("-")
    tier = parts[0].capitalize() if parts and parts[0] else "Opus"
    ver = ".".join(parts[1:])
    return (f"Claude {tier} {ver}".strip() +
            " (Anthropic) — 빠른 대화. SQL 생성/차트에는 Gemini Flash 사용")



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


_re_chart_block = re.compile(r"```chart-config[\s\S]*?```", re.MULTILINE)
_re_details_block = re.compile(r"<details[\s\S]*?</details>", re.MULTILINE)
_re_follow_block = re.compile(r"> 💡 \*\*이런 것도 물어보세요\*\*[\s\S]*$", re.MULTILINE)
_re_bq_table_ref = re.compile(r"skin1004-319714\.[A-Za-z_]\w*\.[A-Za-z_]\w*")
_re_sql_in_details = re.compile(
    r"<details[^>]*>[\s\S]*?```sql\s*([\s\S]*?)```[\s\S]*?</details>")


def _strip_assistant_noise(content: str) -> str:
    """Remove chart JSON, SQL blocks, and follow-up sections from assistant response.

    These blocks are machine-readable or boilerplate and waste context space.
    Keeps markdown text (summaries, tables, insights) for SQL generation context.

    SQL 블록을 지우더라도 거기 쓰인 테이블명은 태그로 보존한다 — 후속 질문
    ("지난달이랑 비교해줘")의 주제 유지에 필요한 유일한 앵커다. 본문 텍스트에
    주제 단어가 없으면(예: 판매수량 답변에 'Product' 단어 부재) 스키마 lazy-load
    매칭도, LLM의 테이블 선택도 매출로 흘러간다 (2026-08-10 6종 시나리오 테스트).
    태그는 맨 앞에 붙인다 — 1500자 절단에서 살아남아야 하므로.
    """
    tables = list(dict.fromkeys(_re_bq_table_ref.findall(content)))
    content = _re_chart_block.sub("[차트 생략]", content)
    content = _re_details_block.sub("[SQL 생략]", content)
    content = _re_follow_block.sub("", content)
    content = content.strip()
    if tables:
        content = "[실행된 쿼리 테이블: " + ", ".join(tables) + "] " + content
    return content


_DIRECT_HISTORY_CAP = 30  # 최근 15턴 — 참조형 질문("아까 그거") 안전 마진


def _clean_messages_for_history(messages: List[Dict]) -> List[Dict]:
    """Strip chart/SQL noise from assistant messages before sending to LLM history.

    Caps to the most recent _DIRECT_HISTORY_CAP messages to bound per-turn
    token cost on long sessions; full text (no 1500-char truncation) is kept
    for messages within the cap so Claude can still track conversation
    accurately within that window.
    """
    capped = messages[-_DIRECT_HISTORY_CAP:] if len(messages) > _DIRECT_HISTORY_CAP else messages
    cleaned = []
    for msg in capped:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("assistant", "model") and isinstance(content, str):
            content = _strip_assistant_noise(content)
        cleaned.append({**msg, "content": content})
    return cleaned


def _build_conversation_context(messages: List[Dict[str, str]]) -> str:
    """Build conversation context — keep last 10 turns (20 messages).

    맥락 소실 최소화 원칙 (2026-08-10 아키텍처 변경):
    - 마지막 AI 답변은 3000자, 그 이전 답변은 800자 — 후속 질문이 참조하는 건
      거의 항상 직전 답변이므로 토큰 예산을 최신에 몰아준다.
    - 마지막으로 실행된 SQL은 원문 그대로 맨 끝에 부착한다. 테이블·지표·필터·
      기간이 전부 담긴 무손실 앵커라, 요약 텍스트가 어떻게 절단되든 후속 질문
      ("지난달이랑 비교해줘", "국가별로 나눠줘")의 기준이 살아남는다.
      지시문("주제를 유지하라")만으로는 LLM이 매출로 회귀하는 것을 실측으로
      확인했다 — 정보를 직접 주는 것이 유일하게 안정적이다.
    """
    if not messages or len(messages) <= 1:
        return ""

    history = messages[:-1]
    lines = []

    if len(history) > 20:
        lines.append(f"[이전 대화 {len(history) - 20}개 메시지 생략 — 최근 10턴만 표시]")
        history = history[-20:]

    last_ai_idx = max(
        (i for i, m in enumerate(history) if m.get("role") in ("assistant", "model")),
        default=-1,
    )
    last_sql = ""
    for i, msg in enumerate(history):
        role = msg.get("role", "user")
        raw = _content_to_text(msg.get("content", ""))
        if not raw:
            continue
        if role == "user":
            if len(raw) > 300:
                raw = raw[:300] + "..."
            lines.append(f"사용자: {raw}")
        elif role in ("assistant", "model"):
            sqls = _re_sql_in_details.findall(raw)
            if sqls:
                last_sql = sqls[-1].strip()
            content = _strip_assistant_noise(raw)
            cap = 3000 if i == last_ai_idx else 800
            if len(content) > cap:
                content = content[:cap] + "..."
            lines.append(f"AI: {content}")

    if last_sql:
        lines.append(
            "[직전 실행 SQL — 후속 질문은 이 테이블·지표·필터를 기준으로 해석]\n"
            + last_sql[:600]
        )
    return "\n".join(lines)


# Direct-lock keywords — queries containing these skip LLM reclassification
_DIRECT_LOCK_KW = frozenset([
    "회사", "뭐하는", "소개", "누가 만들", "주인", "재밌", "안녕", "하이",
    "hello", "hi", "부동산", "주식", "투자", "아파트", "전세", "월세",
    "대출", "연봉", "이직", "항공", "비행기", "호텔", "숙소", "맛집",
])

_FI_QUERY_KEYWORDS = (
    "영업이익", "매출총이익", "매출원가", "판관비", "손익", "이익률", "원가율", "광고선전비",
)


def _requests_fi_data(query: str, enabled_sources=None, db_entry=None) -> bool:
    if any(keyword in (query or "") for keyword in _FI_QUERY_KEYWORDS):
        return True
    if "손익" in (enabled_sources or []):
        return True
    entries = db_entry if isinstance(db_entry, list) else [db_entry]
    return any(isinstance(entry, dict) and entry.get("key") == "손익" for entry in entries)


# ── 성분 미포함 질문 차단 ──────────────────────────────────────────────────────
#
# 자사 제품의 **전성분 데이터가 시스템에 없다** (2026-08-05 확인: BigQuery 전 데이터셋에
# 자사 제품 성분 테이블 없음. Product 테이블에도 성분 컬럼 없음. CS Q&A 시트에 텍스트로만 존재).
#
# 그래서 "X 안 들어간 제품" 류 질문에 SQL 이 제품명 문자열 매칭(`LIKE '%RETINOL%'`)으로
# 답해 왔고, 제품명에 성분이 안 적힌 제품이 "미포함"으로 분류돼 **나이아신아마이드가 든
# 제품이 '나이아신아마이드 미포함 1위'로 나오는 오답**이 났다 (노션 AI Tester 미해결 건).
#
# 포함/미포함은 대칭이 아니다. "들어간 제품"은 라인·제품명으로 근사라도 되지만,
# **"안 들어간 제품"은 전성분을 모르면 원리적으로 판정할 수 없다.** 부재는 증명해야 하는데
# 증명할 데이터가 없다. 면책 문구를 붙인 순위표는 여전히 틀린 순위표다 — 그래서 막는다.

_INGREDIENT_TERMS = (
    "나이아신아마이드", "레티놀", "히알루론산", "판테놀", "세라마이드", "살리실산",
    "아데노신", "알부틴", "비타민c", "비타민 c", "아스코르빅", "마데카소사이드",
    "알란토인", "스쿠알란", "콜라겐", "펩타이드", "글리세린", "우레아",
    "파라벤", "향료", "인공향료", "알코올", "에탄올", "미네랄오일", "실리콘",
    "에센셜오일", "aha", "bha", "pha", "성분",
)

_EXCLUSION_TERMS = (
    "안 들어간", "안들어간", "안 들어있는", "안들어있는", "들어가지 않은", "들어있지 않은",
    "없는", "미포함", "불포함", "제외한", "제외하고", "빼고", "뺀", "무첨가", "프리", "free",
)

INGREDIENT_EXCLUSION_MESSAGE = (
    "**성분 미포함 기준으로는 순위를 낼 수 없습니다.**\n\n"
    "제품별 **전성분 데이터를 시스템이 보유하고 있지 않습니다.** "
    "지금 조회 가능한 것은 매출·수량과 제품명·라인뿐이라, "
    "특정 성분이 **들어있지 않다**는 것은 확인할 방법이 없습니다.\n"
    "(제품명에 성분이 안 적혀 있다고 해서 실제로 안 들어간 것은 아닙니다. "
    "이 방식으로 답하면 해당 성분이 든 제품이 '미포함 1위'로 올라오는 오답이 납니다.)\n\n"
    "**대신 이렇게 하실 수 있습니다**\n"
    "- **제품 라인 기준**으로 물어보시면 정확합니다 — 센텔라, 히알루시카, 톤브라이트닝, "
    "포어마이징, 프로바이오시카, 티트리카, 랩인네이처 등\n"
    "  예) \"센텔라 라인 제외한 제품 판매량 순위\"\n"
    "- **개별 제품의 전성분**은 `@@BP` (제품 Q&A)를 선택해 물어보시면 확인됩니다\n"
    "  예) \"@@BP 센텔라 앰플 전성분 알려줘\""
)


_INCLUSION_TERMS = ("들어간", "들어있는", "포함", "함유", "든 제품", "있는 제품")

# 감지에 쓰는 성분어 중 "성분" 은 일반어라 성분명 추출 대상에서 뺀다.
_GENERIC_TERMS = {"성분", "전성분"}


def _extract_ingredient(query: str) -> Optional[str]:
    """질문에서 성분명을 뽑는다. 일반어('성분')만 있으면 None."""
    q = (query or "").lower()
    hits = [t for t in _INGREDIENT_TERMS if t in q and t not in _GENERIC_TERMS]
    if not hits:
        return None
    return max(hits, key=len)  # "비타민 c" 처럼 긴 쪽 우선


def _ingredient_filter_intent(query: str) -> Optional[tuple[str, bool]]:
    """성분 기준 제품 필터링 질문인가.

    Returns:
        (성분명, contains) 또는 None. contains=False 면 '미포함' 질문.
    """
    q = (query or "").lower()
    if not any(t in q for t in _INGREDIENT_TERMS):
        return None
    excl = any(t in q for t in _EXCLUSION_TERMS)
    incl = any(t in q for t in _INCLUSION_TERMS)
    if not (excl or incl):
        return None
    ing = _extract_ingredient(query)
    if not ing:
        return None
    return (ing, not excl)


def _requests_ingredient_exclusion(query: str) -> bool:
    """성분 '미포함' 기준 필터링을 요구하는 질문인가 (하위 호환)."""
    intent = _ingredient_filter_intent(query)
    return bool(intent and not intent[1])



def _scope_sources(enabled_sources, db_entry):
    """@@ 로 고른 소스를 테이블 화이트리스트의 근거로 쓴다.

    ⚠️ 프론트도 @@ 를 파싱해 enabled_sources 를 보내지만, 서버가 그걸 **믿기만 하면**
    프론트를 거치지 않는 클라이언트(API·골든 하네스)는 스코프 없이 전 테이블을 조회한다.
    실제로 `@@메타광고` 질문이 화이트리스트 밖 integrated_ad 를 읽고 있었다 (2026-08-11).
    FI 권한과 같은 원칙 — 판정 근거는 서버가 직접 가진다.
    클라이언트가 값을 보냈으면 그대로 존중하고(칩 선택), 없을 때만 파싱 결과로 채운다.
    """
    if enabled_sources is not None:
        return enabled_sources
    if not db_entry:
        return None
    return [e["key"] for e in db_entry]


class OrchestratorAgent:
    """Orchestrator-Worker pattern conductor.

    Analyzes query intent and delegates to appropriate Sub Agent.
    Supports both Gemini 2.5 Pro and Claude Sonnet 4.5 based on user selection.
    """

    def __init__(self):
        logger.info("orchestrator_initialized")

        # Build _SOURCE_ROUTE_MAP from _DB_REGISTRY (key + aliases → route)
        if not self._SOURCE_ROUTE_MAP:
            for entry in self._DB_REGISTRY:
                self._SOURCE_ROUTE_MAP[entry["key"]] = entry["route"]
                for alias in entry.get("aliases", []):
                    self._SOURCE_ROUTE_MAP[alias] = entry["route"]

        # v3.0 new agents (lazy init)
        self._notion_agent = None
        self._gws_agent = None

        # Strong refs for fire-and-forget background tasks (prevents GC mid-flight)
        self._bg_tasks: set = set()

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

    # Source name → route mapping (matches frontend DATA_SOURCE_KEYS, clean names)
    # Auto-built from _DB_REGISTRY: key → route
    _SOURCE_ROUTE_MAP = {}  # populated in __init__

    # ═══ @@ 데이터소스 선택 시스템 ═══
    # 사용자가 "@@매출 이번달 합계" 형태로 데이터소스를 직접 지정
    # route: 라우팅 대상, label: 사용자에게 표시되는 이름, desc: 설명
    _DB_REGISTRY = [
        # ── BigQuery 매출 ──
        # ── 보고서 (산출물 — 테이블이 아니다) ──
        {"key": "보고서", "aliases": ["리포트", "report", "reports"], "route": "report", "group": "보고서", "icon": "doc", "label": "보고서", "desc": "질문에 맞춰 절을 조합한 분석 보고서 (본인 + 지목해 공유한 사람만 열람)"},
        {"key": "매출", "aliases": ["sales", "매출데이터", "세일즈"], "route": "bigquery", "group": "매출 데이터", "icon": "chart", "label": "매출", "desc": "통합 매출 — 글로벌 전 플랫폼"},
        {"key": "제품", "aliases": ["product", "제품데이터"], "route": "bigquery", "group": "매출 데이터", "icon": "box", "label": "제품", "desc": "제품별 판매 수량"},
        {"key": "손익", "aliases": ["pl", "손익계산서", "영업이익", "판관비", "재무손익"], "route": "bigquery", "group": "매출 데이터", "icon": "chart", "label": "손익", "desc": "재무 손익 — 영업이익/원가/판관비 (월 단위)"},
        # ── BigQuery 마케팅 ──
        {"key": "광고", "aliases": ["ad", "ads", "광고데이터", "광고비"], "route": "bigquery", "group": "마케팅 데이터", "icon": "megaphone", "label": "광고", "desc": "통합 광고 데이터"},
        {"key": "마케팅", "aliases": ["marketing", "마케팅비용"], "route": "bigquery", "group": "마케팅 데이터", "icon": "dollar", "label": "마케팅", "desc": "통합 마케팅 비용"},
        {"key": "인플루언서", "aliases": ["influencer", "인플"], "route": "bigquery", "group": "마케팅 데이터", "icon": "users", "label": "인플루언서", "desc": "인플루언서 마케팅"},
        {"key": "Shopify", "aliases": ["shopify", "쇼피파이"], "route": "bigquery", "group": "마케팅 데이터", "icon": "cart", "label": "Shopify", "desc": "글로벌 자사몰 판매"},
        {"key": "플랫폼", "aliases": ["platform", "플랫폼데이터"], "route": "bigquery", "group": "마케팅 데이터", "icon": "store", "label": "플랫폼", "desc": "플랫폼 순위/가격"},
        {"key": "아마존검색", "aliases": ["amazon", "아마존"], "route": "bigquery", "group": "마케팅 데이터", "icon": "search", "label": "아마존검색", "desc": "아마존 검색 분석"},
        {"key": "프로모션", "aliases": ["promotion", "행사", "기획전", "프로모"], "route": "bigquery", "group": "마케팅 데이터", "icon": "calendar", "label": "프로모션", "desc": "프로모션 캘린더 (실행 일정)"},
        {"key": "메타광고", "aliases": ["meta", "메타"], "route": "bigquery", "group": "마케팅 데이터", "icon": "phone", "label": "메타광고", "desc": "메타 광고 라이브러리"},
        {"key": "아마존 리뷰", "aliases": ["amazon review"], "route": "bigquery", "group": "마케팅 데이터", "icon": "star", "label": "아마존 리뷰", "desc": "아마존 리뷰"},
        {"key": "큐텐 리뷰", "aliases": ["qoo10 review", "쿠텐 리뷰"], "route": "bigquery", "group": "마케팅 데이터", "icon": "star", "label": "큐텐 리뷰", "desc": "큐텐 리뷰"},
        {"key": "쇼피 리뷰", "aliases": ["shopee review"], "route": "bigquery", "group": "마케팅 데이터", "icon": "star", "label": "쇼피 리뷰", "desc": "쇼피 리뷰"},
        {"key": "스마트스토어 리뷰", "aliases": ["smartstore review", "네이버 리뷰"], "route": "bigquery", "group": "마케팅 데이터", "icon": "star", "label": "스마트스토어 리뷰", "desc": "스마트스토어 리뷰"},
        {"key": "초상권", "aliases": ["모델", "모델사진", "모델초상권", "rights", "bc"], "route": "model_rights", "group": "BC", "icon": "users", "label": "초상권", "desc": "모델 사진 사용 가능 여부·기한 ([BC] 모델 초상권 현황)"},
        # ── Notion (팀별자료 — 벡터 검색, 알파벳순) ──
        {"key": "B2B1", "aliases": ["b2b1", "국내영업", "b2b국내"], "route": "notion", "group": "Notion", "icon": "doc", "label": "B2B1", "desc": "해외영업 (매출/거래처/재고)"},
        {"key": "B2B2", "aliases": ["b2b2", "b2b", "해외영업"], "route": "notion", "group": "Notion", "icon": "doc", "label": "B2B2", "desc": "B2B 프로세스/온보딩"},
        {"key": "BCM", "aliases": ["bcm", "브랜드커뮤니케이션"], "route": "notion", "group": "Notion", "icon": "doc", "label": "BCM", "desc": "브랜드커뮤니케이션팀"},
        {"key": "Craver", "aliases": ["craver", "크레이버", "경영기획"], "route": "notion", "group": "Notion", "icon": "doc", "label": "Craver", "desc": "경영기획"},
        {"key": "CS", "aliases": ["cs", "cs문서", "cs자료", "notion_cs"], "route": "notion", "group": "Notion", "icon": "doc", "label": "CS", "desc": "CS팀 Notion 문서"},
        {"key": "DB", "aliases": ["db", "데이터분석", "데이터팀"], "route": "notion", "group": "Notion", "icon": "doc", "label": "DB", "desc": "데이터분석팀"},
        {"key": "GM EAST", "aliases": ["gm_east", "east", "동부", "gm동부"], "route": "notion", "group": "Notion", "icon": "globe", "label": "GM EAST", "desc": "글로벌마케팅 동부"},
        {"key": "GM WEST", "aliases": ["gm_west", "west", "서부", "gm서부"], "route": "notion", "group": "Notion", "icon": "globe", "label": "GM WEST", "desc": "글로벌마케팅 서부"},
        {"key": "JBT", "aliases": ["jbt", "일본사업"], "route": "notion", "group": "Notion", "icon": "doc", "label": "JBT", "desc": "일본사업팀"},
        {"key": "KBT", "aliases": ["kbt", "국내사업"], "route": "notion", "group": "Notion", "icon": "doc", "label": "KBT", "desc": "국내사업팀"},
        {"key": "BP", "aliases": ["bp", "뷰티파트너", "제품qa", "제품문의", "고객상담"], "route": "cs", "group": "Notion", "icon": "flask", "label": "BP", "desc": "제품 Q&A (성분/사용법)"},
        {"key": "PEOPLE", "aliases": ["people", "피플", "인사", "hr", "피플팀"], "route": "notion", "group": "Notion", "icon": "people", "label": "PEOPLE", "desc": "연차, 보상, 퇴사, 복지"},
        # ── 시스템 ──
        {"key": "gws", "aliases": ["google", "구글", "지메일", "gmail", "캘린더", "드라이브"], "route": "gws", "group": "시스템", "icon": "link", "label": "Google Workspace", "desc": "Gmail, Calendar, Drive"},
        # ── 확장 ──
    ]

    # 특수 명령어 (@@전체, @@ALL, @@전체해제, @@목록)
    _DB_SPECIAL = {
        "전체": "select_all", "all": "select_all",
        "전체해제": "deselect_all", "해제": "deselect_all", "none": "deselect_all",
        "목록": "list", "list": "list", "help": "list",
    }

    @classmethod
    def get_db_registry(cls):
        """Return the full DB registry (for API/frontend)."""
        return cls._DB_REGISTRY

    @classmethod
    def parse_db_prefix(cls, query: str):
        """Parse @@prefix(es) from query. Supports multiple: @@a @@b 질문

        Returns:
            (db_entry_or_special_or_list, clean_query)
            - db_entry dict → single source
            - list[dict] → multiple sources
            - "select_all"/"deselect_all"/"list" string → special command
            - None → no @@ prefix
        """
        import re
        q = query.strip()
        if "@@" not in q:
            return None, query

        # ⚠️ \S+ 토큰화 금지 — "@@아마존 리뷰"가 공백에서 잘려 별칭 '아마존'(아마존검색)에
        # 걸리면 엉뚱한 소스로 격리된다. UI 칩도 "@@아마존 리뷰 " 형태로 입력하므로
        # 실사용 버그였다 (2026-08-06 Playwright 전수 테스트에서 발견).
        # → 등록된 키/별칭을 길이 내림차순(최장 일치)으로 스캔한다.
        names = []
        for entry in cls._DB_REGISTRY:
            names.append((entry["key"].lower(), entry))
            for a in entry["aliases"]:
                names.append((a.lower(), entry))
        names.sort(key=lambda x: len(x[0]), reverse=True)
        special_names = sorted(cls._DB_SPECIAL.items(), key=lambda x: len(x[0]), reverse=True)

        ql = q.lower()
        spans = []      # (start, end, entry|None)
        specials = []
        i = 0
        while True:
            i = ql.find("@@", i)
            if i < 0:
                break
            rest = ql[i + 2:]
            hit = None
            for prefix, special in special_names:
                if rest.startswith(prefix):
                    end = i + 2 + len(prefix)
                    nxt = ql[end:end + 1]
                    if nxt in ("", " ", "\t", "\n", ":"):
                        hit = ("special", special, end + (1 if nxt == ":" else 0))
                        break
            if hit is None:
                for name, entry in names:
                    if rest.startswith(name):
                        end = i + 2 + len(name)
                        nxt = ql[end:end + 1]
                        if nxt in ("", " ", "\t", "\n", ":"):
                            hit = ("entry", entry, end + (1 if nxt == ":" else 0))
                            break
            if hit is None:
                m = re.match(r"@@\S*", ql[i:])
                spans.append((i, i + m.end(), None))
                i = i + m.end()
            else:
                kind, obj, end = hit
                spans.append((i, end, obj if kind == "entry" else None))
                if kind == "special":
                    specials.append(obj)
                i = end

        # 매칭 구간(+뒤따르는 공백 1개)을 제거해 clean 구성
        out, last = [], 0
        for s_, e_, _ in spans:
            out.append(q[last:s_])
            if e_ < len(q) and q[e_] == " ":
                e_ += 1
            last = e_
        out.append(q[last:])
        clean = "".join(out).strip()

        if len(spans) == 1 and specials:
            return specials[0], clean

        entries = []
        for _, _, obj in spans:
            if obj is not None and obj not in entries:
                entries.append(obj)

        if not entries:
            return None, query
        if len(entries) == 1:
            return entries[0], clean
        return entries, clean

    def _allowed_routes(self, enabled_sources: Optional[List[str]]) -> Optional[set]:
        """Derive the set of allowed routes from enabled_sources.

        Default (None): BigQuery + GWS + Direct only. CS/Notion/Team은 @@ 선택 시에만.
        Returns {"direct"} if explicitly empty list (all sources disabled).
        """
        if enabled_sources is None:
            # 기본: 데이터(BQ) + GWS + Direct만. CS/Notion/Team은 @@ 명시 시에만 활성화
            return {"direct", "bigquery", "multi", "gws"}
        routes = {"direct"}  # direct만 기본 허용
        for src in enabled_sources:
            route = self._SOURCE_ROUTE_MAP.get(src)
            if route:
                routes.add(route)
        # Allow multi if bigquery is enabled
        if "bigquery" in routes:
            routes.add("multi")
        return routes

    @classmethod
    def _build_db_command_response(cls, command: str) -> str:
        """Build response for @@전체, @@전체해제, @@목록 special commands."""
        if command == "list":
            lines = ["## 📂 사용 가능한 데이터소스\n"]
            lines.append("`@@데이터소스명 질문` 형태로 특정 데이터만 검색합니다.\n")
            current_group = ""
            for e in cls._DB_REGISTRY:
                g = e.get("group", "")
                if g != current_group:
                    current_group = g
                    lines.append(f"\n### {g}")
                aliases = ", ".join(f"`@@{a}`" for a in e["aliases"][:2]) if e["aliases"] else ""
                alias_text = f" ({aliases})" if aliases else ""
                lines.append(f"- `@@{e['key']}`{alias_text} — {e['desc']}")
            lines.append("\n### 특수 명령")
            lines.append("- `@@전체` — 모든 데이터소스 활성화")
            lines.append("- `@@전체해제` — 모든 데이터소스 비활성화 (직접 대화만)")
            lines.append("- `@@목록` — 이 목록 표시")
            return "\n".join(lines)

        if command == "select_all":
            return "✅ **모든 데이터소스가 활성화**되었습니다.\n\n질문하시면 AI가 자동으로 적절한 데이터소스를 선택합니다.\n\n> 💡 특정 소스만 사용하려면 `@@매출 이번달 합계` 형태로 입력하세요."

        if command == "deselect_all":
            return "🔕 **모든 데이터소스가 비활성화**되었습니다.\n\n데이터 검색 없이 AI 직접 대화만 가능합니다.\n\n> 다시 활성화하려면 `@@전체` 또는 `@@ALL`을 입력하세요."

        return ""

    async def route_and_execute(
        self,
        query: str,
        messages: Optional[List[Dict[str, str]]] = None,
        model_type: str = MODEL_CLAUDE,
        user_email: str = "",
        images: Optional[List[dict]] = None,
        brand_filter: Optional[str] = None,
        can_view_fi: bool = False,
        enabled_sources: Optional[List[str]] = None,
        enabled_team_resources: Optional[Dict[str, list]] = None,
        stream_callback=None,
    ) -> dict:
        """Main entry point: analyze query -> delegate to Sub Agent -> return result.

        Args:
            query: User's natural language question (latest message).
            messages: Full conversation history for context continuity.
            model_type: Always MODEL_CLAUDE (user-facing model selection removed).
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

        # ═══ @@ 데이터소스 직접 지정 ═══
        # 사내 은어·오타 보정 — 라우팅/SQL/캐시/성분 조회 전에 한 번만 (app/core/term_aliases.py)
        from app.core.term_aliases import expand_aliases
        query, _alias_hits = expand_aliases(query)
        if _alias_hits:
            logger.info("alias_expanded", hits=_alias_hits)

        db_entry, clean_query = self.parse_db_prefix(query)

        # Special commands: @@전체, @@전체해제, @@목록
        if isinstance(db_entry, str):
            return {"source": "direct", "answer": self._build_db_command_response(db_entry)}

        # Normalize to list for uniform handling
        if db_entry and not isinstance(db_entry, list):
            db_entry = [db_entry]

        if not can_view_fi and _requests_fi_data(query, enabled_sources, db_entry):
            logger.info("fi_access_denied", path="route_and_execute", query=query[:100])
            return {"source": "bigquery", "answer": FI_ACCESS_DENIED_MESSAGE}

        _ing_intent = _ingredient_filter_intent(query)
        if _ing_intent:
            logger.info("ingredient_query", path="route_and_execute",
                        ingredient=_ing_intent[0], contains=_ing_intent[1])
            return await self._handle_ingredient_query(query, _ing_intent, model_type)

        from app.core.model_rights import model_rights_intent
        _mr_entries = db_entry if isinstance(db_entry, list) else ([db_entry] if isinstance(db_entry, dict) else [])
        _mr_selected = any(e.get("route") == "model_rights" for e in _mr_entries)             or (enabled_sources and list(enabled_sources) == ["초상권"])
        if _mr_selected or model_rights_intent(query):
            _q_mr = (clean_query or query) if _mr_entries else query
            logger.info("model_rights_query", path="route_and_execute", query=_q_mr[:80])
            return await self._handle_model_rights(_q_mr, model_type, images=images)

        # `@@보고서` 로 지정했으면 문구를 보지 않는다. 지정했을 땐 접두어를 뗀
        # 본문으로 만들어야 제목·필터가 깨끗하다
        _rep_selected = any(e.get("route") == "report" for e in (db_entry or []))
        _rep = await self._handle_report(
            (clean_query or query) if _rep_selected else query,
            user_email, explicit=_rep_selected)
        if _rep:
            return _rep

        if db_entry:
            query = clean_query or query
            if not query.strip():
                labels = ", ".join(e["label"] for e in db_entry)
                return {"source": "direct", "answer": f"**{labels}** 데이터소스가 선택되었습니다.\n\n질문을 입력해주세요."}

            # Single source → direct route (fast path)
            if len(db_entry) == 1:
                entry = db_entry[0]
                route = entry["route"]
                logger.info("db_prefix_routed", prefix=entry["key"], route=route, query=query[:80])
                handlers = {
                    "bigquery": self._handle_bigquery,
                    "notion": self._handle_notion,
                    "gws": self._handle_gws,
                    "cs": self._handle_cs,
                    "team": self._handle_team,
                    "multi": self._handle_multi,
                }
                handler = handlers.get(route, self._handle_direct)
                if route in ("bigquery", "multi"):
                    result = await handler(query, messages, conversation_context, model_type, user_email, brand_filter=brand_filter, can_view_fi=can_view_fi, enabled_sources=_scope_sources(enabled_sources, db_entry), source_explicit=True)
                elif route == "notion":
                    result = await self._handle_qdrant(query, messages, conversation_context, model_type, user_email, team_key=entry["key"])
                elif route == "team":
                    result = await self._handle_team(query, messages, conversation_context, model_type, user_email, enabled_team_resources=enabled_team_resources)
                elif route == "direct":
                    result = await self._handle_direct(query, messages, conversation_context, model_type, user_email)
                else:
                    result = await handler(query, messages, conversation_context, model_type, user_email)
                if "answer" in result:
                    result["answer"] = ensure_formatting(result["answer"], domain=route)
                return result

            # Multiple sources → parallel execute and merge
            logger.info("db_multi_prefix", sources=[e["key"] for e in db_entry], query=query[:80])
            import asyncio as _aio
            tasks = []
            for entry in db_entry:
                route = entry["route"]
                if route in ("bigquery", "multi"):
                    tasks.append(("bigquery", entry, self._handle_bigquery(query, messages, conversation_context, model_type, user_email, brand_filter=brand_filter, can_view_fi=can_view_fi, enabled_sources=_scope_sources(enabled_sources, db_entry), source_explicit=True)))
                elif route == "notion":
                    tasks.append(("notion", entry, self._handle_qdrant(query, messages, conversation_context, model_type, user_email, team_key=entry["key"])))
                elif route == "cs":
                    tasks.append(("cs", entry, self._handle_cs(query, messages, conversation_context, model_type, user_email)))
                elif route == "gws":
                    tasks.append(("gws", entry, self._handle_gws(query, messages, conversation_context, model_type, user_email)))
                else:
                    tasks.append(("direct", entry, self._handle_direct(query, messages, conversation_context, model_type, user_email)))

            results = await _aio.gather(*[t[2] for t in tasks], return_exceptions=True)
            combined_parts = []
            for i, res in enumerate(results):
                label = tasks[i][1]["label"]
                if isinstance(res, Exception):
                    combined_parts.append(f"### {label}\n⚠️ 오류: {res}")
                elif isinstance(res, dict) and "answer" in res:
                    combined_parts.append(f"### {label}\n{res['answer']}")
                elif isinstance(res, str):
                    combined_parts.append(f"### {label}\n{res}")

            combined = "\n\n---\n\n".join(combined_parts)
            return {"source": "multi", "answer": ensure_formatting(combined, domain="multi")}

        # Image present → force direct route (vision LLM)
        if images:
            logger.info("orchestrator_image_route_forced", image_count=len(images), query=query[:100])
            result = await self._handle_direct(
                query, messages, conversation_context, model_type, user_email, images=images
            )
            if "answer" in result:
                result["answer"] = ensure_formatting(result["answer"], domain="direct")
            return result

        # Fast path: if enabled_sources maps to a single route, skip classification entirely (like @@)
        allowed = self._allowed_routes(enabled_sources)
        _single_route = None
        if allowed is not None:
            _data_routes = allowed - {"direct"}
            if len(_data_routes) == 1:
                _single_route = next(iter(_data_routes))
                logger.info("single_source_fast_path", route=_single_route, enabled_sources=enabled_sources)

        if _single_route:
            route = _single_route
        else:
            # Step 1: Classify query intent
            # Fast path: keyword match first, LLM fallback only for short ambiguous queries
            route, _confident = self._keyword_classify_ex(query)
            is_system_task = query.strip().startswith("### Task:")
            _is_direct_locked = any(kw in query.lower() for kw in _DIRECT_LOCK_KW)
            # LLM 우선 하이브리드: 키워드가 확신 없이 분류한 경우는 LLM 판정이 기본값.
            # 명백한 케이스(확신 분류·직접 잠금·시스템 태스크)만 키워드로 끝낸다.
            if not _confident and not is_system_task and not _is_direct_locked:
                if len(query.strip()) <= 300:
                    flash = get_flash_client()
                    route = await self._classify_with_llm(query, conversation_context, flash)
            # Apply enabled_sources filter — redirect to direct if route is disabled
            # Exception: keyword-classified notion/cs/team routes bypass the default filter
            # (these are confidently classified by specific keywords, not ambiguous)
            if allowed is not None and route not in allowed:
                if enabled_sources is None and route in ("notion", "cs", "team"):
                    logger.info("route_keyword_override", route=route, reason="keyword-classified, bypassing default filter")
                else:
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

        # Load few-shot skill examples for direct route (non-blocking, fire-and-forget on error)
        _skill_ctx = ""
        if route == "direct":
            try:
                from app.agents.skill_memory import load_skill_context
                _skill_ctx = await asyncio.to_thread(load_skill_context, "direct", query)
            except Exception:
                pass

        # Step 2: Execute via Sub Agent with context
        handlers = {
            "bigquery": self._handle_bigquery,
            "notion": self._handle_notion,
            "gws": self._handle_gws,
            "cs": self._handle_cs,
            "team": self._handle_team,
            "multi": self._handle_multi,
        }
        handler = handlers.get(route, self._handle_direct)
        if route in ("bigquery", "multi"):
            result = await handler(query, messages, conversation_context, model_type, user_email, brand_filter=brand_filter, can_view_fi=can_view_fi, enabled_sources=enabled_sources)
        elif route == "notion":
            result = await self._handle_qdrant(query, messages, conversation_context, model_type, user_email)
        elif route == "direct" or handler == self._handle_direct:
            result = await self._handle_direct(query, messages, conversation_context, model_type, user_email, images=images, stream_callback=stream_callback, skill_context=_skill_ctx)
        else:
            result = await handler(query, messages, conversation_context, model_type, user_email)

        # Step 3: Coherence check — fire-and-forget background (was blocking 2-3s)
        # Only log mismatches; don't delay the response
        if "answer" in result and route not in ("direct", "multi", "cs"):
            try:
                task = asyncio.create_task(self._verify_coherence(query, result["answer"], route))
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
            except Exception:
                pass

        # Step 4: Post-process response for consistent markdown formatting
        if "answer" in result:
            result["answer"] = ensure_formatting(result["answer"], domain=route)

        return result

    async def route_and_stream(
        self,
        query: str,
        messages=None,
        model_type: str = MODEL_CLAUDE,
        user_email: str = "",
        images=None,
        brand_filter=None,
        can_view_fi: bool = False,
        enabled_sources=None,
        enabled_team_resources=None,
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

        # ═══ @@ 데이터소스 직접 지정 (streaming) ═══
        # 사내 은어·오타 보정 — 라우팅/SQL/캐시/성분 조회 전에 한 번만 (app/core/term_aliases.py)
        from app.core.term_aliases import expand_aliases
        query, _alias_hits = expand_aliases(query)
        if _alias_hits:
            logger.info("alias_expanded", hits=_alias_hits)

        db_entry, clean_query = self.parse_db_prefix(query)

        # Special commands
        if isinstance(db_entry, str):
            yield ("source", "direct")
            yield ("done", self._build_db_command_response(db_entry))
            return

        # Normalize to list
        if db_entry and not isinstance(db_entry, list):
            db_entry = [db_entry]

        if not can_view_fi and _requests_fi_data(query, enabled_sources, db_entry):
            logger.info("fi_access_denied", path="route_and_stream", query=query[:100])
            yield ("source", "bigquery")
            yield ("done", FI_ACCESS_DENIED_MESSAGE)
            return

        _ing_intent = _ingredient_filter_intent(query)
        if _ing_intent:
            logger.info("ingredient_query", path="route_and_stream",
                        ingredient=_ing_intent[0], contains=_ing_intent[1])
            _r = await self._handle_ingredient_query(query, _ing_intent, model_type)
            yield ("source", "bigquery")
            yield ("done", _r.get("answer", ""))
            return

        from app.core.model_rights import model_rights_intent
        _mr_entries = db_entry if isinstance(db_entry, list) else ([db_entry] if isinstance(db_entry, dict) else [])
        _mr_selected = any(e.get("route") == "model_rights" for e in _mr_entries)             or (enabled_sources and list(enabled_sources) == ["초상권"])
        if _mr_selected or model_rights_intent(query):
            _q_mr = (clean_query or query) if _mr_entries else query
            logger.info("model_rights_query", path="route_and_stream", query=_q_mr[:80])
            _r = await self._handle_model_rights(_q_mr, model_type, images=images)
            yield ("source", "direct")
            yield ("done", _r.get("answer", ""))
            return

        # 보고서는 조회를 여러 번 돌아 5~10초 걸린다. source 를 먼저 흘려 로딩 표시를 띄운다.
        # ⚠️ 진행 문구를 ("chunk", ...) 로 보내면 안 된다 — routes.py 가 streamed_live 를 세워
        #    뒤따르는 ("done", 본문) 을 통째로 버린다 (2026-08-12 확인).
        from app.reports import registry as _rep_reg
        _rep_selected = any(e.get("route") == "report" for e in _mr_entries)
        _rep_query = (clean_query or query) if _rep_selected else query
        if _rep_selected or _rep_reg.wants_report(_rep_query):
            yield ("source", "bigquery")
            _r = await self._handle_report(_rep_query, user_email, explicit=_rep_selected)
            if _r:
                yield ("done", _r.get("answer", ""))
                return

        if db_entry:
            query = clean_query or query

            if not query.strip():
                labels = ", ".join(e["label"] for e in db_entry)
                yield ("source", "direct")
                yield ("done", f"**{labels}** 데이터소스가 선택되었습니다.\n\n질문을 입력해주세요.")
                return

            # Single source → existing fast path
            if len(db_entry) == 1:
                entry = db_entry[0]
                route = entry["route"]
                logger.info("db_prefix_routed_stream", prefix=entry["key"], route=route)
                yield ("source", route + ":" + entry["label"])

                if route == "bigquery":
                    # @@ single-source path: wiki_context not injected here (by design —
                    # wiki lookup runs after route_and_stream's source yield at line ~618,
                    # but this early-return path exits before that block is reached).
                    from app.core.safety import get_maintenance_manager as _gmm
                    _mm2 = _gmm()
                    if _mm2.active and _mm2.manual:
                        yield ("chunk", f"**데이터 점검 중입니다** — 관리자가 수동으로 점검을 활성화했습니다. 잠시 후 다시 시도해 주세요.\n\n*사유: {_mm2.reason}*")
                        yield ("done", "")
                        return
                    _sp_maint_warn = ("\n\n> ⚠️ 참고: 데이터 테이블이 업데이트 중일 수 있습니다. 수치가 부정확하면 잠시 후 다시 조회해주세요."
                                      if (_mm2.active and not _mm2.manual) else "")
                    from app.agents.sql_agent import run_sql_agent_stream
                    _q = asyncio.Queue()
                    _loop = asyncio.get_running_loop()
                    def _bq():
                        try:
                            for chunk in run_sql_agent_stream(query, conversation_context=conversation_context, model_type=model_type, brand_filter=brand_filter, enabled_sources=_scope_sources(enabled_sources, db_entry), can_view_fi=can_view_fi):
                                _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", chunk))
                        except Exception as e:
                            _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", f"오류: {e}"))
                        _loop.call_soon_threadsafe(_q.put_nowait, ("end", None))
                    _loop.run_in_executor(None, _bq)
                    while True:
                        mt, data = await _q.get()
                        if mt == "end":
                            break
                        yield ("chunk", data)
                    if _sp_maint_warn:
                        yield ("chunk", _sp_maint_warn)
                    yield ("done", "")
                    return

                from app.core.safety import get_circuit
                circuit = get_circuit(route)
                handlers = {"gws": self._handle_gws, "cs": self._handle_cs, "team": self._handle_team, "multi": self._handle_multi}
                handler = handlers.get(route, self._handle_direct)
                try:
                    if route == "notion":
                        result = await asyncio.wait_for(self._handle_qdrant(query, messages, conversation_context, model_type, user_email, team_key=entry["key"]), timeout=30.0)
                    elif route == "team":
                        result = await asyncio.wait_for(handler(query, messages, conversation_context, model_type, user_email, enabled_team_resources=enabled_team_resources), timeout=30.0)
                    else:
                        result = await asyncio.wait_for(handler(query, messages, conversation_context, model_type, user_email), timeout=300.0 if route == "multi" else 30.0)
                except asyncio.TimeoutError:
                    result = {"answer": "⚠️ 분석이 예상보다 오래 걸리고 있습니다. 더 짧은 기간이나 구체적인 조건으로 다시 질문해 보세요.\n\n> 💡 **이런 식으로 질문해 보세요**\n> - \"2025년 1분기 일본 매출 알려줘\" (기간+국가 한정)\n> - \"이번 달 아마존 매출 현황\" (채널 지정)\n> - \"센텔라 앰플 최근 3개월 매출 추이\" (제품 지정)", "source": route}
            else:
                # Multiple sources → parallel, merge results
                logger.info("db_multi_prefix_stream", sources=[e["key"] for e in db_entry])
                yield ("source", "multi:" + "+".join(e["label"] for e in db_entry))

                tasks = []
                for entry in db_entry:
                    r = entry["route"]
                    if r in ("bigquery", "multi"):
                        tasks.append(("bigquery", entry, self._handle_bigquery(query, messages, conversation_context, model_type, user_email, brand_filter=brand_filter, can_view_fi=can_view_fi, enabled_sources=_scope_sources(enabled_sources, db_entry), source_explicit=True)))
                    elif r == "notion":
                        tasks.append(("notion", entry, self._handle_qdrant(query, messages, conversation_context, model_type, user_email, team_key=entry["key"])))
                    elif r == "cs":
                        tasks.append(("cs", entry, self._handle_cs(query, messages, conversation_context, model_type, user_email)))
                    else:
                        tasks.append(("direct", entry, self._handle_direct(query, messages, conversation_context, model_type, user_email)))

                results = await asyncio.gather(*[t[2] for t in tasks], return_exceptions=True)
                parts = []
                for i, res in enumerate(results):
                    label = tasks[i][1]["label"]
                    if isinstance(res, Exception):
                        parts.append(f"### {label}\n⚠️ 오류: {res}")
                    elif isinstance(res, dict) and "answer" in res:
                        parts.append(f"### {label}\n{res['answer']}")
                    elif isinstance(res, str):
                        parts.append(f"### {label}\n{res}")
                result = {"answer": "\n\n---\n\n".join(parts)}
            if "answer" in result:
                result["answer"] = ensure_formatting(result["answer"], domain=route)
            answer = result.get("answer", "")
            pos = 0
            while pos < len(answer):
                end = min(pos + 80, len(answer))
                if end < len(answer):
                    nl = answer.find("\n", pos, end + 20)
                    if nl > pos:
                        end = nl + 1
                yield ("chunk", answer[pos:end])
                pos = end
                await asyncio.sleep(0.02)
            yield ("done", "")
            return

        # Image → non-streaming direct
        if images:
            result = await self._handle_direct(
                query, messages, conversation_context, model_type, user_email, images=images
            )
            yield ("source", "direct")
            yield ("done", ensure_formatting(result.get("answer", ""), domain="direct"))
            return

        is_system_task = query.strip().startswith("### Task:")

        # Fast path: single route from enabled_sources → skip classification (like @@)
        allowed = self._allowed_routes(enabled_sources)
        _single_route = None
        if allowed is not None:
            _data_routes = allowed - {"direct"}
            if len(_data_routes) == 1:
                _single_route = next(iter(_data_routes))
                logger.info("stream_single_source_fast_path", route=_single_route)

        if _single_route:
            route, _confident = _single_route, True
        else:
            route, _confident = self._keyword_classify_ex(query)

        # Wave 1: Emit source hint IMMEDIATELY
        yield ("source", route)

        # Wiki lookup runs AFTER source yield — loading indicator shows first.
        wiki_context = ""
        try:
            from app.knowledge.wiki_search import search_with_pages
            wiki_context = await search_with_pages(query, limit=4)
            if wiki_context:
                logger.info("wiki_context_injected", length=len(wiki_context))
        except Exception as e:
            logger.warning("wiki_lookup_failed", error=str(e)[:200])

        # Skill memory: few-shot examples from 👍 feedback (direct route only)
        _stream_skill_ctx = ""
        if route == "direct":
            try:
                from app.agents.skill_memory import load_skill_context
                _stream_skill_ctx = await asyncio.to_thread(load_skill_context, "direct", query)
            except Exception:
                pass

        if not _single_route:
            # Re-classify short ambiguous queries with LLM (only if no strong direct signal)
            _is_direct_locked = any(kw in query.lower() for kw in _DIRECT_LOCK_KW)
            # LLM 우선 하이브리드: 확신 없는 분류는 LLM 판정이 기본값 (비스트리밍과 동일)
            if not _confident and not is_system_task and not _is_direct_locked:
                if len(query.strip()) <= 300:
                    flash = get_flash_client()
                    new_route = await self._classify_with_llm(query, conversation_context, flash)
                    if new_route != route:
                        route = new_route
                        yield ("source", route)

            # Apply enabled_sources filter
            # Exception: keyword-classified notion/cs/team bypass default filter
            if allowed is not None and route not in allowed:
                if enabled_sources is None and route in ("notion", "cs", "team"):
                    logger.info("stream_route_keyword_override", route=route)
                else:
                    logger.info("stream_route_filtered", original_route=route, allowed=list(allowed))
                    if route != "direct":
                        route = "direct"
                        yield ("source", route)

        # Direct route → real-time streaming
        if route == "direct" and not is_system_task:
            llm = get_llm_client(MODEL_CLAUDE)
            today = datetime.now().strftime("%Y년 %m월 %d일 (%A)")
            system = self._build_direct_system_prompt()

            # Static prompt as a cached block; dynamic parts appended as separate,
            # uncached blocks so they don't invalidate the cached prefix (see
            # ClaudeClient._wrap_system and _build_direct_system_prompt).
            date_line = f"오늘 날짜는 {today}입니다."
            extra_blocks: List[str] = []
            if self._needs_web_search(query):
                _loop_s = asyncio.get_running_loop()
                search_context = await _loop_s.run_in_executor(None, self._gather_search_context, query)
                if search_context:
                    extra_blocks.append(f"## 참고할 최신 검색 정보 (Google 검색 결과)\n{search_context}")
            if wiki_context:
                extra_blocks.append(
                    "## 참고: 신뢰 상태가 표시된 지식 위키 컨텍스트\n"
                    f"{wiki_context}\n"
                    "위 내용은 원문 자체가 아니라 이전 답변에서 추출된 조직 기억입니다. "
                    "각 항목의 신뢰 규칙을 반드시 지키고, 원 데이터 조회 결과와 다르면 "
                    "원 데이터와 충돌 사실을 우선 설명하세요."
                )
            if _stream_skill_ctx:
                extra_blocks.append(_stream_skill_ctx)

            final_system = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": date_line},
            ] + [{"type": "text", "text": block} for block in extra_blocks]

            # Stream via thread + queue
            _q: asyncio.Queue = asyncio.Queue()
            _loop = asyncio.get_running_loop()

            def _worker():
                try:
                    if messages and len(messages) > 1 and hasattr(llm, 'generate_with_history_stream'):
                        gen = llm.generate_with_history_stream(
                            messages=_clean_messages_for_history(messages),
                            system_instruction=final_system, temperature=0.5,
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

            # Maintenance check (mirrors non-streaming path)
            from app.core.safety import get_maintenance_manager
            _mm = get_maintenance_manager()
            if _mm.active and _mm.manual:
                yield ("chunk", f"**데이터 점검 중입니다** — 관리자가 수동으로 점검을 활성화했습니다. 잠시 후 다시 시도해 주세요.\n\n*사유: {_mm.reason}*")
                yield ("done", "")
                return
            _stream_maintenance_warning = (
                "\n\n> ⚠️ 참고: 데이터 테이블이 업데이트 중일 수 있습니다. 수치가 부정확하면 잠시 후 다시 조회해주세요."
                if (_mm.active and not _mm.manual) else ""
            )

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
                        wiki_context=wiki_context,
                        can_view_fi=can_view_fi,
                    ):
                        _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", chunk))
                except Exception as e:
                    _loop.call_soon_threadsafe(_q.put_nowait, ("chunk", f"오류: {e}"))
                _loop.call_soon_threadsafe(_q.put_nowait, ("end", None))

            _loop.run_in_executor(None, _bq_worker)

            # Wave 2: 5min timeout for BQ route (SQL gen + execute + format)
            _bq_start = asyncio.get_event_loop().time()
            while True:
                try:
                    msg_type, data = await asyncio.wait_for(_q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_event_loop().time() - _bq_start
                    if elapsed > 90.0:
                        yield ("chunk", "\n\n⚠️ 데이터 분석이 90초를 초과했습니다. 더 구체적인 조건(기간, 국가, 제품 등)으로 다시 질문해주세요.")
                        break
                    continue
                if msg_type == "end":
                    break
                yield ("chunk", data)

            if _stream_maintenance_warning:
                yield ("chunk", _stream_maintenance_warning)
            yield ("done", "")
            return

        # 지식 회수 루프: cs/multi 라우트에 wiki 팩트 주입 (direct/BQ는 위에서 이미 처리)
        _handler_ctx = conversation_context
        if wiki_context and route in ("cs", "multi"):
            _wiki_block = f"\n\n[참고: 관련 사내 지식]\n{wiki_context}"
            _handler_ctx = (conversation_context + _wiki_block) if conversation_context else _wiki_block.lstrip()

        # CS/Multi → true token streaming (same pattern as the BigQuery route
        # above: prep runs async, only the final LLM answer streams).
        # No circuit breaker here, matching the BigQuery streaming path —
        # generator-based flows don't compose with the is_available() gate.
        #
        # Note: "team" route is not included here — it's currently
        # unreachable (the keyword classifier and _DB_REGISTRY both route
        # team-resource queries to "notion" instead; see _keyword_classify).
        # A pre-existing condition, not introduced by this streaming work.
        if route in ("cs", "multi"):
            from app.core.stream_bridge import stream_with_timeout, StreamTimeout

            if route == "cs":
                from app.agents.cs_agent import run_stream as _route_stream_fn
                _contextualized_query = (
                    f"[이전 대화]\n{_handler_ctx}\n\n[현재 질문]\n{query}" if _handler_ctx else query
                )
                _stream_gen = _route_stream_fn(_contextualized_query, model_type=model_type)
                _stream_timeout = 30.0
            else:  # multi
                _stream_gen = self._handle_multi_stream(
                    query, _handler_ctx, model_type, brand_filter=brand_filter, can_view_fi=can_view_fi, enabled_sources=enabled_sources
                )
                _stream_timeout = 300.0

            try:
                async for chunk in stream_with_timeout(_stream_gen, timeout_s=_stream_timeout):
                    yield ("chunk", chunk)
            except StreamTimeout:
                logger.warning("route_timeout", route=route, timeout_s=_stream_timeout)
                yield ("chunk", "\n\n⚠️ 분석이 예상보다 오래 걸리고 있습니다. 조회 범위를 좁혀서 다시 시도해 주세요.\n\n> 💡 **이런 식으로 질문해 보세요**\n> - 기간을 한정: \"2025년 1분기\" 대신 \"2025년 3월\"\n> - 국가/채널 지정: \"일본 큐텐 매출\"\n> - 제품 지정: \"센텔라 앰플 매출 추이\"")
            except Exception as e:
                logger.error("route_execution_failed", route=route, error=str(e))
                yield ("chunk", "⚠️ 요청을 처리하는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n다른 방식으로 질문하시면 도움이 될 수 있습니다:\n- 질문을 더 구체적으로 (기간, 국가, 제품 등 조건 추가)\n- 복잡한 질문은 나누어서 하나씩 질문")
            yield ("done", "")
            return

        # Non-streaming routes (Notion, GWS, Team) → simulate streaming
        # Timeout: GWS 60s, others 30s.
        # 주의: 예전엔 여기 45s 인데 gws_agent 내부는 300s 라 어긋나 있었다 —
        # 주석엔 "inner agent 30s" 라고 적혀 있었지만 실제 값은 300s 였다.
        # 지금은 내부가 (도구 호출 + 정리 40s) 구조라 60s 면 충분하다.
        # Note: "team" is currently unreachable (see comment above) but this
        # dispatch is kept as-is in case that classification changes.
        from app.core.safety import get_circuit

        handlers = {
            "gws": self._handle_gws,
            "team": self._handle_team,
        }
        handler = handlers.get(route, self._handle_direct)
        _route_timeout = 60.0 if route == "gws" else 30.0

        # Check circuit breaker before calling
        circuit = get_circuit(route)
        if not circuit.is_available():
            logger.warning("circuit_open_fallback", route=route)
            result = {"answer": f"⚠️ {route} 서비스가 일시적으로 불안정합니다. 잠시 후 다시 시도해주세요.", "source": route}
        else:
            try:
                if route == "notion":
                    result = await asyncio.wait_for(
                        self._handle_qdrant(query, messages, conversation_context, model_type, user_email),
                        timeout=20.0,
                    )
                elif route == "team":
                    result = await asyncio.wait_for(
                        handler(query, messages, conversation_context, model_type, user_email, enabled_team_resources=enabled_team_resources),
                        timeout=_route_timeout,
                    )
                else:
                    result = await asyncio.wait_for(
                        handler(query, messages, _handler_ctx, model_type, user_email),
                        timeout=_route_timeout,
                    )
                circuit.record_success()
            except asyncio.TimeoutError:
                logger.warning("route_timeout", route=route, timeout_s=_route_timeout)
                circuit.record_failure()
                result = {"answer": "⚠️ 분석이 예상보다 오래 걸리고 있습니다. 조회 범위를 좁혀서 다시 시도해 주세요.\n\n> 💡 **이런 식으로 질문해 보세요**\n> - 기간을 한정: \"2025년 1분기\" 대신 \"2025년 3월\"\n> - 국가/채널 지정: \"일본 큐텐 매출\"\n> - 제품 지정: \"센텔라 앰플 매출 추이\"", "source": route}
            except Exception as e:
                logger.error("route_execution_failed", route=route, error=str(e))
                circuit.record_failure()
                result = {"answer": "⚠️ 요청을 처리하는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n다른 방식으로 질문하시면 도움이 될 수 있습니다:\n- 질문을 더 구체적으로 (기간, 국가, 제품 등 조건 추가)\n- 복잡한 질문은 나누어서 하나씩 질문", "source": route}

        if "answer" in result:
            result["answer"] = ensure_formatting(result["answer"], domain=route)

        # Simulate streaming: larger chunks for faster perceived delivery
        import asyncio as _aio
        answer = result.get("answer", "")
        if answer:
            pos = 0
            while pos < len(answer):
                # ~80 chars per chunk at line boundaries (fast, natural)
                end = min(pos + 80, len(answer))
                if end < len(answer):
                    nl = answer.find("\n", pos, end + 20)
                    if nl > pos:
                        end = nl + 1
                    else:
                        sp = answer.rfind(" ", pos, end + 10)
                        if sp > pos:
                            end = sp + 1
                yield ("chunk", answer[pos:end])
                pos = end
                await _aio.sleep(0.015)  # 15ms — smooth, fast delivery
        yield ("done", "")

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
  + **프로모션/행사 일정도 여기다** — 어느 팀이 어느 몰에서 언제 프로모션을 하는지는
    사내 프로모션 캘린더(BigQuery)에 있다. 문서도 개인 캘린더도 아니다
- notion: 사내 문서, 정책, 매뉴얼, 프로세스 관련
- gws: Google Drive 파일, Gmail 메일, **개인** Calendar 일정 (내 일정·회의·미팅)
- cs: 제품 CS 상담 (성분, 사용법, 비건인증, 피부 관련 질문, 제품 문의)
- multi: 내부 데이터 + 외부 정보가 모두 필요한 복합 분석 질문
  예시: "날씨가 매출에 영향?", "매출 하락 원인", "시장 트렌드와 매출 비교", "인도네시아 경제 상황이 판매에 미치는 영향"
- direct: 일반 지식, 용어 설명, 간단한 질문, 실시간 정보 (날씨, 뉴스 등)

판단 기준:
- 데이터 조회만 → bigquery
- 제품 성분/사용법/CS 문의 → cs
- ⚠️ 제품명 + 국가/지역(남미, 북미, 동남아, 유럽, 특정 국가 등) + 반응/매출/판매/인기/실적 → bigquery (판매 데이터 조회)
  예: "포어마이징 벨벳 선크림 남미 반응" → bigquery (CS 아님!)
  예: "히알루론산 세럼 동남아 얼마나 팔려?" → bigquery
- ⚠️ "프로모션/행사/기획전 + 일정·스케줄·언제·예정" → **bigquery** (프로모션 캘린더)
  예: "인도네시아 프로모션 일정" · "8월에 무슨 행사 있어?" · "쇼피 프로모션 언제야?"
  반면 "내 일정", "회의 잡힌 거", "미팅 언제야" 는 gws (개인 캘린더)
- SKIN1004 데이터 + 외부맥락(날씨/시장/경쟁/원인/영향/트렌드) → multi
- 외부 정보만 → direct
- ⚠️ SKIN1004/매출/제품과 무관한 질문(부동산, 주식, 일반상식, 개인질문 등)은 이전 대화가 BQ였어도 반드시 direct!
- 이전 대화 맥락을 참고하여 "그거", "아까", "다시" 같은 참조를 이해하세요.
- ⚠️ 이전 답변에 대한 확인/설명 요청 → direct: "이거 ~기준인가요?", "방금 거 ~맞나요?", "위에 나온 거 ~인가요?", "저거 ~뭔가요?", "어느 ~기준?", "~기준이 뭐야?" 등 새로운 데이터 조회 없이 이전 답변을 설명/확인하는 질문은 반드시 direct!
- ⚠️ 이전 답변이 📊 BigQuery 데이터(숫자/표 형태, 개수/집계 결과)이고, 현재 질문이 그 데이터 목록/명세 요청("명시해줘", "뭔지 알려줘", "목록 보여줘", "어떤 거야", "뭐뭐야", "보여줘봐", "뭔가요") → bigquery (새로운 데이터 조회 필요)
{context_section}현재 질문: {query}

경로 하나만 답변 (bigquery/notion/gws/cs/multi/direct):"""

        try:
            response = await asyncio.to_thread(llm.generate, prompt, temperature=0.0)
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
        "라인", "차트", "그래프", "그려", "시각화", "도표", "플롯", "그래프로", "차트로", "시각화해",
        "막대그래프", "원형그래프", "꺾은선", "파이차트", "바차트", "그려줘",
        "재고", "판매", "거래", "실적", "성과", "수출", "팔린", "팔리",
        "데이터", "조회", "집계", "합계", "평균",
        "분석", "추이", "증감", "성장률",
        "top", "순위", "랭킹",
        # Product listing queries → BigQuery Product table
        "제품 리스트", "제품 목록", "제품 종류", "전체 제품",
        "어떤 제품", "제품이 뭐", "제품 수", "몇 개 제품",
        "제품 현황", "제품 카테고리",
        # Marketing / Advertising (마케팅 테이블)
        "광고", "광고비", "광고 비용", "마케팅", "마캐팅", "마케팅비", "마케팅 비용", "지출",
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
        "유가 협업", "무가 협업", "시딩", "매니저별",
        # Review (리뷰 테이블)
        "리뷰", "review", "평점", "별점",
        "리뷰 분석", "고객 리뷰", "제품 리뷰",
        "스마트스토어 리뷰", "아마존 리뷰", "쇼피 리뷰", "큐텐 리뷰",
        # Shopify
        "shopify", "쇼피파이", "자사몰 매출", "반품", "환불",
        # Platform metrics
        "플랫폼 순위", "제품 순위", "랭킹 데이터", "할인가",
        "예스스타일", "yesstyle", "스타일코리안", "스타일바나", "올리브영",
        "졸스", "소시올라", "pureseoul", "barecare",
        "순위 상승", "순위 변동", "순위 추이", "경쟁사 순위",
        # Repurchase
        "재구매", "재구매율", "리텐션", "코호트",
        # Financial P&L (FI_LLM_Flat)
        "영업이익", "매출총이익", "매출원가", "판관비", "손익", "이익률", "원가율", "광고선전비",
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
        "날씨", "영향",
        # NOTE: "원인", "이유", "왜" removed — these with data keywords mean
        # "analyze our data to find the cause", not "search the web". Same logic as "상관".
        "트렌드", "경쟁", "뉴스",
        "환율", "전망", "예측",
        "연관",
        # NOTE: "상관" removed — "상관관계" is internal data correlation analysis,
        # not external web context. Routes correctly to bigquery instead of multi.
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
        "불량", "교환", "환불", "반품", "이물질",
        "피부과", "시술", "직사광선",
        "병풀", "패치 테스트", "패치테스트",
        "skin1004", "스킨1004",
        "방부제", "향료", "인공색소", "파라벤", "sls", "글루텐",
        "직구", "매장",
        "기름지", "피부 관리", "피부관리",
        "지속시간", "선물세트", "뚜껑", "함께 써도", "같이 써도",
        "정품", "적립금", "배송 기간", "배송기간", "배송 얼마나",
    ]

    _TEAM_KEYWORDS = [
        "자료 어디", "시트 어디", "시트 찾아", "링크 찾아", "링크 줘",
        "어디있어", "어디 있어", "자료 줘",
        "jbt 시트", "bcm 시트", "east 시트", "west 시트",
        "jbt 자료", "bcm 자료", "east 자료", "west 자료",
        "bea", "bxm", "플래그십",
        "예산 시트", "pr 시트", "운영 시트", "대시보드 링크",
        "팀 자료", "팀별 자료", "db hub", "데이터 허브",
        # PEOPLE/HR keywords
        "연차", "휴가", "휴일대체", "휴직", "육아휴직", "퇴사", "퇴직금", "경조", "경조휴가", "졸업",
        "회의실 예약", "명함", "법인서류", "법인카드", "증명서", "급여", "계약서",
        "채용", "면접", "인수인계", "성과급", "성과금", "보상", "인센티브",
        "vpn", "프린터", "잔디", "다우오피스", "노트북", "비밀번호 초기화", "계정 초기화",
        "복지", "사내근로복지", "피플팀", "교육 신청",
        "수습", "수습기간", "수습 기간", "야근", "식대", "재택근무", "재택",
        "4대보험", "연말정산", "워크샵", "동호회", "멘토링", "승진",
        "출퇴근", "점심시간",
        "모니터", "키보드", "마우스", "외장하드", "포맷",
        "장애 신고", "시스템 장애", "보안 프로그램",
        # Company info
        "사업자", "등록번호", "법인번호", "법인등록", "대표자", "대표이사",
        "조직도", "회사 정보", "회사정보", "기업 정보", "기업정보",
        # ⚠️ 업무 문서·절차 키워드는 여기(_TEAM_KEYWORDS)에 넣지 마라.
        # 이 목록을 쓰는 아래쪽 검사에는 **데이터 가드가 없어서** "티어별 매출" 같은
        # 데이터 질문까지 notion 으로 새어나간다. 대신 _TEAM_SPECIFIC 에 넣으면
        # _DATA_OVERRIDE 가드가 걸려 데이터 질문은 bigquery 로 남는다.
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

    # ── 이 서비스 자신의 기능을 묻는 질문 ────────────────────────────────────
    # ⛔ 이런 질문은 **사내 문서에도 CS Q&A 에도 답이 없다.** 실측(2026-08-13):
    #    "보고서 기능은 어떤 때 쓰면 좋아?" → notion 으로 새서 사내 문서를 뒤졌고,
    #    "보고서 사용법 알려줘" → '사용법' 때문에 **확신을 갖고 cs**(제품 사용법)로 갔다.
    #    확신 분류라 LLM 재판정도 못 탄다 — 조용히 엉뚱한 답이 나가는 형태다.
    # 대응은 키워드 추가가 아니라 **대상 판정**이다: 질문의 대상이 우리 자신인가.
    _APP_SELF = ["셀라", "cella", "이 시스템", "이시스템", "이 서비스", "이 앱",
                 "챗봇", "어시스턴트", "너는", "너가", "네가", "니가", "당신"]
    _FEATURE_WORD = ["기능", "사용법", "쓰는 법", "쓰는법", "만드는 법", "만드는 방법",
                     "어떻게 써", "어떻게 쓰", "뭘 할 수", "뭐 할 수", "무엇을 할 수"]

    def _is_self_feature_question(self, q: str) -> bool:
        """우리 서비스 자신의 기능을 설명해 달라는 질문인가.

        ⚠️ **외부 플랫폼·사내 툴 이름이 있으면 아니다** — "틱톡샵 접속 방법"·"잔디 사용법"
           은 사내 문서(notion)가 맞다. "센텔라 앰플 사용법"도 CS 가 맞다.
        """
        if any(p in q for p in self._PLATFORM_TOOL_NAMES):
            return False
        try:
            # 보고서 기능 질문은 보고서 쪽 규칙이 이미 정의해 뒀다 — 한 곳에서만 정한다
            from app.reports.registry import _DATA_NOUN, _REPORT_META
            if _REPORT_META.search(q):
                return True
            data_noun = bool(_DATA_NOUN.search(q))
        except Exception:
            data_noun = False
        if not any(s in q for s in self._APP_SELF):
            return False
        # 자기 얘기이면서 기능을 묻거나, 데이터 명사가 하나도 없으면 설명 요구다
        return any(f in q for f in self._FEATURE_WORD) or not data_noun

    # Compound notion keywords that take priority over _DATA_KEYWORDS exclusion
    # e.g. "반품 정책 알려줘" → notion (not blocked by "반품" in data keywords)
    _COMPOUND_NOTION = ["반품 정책", "반품정책"]

    # Wave 2: Hard-override keywords — always direct, no exceptions
    _DIRECT_OVERRIDE = [
        # Greetings / short social
        "안녕", "하이", "hello", "hi", "감사", "고마워", "ㅎㅇ", "ㅋㅋ", "ㅎㅎ",
        # Company identity (주의: "회사 매출" 같은 데이터 질문과 구분 필요 — _DATA_OVERRIDE_GUARD로 방어)
        "뭐하는", "소개", "누가 만들", "주인",
        # External topics (never route to BQ/Notion)
        "부동산", "주식", "투자", "아파트", "전세", "월세", "대출", "연봉", "이직",
        "비트코인", "코인", "암호화폐", "주가", "상장",
        "항공", "비행기", "호텔", "숙소", "여행지", "맛집",
        "날씨 알려", "오늘 날씨",
        # Fun / chitchat
        "재밌", "농담", "웃긴", "심심",
    ]

    # 위 override 키워드가 있어도 데이터 키워드가 있으면 bigquery로 허용
    _DATA_OVERRIDE_GUARD = [
        "매출", "판매", "수량", "주문", "광고", "마케팅", "인플루언서",
        "비용", "실적", "국가별", "월별", "분기별", "채널별", "비율", "차트", "그려",
    ]

    def _keyword_classify(self, query: str) -> str:
        """(호환용) 라우트만 반환 — 확신 플래그가 필요하면 _keyword_classify_ex."""
        return self._keyword_classify_ex(query)[0]

    def _keyword_classify_ex(self, query: str) -> tuple:
        """키워드 분류 + 확신 플래그 → (route, confident).

        Priority: Hard overrides > System tasks > Full data request > How-to (Notion) > Notion (explicit) > GWS > CS > Data > External > Direct

        confident=False 는 "특정 의도 키워드에 걸려서가 아니라 **폴스루/가드 강등**으로
        direct 가 됐다"는 뜻이다. 호출부는 이 경우 LLM 분류를 기본값으로 쓴다
        (2026-08-06 LLM 우선 하이브리드 — 키워드 목록에 단어가 없어 direct 로 새던
        사고 4건이 계기. 명백한 케이스만 키워드로 끝내 지연·비용을 아낀다).
        """
        # Open WebUI system tasks (title/tag/follow-up) → direct, skip BQ false routing
        if query.strip().startswith("### Task:"):
            return ("direct", True)

        q = query.lower()

        # 5자 미만 초단문 — 인사일 수도, "B2B" 같은 후속 조건일 수도 있다 → LLM 판단
        if len(q.strip()) < 5:
            return ("direct", False)

        # Wave 2: Hard-override to direct (greetings, external topics, chitchat)
        # 단, 데이터 키워드가 있으면 override 건너뜀 ("회사 1분기 매출" 등)
        if any(kw in q for kw in self._DIRECT_OVERRIDE):
            if not any(kw in q for kw in self._DATA_OVERRIDE_GUARD):
                return ("direct", True)

        # Capability questions ("이미지 분석 가능해?", "차트 그릴 수 있어?") → direct
        # 단, PEOPLE/HR·팀 키워드가 있으면 건너뜀 ("육아휴직 쓸 수 있어" 등은 팀 자료 질문)
        if any(p in q for p in self._CAPABILITY_PATTERNS):
            if not any(kw in q for kw in self._TEAM_KEYWORDS):
                return ("direct", True)

        # 이 서비스 **자신의 기능**을 묻는 질문 → direct. 사내 문서에도 CS Q&A 에도 답이 없다
        if self._is_self_feature_question(q):
            return ("direct", True)

        # Full data request → always bigquery (handled by _handle_bigquery → _handle_fulldata_request)
        if any(kw in q for kw in self._FULLDATA_KEYWORDS):
            return ("bigquery", True)

        # Team/HR resource check — BEFORE howto/notion to catch HR queries
        # BUT if strong data keywords present → bigquery takes priority
        _TEAM_SPECIFIC = ["jbt ", "bcm ", "east ", "west ", "bea ", "bxm ", "플래그십",
                          "팀 자료", "팀별 자료", "db hub", "데이터 허브",
                          "연차", "휴가", "휴일대체", "퇴사", "퇴직금", "경조", "졸업",
                          "성과급", "성과금", "보상", "인센티브",
                          "회의실 예약", "명함", "법인서류", "채용", "면접",
                          "vpn", "프린터", "피플팀", "복지", "교육 신청",
                          "잔디", "다우오피스", "급여", "증명서", "계약서",
                          # 업무 문서·절차 (데이터 키워드가 함께 있으면 아래 가드가 bigquery 로 보낸다)
                          "가이드라인", "매뉴얼", "메뉴얼", "가격표", "단가표", "공급가",
                          "등록 절차", "등록절차", "신청 절차", "신청절차", "출장", "erp"]
        _DATA_OVERRIDE = ["매출", "비용", "합계", "월별", "조회수", "저장수", "좋아요수",
                          "협업건", "유가 협업", "무가 협업", "시딩", "인플루언서",
                          "광고비", "roas", "ctr", "cpv", "cpe", "전환", "클릭"]
        has_team = any(kw in q for kw in _TEAM_SPECIFIC)
        has_data = any(kw in q for kw in _DATA_OVERRIDE)
        if has_team and not has_data:
            return ("notion", True)

        # How-to / guide questions about platforms → Notion (not BigQuery)
        if any(kw in q for kw in self._HOWTO_KEYWORDS):
            return ("notion", True)
        if any(kw in q for kw in self._HOWTO_BROAD_KEYWORDS):
            if any(p in q for p in self._PLATFORM_TOOL_NAMES):
                return ("notion", True)

        # Pre-compute data keyword match (used in Notion guard + later routing)
        has_data = any(kw in q for kw in self._DATA_KEYWORDS)

        # Notion check — but defer to bigquery when strong data keywords present
        if any(kw in q for kw in self._NOTION_KEYWORDS):
            if any(kw in q for kw in self._COMPOUND_NOTION):
                return ("notion", True)
            if not has_data:
                return ("notion", True)

        # GWS check — highest priority for personal workspace queries
        # 단, "화상회의 프로그램 뭐 써" 같은 툴 식별 질문은 개인 데이터 검색이 아니므로 제외
        _TOOL_IDENTITY_PATTERNS = ["뭐 써", "뭐써", "뭐 사용해", "뭐사용해", "어떤 프로그램",
                                     "어떤 툴", "무슨 프로그램", "무슨 툴"]
        # 프로모션 캘린더 도입(2026-08-11)으로 '일정·스케줄·캘린더'가 두 도메인에 걸친다.
        # "인도네시아 프로모션 일정" 은 BigQuery 프로모션 테이블이지 개인 구글 캘린더가 아니다.
        # 키워드를 늘리는 대신 **확신 플래그의 조건을 좁힌다** (오분류 대응 원칙).
        # 단 "내 캘린더"·메일·드라이브처럼 개인 워크스페이스가 명시되면 그대로 GWS.
        _PROMO_TERMS = ["프로모션", "프로모", "행사", "기획전", "메가와리", "메가세일",
                        "런칭", "출시 일정", "판촉"]
        _PERSONAL_SCOPE = ["내 ", "제 ", "메일", "gmail", "드라이브", "drive",
                           "회의록", "회의", "미팅", "스프레드시트", "구글시트"]
        if any(kw in q for kw in self._GWS_KEYWORDS):
            _promo_ctx = (any(t in q for t in _PROMO_TERMS)
                          and not any(p in q for p in _PERSONAL_SCOPE))
            if not any(p in q for p in _TOOL_IDENTITY_PATTERNS) and not _promo_ctx:
                return ("gws", True)

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
                "예스스타일", "스타일코리안", "졸스", "소시올라", "올리브영",
                "순위", "랭킹", "플랫폼", "카테고리", "노출",
                "재구매", "코호트",
                "영업이익", "매출총이익", "판관비", "손익", "이익률", "영업이익률",
                # 판매 표현 — "올해 가장 많이 팔린 제품 top5"가 '올해'(검색 키워드) 때문에
                # direct 로 새던 구멍 (2026-08-06 골든셋 첫 런에서 발견)
                "팔린", "팔리", "베스트셀러", "판매량", "인기 제품",
            ]
            if not any(t in q for t in _SKIN1004_TERMS):
                return ("direct", False)  # 가드 강등 — 확신 없음, LLM 재판정 대상

        # CS check — product Q&A, ingredients, usage, skincare
        # When both CS + DATA keywords present, only prefer BQ for strong analytics keywords
        # (매출, 수량, 주문 etc.), not ambiguous ones like "라인", "제품 목록"
        _STRONG_DATA = [
            "매출", "수량", "주문", "sales", "revenue",
            "판매량", "판매 수량", "세일즈", "매상", "갯수", "개수",
            "국가별", "월별", "분기별", "대륙별", "플랫폼별", "연도별", "채널별", "카테고리별", "카테고리",
            # Region keywords — when paired with product names → sales data intent, not CS
            "남미", "북미", "동남아", "유럽", "중동", "cis", "아시아",
            "인도네시아", "말레이시아", "태국", "베트남", "필리핀", "미국", "일본", "중국",
            "재고", "집계", "합계", "통계", "데이터", "조회",
            "차트", "그래프", "그려", "시각화", "도표", "플롯", "그래프로", "차트로", "시각화해",
            "막대그래프", "원형그래프", "꺾은선", "파이차트", "바차트", "그려줘",
            "top", "순위", "랭킹", "성장률", "증감", "추이",
            "비교", "비중", "비율", "전년 대비", "전년대비", "대비",
            "베스트셀러", "인기 제품", "가장 많이 팔",
            # Marketing strong data keywords
            "광고비", "광고 비용", "마케팅비", "마케팅 비용", "퍼포먼스", "시딩", "총액", "메가와리", "ROAS", "CTR",
            "노출수", "클릭수", "전환율", "전환수",
            "인플루언서", "리뷰", "GMV",
            "신규", "업체", "거래처", "바이어", "b2b", "b2c",
            # Meta ads — override CS even when "skin1004" present
            "광고", "메타 광고", "메타광고", "활성 광고", "비활성 광고",
            "분포", "현황", "건수",
            # Repurchase
            "재구매", "재구매율", "리텐션", "코호트", "재방문",
            # Financial P&L (FI_LLM_Flat)
            "영업이익", "매출총이익", "매출원가", "판관비", "손익", "이익률", "원가율", "광고선전비",
        ]
        # Team resource check — team data lookups (before CS to avoid overlap)
        if any(kw in q for kw in self._TEAM_KEYWORDS):
            return ("notion", True)

        has_strong_data = any(kw in q for kw in _STRONG_DATA)
        if any(kw in q for kw in self._CS_KEYWORDS) and not has_strong_data:
            return ("cs", True)
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
                    return ("bigquery", True)
            return ("multi", True)

        if has_data:
            # Guard: data keywords present but NO SKIN1004 business context → direct
            # e.g. "육룡이 나르샤 평점" → "평점" matches data but not about our products
            _BIZ_CONTEXT = [
                "skin1004", "스킨", "센텔라", "히알루", "커먼랩스", "좀비뷰티", "랩인네이처", "크레이버",
                "매출", "수량", "주문", "판매", "재고", "실적", "매상", "세일즈",
                "쇼피", "아마존", "틱톡", "라자다", "큐텐", "shopify", "쇼피파이", "올리브영",
                "광고비", "광고", "메타", "roas", "ctr", "마케팅비", "마케팅 비용", "마케팅", "비용", "노출수", "클릭수",
                "퍼포먼스", "시딩", "총액", "얼마", "메가와리",
                "인플루언서", "반품", "환불", "b2b", "b2c", "거래처", "업체",
                # 거래 이력 질문 ("첫 거래일자", "거래 시작일") — 국가명만 있고 매출/판매
                # 단어가 없어도 우리 거래 데이터 질문이다 (2026-08-06 direct 오분류 사고)
                "거래일", "거래 시작", "첫 거래", "거래 이력", "거래 내역", "수출",
                # 판매 표현 — "가장 많이 팔린 제품" (2026-08-06 골든셋 첫 런에서 발견)
                "팔린", "팔리", "베스트셀러", "판매량", "인기 제품",
                "리뷰", "평점", "별점", "스마트스토어", "네이버스토어",
                "예스스타일", "yesstyle", "스타일코리안", "졸스", "소시올라",
                "재구매", "재구매율", "순위 상승", "순위 변동",
                "영업이익", "매출총이익", "매출원가", "판관비", "손익", "이익률", "원가율", "광고선전비",
                "국가별", "월별", "팀별", "채널별", "제품별", "브랜드별", "사업부",
                "데이터", "테이블", "컬럼", "있나요", "존재", "포함",
                "revenue", "platform", "campaign", "google ads", "cost",
                "impression", "conversion", "cpc", "cpv", "cpe",
            ]
            if any(t in q for t in _BIZ_CONTEXT):
                return ("bigquery", True)
            return ("direct", False)  # 데이터 단어는 있는데 사업 맥락 불명 — LLM 재판정
        return ("direct", False)  # 아무 키워드도 안 걸림 — LLM 재판정

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

    # ── BQ 의도 확인 (grill-me style) ──────────────────────────────
    # 기간·채널이 모두 빠진 매우 짧은 BQ 쿼리에서만 활성화.
    _BQ_PERIOD_KW = [
        "이번달", "이번 달", "지난달", "지난 달", "저번달", "저번 달",
        "이번주", "지난주", "이번 분기", "지난 분기",
        "올해", "작년", "재작년", "올 해", "작 년",
        "2022", "2023", "2024", "2025", "2026",
        "1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월",
        "q1", "q2", "q3", "q4", "분기", "반기", "최근", "최근 3", "최근 6", "최근 1년",
        "전월", "전년", "전주", "주간", "월간", "연간", "누적",
    ]
    _BQ_CHANNEL_KW = [
        "아마존", "amazon", "쇼피", "shopee", "큐텐", "qoo10", "틱톡", "tiktok",
        "라자다", "lazada", "shopify", "쇼피파이", "스마트스토어", "네이버",
        "올리브영", "예스스타일", "전체", "all", "합산", "통합", "b2b", "b2c",
        "글로벌", "한국", "일본", "미국", "동남아", "유럽", "중동",
    ]

    def _bq_needs_clarification(
        self, query: str, conversation_context: str, source_explicit: bool = False
    ) -> bool:
        """기간·채널 모두 없는 짧은 BQ 쿼리면 True.

        ⚠️ 사용자가 `@@`나 데이터소스 칩으로 소스를 직접 고른 경우는 되묻지 않는다.
        이미 범위를 좁히는 행동을 한 사람에게 "아마존? 큐텐? 쇼피?"라고 되묻는 것은
        무의미하고(그 소스에 없는 판매몰을 묻게 된다), 스트리밍 경로는 애초에
        되묻지 않아 같은 질문이 경로에 따라 다르게 동작했다 (2026-08-11 발견).
        """
        if source_explicit:
            return False
        if len(query.strip()) > 40:
            return False
        if conversation_context:
            return False
        q = query.lower()
        has_period = any(kw in q for kw in self._BQ_PERIOD_KW)
        has_channel = any(kw in q for kw in self._BQ_CHANNEL_KW)
        return not has_period and not has_channel

    async def _ask_bq_clarification(self, query: str, model_type: str) -> str:
        """Flash로 1~2개 의도 확인 질문 생성."""
        flash = get_flash_client()
        prompt = f"""SKIN1004 데이터 분석 AI입니다. 사용자가 "{query}라고 질문했습니다.
더 정확한 답변을 위해 필요한 정보를 1~2개 질문으로 물어보세요.
- 기간 (이번달? 올해? 특정 분기?)
- 채널/플랫폼 (전체? 아마존? 큐텐? 쇼피?)
중 빠진 것만 물어보세요. 마크다운 없이 자연스럽게 짧게 (2줄 이내)."""
        try:
            return await asyncio.to_thread(flash.generate, prompt, temperature=0.3)
        except Exception:
            return "어떤 기간과 채널(플랫폼)을 기준으로 보고 싶으신가요? (예: 이번달 아마존, 올해 전체 등)"

    async def _handle_bigquery(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
        brand_filter: Optional[str] = None,
        can_view_fi: bool = False,
        enabled_sources: Optional[List[str]] = None,
        source_explicit: bool = False,
    ) -> dict:
        """BigQuery Agent with conversation context.

        Falls back to a helpful data-error message if SQL generation fails,
        preserving context that this was a SKIN1004 internal data query.
        """
        # 의도 확인: 기간·채널 없는 짧은 쿼리 → 먼저 물어보기
        # (소스를 직접 고른 질문은 제외 — 스트리밍 경로와 동작을 맞춘다)
        if self._bq_needs_clarification(
            query, conversation_context,
            source_explicit=source_explicit or enabled_sources is not None,
        ):
            clarify_q = await self._ask_bq_clarification(query, model_type)
            return {"source": "bigquery", "answer": clarify_q}

        # Check for "full data" follow-up request
        if self._is_fulldata_request(query, conversation_context):
            return await self._handle_fulldata_request(
                query,
                messages,
                conversation_context,
                model_type,
                can_view_fi=can_view_fi,
            )

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
                can_view_fi=can_view_fi,
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
                    can_view_fi=can_view_fi,
                )
                if "오류" in answer and ("SQL" in answer or "생성되지" in answer):
                    logger.warning("bigquery_sql_failed_fallback_to_direct", query=query[:100])
                    return await self._handle_bigquery_fallback(
                        query, messages, conversation_context, model_type, user_email
                    )
            # 실시간 팩트 캡처 — 답변에서 재사용 가능한 사실 추출 → knowledge_wiki (fire-and-forget)
            _bq_task = asyncio.create_task(self._capture_bq_facts(query, answer))
            self._bg_tasks.add(_bq_task)
            _bq_task.add_done_callback(self._bg_tasks.discard)
            return {"source": "bigquery", "answer": answer + _maintenance_warning}
        except Exception as e:
            logger.error("orchestrator_bigquery_failed", error=str(e))
            return await self._handle_bigquery_fallback(
                query, messages, conversation_context, model_type, user_email
            )

    async def _capture_bq_facts(self, query: str, answer: str) -> None:
        """BQ 답변에서 팩트 추출 → knowledge_wiki 저장 (fire-and-forget)."""
        try:
            from app.knowledge.wiki_extractor import extract_and_save_from_qa
            await asyncio.to_thread(extract_and_save_from_qa, query, answer)
        except Exception as e:
            logger.debug("bq_fact_capture_failed", error=str(e)[:100])

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
        llm = get_llm_client(MODEL_CLAUDE)
        fallback_prompt = f"""사용자가 Craver 내부 매출/판매 데이터를 조회하려 했으나, 데이터베이스에서 조회에 실패했습니다.

사용자 질문: {query}

다음 규칙에 따라 답변하세요:
1. 요청한 데이터를 조회할 수 없었다는 점을 간결하게 안내하세요.
2. 질문을 좀 더 구체적으로 바꿔보라고 제안하세요 (예: 기간, 국가, 채널, 제품명 등을 명시).
3. 가능한 질문 예시를 2-3개 제시하세요.
4. 일반적인 인터넷 정보로 답변하지 마세요. 이것은 Craver 내부 데이터 질문입니다.
5. 한국어로 답변하세요.
6. "오류가 발생" 같은 표현 대신 "데이터를 조회하지 못했습니다" 등 부드러운 표현을 쓰세요."""

        try:
            answer = await asyncio.to_thread(llm.generate, fallback_prompt, temperature=0.3)
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
        can_view_fi: bool = False,
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
                can_view_fi=can_view_fi,
            )
            return {"source": "bigquery", "answer": answer}
        except Exception as e:
            logger.error("fulldata_request_failed", error=str(e))
            return {
                "source": "bigquery",
                "answer": "죄송합니다. 전체 데이터 조회 중 일시적인 문제가 발생했습니다.\n\n**해결 방법:**\n- 잠시 후 동일한 질문을 다시 시도해 주세요\n- 조회 범위를 좁혀보세요 (예: 특정 국가나 짧은 기간)\n- 원래 질문을 다시 입력하면 요약 결과를 먼저 확인할 수 있습니다",
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

    async def _handle_report(self, query: str, user_email: str,
                             explicit: bool = False) -> Optional[dict]:
        """등록된 보고서에 해당하는 질문이면 보고서를 만들고 요약을 돌려준다.

        해당 없으면 None — 평소 라우팅으로 흘러간다.

        보고서는 **본인과 본인이 지목해 공유한 사람만** 열람하므로 소유자를 확정할 수
        있어야 한다 (공유를 걸 수 있는 사람도 소유자뿐이다). 이메일로
        `users.id` 를 서버에서 조회한다 (JWT·프론트 값을 믿지 않는 기존 원칙과 같다).
        신원을 못 잡으면 만들지 않는다 — 주인 없는 보고서를 남기지 않기 위해서다.
        """
        from app.reports import registry, service

        # 보고서는 명시적으로 요청했을 때만 만든다 — `@@보고서` 지정(explicit) 이거나
        # 질문에 "보고서/리포트" 라고 적었을 때. `@@보고서` 는 문구를 보지 않는다
        if not explicit and not registry.wants_report(query):
            return None

        # ⚠️ 아래 건너뜀은 **사용자가 보고서를 달라고 했는데 일반 답변이 나가는** 상황이다.
        #    에러가 아니라 조용한 강등이라 아무도 모른다. INFO 로 남기면 프로덕션에서
        #    통째로 버려져(앱 INFO 0건) 영영 안 보인다 — 반드시 WARNING 이다 (CLAUDE.md).
        if not user_email:
            logger.warning("report_skipped_no_user", query=query[:80])
            return None

        from app.db.mariadb import fetch_one
        row = await asyncio.to_thread(
            fetch_one, "SELECT id FROM users WHERE email = %s", (user_email,))
        if not row:
            logger.warning("report_skipped_unknown_user", email=user_email[:40],
                           query=query[:80])
            return None

        try:
            result = await asyncio.to_thread(
                lambda: service.run(query, row["id"], explicit=explicit))
        except Exception as e:
            logger.warning("report_failed", error=str(e)[:300], query=query[:80])
            return {
                "source": "bigquery",
                "answer": "보고서를 만드는 중 문제가 발생했습니다. "
                          "잠시 후 다시 시도해주시고, 계속되면 DB팀에 알려주세요.",
            }
        if not result:
            # wants_report 가 True 였는데 여기까지 와서 비었다 = 라우팅이 갈린 것이다.
            # 조용히 흘려보내면 "보고서 달랬는데 왜 안 나오지"가 재현 불가로 남는다
            logger.warning("report_route_yielded_nothing", query=query[:80])
            return None

        logger.info("report_delivered", report_id=result["report_id"],
                    spec=result["spec"], sec=result.get("elapsed_sec"))
        return {"source": "bigquery", "answer": service.to_markdown(result),
                "report_id": result["report_id"]}

    async def _handle_model_rights(self, query: str, model_type: str, images=None) -> dict:
        """모델 초상권 질문 — 판정(기간·만료)은 DB 데이터가 하고 LLM 은 설명만 한다.

        시트 → MariaDB 적재본(model_rights)에서 오늘 날짜 기준 판정을 끝낸
        컨텍스트를 만들어 넘긴다. 잘못 쓰면 모델당 수백만 원짜리 실수라
        LLM 이 기간을 계산하게 두지 않는다 (전성분 핸들러와 같은 원칙).

        사진이 첨부되면 얼굴 인식(buffalo_l ONNX, 서브프로세스)으로 등록된 모델과
        대조해 인물을 식별한다 — 사진 일부(크롭)여도 얼굴만 보이면 매칭된다.
        서브프로세스인 이유: onnxruntime 세션 ~400MB 를 2GB WAS 앱에 상주시키지
        않기 위해 (실행 후 즉시 반환).
        """
        import asyncio as _asyncio

        from app.core.model_rights import get_rights_context

        face_note = ""
        if images:
            try:
                import json as _json
                import os as _os
                import sys as _sys
                import tempfile as _tmp

                from pathlib import Path as _P
                with _tmp.NamedTemporaryFile(suffix=".jpg", delete=False) as _tf:
                    _tf.write(images[0]["data"])
                    _img_path = _tf.name
                _script = str(_P(__file__).resolve().parent.parent.parent
                              / "scripts" / "identify_model_face.py")
                proc = await _asyncio.create_subprocess_exec(
                    _sys.executable, _script, _img_path,
                    stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.PIPE)
                out, err = await _asyncio.wait_for(proc.communicate(), timeout=120)
                _os.unlink(_img_path)
                _lines = [l for l in out.decode("utf-8", "replace").splitlines() if l.strip().startswith("{")]
                r = _json.loads(_lines[-1]) if _lines else {}
                if r.get("match"):
                    face_note = (f"[사진 인식 결과] 업로드된 사진 속 인물: **{r['match']}** "
                                 f"(유사도 {r['score']:.2f}, 얼굴 {r['n_faces']}개 감지)\n")
                    query = f"{r['match']} — {query}"
                elif r.get("maybe"):
                    face_note = (f"[사진 인식 결과] 확신은 낮지만 **{r['maybe']}** 로 보임 "
                                 f"(유사도 {r['score']:.2f}) — 확실하지 않으니 이름으로 재확인 권장\n")
                    query = f"{r['maybe']} — {query}"
                elif r.get("n_faces"):
                    face_note = (f"[사진 인식 결과] 얼굴은 감지했으나 등록된 모델({r.get('enrolled', 0)}명)과 "
                                 "일치하지 않음 — 모델 이름으로 질문해 주세요\n")
                else:
                    face_note = "[사진 인식 결과] 사진에서 얼굴을 찾지 못함\n"
                logger.info("model_face_identify", result={k: v for k, v in r.items() if k != "second"})
            except Exception as e:
                logger.error("model_face_identify_failed", error=str(e)[:200])
                face_note = "[사진 인식 결과] 인식 실패 — 모델 이름으로 질문해 주세요\n"

        try:
            ctx = await _asyncio.to_thread(get_rights_context, query)
        except Exception as e:
            logger.error("model_rights_failed", error=str(e)[:200])
            ctx = ""
        if ctx and face_note:
            ctx = face_note + "\n" + ctx
        if not ctx:
            return {"source": "direct", "answer": (
                "모델 초상권 데이터가 아직 적재되지 않았습니다. "
                "관리자에게 초상권 시트 동기화를 요청해 주세요.")}

        from app.core.llm import get_flash_client
        prompt = (
            "너는 마케팅팀의 모델 초상권 안내 담당이다. 아래 판정 데이터만 근거로 "
            "사용자 질문에 답하라.\n"
            "규칙: 판정(사용 가능/만료/불가)은 데이터에 이미 계산돼 있다 — 절대 스스로 "
            "기간을 재계산하지 마라. 만료·불가·불명 건은 반드시 담당자 문의를 안내하라. "
            "표로 정리하고, 마지막에 '기재된 매체·기간 외 사용 시 추가 초상권 비용 발생' "
            "경고를 짧게 붙여라.\n\n"
            f"## 초상권 판정 데이터\n{ctx}\n\n## 사용자 질문\n{query}"
        )
        try:
            llm = get_flash_client()
            answer = await _asyncio.to_thread(llm.generate, prompt, temperature=0.1)
        except Exception as e:
            logger.error("model_rights_llm_failed", error=str(e)[:200])
            answer = f"### 모델 초상권 현황 (자동 판정)\n\n```\n{ctx}\n```"
        return {"source": "direct", "answer": answer}

    async def _handle_ingredient_query(self, query: str, intent, model_type: str) -> dict:
        """성분 기준 제품 질문을 전성분 데이터로 답한다.

        LLM 이 SQL 을 짜게 두지 않는다. 성분 판정은 결정적이어야 하고, 제품명
        문자열 매칭으로 흘러가면 처음의 오답(성분이 든 제품이 '미포함 1위')이
        그대로 재현되기 때문이다. 여기서 제품 목록을 확정한 뒤 그 목록으로만
        집계한다.
        """
        import asyncio as _asyncio

        ingredient, contains = intent
        from app.core.ingredients import resolve_products_by_ingredient

        try:
            res = await _asyncio.to_thread(resolve_products_by_ingredient, ingredient, contains)
        except Exception as e:
            logger.error("ingredient_resolve_failed", error=str(e)[:200])
            return {"source": "bigquery", "answer": INGREDIENT_EXCLUSION_MESSAGE}

        products = res.get("products") or []
        if not res.get("total_known"):
            # 적재가 안 됐거나 비어 있으면 예전처럼 정직하게 거절한다
            return {"source": "bigquery", "answer": INGREDIENT_EXCLUSION_MESSAGE}

        label = "포함" if contains else "미포함"
        if not products:
            return {"source": "bigquery", "answer": (
                f"**{ingredient} {label} 제품이 없습니다.**\n\n"
                f"{res['coverage_note']}"
            )}

        # 확정된 제품 목록으로만 집계 — 성분 판정은 이미 끝났다
        from app.core.bigquery import get_bigquery_client

        inlist = ", ".join("'" + p.replace("'", "\\'") + "'" for p in products[:900])
        sql = (
            "SELECT Product AS product_name, SUM(Total_Qty) AS qty\n"
            "FROM `skin1004-319714.Sales_Integration.Product`\n"
            f"WHERE Product IN ({inlist})\n"
            "  AND Date >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH)\n"
            "  AND Product != 'Sachet'\n"
            "GROUP BY product_name ORDER BY qty DESC LIMIT 20"
        )
        try:
            bq = get_bigquery_client()
            rows = await _asyncio.to_thread(bq.execute_query, sql, 180.0, 100)
        except Exception as e:
            logger.error("ingredient_bq_failed", error=str(e)[:200])
            return {"source": "bigquery", "answer": (
                f"{ingredient} {label} 제품 {len(products)}종을 찾았으나 판매 데이터 조회에 실패했습니다."
            )}

        lines = [
            f"### 🧪 {ingredient} {label} 제품 판매수량 (최근 12개월)",
            "",
            f"전성분 기준으로 **{len(products)}종**이 해당합니다.",
            "",
            "| 순위 | 제품 | 판매수량 |",
            "| ---: | :--- | ---: |",
        ]
        for i, r in enumerate(rows[:15], 1):
            q = int(r.get("qty") or 0)
            lines.append(f"| {i} | {r.get('product_name')} | {q:,} |")
        lines += [
            "",
            f"> ⚠️ {res['coverage_note']}",
        ]
        return {"source": "bigquery", "answer": "\n".join(lines)}

    async def _handle_qdrant(self, query, messages, conversation_context, model_type, user_email="", team_key=None):
        from app.agents.qdrant_agent import run as run_qdrant
        # 벡터 임베딩에는 순수 query만 사용 — 대화 히스토리를 넣으면 검색 정확도 하락
        try:
            result = await run_qdrant(query, team_key=team_key, model_type=model_type)
            return {"source": "notion", "answer": result}
        except Exception as e:
            return {"source": "notion", "answer": f"사내 문서 검색 중 오류: {str(e)}"}

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
            return {"source": "cs", "answer": "죄송합니다. CS 데이터 조회 중 일시적인 문제가 발생했습니다.\n\n**다시 시도해 주세요.** 제품명이나 성분명을 포함하면 더 정확한 결과를 얻을 수 있습니다.\n\n> 💡 **이런 식으로 질문해 보세요**\n> - \"센텔라 앰플 사용법 알려줘\"\n> - \"마다가스카르 센텔라 성분이 뭐야?\"\n> - \"히알루 시카 수분크림 특징\""}


    async def _handle_team(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
        enabled_team_resources: Optional[Dict[str, list]] = None,
    ) -> dict:
        """Team Resource Agent — 팀별 자료 검색."""
        from app.agents.team_agent import run as run_team_agent

        contextualized_query = query
        if conversation_context:
            contextualized_query = f"[이전 대화]\n{conversation_context}\n\n[현재 질문]\n{query}"
        try:
            result = await run_team_agent(contextualized_query, model_type=model_type, allowed_resources=enabled_team_resources)
            return {"source": "team", "answer": result}
        except Exception as e:
            logger.error("orchestrator_team_failed", error=str(e))
            return {"source": "team", "answer": "팀별 자료 검색 중 일시적인 문제가 발생했습니다.\n\n**다시 시도해 주세요.** 검색 키워드를 바꾸거나 더 구체적으로 질문하면 도움이 됩니다.\n\n> 💡 **이런 식으로 질문해 보세요**\n> - \"HR 연차 규정 알려줘\"\n> - \"마케팅팀 브랜드 가이드라인\"\n> - \"영업팀 거래처 목록\""}

    async def _multi_prepare(
        self,
        query: str,
        conversation_context: str,
        model_type: str,
        brand_filter: Optional[str] = None,
        can_view_fi: bool = False,
        enabled_sources: Optional[List[str]] = None,
    ):
        """Shared prep for _handle_multi/_handle_multi_stream: run web search +
        BigQuery in parallel, return everything the synthesis step needs.

        Returns:
            (web_context, bq_answer, sub_results, ctx_section, today)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        sub_results = {}

        # 대화 맥락: 참조형 질문("아까 그 데이터", "위 md처럼") 해석에 필요 (2026-06-11, fid 36)
        _ctx = (conversation_context or "").strip()
        ctx_section = f"\n## 이전 대화 맥락\n{_ctx[:3000]}\n" if _ctx else ""

        # --- Prepare prompts ---
        search_prompt = f"""질문과 관련된 최신 외부 정보를 검색하여 핵심만 간결히 정리하세요.
내부 매출 데이터는 제외. 시장 동향, 뉴스, 경쟁 환경 위주.
오늘: {today}
질문: {query}
항목별로 간결하게 정리:"""

        data_query_prompt = f"""사용자의 복합 질문에서 BigQuery 매출/주문 데이터 조회에 필요한 부분만 추출하세요.
외부 분석(날씨, 시장, 원인 등)은 제외하고, 순수 데이터 조회 질문으로 변환하세요.
{ctx_section}
원래 질문: {query}

예시:
- "날씨가 인도네시아 매출에 영향?" → "인도네시아 최근 매출 데이터 조회"
- "경쟁사 대비 태국 쇼피 매출 분석" → "태국 쇼피 매출 데이터 조회"
- "환율 변동으로 베트남 매출 하락 원인" → "베트남 최근 월별 매출 추이"

질문에 "아까", "그", "위에" 같은 참조가 있으면 위 대화 맥락에서 대상을 찾아 구체적인 질문으로 변환하세요.
데이터 조회 질문만 한 줄로 작성:"""

        # --- Steps 1+2: Google Search + BigQuery in parallel (v6.4) ---
        # v6.5: Use Flash for search (was Pro — 60-80s → 30-40s)
        def _web_search_sync():
            flash = get_flash_client()
            return flash.generate_with_search(search_prompt, temperature=0.2, max_output_tokens=4096)

        async def _bq_query_async():
            # Maintenance: only hard-block on manual maintenance
            from app.core.safety import get_maintenance_manager
            mm = get_maintenance_manager()
            if mm.active and mm.manual:
                return "", "데이터 점검 중으로 매출 데이터 조회가 일시 중단되었습니다."

            flash = get_flash_client()
            data_query = await asyncio.to_thread(flash.generate, data_query_prompt, temperature=0.0)
            data_query = data_query.strip()
            logger.info("multi_data_query_rewritten", original=query[:100], rewritten=data_query[:100])
            from app.agents.sql_agent import run_sql_agent
            answer = await run_sql_agent(
                data_query,
                conversation_context=conversation_context,
                model_type=model_type,
                brand_filter=brand_filter,
                enabled_sources=enabled_sources,
                can_view_fi=can_view_fi,
            )
            return data_query, answer

        web_context = ""
        bq_answer = ""

        try:
            gathered = await asyncio.gather(
                asyncio.to_thread(_web_search_sync),
                _bq_query_async(),
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

        return web_context, bq_answer, sub_results, ctx_section, today

    def _build_multi_synthesis_prompt(
        self, query: str, ctx_section: str, bq_answer: str, web_context: str, today: str
    ) -> str:
        """Build the multi-route synthesis prompt (shared by _handle_multi/_handle_multi_stream)."""
        return f"""당신은 Craver의 데이터 분석 전문 AI입니다.
내부 데이터와 외부 정보를 종합하여 **분석 보고서 형식**으로 답변하세요.

## 사용자 질문
{query}
{ctx_section}
{"사용자 질문이 이전 대화를 참조하면('아까', '그', '위 형식처럼' 등) 위 대화 맥락을 반영해 답변하세요." if ctx_section else ""}
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
*분석 기준: Craver 내부 데이터 + Google 검색 ({today})*

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

    async def _handle_multi(
        self,
        query: str,
        messages: List[Dict[str, str]],
        conversation_context: str,
        model_type: str,
        user_email: str = "",
        brand_filter: Optional[str] = None,
        can_view_fi: bool = False,
        enabled_sources: Optional[List[str]] = None,
        source_explicit: bool = False,
    ) -> dict:
        """Multi-source analysis: internal data (BigQuery) + external info (Google Search).

        v6.4: Steps 1+2 run in parallel via asyncio.to_thread, synthesis uses Flash.

        source_explicit 은 `@@` 단일 소스 경로에서 넘어온다 (되묻기 억제용).
        내부 BQ 조회는 _multi_prepare 가 sql_agent 를 직접 부르므로 되묻기 자체가 없다.
        """
        web_context, bq_answer, sub_results, ctx_section, today = await self._multi_prepare(
            query, conversation_context, model_type, brand_filter, can_view_fi, enabled_sources
        )
        synthesis_prompt = self._build_multi_synthesis_prompt(query, ctx_section, bq_answer, web_context, today)

        # --- Step 3: Synthesize with Flash for speed (v6.4) ---
        flash = get_flash_client()
        try:
            answer = await asyncio.to_thread(flash.generate, synthesis_prompt, temperature=0.3)
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

    async def _handle_multi_stream(
        self,
        query: str,
        conversation_context: str,
        model_type: str,
        brand_filter: Optional[str] = None,
        can_view_fi: bool = False,
        enabled_sources: Optional[List[str]] = None,
    ):
        """Streaming variant of _handle_multi — yields answer text chunks.

        Reuses the same parallel web-search + BigQuery prep as _handle_multi;
        only the final synthesis call streams instead of blocking.
        """
        web_context, bq_answer, _sub_results, ctx_section, today = await self._multi_prepare(
            query, conversation_context, model_type, brand_filter, can_view_fi, enabled_sources
        )
        synthesis_prompt = self._build_multi_synthesis_prompt(query, ctx_section, bq_answer, web_context, today)

        flash = get_flash_client()
        from app.core.stream_bridge import stream_sync_generator
        try:
            async for chunk in stream_sync_generator(lambda: flash.generate_stream(synthesis_prompt, temperature=0.3)):
                yield chunk
        except Exception as e:
            logger.warning("multi_synthesize_stream_failed", error=str(e))
            parts = []
            if bq_answer:
                parts.append(f"## 내부 데이터\n{bq_answer}")
            if web_context:
                parts.append(f"## 외부 정보\n{web_context}")
            yield "\n\n".join(parts) if parts else "분석에 필요한 정보를 수집하지 못했습니다."

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
            answer = await asyncio.to_thread(flash.generate, prompt_with_context, temperature=0.3)
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
            answer = await asyncio.to_thread(flash.generate, prompt, temperature=0.3)
            return {"source": "direct", "answer": answer}
        except Exception as e:
            logger.warning("followup_generation_failed", error=str(e))
            return {"source": "direct", "answer": '{"follow_ups": []}'}

    def _build_direct_system_prompt(self) -> str:
        """Build system prompt for direct LLM route (shared by _handle_direct and route_and_stream).

        Deliberately excludes the current date — the caller appends it as a
        separate, uncached block so this (large, static) prompt stays byte-identical
        across requests and reuses Anthropic's prompt cache. See ClaudeClient._wrap_system.
        """
        model_name = _model_display_name()
        # Import the full system prompt from _handle_direct inline (it's too long to duplicate)
        # We reference the same structure
        return f"""당신은 Craver의 AI 어시스턴트입니다. ({model_name} 기반)
이 시스템은 **임재필(Jeffrey Im)**이 기획·개발하여 운영하고 있습니다.

{LANGUAGE_DETECTION_RULE}

## 회사 소개
(주)크레이버코퍼레이션(Craver Corporation) — "WHAT DO YOU CRAVE?"
공동대표: 전항일/천주혁. 설립 2014년 8월. 서울 강남구 테헤란로 129.
브랜드: SKIN1004(스킨천사, 메인)과 umma(우마)가 주력, CommonLabs·Zombie Beauty는 소규모. 마다가스카르 센텔라 기반 클린 뷰티(Cruelty-Free & Vegan).
글로벌 K-뷰티 리더. Shopee/YesStyle/StyleKorean 카테고리 1위. 리테일: Costco, ULTA, H&M, 올리브영.
진출: 한국, 북미, 유럽, 동남아, 일본, 중국, 중남미, 중동.
"우리 회사" = Craver Corporation / SKIN1004. 회사 질문은 이 정보로 답변(웹검색 불필요).

## 대표 제품 (공식 제품명 — 제품 질문 시 이 목록의 명칭만 사용)
- **마다가스카르 센텔라 100 앰플** (100ml/55ml) — 시그니처 베스트셀러
- 마다가스카르 센텔라 토닝 토너 (210ml)
- 마다가스카르 센텔라 라이트 클렌징 오일
- 마다가스카르 센텔라 수딩 크림 (75ml)
- 마다가스카르 센텔라 앰플 폼 (클렌저)
- 마다가스카르 센텔라 퀵 카밍 패드
- 마다가스카르 센텔라 히알루-시카 워터핏 선 세럼 (50ml)
- 센텔라 에어핏 선크림 라이트/플러스 (무기자차)
- 마다가스카르 센텔라 톤 브라이트닝 캡슐 앰플 / 톤 브라이트닝 크림
- 포어마이징 라인: 퀵 클레이 스틱 마스크, 프레시 앰플, 딥 클렌징 폼, 라이트 젤 크림
- 랩인네이처 라인: 레티놀/나이아신아마이드/마트릭실 부스팅 샷 앰플
- 기타 라인: 프로바이오시카, 티트리카, 히알루테카, 센텔라테카
⛔ **제품명 창작 금지**: 위 목록에 없는 제품명을 임의로 조합·생성하지 마세요. 확실하지 않으면 라인 이름까지만 언급하고, 정확한 제품 목록·매출은 데이터 조회(BigQuery)를 제안하세요.

## 시스템 기능
사용자가 "이 시스템/셀라가 뭘 할 수 있냐"고 물으면 **아래 목록으로 답하세요.**
⛔ 여기 적힌 기능을 "없다"고 답하지 마세요 — 실제로 있는 기능입니다.
- BigQuery SQL 실행 (매출/수량/순위) · 차트 자동 생성 · 이미지 분석
- Notion 사내 문서 검색 · CS 제품 Q&A · Google 실시간 웹검색
- Google Workspace 연동 (Gmail/Calendar/Drive)
- **보고서** — 질문 하나로 총량·추세·구성·전년 대비·순위 같은 절을 조합한 문서를 만듭니다.
  - **만들어지는 조건**: 질문에 "보고서"(또는 "리포트")라고 적거나, 입력창에서 `@@보고서`를 고를 때만.
    조회를 8~12회 돌아 10~60초가 걸리는 기능이라 원할 때만 나가도록 해 두었습니다.
  - 채팅에는 요약 몇 줄과 [보고서 열기] 링크가 옵니다. 질문 속 국가·팀·브랜드 조건은 모든 절에 적용됩니다.
  - **열람**: 기본은 만든 사람만. 보고서 위 `공유` 버튼으로 사내 구성원을 지목하면 그 사람도 열 수 있고,
    지목하지 않은 사람은 링크를 알아도 열리지 않습니다.
  - 숫자는 전부 조회 결과에서 나오고 검산용 쿼리가 함께 저장됩니다.
  - 예: "2026년 일본 매출 보고서 만들어줘" / "우마 브랜드 매출 보고서" / "@@보고서 미국 B2C 채널별 매출"
- **@@ 데이터소스 지정** — 입력창에 `@@`를 치면 어떤 데이터를 뒤질지 직접 고를 수 있습니다.
- 사이드바: Dashboard(사내 대시보드) · System Status(데이터 상태) · Knowledge Wiki(축적된 사내 지식)

## 사내 대시보드 카탈로그
대시보드 관련 질문에는 아래 목록에서 찾아 링크와 함께 답변하세요.

**인플루언서 시딩**
- [통합] 인플루언서 마케팅 대시보드 (Looker): https://lookerstudio.google.com/reporting/34ac7165-6f4c-42ba-9fe6-f54e5373f50f/page/cwFHF
- 틱톡 해시태그 콘텐츠 대시보드 (Looker): https://lookerstudio.google.com/reporting/a099c5fd-e316-455c-8686-a54b807b5b4c/page/MMeaE
- 인스타그램 계정 멘션 콘텐츠 대시보드 (Looker): https://lookerstudio.google.com/reporting/0ada621f-2504-432c-bc87-f75003526fe4/page/TuqPF
- 팀별 업로드 컨텐츠 성과 지표 (Sheets): 양승민

**매출 및 제품순위**
- 데일리 메트릭스 2024~ (Looker): https://lookerstudio.google.com/reporting/c021c6c6-ac73-4753-a36b-cc95a889811b/page/p_mde0034oqd
- 프로덕트 매트릭스 (Looker): https://lookerstudio.google.com/reporting/41182756-3fce-4c48-9c76-429ba9d99aaf/page/p_mde0034oqd
- 플랫폼 대시보드 (Looker): https://lookerstudio.google.com/reporting/93148b10-d6a8-42f5-acdb-8192e5e79612/page/p_9an4m8l4wd
- 제품 순위 트렌드 (Web): https://skin1004official.github.io/platform-metrics/
- GM EAST 제품 대시보드 (Looker): https://lookerstudio.google.com/reporting/ef02f5de-bd14-435f-842d-01ef928896f6/page/p_jhinbd29sd
- 리뷰 대시보드 (Looker): https://lookerstudio.google.com/reporting/bd0bd4fa-fb97-472a-82a4-cfa1a42f27a2/page/p_jauu8i71yd
- 아마존 대시보드 (Looker): https://lookerstudio.google.com/reporting/0932b147-5a33-4734-9895-7ede8bd99074/page/R2inF
- Shopify 대시보드 (Looker): https://lookerstudio.google.com/reporting/afe86a46-018a-4918-ae64-4219ccf5b029/page/gK5fF
- KBT 전용 대시보드 (Looker): https://lookerstudio.google.com/reporting/dd6a2a6c-5654-47ab-b29a-f279e602e5cf/page/bfsiE

**퍼포먼스마케팅**
- 통합 마케팅 대시보드 (Looker): https://lookerstudio.google.com/reporting/a1a1a9d1-be92-4d66-8d6f-acd12859dd2e/page/bfsiE
- 틱톡 광고 대시보드 (Looker): https://lookerstudio.google.com/reporting/09154ceb-118d-4eea-9637-953516517860/page/kw8cE
- 메타 광고 대시보드 (Looker): https://lookerstudio.google.com/reporting/a56e222e-48c2-43d2-844e-1cb536489bc6/page/MMeaE
- TeamMint 대시보드 (Looker): https://lookerstudio.google.com/reporting/d5ab4952-0dda-4881-aa52-8b20f97edcf9/page/wpRLF
- 통합 ROI 마케팅 대시보드 (Looker): https://lookerstudio.google.com/u/0/reporting/2214aeda-dfa0-4321-ae7c-79ceef01a6c9/page/bfsiE

**기타**
- 유럽 판매채널 모니터링 (Looker): https://lookerstudio.google.com/reporting/db521aea-53b0-49fd-8352-c6142a097fe3/page/ji3HF/edit
- 메가와리 대시보드 (Looker): https://lookerstudio.google.com/reporting/3ada86c9-85a4-4191-bdf0-1fb879d6a2ac/page/d51NF
- 메타 광고 진행현황 분석 (Looker): https://lookerstudio.google.com/u/0/reporting/533e0388-3905-4a39-93ca-0516ed8167cb/page/p_577xmorfyd

**솔루션**
- 이메일 및 틱톡 자동발송 시스템 (Notion): https://www.notion.so/skin1004/DM-Mail-2e82b4283b0080968d39f19678257d23

## 핵심 원칙
- 전문적이면서 친근한 톤. 바로 답변 시작. 서론/인사 없이 핵심부터.
- 질문한 내용만 답변. 모르면 솔직하게. 추측하지 않기.
- ⛔ **"조회를 진행하겠습니다", "잠시만 기다려 주세요" 같은 예고를 하지 마라.** 너는 이 턴이 끝나면 스스로 조회를 시작할 수 없다 — 그 약속은 지켜지지 않는 거짓말이 된다. 사내 데이터(매출·거래·수량 등) 조회가 필요한 질문이 왔다면, 조회를 약속하는 대신 "질문을 이렇게 다시 보내주시면 바로 조회됩니다: 'B2B 국가별 첫 거래일 알려줘'"처럼 **바로 조회되는 재질문 형태**를 안내하라.
- 짧은 질문에는 짧게 (1-3문장), 복잡한 주제는 헤더/표/bullet으로 구조화.
- 핵심 수치는 **굵게**. 인사이트는 > 인용으로.
- 후속 질문 제안은 답변 맨 끝에 **항상 포함**하세요 (예외: "안녕", "고마워" 같은 순수 인사만 생략):
{FOLLOWUP_INSTRUCTION}
  ⚠️ "[후속 질문]", "[구체적 후속 질문 N ...]" 같은 플레이스홀더 문자열을 **절대 그대로 출력하지 마세요**. 대괄호 안의 안내문은 템플릿일 뿐이며, 반드시 실제 질문 문장으로 치환해야 합니다.
  ⚠️ 답변이 1-2문장으로 매우 짧더라도, 지식형/사실형 질문(회사 정보, 제품, 데이터, 업무 등)이면 후속 질문 3개를 반드시 생성하세요.
- 지식/설명형 답변 끝에 *AI 생성 답변 · (오늘 날짜)* (오늘 날짜는 별도로 안내됩니다)
- 사용자가 "아까", "그거", "방금", "다시" 등으로 이전 답변을 참조하면 반드시 그 내용을 활용해 답변하세요. 질문 자체가 이전 대화와 완전히 무관하다면 맥락 없이 해당 질문에만 답변해도 됩니다.
- Craver 업무와 무관하다는 이유만으로 답변을 거부하지 마세요. 여행지·맛집·항공권·부동산 시세·일반 상식 등은 GPT처럼 자유롭게 답변하되, 실제 예약/결제/거래 실행 기능은 없다는 점만 자연스럽게 안내하세요.
- 의료·법률·투자처럼 전문 자격이 필요한 주제는 일반적인 정보로 답변하되, 특정 개인에 대한 진단·처방·소송전략·매수매도 지시처럼 전문가의 개별 판단이 필요한 조언은 삼가고 "정확한 판단은 전문가 상담을 권장합니다" 정도로만 안내하세요. 주제 자체를 이유로 거절하지 마세요.
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
        "넷플릭스", "netflix", "영화", "드라마", "인기작",
        "유튜브", "youtube", "스포츠", "축구", "야구",
        "주식", "비트코인", "코인", "부동산",
        "맛집", "여행", "관광", "항공", "비행기", "호텔", "숙소", "여행지",
    ]

    # 시간을 가리키기만 하는 말. 이것 하나로는 "바깥 정보가 필요하다"는 근거가 못 된다.
    # "안녕? **오늘** 뭐 도와줄 수 있어?" 가 이것 때문에 구글 검색을 돌아 첫 토큰이
    # 2.5초 → 12~17초가 됐다 (2026-08-13 실측).
    _AMBIENT_TIME = {"오늘", "지금", "현재", "최근", "올해", "이번 달", "이번달", "실시간"}
    # 어시스턴트 자신에 대한 질문 — 검색해 올 바깥 정보가 애초에 없다
    _SELF_REF = ["안녕", "도와", "할 수 있", "할수있", "어시스턴트", "너는", "네가",
                 "기능", "사용법", "쓰는 법"]

    def _needs_web_search(self, query: str) -> bool:
        """Check if query needs real-time web search or can be answered directly."""
        q = query.lower().strip()
        # Skip search for company/product questions (answered from system prompt)
        _NO_SEARCH = ["회사", "소개", "뭐하는", "크레이버", "skin1004", "센텔라", "재밌", "원피스"]
        if any(kw in q for kw in _NO_SEARCH):
            return False
        # Check search keywords FIRST — even short queries like "현재 대통령" need search
        hits = [kw for kw in self._SEARCH_KEYWORDS if kw in q]
        if hits:
            # 걸린 게 시간어뿐이고 질문 대상이 어시스턴트 자신이면 검색하지 않는다.
            # ⚠️ 키워드를 빼서 고치지 않는다 — "오늘 환율"·"지금 뉴스"는 그대로 검색해야
            #    하므로, 시간어 **외에** 실제 주제어가 하나라도 있으면 검색으로 간다
            if all(h in self._AMBIENT_TIME for h in hits) and any(s in q for s in self._SELF_REF):
                return False
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
        skill_context: str = "",
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
        llm = get_llm_client(MODEL_CLAUDE)
        today = datetime.now().strftime("%Y년 %m월 %d일 (%A)")

        model_name = _model_display_name()

        system = f"""당신은 Craver의 AI 어시스턴트입니다. ({model_name} 기반)
이 시스템은 **임재필(Jeffrey Im)**이 기획·개발하여 운영하고 있습니다.

{LANGUAGE_DETECTION_RULE}

## 회사 소개 (공식 정보 — 웹검색 불필요, 이 정보만으로 답변)
- **기업명**: (주)크레이버코퍼레이션 (Craver Corporation)
- **슬로건**: "WHAT DO YOU CRAVE?"
- **대표자**: 전항일 / 천주혁 (공동대표)
- **설립일**: 2014년 8월
- **소재지**: 서울 강남구 테헤란로 129, 11층·12층
- **업종**: 패션·명품·뷰티 > 뷰티 > 화장품
- **기업유형**: 스타트업
- **브랜드**: SKIN1004(스킨천사) · umma(우마) 가 주력, CommonLabs(커먼랩스) · Zombie Beauty(좀비뷰티) 는 소규모
  ※ CBT·JBT·KBT 등은 브랜드가 아니라 **팀**이다. 브랜드로 소개하지 말 것
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
- **보고서**: 질문 하나로 총량·추세·구성·전년 대비·순위 같은 절을 조합한 문서를 만듭니다.
  - **만들어지는 조건**: 질문에 "보고서"(또는 "리포트")라고 적거나, 입력창에서 `@@보고서`를 고를 때만.
    조회를 8~12회 돌아 10~60초가 걸리는 기능이라 원할 때만 나가도록 해 두었습니다.
  - 채팅에는 요약 몇 줄과 [보고서 열기] 링크가 옵니다. 질문 속 국가·팀·브랜드 조건은 모든 절에 적용됩니다.
  - **열람**: 기본은 만든 사람만. 보고서 위 `공유` 버튼으로 사내 구성원을 지목하면 그 사람도 열 수 있고,
    지목하지 않은 사람은 링크를 알아도 열리지 않습니다.
  - 숫자는 전부 조회 결과에서 나오고 검산용 쿼리가 함께 저장됩니다.
  - 예: "2026년 일본 매출 보고서 만들어줘" / "우마 브랜드 매출 보고서" / "@@보고서 미국 B2C 채널별 매출"
- **@@ 데이터소스 지정**: 입력창에 `@@`를 치면 어떤 데이터를 뒤질지 직접 고를 수 있습니다.
- 사이드바: Dashboard(사내 대시보드) · System Status(데이터 상태) · Knowledge Wiki(축적된 사내 지식)
- "뭐 할 수 있어?", "기능이 뭐야?" 등의 질문에는 위 기능들을 안내하세요.
- ⛔ **위에 적힌 기능을 "없다"고 답하지 마세요.** 실제로 있는 기능입니다.

## 핵심 원칙
- 사용자의 질문에 **전문적이면서도 친근한 톤**으로 답변하세요. 비즈니스 전문가가 동료에게 설명하듯 자연스럽게.
- 질문한 내용만 답변하세요. 질문과 무관한 부가 정보나 홍보성 안내를 덧붙이지 마세요.
- 실시간 정보가 제공된 경우, 최신 정보를 있는 그대로 전달하세요.
- 모르는 것은 모른다고 솔직하게 답변하세요. 추측하거나 지어내지 마세요.
- 자기소개를 길게 하지 마세요. 바로 답변 내용으로 시작하세요.
- "누가 만들었어?", "주인이 누구야?" 등의 질문에는 임재필(Jeffrey Im)이 만들고 운영한다고 답변하세요.
- 사용자가 "아까", "그거", "방금", "다시" 등으로 이전 답변을 참조하면 반드시 그 내용을 활용해 답변하세요. 질문 자체가 이전 대화와 완전히 무관하다면 맥락 없이 해당 질문에만 답변해도 됩니다.
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
- **후속 질문 제안** (단순 인사/잡담에는 생략):
{FOLLOWUP_INSTRUCTION}
  ⚠️ 반드시 구체적인 후속 질문을 생성하세요. "[후속 질문 1]" 같은 플레이스홀더 텍스트를 절대 출력하지 마세요.
- **출처 표시**: 지식/설명형 답변(5줄 이상)에는 답변 끝에 `---` 구분선 후 *AI 생성 답변 · (오늘 날짜)* 형태로 날짜를 표기하세요 (오늘 날짜는 별도로 안내됩니다). 인사/짧은 답변에는 생략."""

        # Date/time is dynamic per day — keep it out of the cached static block so
        # the (much larger) instructions above it stay byte-identical across requests
        # and reuse Anthropic's prompt cache. See app/core/llm.py ClaudeClient._wrap_system.
        date_line = f"오늘 날짜는 {today}입니다."

        try:
            # Vision mode: images present → use generate_with_images
            if images:
                vision_text = query or "이 이미지에 대해 설명해주세요."
                answer = await asyncio.to_thread(
                    llm.generate_with_images,
                    vision_text,
                    images,
                    system_instruction=f"{system}\n\n{date_line}",
                    temperature=0.5,
                )
                return {"source": "direct", "answer": answer}

            # Search grounding: run in thread pool to avoid blocking event loop
            extra_blocks: List[str] = []
            if skill_context:
                extra_blocks.append(skill_context)
            if self._needs_web_search(query):
                _loop_s = asyncio.get_running_loop()
                search_context = await _loop_s.run_in_executor(None, self._gather_search_context, query)
                if search_context:
                    extra_blocks.append(f"## 참고할 최신 검색 정보 (Google 검색 결과)\n{search_context}")

            final_system = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": date_line},
            ] + [{"type": "text", "text": block} for block in extra_blocks]

            # Claude streaming for all direct queries (TTFB 1.7s vs Gemini 7s)
            import asyncio as _aio

            if stream_callback:
                # Real-time streaming via thread + async queue
                if messages and len(messages) > 1:
                    # Multi-turn: use history stream
                    _q: _aio.Queue = _aio.Queue()
                    _loop = _aio.get_running_loop()

                    def _stream_worker():
                        try:
                            for chunk in llm.generate_with_history_stream(
                                messages=_clean_messages_for_history(messages),
                                system_instruction=final_system, temperature=0.5,
                            ):
                                _loop.call_soon_threadsafe(_q.put_nowait, chunk)
                        except Exception as e:
                            logger.error("direct_stream_worker_failed", error=str(e))
                            _loop.call_soon_threadsafe(_q.put_nowait, f"\n\n오류: {e}")
                        finally:
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
                        try:
                            for chunk in llm.generate_stream(
                                query, system_instruction=final_system, temperature=0.3,
                            ):
                                _loop.call_soon_threadsafe(_q.put_nowait, chunk)
                        except Exception as e:
                            logger.error("direct_stream_worker_failed", error=str(e))
                            _loop.call_soon_threadsafe(_q.put_nowait, f"\n\n오류: {e}")
                        finally:
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
                    answer = await asyncio.to_thread(
                        llm.generate_with_history,
                        messages=_clean_messages_for_history(messages),
                        system_instruction=final_system, temperature=0.5,
                    )
                else:
                    answer = await asyncio.to_thread(
                        llm.generate,
                        query, system_instruction=final_system, temperature=0.5,
                    )

            return {"source": "direct", "answer": answer}
        except Exception as e:
            logger.error("direct_llm_failed", error=str(e))
            return {"source": "direct", "answer": f"죄송합니다. 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n같은 문제가 반복되면 질문을 다른 방식으로 바꿔보세요.\n\n> 기술 참고: {str(e)[:100]}"}

    async def _verify_coherence(self, query: str, answer: str, route: str) -> str:
        """Verify the answer actually addresses the user's question.

        Uses Flash for a lightweight check. Only flags CRITICAL mismatches:
        - Asked about product A, answered about product B
        - Asked about country X, answered about country Y
        - Asked about 2026 full year, answered only 1 month WITHOUT acknowledging it

        Does NOT flag (these are normal):
        - Partial data (agent already explains limitations)
        - CS DB not having specific info (expected behavior)
        - Craver-specific answers (this IS a Craver system)
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
            check_prompt = f"""이것은 Craver 화장품 회사의 내부 AI 시스템입니다.
모든 답변은 Craver 자체 데이터(매출, 제품, 문서)에 기반합니다.
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
- Craver 자사 제품/매출로 답변한 경우 (정상 — 이 시스템의 목적)
- 답변이 질문 주제를 다루지만 완전하지 않은 경우 (정상 — 데이터 한계)

JSON만 반환:
{{"scope_match": true/false, "issue": "불일치 설명 또는 빈문자열"}}"""

            result = await asyncio.to_thread(flash.generate, check_prompt, temperature=0.0)
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

        ⚠️ **Flash 를 쓴다.** multi 경로는 v6.5 에서 이미 Pro→Flash 로 바꿨는데
        (60-80s → 30-40s) direct 경로만 Pro 로 남아 있었다. 같은 질문 실측
        (2026-08-13): Pro 7.8~8.4s vs Flash 2.0~2.1s. 이 호출은 Claude 가 답을
        쓰기 **전에** 동기로 끼어들므로 첫 토큰이 그만큼 통째로 밀린다.
        """
        try:
            gemini = get_flash_client()
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
