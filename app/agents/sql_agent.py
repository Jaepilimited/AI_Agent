"""Text-to-SQL Agent using LangGraph.

Workflow: generate_sql → validate_sql → execute_sql → format_answer
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import structlog
from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.core.bigquery import get_bigquery_client
from app.core.llm import get_gemini_client
from app.core.security import sanitize_sql, validate_sql
from app.models.state import AgentState

logger = structlog.get_logger(__name__)

# Load prompts
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


# --- LangGraph Nodes ---


def generate_sql(state: AgentState) -> Dict[str, Any]:
    """Generate SQL from natural language query.

    Args:
        state: Current agent state with user query.

    Returns:
        Updated state with generated_sql.
    """
    query = state["query"]
    logger.info("generating_sql", query=query)

    llm = get_gemini_client()
    system_prompt = _load_prompt("sql_generator.txt")

    # Get table schema for context if available
    schema_context = ""
    try:
        bq = get_bigquery_client()
        settings = get_settings()
        schema = bq.get_table_schema(settings.sales_table_full_path)
        schema_lines = [
            f"  - {col['name']} ({col['type']}): {col['description']}"
            for col in schema
        ]
        schema_context = "\n\n### 실제 테이블 스키마\n" + "\n".join(schema_lines)
    except Exception as e:
        logger.warning("schema_fetch_failed", error=str(e))

    today = datetime.now().strftime("%Y-%m-%d")
    date_context = f"\n\n## 오늘 날짜\n{today} (사용자가 '이번 달', '지난 달', '올해' 등 상대적 날짜를 사용하면 이 날짜를 기준으로 계산하세요)"
    full_prompt = f"{system_prompt}{schema_context}{date_context}\n\n## 사용자 질문\n{query}"

    try:
        sql = llm.generate(full_prompt, temperature=0.0)
        sql = sanitize_sql(sql)
        logger.info("sql_generated", sql=sql[:200])
        return {"generated_sql": sql, "error": None}
    except Exception as e:
        logger.error("sql_generation_failed", error=str(e))
        return {"generated_sql": None, "error": f"SQL 생성 실패: {str(e)}"}


def validate_sql_node(state: AgentState) -> Dict[str, Any]:
    """Validate generated SQL for safety.

    Args:
        state: Current agent state with generated_sql.

    Returns:
        Updated state with sql_valid flag.
    """
    sql = state.get("generated_sql")
    if not sql:
        return {"sql_valid": False, "error": "SQL이 생성되지 않았습니다."}

    is_valid, error_msg = validate_sql(sql)

    if not is_valid:
        logger.warning("sql_validation_failed", error=error_msg, sql=sql[:200])
        return {"sql_valid": False, "error": f"SQL 검증 실패: {error_msg}"}

    logger.info("sql_validation_passed", sql=sql[:200])
    return {"sql_valid": True, "error": None}


def execute_sql(state: AgentState) -> Dict[str, Any]:
    """Execute validated SQL against BigQuery.

    Args:
        state: Current agent state with validated SQL.

    Returns:
        Updated state with sql_result.
    """
    sql = state.get("generated_sql")
    if not sql or not state.get("sql_valid"):
        return {"sql_result": None, "error": "실행할 수 없는 SQL입니다."}

    logger.info("executing_sql", sql=sql[:200])

    try:
        bq = get_bigquery_client()
        results = bq.execute_query(sql, timeout=30.0, max_rows=1000)
        logger.info("sql_executed", row_count=len(results))
        return {"sql_result": results, "error": None}
    except Exception as e:
        logger.error("sql_execution_failed", error=str(e))
        return {"sql_result": None, "error": f"SQL 실행 실패: {str(e)}"}


def format_answer(state: AgentState) -> Dict[str, Any]:
    """Format SQL results into a natural language answer with optional chart.

    Args:
        state: Current agent state with sql_result.

    Returns:
        Updated state with answer (and chart if applicable).
    """
    query = state["query"]
    sql = state.get("generated_sql", "")
    results = state.get("sql_result")
    error = state.get("error")

    # Handle error cases
    if error:
        return {
            "answer": f"죄송합니다. 질문을 처리하는 중 오류가 발생했습니다.\n\n오류: {error}"
        }

    if not results:
        return {
            "answer": "조회 결과가 없습니다. 검색 조건을 확인해 주세요."
        }

    # Format results for LLM
    llm = get_gemini_client()

    # Limit result preview for prompt
    result_preview = json.dumps(results[:20], ensure_ascii=False, indent=2, default=str)

    prompt = f"""다음은 사용자의 질문과 BigQuery 실행 결과입니다.
결과를 바탕으로 사용자에게 친절하고 정확한 한국어 답변을 작성하세요.

## 사용자 질문
{query}

## 실행된 SQL
```sql
{sql}
```

## 실행 결과 (총 {len(results)}행)
```json
{result_preview}
```

## 답변 작성 규칙
1. 핵심 수치를 먼저 제시하세요.
2. 금액은 천 단위 구분 쉼표를 사용하세요.
3. 필요하면 표 형식으로 정리하세요.
4. 데이터의 의미나 트렌드를 간단히 설명하세요.
5. 결과가 20행을 초과하면 주요 항목만 요약하세요.
"""

    try:
        answer = llm.generate(prompt, temperature=0.3)

        # Try to generate a chart if appropriate
        chart_markdown = _try_generate_chart(llm, query, sql, result_preview, results)
        if chart_markdown:
            answer = answer + "\n\n" + chart_markdown

        return {"answer": answer}
    except Exception as e:
        logger.error("answer_formatting_failed", error=str(e))
        # Fallback: return raw results
        return {
            "answer": f"SQL 실행 결과 ({len(results)}행):\n```json\n{result_preview}\n```"
        }


def _try_generate_chart(llm, query: str, sql: str, result_preview: str, results: list) -> str:
    """Attempt to generate a chart for the SQL results.

    Returns markdown/HTML string with interactive chart, or empty string.
    """
    from app.core.chart import generate_chart, get_chart_config_prompt

    try:
        config_prompt = get_chart_config_prompt(query, sql, result_preview, len(results))
        config_json = llm.generate_json(config_prompt)
        logger.info("chart_config_raw", config_json=config_json[:500])
        config = json.loads(config_json)

        if not config.get("needs_chart"):
            logger.info("chart_not_needed", config=config)
            return ""

        logger.info("chart_requested", chart_type=config.get("chart_type"), group_column=config.get("group_column"))

        # Generate PNG chart
        filename = generate_chart(config, results)
        if filename:
            settings = get_settings()
            chart_url = f"{settings.chart_base_url}/static/charts/{filename}"
            logger.info("chart_url_generated", url=chart_url)
            return f"\n\n![chart]({chart_url})"
        logger.warning("chart_generate_returned_none")
        return ""
    except Exception as e:
        logger.error("chart_generation_skipped", error=str(e), error_type=type(e).__name__)
        return ""


# --- Routing Functions ---


def should_execute(state: AgentState) -> str:
    """Decide whether to execute SQL or return error.

    Args:
        state: Current agent state.

    Returns:
        Next node name.
    """
    if state.get("sql_valid"):
        return "execute_sql"
    return "format_answer"


def should_retry(state: AgentState) -> str:
    """Decide whether to retry SQL generation.

    Args:
        state: Current agent state.

    Returns:
        Next node name.
    """
    retry_count = state.get("retry_count", 0)
    if state.get("error") and retry_count < 2:
        return "generate_sql"
    return "format_answer"


# --- Build Graph ---


def build_sql_agent_graph() -> StateGraph:
    """Build the Text-to-SQL LangGraph workflow.

    Returns:
        Compiled LangGraph StateGraph.
    """
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("validate_sql", validate_sql_node)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("format_answer", format_answer)

    # Define edges
    workflow.set_entry_point("generate_sql")
    workflow.add_edge("generate_sql", "validate_sql")
    workflow.add_conditional_edges(
        "validate_sql",
        should_execute,
        {
            "execute_sql": "execute_sql",
            "format_answer": "format_answer",
        },
    )
    workflow.add_edge("execute_sql", "format_answer")
    workflow.add_edge("format_answer", END)

    return workflow.compile()


# Module-level compiled graph
sql_agent = build_sql_agent_graph()


async def run_sql_agent(query: str) -> str:
    """Run the Text-to-SQL agent on a query.

    Args:
        query: Natural language question about data.

    Returns:
        Natural language answer based on SQL results.
    """
    initial_state: AgentState = {
        "query": query,
        "route_type": "text_to_sql",
        "generated_sql": None,
        "sql_valid": None,
        "sql_result": None,
        "retrieved_docs": None,
        "doc_relevance": None,
        "web_search_results": None,
        "answer": "",
        "needs_retry": False,
        "retry_count": 0,
        "error": None,
        "messages": None,
    }

    logger.info("sql_agent_started", query=query)
    result = sql_agent.invoke(initial_state)
    logger.info("sql_agent_completed", answer_length=len(result.get("answer", "")))
    return result["answer"]
