"""v3.0 Multi-Model configuration.

All agents now use Claude Sonnet 5 via Anthropic SDK.
"""

from enum import Enum


class AgentModel(Enum):
    ORCHESTRATOR = "claude-sonnet-5"
    BIGQUERY_AGENT = "claude-sonnet-5"
    QUERY_VERIFIER = "claude-sonnet-5"
    NOTION_AGENT = "claude-sonnet-5"
    GWS_AGENT = "claude-sonnet-5"
    DIRECT_LLM = "claude-sonnet-5"
