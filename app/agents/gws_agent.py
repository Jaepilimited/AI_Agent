"""Google Workspace Sub Agent (v4.2 — per-user OAuth2 + timeout + recursion limit).

Replaces MCP-based single-user approach with individual OAuth2 authentication.
Each user authenticates with their own Google account to access Gmail/Drive/Calendar.
Uses Claude Sonnet as ReAct agent with bound API tools.

v4.1: Added 120s timeout for ReAct agent to prevent 300s+ hangs on complex searches.
v4.2: Added recursion_limit=10 to cap tool call iterations (~4-5 tool calls max).
"""

import asyncio
from typing import List

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.core.google_auth import GoogleAuthManager
from app.core.google_workspace import list_calendar_events, search_drive, search_gmail
from app.models.agent_models import AgentModel

logger = structlog.get_logger(__name__)

_auth_manager = None


def _get_auth_manager() -> GoogleAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GoogleAuthManager()
    return _auth_manager


class GWSAgent:
    """Google Workspace agent with per-user OAuth2 authentication."""

    def __init__(self):
        settings = get_settings()
        self.llm = ChatAnthropic(
            model=AgentModel.GWS_AGENT.value,
            anthropic_api_key=settings.anthropic_api_key,
            temperature=0,
            max_tokens=4096,
        )

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

        # Build tools with user's credentials bound via closure
        all_tools = self._build_tools(creds)

        # Pre-classify query to restrict tools and reduce ReAct iterations
        tool_type = self._classify_tool(query)
        if tool_type == "calendar":
            tools = [t for t in all_tools if t.name == "calendar_search"]
        elif tool_type == "gmail":
            tools = [t for t in all_tools if t.name == "gmail_search"]
        elif tool_type == "drive":
            tools = [t for t in all_tools if t.name == "drive_search"]
        else:
            tools = all_tools

        tool_hint = ""
        if tool_type != "all":
            tool_hint = f"\n8. 이 질문은 {tool_type} 관련입니다. {tools[0].name} 도구만 사용하세요. 다른 도구는 호출하지 마세요."

        try:
            agent = create_react_agent(self.llm, tools)
            system_msg = (
                "당신은 SKIN1004의 Google Workspace 검색 AI입니다.\n"
                "사용자의 Gmail, Google Drive, Google Calendar에서 정보를 검색합니다.\n\n"
                "## 답변 형식 규칙\n"
                "1. 항상 한국어로 답변하세요.\n"
                "2. 검색 결과를 아래 구조로 정리하세요:\n\n"
                "### 📬 [검색 유형] 검색 결과\n\n"
                "**검색 조건**: [사용한 검색어/기간]\n"
                "**결과**: [N]건\n\n"
                "결과를 마크다운 표 또는 번호 목록으로 정리하세요:\n"
                "- 메일: 제목, 보낸사람, 날짜, 요약을 표로\n"
                "- 일정: 날짜별로 그룹핑하여 시간/제목/장소\n"
                "- 파일: 파일명, 유형, 수정일, 링크를 표로\n\n"
                "3. 검색 결과가 없으면 '검색 결과가 없습니다'라고 간결하게 답변하세요.\n"
                "4. 날짜/시간은 한국어 형식으로 (예: 2026년 2월 12일 오후 3시)\n"
                "5. 핵심 항목은 **굵게** 강조하세요.\n"
                "6. 도구는 **1번만** 호출하세요. 결과가 없어도 다른 도구로 재시도하지 마세요. 바로 '검색 결과가 없습니다'로 답하세요.\n"
                "7. **캘린더 시간 필터링 규칙**: calendar_search의 query 파라미터는 이벤트 제목/설명 텍스트 검색입니다. "
                "시간 기반 질문('오전 일정', '11시 일정', '오후 3시 미팅')은 query를 비워서(\"\") 전체 일정을 가져온 뒤, "
                "결과에서 해당 시간대 일정만 골라서 답변하세요. 절대로 '오전', '11시', '오후' 같은 시간 표현을 query에 넣지 마세요."
                + tool_hint
            )
            result = await asyncio.wait_for(
                agent.ainvoke(
                    {
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": query},
                        ]
                    },
                    config={"recursion_limit": 6},
                ),
                timeout=30.0,
            )
            return result["messages"][-1].content
        except asyncio.TimeoutError:
            logger.warning("gws_agent_timeout", query=query[:100], user_email=user_email)
            return (
                "Google Workspace 검색이 시간 초과되었습니다 (30초).\n\n"
                "**해결 방법**:\n"
                "- 더 구체적인 검색어를 사용해주세요 (예: 발신자, 제목, 날짜 범위)\n"
                "- 검색 범위를 좁혀주세요 (예: '오늘 메일' 대신 '오늘 SKIN1004 메일')"
            )
        except GraphRecursionError:
            logger.warning("gws_agent_recursion_limit", query=query[:100], user_email=user_email)
            return (
                "Google Workspace 검색이 최대 반복 횟수에 도달했습니다.\n\n"
                "검색을 완료하기 위해 더 구체적인 질문으로 다시 시도해주세요.\n"
                "예: 기간, 발신자, 키워드 등을 함께 지정해주세요."
            )
        except Exception as e:
            logger.error("gws_agent_failed", error=str(e), user_email=user_email)
            return f"Google Workspace 검색 중 오류 발생: {str(e)}"

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
