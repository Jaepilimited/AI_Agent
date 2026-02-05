"""Google Workspace MCP Server connection.

WARNING: Community version - review security before production use.
"""

import os

from langchain_mcp_adapters.client import MultiServerMCPClient


async def get_gws_mcp_tools():
    """Return Google Workspace MCP tools."""
    client = MultiServerMCPClient({
        "google-workspace": {
            "command": "npx",
            "args": ["-y", "google-workspace-mcp-server"],
            "env": {
                "GOOGLE_APPLICATION_CREDENTIALS": os.getenv(
                    "GOOGLE_APPLICATION_CREDENTIALS", ""
                ),
            },
        }
    })
    return await client.get_tools()
