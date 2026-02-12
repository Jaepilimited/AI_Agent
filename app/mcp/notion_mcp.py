"""Notion MCP Server connection."""

import json
import os

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import get_settings


async def get_notion_mcp_tools():
    """Return Notion MCP tools."""
    token = get_settings().notion_mcp_token
    # Pass auth via both NOTION_TOKEN and OPENAPI_MCP_HEADERS for compatibility
    headers_json = json.dumps({
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    })
    env = {
        **os.environ,
        "NOTION_TOKEN": token,
        "OPENAPI_MCP_HEADERS": headers_json,
    }
    client = MultiServerMCPClient({
        "notion": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "env": env,
        }
    })
    return await client.get_tools()
