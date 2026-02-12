"""Query Analyzer - Intent detection and routing."""

import json
from pathlib import Path
from typing import Any, Dict, Literal

import structlog

from app.core.llm import get_llm_client
from app.models.schemas import QueryAnalysis

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


ROUTE_TYPE = Literal["text_to_sql", "rag", "direct_llm", "multi_agent"]


def analyze_query(query: str) -> QueryAnalysis:
    """Analyze user query and determine the routing path.

    Args:
        query: User's natural language question.

    Returns:
        QueryAnalysis with route_type and reasoning.
    """
    logger.info("analyzing_query", query=query[:100])

    llm = get_llm_client()
    system_prompt = _load_prompt("query_analyzer.txt")

    try:
        response = llm.generate_json(
            prompt=f"사용자 질문: {query}",
            system_instruction=system_prompt,
            temperature=0.0,
        )

        data = json.loads(response)
        route_type = data.get("route_type", "direct_llm")
        reasoning = data.get("reasoning", "")

        # Validate route_type
        valid_routes = {"text_to_sql", "rag", "direct_llm", "multi_agent"}
        if route_type not in valid_routes:
            logger.warning("invalid_route_type", route_type=route_type)
            route_type = "direct_llm"

        result = QueryAnalysis(
            route_type=route_type,
            reasoning=reasoning,
        )
        logger.info("query_analyzed", route_type=route_type, reasoning=reasoning[:100])
        return result

    except json.JSONDecodeError as e:
        logger.error("json_parse_failed", error=str(e))
        return QueryAnalysis(
            route_type="direct_llm",
            reasoning="분류 실패 - 기본값 사용",
        )
    except Exception as e:
        logger.error("query_analysis_failed", error=str(e))
        return QueryAnalysis(
            route_type="direct_llm",
            reasoning=f"분석 오류: {str(e)}",
        )


def analyze_query_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph node wrapper for query analysis.

    Args:
        state: Current agent state.

    Returns:
        Updated state with route_type.
    """
    query = state["query"]
    analysis = analyze_query(query)
    return {"route_type": analysis.route_type}
