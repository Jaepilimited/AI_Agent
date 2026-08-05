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
from typing import List

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

        prompt = (
            "당신은 Craver의 Google Workspace 비서입니다. 아래 **검색 결과만** 사용해 "
            "사용자 질문에 한국어로 답하세요. 결과에 없는 내용을 지어내지 마세요.\n\n"
            f"## 사용자 질문\n{query}\n\n"
            f"## 검색 결과\n{results[:12000]}\n\n"
            "## 형식\n"
            "- 일정: 날짜별로 묶어 시간·제목·장소를 표로\n"
            "- 메일: 제목·보낸사람·날짜·요약을 표로\n"
            "- 파일: 파일명·유형·수정일·링크를 표로\n"
            "- 날짜/시간은 한국어로 (예: 2026년 2월 12일 오후 3시)\n"
            "- 질문이 특정 시간대(오전, 11시 등)를 물으면 결과 중 해당 시간대만 골라 답하세요\n"
            "- 결과가 비어 있으면 '검색 결과가 없습니다'라고만 답하세요"
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

        def _calendar():
            # 시간 표현("오전", "11시")을 검색어로 넣으면 결과가 0건이 된다.
            # 제목 검색이 아니라 기간 조회이므로 query 는 비우고 days_ahead 만 조절한다.
            q = (query or "").lower()
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
                ms = search_gmail(creds, query, max_results=10)
            except Exception as e:
                return f"[메일 오류] {str(e)[:200]}"
            if not ms:
                return "[메일] 검색 결과가 없습니다."
            lines = ["[메일]"]
            for m in ms:
                lines.append(f"- {m['subject']} (보낸사람: {m['from']}, 날짜: {m['date']})\n  {m['snippet']}")
            return "\n".join(lines)

        def _drive():
            try:
                fs = search_drive(creds, query, max_results=10)
            except Exception as e:
                return f"[드라이브 오류] {str(e)[:200]}"
            if not fs:
                return "[드라이브] 검색 결과가 없습니다."
            lines = ["[드라이브]"]
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
        q = query.lower()
        cal_kw = ["캘린더", "calendar", "일정", "schedule", "내일", "오늘",
                   "이번주", "다음주", "모레", "스케줄", "회의", "미팅",
                   "약속", "일주일", "이번달 일정", "며칠"]
        mail_kw = ["메일", "mail", "gmail", "편지", "이메일", "받은",
                   "보낸", "inbox", "발송", "수신", "발신", "invoice",
                   "shipping", "메시지"]
        drive_kw = ["드라이브", "drive", "파일", "file", "폴더", "문서",
                    "시트", "sheet", "용량"]

        cal = any(k in q for k in cal_kw)
        mail = any(k in q for k in mail_kw)
        drive = any(k in q for k in drive_kw)

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
                results = search_gmail(creds, query, max_results=10)
                if not results:
                    return "검색 결과가 없습니다."
                lines = []
                for m in results:
                    lines.append(f"- **{m['subject']}** (보낸사람: {m['from']}, 날짜: {m['date']})\n  {m['snippet']}")
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
