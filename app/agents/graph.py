"""LangGraph integrated workflow - Main orchestration graph.

Routes queries through: SQL Agent | RAG Agent | Direct LLM | Multi-Agent
"""

import time
import uuid
from datetime import datetime
from typing import Any, Dict

import structlog
from langgraph.graph import END, StateGraph

from app.agents.rag_agent import (
    check_hallucination,
    generate_answer as rag_generate_answer,
    grade_documents,
    retrieve_docs,
    route_after_grading,
    route_after_hallucination_check,
    rewrite_query,
    web_search,
)
from app.agents.router import analyze_query_node
from app.agents.sql_agent import (
    execute_sql,
    format_answer as sql_format_answer,
    generate_sql,
    should_execute,
    validate_sql_node,
)
from app.core.llm import get_llm_client
from app.models.state import AgentState

logger = structlog.get_logger(__name__)


# --- Additional Nodes ---


def direct_llm_answer(state: AgentState) -> Dict[str, Any]:
    """Generate a direct LLM answer without data/doc retrieval.

    Args:
        state: Current agent state.

    Returns:
        Updated state with answer.
    """
    query = state["query"]
    logger.info("direct_llm_answer", query=query[:100])

    llm = get_llm_client()
    today = datetime.now().strftime("%Y년 %m월 %d일 (%A)")
    system = f"""당신은 SKIN1004의 AI 어시스턴트입니다.
오늘 날짜는 {today}입니다.
사용자의 일반적인 질문에 친절하고 정확하게 한국어로 답변하세요.
SKIN1004은 한국 화장품 브랜드로, 동남아시아(태국, 베트남, 필리핀, 말레이시아, 인도네시아) 시장에서
Shopee, Lazada, TikTok Shop, Amazon 등의 플랫폼을 통해 판매하고 있습니다.
매출이나 데이터 관련 질문은 데이터 분석 기능으로 처리할 수 있으니, 데이터가 필요한 질문은 구체적으로 해달라고 안내하세요."""

    try:
        # Use conversation history if available
        messages = state.get("messages")
        if messages and len(messages) > 1:
            answer = llm.generate_with_history(
                messages=messages,
                system_instruction=system,
                temperature=0.5,
            )
        else:
            answer = llm.generate(query, system_instruction=system, temperature=0.5)
        return {"answer": answer}
    except Exception as e:
        logger.error("direct_llm_failed", error=str(e))
        return {"answer": f"답변 생성 중 오류가 발생했습니다: {str(e)}"}


def multi_agent_answer(state: AgentState) -> Dict[str, Any]:
    """Handle multi-agent queries by combining SQL and RAG results.

    Runs SQL agent and RAG agent sequentially, then combines results.

    Args:
        state: Current agent state.

    Returns:
        Updated state with combined answer.
    """
    query = state["query"]
    logger.info("multi_agent_started", query=query[:100])

    parts = []

    # 1. Try SQL Agent
    try:
        sql_state = generate_sql(state)
        state_with_sql = {**state, **sql_state}
        val_state = validate_sql_node(state_with_sql)
        state_with_val = {**state_with_sql, **val_state}

        if state_with_val.get("sql_valid"):
            exec_state = execute_sql(state_with_val)
            state_with_exec = {**state_with_val, **exec_state}
            if state_with_exec.get("sql_result"):
                fmt_state = sql_format_answer(state_with_exec)
                parts.append(f"## 📊 데이터 분석 결과\n\n{fmt_state.get('answer', '')}")
    except Exception as e:
        logger.warning("multi_agent_sql_failed", error=str(e))

    # 2. Try RAG Agent
    try:
        rag_state = retrieve_docs(state)
        state_with_docs = {**state, **rag_state}
        if state_with_docs.get("retrieved_docs"):
            grade_state = grade_documents(state_with_docs)
            state_with_grade = {**state_with_docs, **grade_state}
            answer_state = rag_generate_answer(state_with_grade)
            parts.append(f"## 📄 문서 분석 결과\n\n{answer_state.get('answer', '')}")
    except Exception as e:
        logger.warning("multi_agent_rag_failed", error=str(e))

    if not parts:
        # Fallback to direct LLM
        return direct_llm_answer(state)

    combined = "\n\n---\n\n".join(parts)
    return {"answer": combined}


def log_qa(state: AgentState) -> Dict[str, Any]:
    """Log the QA interaction to BigQuery.

    Args:
        state: Current agent state with all results.

    Returns:
        Unchanged state (logging is side-effect).
    """
    try:
        from app.core.bigquery import get_bigquery_client

        bq = get_bigquery_client()
        log_entry = {
            "id": str(uuid.uuid4()),
            "query": state.get("query", ""),
            "route_type": state.get("route_type", ""),
            "generated_sql": state.get("generated_sql", ""),
            "retrieved_docs": state.get("retrieved_docs", []) or [],
            "answer": state.get("answer", "")[:5000],  # Truncate long answers
        }
        bq.insert_qa_log(log_entry)
    except Exception as e:
        logger.warning("qa_logging_failed", error=str(e))

    return {}


# --- Main Router ---


def route_by_type(state: AgentState) -> str:
    """Route to the appropriate agent based on route_type.

    Args:
        state: Current agent state.

    Returns:
        Next node name.
    """
    route_type = state.get("route_type", "direct_llm")
    logger.info("routing_query", route_type=route_type)

    route_map = {
        "text_to_sql": "generate_sql",
        "rag": "retrieve_docs",
        "direct_llm": "direct_llm_answer",
        "multi_agent": "multi_agent_answer",
    }
    return route_map.get(route_type, "direct_llm_answer")


# --- Build Main Graph ---


def build_main_graph() -> StateGraph:
    """Build the main LangGraph orchestration workflow.

    Returns:
        Compiled LangGraph StateGraph.
    """
    workflow = StateGraph(AgentState)

    # --- Add ALL nodes ---

    # Router
    workflow.add_node("analyze_query", analyze_query_node)

    # SQL Agent nodes
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("validate_sql", validate_sql_node)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("sql_format_answer", sql_format_answer)

    # RAG Agent nodes
    workflow.add_node("retrieve_docs", retrieve_docs)
    workflow.add_node("grade_documents", grade_documents)
    workflow.add_node("web_search", web_search)
    workflow.add_node("rewrite_query", rewrite_query)
    workflow.add_node("rag_generate_answer", rag_generate_answer)
    workflow.add_node("check_hallucination", check_hallucination)

    # Direct LLM
    workflow.add_node("direct_llm_answer", direct_llm_answer)

    # Multi-Agent
    workflow.add_node("multi_agent_answer", multi_agent_answer)

    # Logging
    workflow.add_node("log_qa", log_qa)

    # --- Define Edges ---

    # Entry point
    workflow.set_entry_point("analyze_query")

    # Route from analyzer
    workflow.add_conditional_edges(
        "analyze_query",
        route_by_type,
        {
            "generate_sql": "generate_sql",
            "retrieve_docs": "retrieve_docs",
            "direct_llm_answer": "direct_llm_answer",
            "multi_agent_answer": "multi_agent_answer",
        },
    )

    # --- SQL Agent Path ---
    workflow.add_edge("generate_sql", "validate_sql")
    workflow.add_conditional_edges(
        "validate_sql",
        should_execute,
        {
            "execute_sql": "execute_sql",
            "format_answer": "sql_format_answer",
        },
    )
    workflow.add_edge("execute_sql", "sql_format_answer")
    workflow.add_edge("sql_format_answer", "log_qa")

    # --- RAG Agent Path ---
    workflow.add_edge("retrieve_docs", "grade_documents")
    workflow.add_conditional_edges(
        "grade_documents",
        route_after_grading,
        {
            "generate_answer": "rag_generate_answer",
            "web_search": "web_search",
        },
    )
    workflow.add_edge("web_search", "rag_generate_answer")
    workflow.add_edge("rag_generate_answer", "check_hallucination")
    workflow.add_conditional_edges(
        "check_hallucination",
        route_after_hallucination_check,
        {
            "rewrite_query": "rewrite_query",
            END: "log_qa",
        },
    )
    workflow.add_edge("rewrite_query", "retrieve_docs")

    # --- Direct LLM Path ---
    workflow.add_edge("direct_llm_answer", "log_qa")

    # --- Multi-Agent Path ---
    workflow.add_edge("multi_agent_answer", "log_qa")

    # --- Logging → End ---
    workflow.add_edge("log_qa", END)

    return workflow.compile()


# Module-level compiled graph
main_graph = build_main_graph()


async def run_agent(query: str) -> str:
    """Run the main agent pipeline.

    Args:
        query: User's natural language question.

    Returns:
        Natural language answer.
    """
    initial_state: AgentState = {
        "query": query,
        "route_type": "direct_llm",
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
        "conversation_context": None,
        "model_type": None,
    }

    start = time.time()
    logger.info("agent_started", query=query)

    result = main_graph.invoke(initial_state)

    elapsed_ms = int((time.time() - start) * 1000)
    logger.info(
        "agent_completed",
        route_type=result.get("route_type"),
        latency_ms=elapsed_ms,
    )

    return result.get("answer", "응답을 생성할 수 없습니다.")
