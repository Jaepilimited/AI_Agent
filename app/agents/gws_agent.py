"""Google Workspace Sub Agent (v3.0).

Accesses Drive, Gmail, Calendar via Google Workspace MCP.
WARNING: Community MCP - security review required.
Uses Sonnet 4 for cost efficiency.
"""

from langchain_anthropic import ChatAnthropic
from langchain.agents import create_react_agent

from app.models.agent_models import AgentModel
from app.mcp.gws_mcp import get_gws_mcp_tools


class GWSAgent:
    def __init__(self):
        self.llm = ChatAnthropic(
            model=AgentModel.GWS_AGENT.value,
            temperature=0,
            max_tokens=4096,
        )

    async def run(self, query: str) -> str:
        """Search Google Workspace for relevant info.

        Args:
            query: User question.

        Returns:
            Answer text.
        """
        try:
            tools = await get_gws_mcp_tools()
            agent = create_react_agent(self.llm, tools)
            result = await agent.ainvoke({
                "messages": [{"role": "user", "content": query}]
            })
            return result["messages"][-1].content
        except Exception as e:
            return f"Google Workspace 검색 중 오류 발생: {str(e)}"
