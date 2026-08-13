"""Google Workspace Sub Agent (v4.3 — per-user OAuth2 + timeout + recursion limit).

Replaces MCP-based single-user approach with individual OAuth2 authentication.
Each user authenticates with their own Google account to access Gmail/Drive/Calendar.
Uses Gemini Flash as ReAct agent with bound API tools.

v4.1: Added 120s timeout for ReAct agent to prevent 300s+ hangs on complex searches.
v4.2: Added recursion_limit=10 to cap tool call iterations (~4-5 tool calls max).
v4.3: Switched from Claude Sonnet to Gemini Flash (Sonnet was the only live
consumer of AgentModel.GWS_AGENT — no other user-facing model selection
exists in this app, so there's no reason to keep two model providers here).
"""

import asyncio
import re
from datetime import datetime, timedelta
from typing import List
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger(__name__)

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.tools import tool
    from langgraph.errors import GraphRecursionError
    from langgraph.prebuilt import create_react_agent
    _LANGCHAIN_AVAILABLE = True
except Exception as e:
    logger.warning("gws_agent_langchain_import_failed", error=str(e))
    _LANGCHAIN_AVAILABLE = False

from app.config import get_settings
from app.core.google_auth import GoogleAuthManager
from app.core.google_workspace import list_calendar_events, search_drive, search_gmail

_auth_manager = None
_SEOUL = ZoneInfo("Asia/Seoul")


def _current_question(query: str) -> str:
    """Remove conversation history before using text as an API search query."""
    marker = "[현재 질문]"
    if marker in (query or ""):
        return query.rsplit(marker, 1)[1].strip()
    return (query or "").strip()


_GMAIL_OPERATOR_RE = re.compile(
    r'(?<![\w-])-?(?:from|to|cc|bcc|subject|label|in|is|has|filename|'
    r'after|before|older|newer|newer_than|older_than):(?:"[^"]*"|\S+)',
    re.IGNORECASE,
)


def build_gmail_query(query: str, now: datetime | None = None) -> str:
    """Translate a natural Korean mail request into Gmail search syntax.

    Gmail's API does not understand instructions such as ``오늘 메일 요약``.
    Relative dates and common mail states are converted deterministically,
    while explicit Gmail operators supplied by the user are preserved.
    """
    question = _current_question(query)
    lowered = question.lower()
    current = now or datetime.now(_SEOUL)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SEOUL)
    current = current.astimezone(_SEOUL)
    today = current.date()

    explicit_operators = _GMAIL_OPERATOR_RE.findall(question)
    operators_lower = " ".join(explicit_operators).lower()
    has_date_operator = any(
        f"{name}:" in operators_lower
        for name in ("after", "before", "older", "newer", "newer_than", "older_than")
    )

    generated: List[str] = []
    if not has_date_operator:
        start = end = None
        if any(word in lowered for word in ("어제", "yesterday")):
            start, end = today - timedelta(days=1), today
        elif any(word in lowered for word in ("오늘", "today")):
            start, end = today, today + timedelta(days=1)
        elif any(word in lowered for word in ("그제", "그저께")):
            start, end = today - timedelta(days=2), today - timedelta(days=1)
        elif any(word in lowered for word in ("지난주", "지난 주", "last week")):
            this_monday = today - timedelta(days=today.weekday())
            start, end = this_monday - timedelta(days=7), this_monday
        elif any(word in lowered for word in ("이번주", "이번 주", "this week")):
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=7)
        elif any(word in lowered for word in ("지난달", "지난 달", "last month")):
            end = today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
        elif any(word in lowered for word in ("이번달", "이번 달", "this month")):
            start = today.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        elif any(word in lowered for word in ("최근", "latest", "recent")):
            generated.append("newer_than:7d")

        if start is not None and end is not None:
            generated.extend([
                f"after:{start:%Y/%m/%d}",
                f"before:{end:%Y/%m/%d}",
            ])

    if any(word in lowered for word in ("읽지 않은", "안 읽은", "미열람", "unread")):
        if "is:" not in operators_lower:
            generated.append("is:unread")
    if any(word in lowered for word in ("첨부파일", "첨부 파일", "attachment")):
        if "has:" not in operators_lower:
            generated.append("has:attachment")

    sent = any(word in lowered for word in ("보낸 메일", "발송한", "발신한", "sent mail"))
    received = any(word in lowered for word in (
        "받은 메일", "수신 메일", "수신한", "수신 받은", "들어온 메일",
        "도착한 메일", "온 메일", "received",
    ))
    if sent and "in:" not in operators_lower:
        generated.append("in:sent")
    elif received and not any(key in operators_lower for key in ("from:", "in:")):
        generated.append("-from:me")

    residual = _GMAIL_OPERATOR_RE.sub(" ", question.lower())
    stop_phrases = (
        "요약해주세요", "정리해주세요", "검색해주세요", "확인해주세요",
        "요약해줘", "정리해줘", "검색해줘", "찾아줘", "보여줘", "알려줘", "확인해줘",
        "읽지 않은", "안 읽은", "첨부 파일", "받은 메일", "보낸 메일", "수신 받은",
        "들어온 메일", "도착한 메일", "수신 메일", "온 메일",
        "지난 주", "이번 주", "지난 달", "이번 달", "last week", "this week",
        "last month", "this month", "sent mail",
        "yesterday", "today", "latest", "recent", "received", "attachment", "unread",
        "그저께", "지난주", "이번주", "지난달", "이번달", "어제", "오늘", "그제", "최근",
        "발송한", "발신한", "수신한", "미열람", "첨부파일",
        "이메일", "gmail", "메일", "내용", "본문", "요약", "정리", "검색", "확인",
        "내", "나의", "좀", "관련", "대해서", "해줘", "해주세요", "줘", "주세요",
    )
    for phrase in sorted(stop_phrases, key=len, reverse=True):
        residual = residual.replace(phrase, " ")
    residual = re.sub(r"[^0-9a-zA-Z가-힣@._+-]+", " ", residual)
    keywords = [token for token in residual.split() if len(token) > 1]

    return " ".join([*explicit_operators, *generated, *keywords]).strip()


def _get_auth_manager() -> GoogleAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GoogleAuthManager()
    return _auth_manager


def _extract_text(content) -> str:
    """Extract the text block from a LangChain chat model response.

    ``content`` is a plain string for simple replies, but both Gemini and
    Claude can return a list of content blocks instead (e.g. a "thinking"
    block alongside the "text" block) for more complex generations — this
    pulls out just the text.
    """
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class GWSAgent:
    """Google Workspace agent with per-user OAuth2 authentication."""

    def __init__(self):
        # LangChain(ChatGoogleGenerativeAI + create_react_agent)은 더 이상 쓰지 않는다.
        # 그 래퍼가 **프록시를 통과하지 못해** 호출이 통째로 멈췄다 —
        # 실측: 기본/transport='rest' 모두 45초 무응답, 결국 300초 타임아웃까지 매달렸다.
        # 답변 정리는 앱이 다른 곳에서 쓰는 REST 클라이언트(get_flash_client, 실측 2.4초)로 한다.
        pass

    async def run(self, query: str, user_email: str = "") -> str:
        """Search Google Workspace for relevant info.

        Args:
            query: User question.
            user_email: User's email for OAuth credential lookup.

        Returns:
            Answer text, or auth URL if not authenticated.
        """
        # No user_email → can't authenticate
        if not user_email:
            return (
                "Google Workspace 기능을 사용하려면 로그인이 필요합니다.\n"
                "로그아웃 후 다시 로그인해주세요."
            )

        auth_manager = _get_auth_manager()
        creds = auth_manager.get_credentials(user_email)

        # No valid token → auto-connect prompt with auth URL
        if creds is None:
            auth_url = auth_manager.get_auth_url(user_email)
            return (
                "Google Workspace에 접근하려면 Google 계정 연결이 필요합니다.\n\n"
                "잠시 후 Google 로그인 창이 열립니다. 연결 완료 후 같은 질문을 다시 해주세요.\n\n"
                f"<!-- gws-auth:{auth_url} -->"
            )

        # ── 도구 직접 호출 (ReAct 루프 없음) ──────────────────────────────
        #
        # 원래는 create_react_agent + ChatGoogleGenerativeAI 였는데,
        # 그 LangChain 래퍼가 **프록시를 통과하지 못해** 호출이 통째로 멈췄다.
        # 실측: 기본/transport='rest' 모두 45초 안에 응답 없음 → 300초 타임아웃까지
        # 매달렸고, 사용자에겐 "분석이 오래 걸립니다" 만 보였다 (👎 3건).
        # 반면 앱이 다른 곳에서 쓰는 REST 클라이언트(get_flash_client)는 2.4초에 응답한다.
        #
        # 게다가 시스템 프롬프트가 이미 "도구는 1번만 호출하라"고 요구하고 있었다.
        # 한 번만 부를 거면 ReAct 루프가 필요 없다 — 분류해서 직접 부르고 결과를
        # 정리만 시키는 편이 빠르고 결과도 예측 가능하다.
        tool_type = self._classify_tool(query)
        results = await asyncio.to_thread(self._collect, creds, query, tool_type)

        if not results.strip():
            return "검색 결과가 없습니다."

        from app.core.llm import get_flash_client

        # 형식 안내는 **실제로 조회한 종류만** 넣는다. 셋을 다 나열하면
        # "메일 및 파일 정보는 포함되어 있지 않아…" 같은 군더더기가 붙는다.
        _fmt = {
            "calendar": "- 날짜별로 묶어 시간·제목·장소를 표로 정리하세요",
            "gmail": "- 제목·보낸사람·날짜·요약을 표로 정리하세요",
            "drive": "- 파일명·유형·수정일·링크를 표로 정리하세요",
        }
        fmt_lines = (
            _fmt.get(tool_type)
            or "- 종류별(일정/메일/파일)로 나눠 표로 정리하세요"
        )
        prompt = (
            "당신은 Craver의 Google Workspace 비서입니다. 아래 **검색 결과만** 사용해 "
            "사용자 질문에 한국어로 답하세요. 결과에 없는 내용을 지어내지 마세요.\n\n"
            f"## 사용자 질문\n{query}\n\n"
            f"## 검색 결과\n{results[:24000]}\n\n"
            "## 형식\n"
            f"{fmt_lines}\n"
            "- 날짜/시간은 한국어로 (예: 2026년 2월 12일 오후 3시)\n"
            "- 질문이 특정 시간대(오전, 11시 등)를 물으면 결과 중 해당 시간대만 골라 답하세요\n"
            "- 결과가 비어 있으면 '검색 결과가 없습니다'라고만 답하세요\n"
            "- **조회하지 않은 종류(메일·파일 등)를 언급하지 마세요.** "
            "사용자가 묻지 않은 것을 '포함되어 있지 않다'고 덧붙이지 말 것"
        )
        try:
            llm = get_flash_client()
            answer = await asyncio.wait_for(
                asyncio.to_thread(llm.generate, prompt, None, 0.2), timeout=40.0
            )
            return answer or results
        except asyncio.TimeoutError:
            logger.warning("gws_format_timeout", user_email=user_email)
            return results  # 정리에 실패해도 원본 결과는 돌려준다
        except Exception as e:
            logger.error("gws_format_failed", error=str(e)[:200], user_email=user_email)
            return results

    def _collect(self, creds, query: str, tool_type: str) -> str:
        """분류된 도구를 직접 호출해 원본 결과 텍스트를 모은다 (블로킹 — to_thread 로 부를 것)."""
        parts = []
        current_query = _current_question(query)

        def _calendar():
            # 시간 표현("오전", "11시")을 검색어로 넣으면 결과가 0건이 된다.
            # 제목 검색이 아니라 기간 조회이므로 query 는 비우고 days_ahead 만 조절한다.
            q = current_query.lower()
            days = 7
            if any(k in q for k in ("오늘", "today")):
                days = 1
            elif any(k in q for k in ("내일", "tomorrow")):
                days = 2
            elif any(k in q for k in ("이번주", "이번 주", "this week")):
                days = 7
            elif any(k in q for k in ("다음주", "다음 주", "next week")):
                days = 14
            elif any(k in q for k in ("이번달", "이번 달", "한달", "this month")):
                days = 31
            try:
                ev = list_calendar_events(creds, query=None, days_ahead=days)
            except Exception as e:
                return f"[캘린더 오류] {str(e)[:200]}"
            if not ev:
                return "[캘린더] 일정이 없습니다."
            lines = ["[캘린더]"]
            for e in ev:
                loc = f" (장소: {e['location']})" if e.get("location") else ""
                lines.append(f"- {e['summary']}: {e['start']} ~ {e['end']}{loc}")
            return "\n".join(lines)

        def _gmail():
            try:
                gmail_query = build_gmail_query(current_query)
                ms = search_gmail(creds, gmail_query, max_results=10)
            except Exception as e:
                return f"[메일 오류] {str(e)[:200]}"
            if not ms:
                return "[메일] 검색 결과가 없습니다."
            lines = ["[메일]"]
            for m in ms:
                content = m.get("body") or m.get("snippet", "")
                lines.append(
                    f"- {m['subject']} (보낸사람: {m['from']}, 날짜: {m['date']})"
                    f"\n  {content}"
                )
            return "\n".join(lines)

        def _drive():
            # 캘린더와 같은 원리 — 사용자 문장 전체를 검색어로 넣으면 항상 0건이다.
            # ① "사진/영상/PDF" 같은 유형 표현 → mimeType 필터로 변환
            # ② 조사·명령어를 걷어낸 핵심 키워드만 name/fullText 검색에 사용
            #    (키워드가 안 남으면 유형 필터만으로 최근 파일을 보여준다)
            q = current_query.lower()
            mime = None
            for kws, m in (
                (("사진", "이미지", "image", "photo", "jpg", "png"), "image/"),
                (("영상", "동영상", "video", "mp4"), "video/"),
                (("pdf",), "application/pdf"),
                (("스프레드시트", "시트", "spreadsheet", "엑셀"), "spreadsheet"),
                (("슬라이드", "ppt", "발표자료"), "presentation"),
            ):
                if any(k in q for k in kws):
                    mime = m
                    break
            _STOP = {
                "내", "나의", "우리", "드라이브", "drive", "구글", "google", "파일", "폴더",
                "사진", "이미지", "image", "photo", "영상", "동영상", "video", "pdf",
                "스프레드시트", "시트", "엑셀", "슬라이드", "ppt", "발표자료", "문서",
                "찾아줘", "찾아", "검색해줘", "검색", "보여줘", "알려줘", "뭐", "뭐가",
                "있어", "있나", "있지", "최근", "최근에", "올린", "저장한", "들어간",
                "관련", "관련된", "좀", "해줘", "주세요", "에서", "은", "는", "이", "가",
            }
            tokens = [t.strip("?.,!") for t in q.split()]
            kw = " ".join(t for t in tokens if t and t not in _STOP)
            try:
                fs = search_drive(creds, kw, max_results=10, mime_contains=mime)
                # 키워드+유형 동시 검색이 0건이면 유형만으로 완화 재시도
                if not fs and kw and mime:
                    fs = search_drive(creds, "", max_results=10, mime_contains=mime)
            except Exception as e:
                return f"[드라이브 오류] {str(e)[:200]}"
            if not fs:
                return "[드라이브] 검색 결과가 없습니다."
            lines = [f"[드라이브] (검색어: {kw or '전체'}{', 유형: ' + mime if mime else ''})"]
            for f in fs:
                lines.append(f"- {f['name']} ({f['mimeType']}, 수정: {f['modifiedTime']})\n  {f['webViewLink']}")
            return "\n".join(lines)

        picked = {"calendar": [_calendar], "gmail": [_gmail], "drive": [_drive]}.get(
            tool_type, [_calendar, _gmail, _drive]
        )
        for fn in picked:
            try:
                parts.append(fn())
            except Exception as e:
                parts.append(f"[{fn.__name__} 실패] {str(e)[:150]}")
        return "\n\n".join(parts)

    @staticmethod
    def _classify_tool(query: str) -> str:
        """Pre-classify query to select the appropriate GWS tool.

        Returns: "calendar", "gmail", "drive", or "all".
        """
        # 공백 차이로 분류가 빗나가지 않게 정규화한다.
        # ("이번 주 일정" 이 cal 키워드 "이번주" 에 안 걸려 all 로 빠지던 문제)
        q = _current_question(query).lower().replace(" ", "")
        cal_explicit_kw = ["캘린더", "calendar", "일정", "schedule", "스케줄",
                           "회의", "미팅", "약속"]
        cal_time_kw = ["내일", "오늘", "이번주", "다음주", "모레", "일주일",
                       "이번달일정", "며칠"]
        mail_kw = ["메일", "mail", "gmail", "편지", "이메일", "받은",
                   "보낸", "inbox", "발송", "수신", "발신", "invoice",
                   "shipping", "메시지"]
        drive_kw = ["드라이브", "drive", "파일", "file", "폴더", "문서",
                    "시트", "sheet", "용량"]

        cal_explicit = any(k in q for k in cal_explicit_kw)
        cal = cal_explicit or any(k in q for k in cal_time_kw)
        mail = any(k in q for k in mail_kw)
        drive = any(k in q for k in drive_kw)

        # "오늘"은 메일의 기간 조건이지 캘린더 요청이 아니다. 반면
        # "오늘 메일과 일정"처럼 일정 자체를 명시하면 여러 도구를 조회한다.
        if mail and not drive and not cal_explicit:
            return "gmail"
        if drive and not mail and not cal_explicit:
            return "drive"

        # Single tool detected
        if cal and not mail and not drive:
            return "calendar"
        if mail and not cal and not drive:
            return "gmail"
        if drive and not cal and not mail:
            return "drive"
        return "all"

    def _build_tools(self, creds) -> List:
        """Build LangChain tools with user credentials bound.

        Args:
            creds: Valid Google OAuth2 Credentials.

        Returns:
            List of LangChain tools.
        """

        @tool
        def gmail_search(query: str) -> str:
            """Gmail에서 메일을 검색합니다. query에 검색어를 입력하세요. 예: 'from:boss', 'subject:보고서', '최근 메일'"""
            try:
                results = search_gmail(creds, build_gmail_query(query), max_results=10)
                if not results:
                    return "검색 결과가 없습니다."
                lines = []
                for m in results:
                    content = m.get("body") or m.get("snippet", "")
                    lines.append(f"- **{m['subject']}** (보낸사람: {m['from']}, 날짜: {m['date']})\n  {content}")
                return "\n".join(lines)
            except Exception as e:
                return f"Gmail 검색 오류: {str(e)}"

        @tool
        def drive_search(query: str) -> str:
            """Google Drive에서 파일을 검색합니다. query에 검색어를 입력하세요. 예: '보고서', '회의록'"""
            try:
                results = search_drive(creds, query, max_results=10)
                if not results:
                    return "검색 결과가 없습니다."
                lines = []
                for f in results:
                    lines.append(f"- **{f['name']}** ({f['mimeType']}, 수정: {f['modifiedTime']})\n  {f['webViewLink']}")
                return "\n".join(lines)
            except Exception as e:
                return f"Drive 검색 오류: {str(e)}"

        @tool
        def calendar_search(query: str = "", days_ahead: int = 7) -> str:
            """Google Calendar 일정을 조회합니다.

            query: 이벤트 제목/설명에서 텍스트를 검색합니다. 시간 필터링이 아닙니다!
                   "오전", "11시", "오후 3시" 같은 시간 표현을 query에 넣지 마세요 — 결과가 없습니다.
                   시간대별 일정을 찾으려면 query를 비우고("") 전체 일정을 가져온 뒤 시간으로 필터링하세요.
                   제목 검색 예시: query="틱톡", query="타운홀"
            days_ahead: 며칠 후까지 조회할지 (기본 7일)
            """
            try:
                results = list_calendar_events(creds, query=query or None, days_ahead=days_ahead)
                if not results:
                    return "일정이 없습니다."
                lines = []
                for e in results:
                    loc = f" (장소: {e['location']})" if e['location'] else ""
                    lines.append(f"- **{e['summary']}**: {e['start']} ~ {e['end']}{loc}")
                return "\n".join(lines)
            except Exception as e:
                return f"Calendar 조회 오류: {str(e)}"

        return [gmail_search, drive_search, calendar_search]
