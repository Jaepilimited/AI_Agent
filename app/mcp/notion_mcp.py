"""Notion MCP Server connection."""

import os

from langchain_mcp_adapters.client import MultiServerMCPClient


async def get_notion_mcp_tools():
    """Return Notion MCP tools."""
    client = MultiServerMCPClient({
        "notion": {
            "command": "npx",
            "args": ["-y", "@anthropic/notion-mcp-server"],
            "env": {
                "NOTION_API_KEY": os.getenv("NOTION_MCP_TOKEN", ""),
            },
        }
    })
    return await client.get_tools()
