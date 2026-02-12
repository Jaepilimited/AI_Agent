"""Agentic RAG Agent using LangGraph.

Implements:
- Adaptive RAG (difficulty-based routing)
- Corrective RAG (Tavily web search fallback if docs are irrelevant)
- Self-Reflective RAG (hallucination check → retry)
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from langgraph.graph import END, StateGraph

from app.core.llm import get_llm_client
from app.models.state import AgentState
from app.rag.retriever import get_retriever

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


# --- LangGraph Nodes ---


def retrieve_docs(state: AgentState) -> Dict[str, Any]:
    """Retrieve relevant documents via vector search.

    Args:
        state: Current agent state with user query.

    Returns:
        Updated state with retrieved_docs.
    """
    query = state["query"]
    logger.info("retrieving_docs", query=query[:100])

    try:
        retriever = get_retriever(top_k=5)
        documents = retriever.retrieve(query)
        doc_contents = [doc["content"] for doc in documents]
        logger.info("docs_retrieved", count=len(doc_contents))
        return {"retrieved_docs": doc_contents, "error": None}
    except Exception as e:
        logger.error("doc_retrieval_failed", error=str(e))
        return {"retrieved_docs": [], "error": f"문서 검색 실패: {str(e)}"}


def grade_documents(state: AgentState) -> Dict[str, Any]:
    """Grade retrieved documents for relevance.

    Args:
        state: Current agent state with retrieved_docs.

    Returns:
        Updated state with doc_relevance.
    """
    query = state["query"]
    docs = state.get("retrieved_docs", [])

    if not docs:
        return {"doc_relevance": "no"}

    retriever = get_retriever()
    doc_dicts = [{"content": doc, "id": str(i)} for i, doc in enumerate(docs)]
    relevant_docs, relevance = retriever.grade_documents(query, doc_dicts)

    # Update retrieved_docs with only relevant ones
    relevant_contents = [doc["content"] for doc in relevant_docs]

    logger.info("docs_graded", total=len(docs), relevant=len(relevant_contents))
    return {
        "retrieved_docs": relevant_contents,
        "doc_relevance": relevance,
    }


def web_search(state: AgentState) -> Dict[str, Any]:
    """Perform web search as CRAG fallback when docs aren't relevant.

    Args:
        state: Current agent state.

    Returns:
        Updated state with web_search_results.
    """
    query = state["query"]
    logger.info("performing_web_search", query=query[:100])

    try:
        from tavily import TavilyClient
        from app.config import get_settings

        settings = get_settings()
        if not settings.tavily_api_key:
            logger.warning("tavily_api_key_not_set")
            return {"web_search_results": [], "error": "Tavily API 키가 설정되지 않았습니다."}

        client = TavilyClient(api_key=settings.tavily_api_key)
        results = client.search(query, max_results=3)

        web_results = []
        for result in results.get("results", []):
            web_results.append(
                f"[{result.get('title', '')}]\n{result.get('content', '')}\nURL: {result.get('url', '')}"
            )

        logger.info("web_search_completed", result_count=len(web_results))
        return {"web_search_results": web_results, "error": None}

    except Exception as e:
        logger.error("web_search_failed", error=str(e))
        return {"web_search_results": [], "error": f"웹 검색 실패: {str(e)}"}


def rewrite_query(state: AgentState) -> Dict[str, Any]:
    """Rewrite the query for better retrieval.

    Args:
        state: Current agent state.

    Returns:
        Updated state with rewritten query.
    """
    query = state["query"]
    logger.info("rewriting_query", original=query[:100])

    llm = get_llm_client()
    prompt = f"""사용자의 질문을 문서 검색에 더 적합하도록 재작성하세요.
원래 의미를 유지하면서, 더 구체적이고 검색에 효과적인 키워드를 포함하세요.

원래 질문: {query}

재작성된 질문만 출력하세요."""

    try:
        rewritten = llm.generate(prompt, temperature=0.0)
        logger.info("query_rewritten", rewritten=rewritten[:100])
        return {"query": rewritten.strip()}
    except Exception as e:
        logger.warning("query_rewrite_failed", error=str(e))
        return {}  # Keep original query


def generate_answer(state: AgentState) -> Dict[str, Any]:
    """Generate answer from retrieved documents and/or web results.

    Args:
        state: Current agent state with docs and web results.

    Returns:
        Updated state with answer.
    """
    query = state["query"]
    docs = state.get("retrieved_docs", [])
    web_results = state.get("web_search_results", [])

    # Combine all context
    context_parts = []
    if docs:
        for i, doc in enumerate(docs):
            context_parts.append(f"[문서 {i+1}]\n{doc}")
    if web_results:
        for i, result in enumerate(web_results):
            context_parts.append(f"[웹 검색 {i+1}]\n{result}")

    if not context_parts:
        return {"answer": "관련 문서를 찾을 수 없습니다. 질문을 다시 확인해 주세요."}

    context = "\n\n---\n\n".join(context_parts)

    # Load and fill prompt template
    prompt_template = _load_prompt("rag_generator.txt")
    prompt = prompt_template.replace("{context}", context).replace("{query}", query)

    llm = get_llm_client()
    try:
        answer = llm.generate(prompt, temperature=0.3)
        return {"answer": answer}
    except Exception as e:
        logger.error("answer_generation_failed", error=str(e))
        return {"answer": f"답변 생성 중 오류가 발생했습니다: {str(e)}"}


def check_hallucination(state: AgentState) -> Dict[str, Any]:
    """Self-reflective check for hallucination.

    Args:
        state: Current agent state with answer.

    Returns:
        Updated state with needs_retry flag.
    """
    answer = state.get("answer", "")
    docs = state.get("retrieved_docs", [])
    retry_count = state.get("retry_count", 0)

    if not answer or not docs or retry_count >= 2:
        return {"needs_retry": False}

    llm = get_llm_client()
    context = "\n\n".join(docs[:3])

    prompt = f"""다음 답변이 제공된 문서 내용에 기반한 것인지 판단하세요.

문서:
{context[:1500]}

답변:
{answer[:1000]}

답변이 문서 내용에 기반하면 "grounded", 문서에 없는 내용을 포함하면 "hallucination"으로만 답변하세요."""

    try:
        result = llm.generate(prompt, temperature=0.0).strip().lower()
        if "hallucination" in result:
            logger.warning("hallucination_detected", retry_count=retry_count)
            return {"needs_retry": True, "retry_count": retry_count + 1}
        return {"needs_retry": False}
    except Exception as e:
        logger.warning("hallucination_check_failed", error=str(e))
        return {"needs_retry": False}


# --- Routing Functions ---


def route_after_grading(state: AgentState) -> str:
    """Route based on document relevance grading.

    If relevant → generate_answer
    If not relevant → web_search (CRAG)
    """
    if state.get("doc_relevance") == "yes":
        return "generate_answer"
    return "web_search"


def route_after_hallucination_check(state: AgentState) -> str:
    """Route based on hallucination check.

    If needs retry → rewrite_query → retrieve again
    If good → end
    """
    if state.get("needs_retry") and state.get("retry_count", 0) < 2:
        return "rewrite_query"
    return END


# --- Build Graph ---


def build_rag_agent_graph() -> StateGraph:
    """Build the Agentic RAG LangGraph workflow.

    Returns:
        Compiled LangGraph StateGraph.
    """
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("retrieve_docs", retrieve_docs)
    workflow.add_node("grade_documents", grade_documents)
    workflow.add_node("web_search", web_search)
    workflow.add_node("rewrite_query", rewrite_query)
    workflow.add_node("generate_answer", generate_answer)
    workflow.add_node("check_hallucination", check_hallucination)

    # Define edges
    workflow.set_entry_point("retrieve_docs")
    workflow.add_edge("retrieve_docs", "grade_documents")

    # Conditional: relevant docs → answer, irrelevant → web search
    workflow.add_conditional_edges(
        "grade_documents",
        route_after_grading,
        {
            "generate_answer": "generate_answer",
            "web_search": "web_search",
        },
    )

    # Web search → generate answer
    workflow.add_edge("web_search", "generate_answer")

    # After answer → hallucination check
    workflow.add_edge("generate_answer", "check_hallucination")

    # Conditional: hallucination → retry, good → end
    workflow.add_conditional_edges(
        "check_hallucination",
        route_after_hallucination_check,
        {
            "rewrite_query": "rewrite_query",
            END: END,
        },
    )

    # Retry loop: rewrite → retrieve again
    workflow.add_edge("rewrite_query", "retrieve_docs")

    return workflow.compile()


# Module-level compiled graph
rag_agent = build_rag_agent_graph()


async def run_rag_agent(query: str) -> str:
    """Run the Agentic RAG agent on a query.

    Args:
        query: Natural language question about documents/policies.

    Returns:
        Natural language answer based on retrieved documents.
    """
    initial_state: AgentState = {
        "query": query,
        "route_type": "rag",
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

    logger.info("rag_agent_started", query=query)
    result = rag_agent.invoke(initial_state)
    logger.info("rag_agent_completed", answer_length=len(result.get("answer", "")))
    return result["answer"]
