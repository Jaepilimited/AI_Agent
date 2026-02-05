"""Notion Sub Agent (v3.0).

Accesses internal documents via Notion MCP.
Real-time document search without embedding indexing.
Uses Sonnet 4 for cost efficiency.
"""

from langchain_anthropic import ChatAnthropic
from langchain.agents import create_react_agent

from app.models.agent_models import AgentModel
from app.mcp.notion_mcp import get_notion_mcp_tools


class NotionAgent:
    def __init__(self):
        self.llm = ChatAnthropic(
            model=AgentModel.NOTION_AGENT.value,
            temperature=0,
            max_tokens=4096,
        )

    async def run(self, query: str) -> str:
        """Search Notion documents and generate answer.

        Args:
            query: User question.

        Returns:
            Answer text.
        """
        try:
            tools = await get_notion_mcp_tools()
            agent = create_react_agent(self.llm, tools)
            result = await agent.ainvoke({
                "messages": [{"role": "user", "content": query}]
            })
            return result["messages"][-1].content
        except Exception as e:
            return f"Notion 검색 중 오류 발생: {str(e)}"
