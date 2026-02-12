"""v3.0 Multi-Model configuration.

All agents now use Claude Sonnet 4.5 via Anthropic SDK.
"""

from enum import Enum


class AgentModel(Enum):
    ORCHESTRATOR = "claude-sonnet-4-5-20250929"
    BIGQUERY_AGENT = "claude-sonnet-4-5-20250929"
    QUERY_VERIFIER = "claude-sonnet-4-5-20250929"
    NOTION_AGENT = "claude-sonnet-4-5-20250929"
    GWS_AGENT = "claude-sonnet-4-5-20250929"
    DIRECT_LLM = "claude-sonnet-4-5-20250929"
