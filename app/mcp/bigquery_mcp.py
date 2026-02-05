"""BigQuery MCP Server connection.

Existing sql_agent.py direct BigQuery calls are preserved.
This module adds MCP-based connection as a new alternative.
"""

import os

from langchain_mcp_adapters.client import MultiServerMCPClient


async def get_bigquery_mcp_tools():
    """Return BigQuery MCP tools."""
    client = MultiServerMCPClient({
        "bigquery": {
            "command": "npx",
            "args": ["-y", "@anthropic/bigquery-mcp-server"],
            "env": {
                "GOOGLE_PROJECT_ID": os.getenv("GCP_PROJECT_ID", "skin1004-319714"),
                "GOOGLE_APPLICATION_CREDENTIALS": os.getenv(
                    "GOOGLE_APPLICATION_CREDENTIALS", ""
                ),
            },
        }
    })
    return await client.get_tools()
