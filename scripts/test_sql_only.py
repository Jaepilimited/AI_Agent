"""Check what SQL is generated for ampoule 100ml query."""
import sys
sys.path.insert(0, "C:/Users/DB_PC/Desktop/python_bcj/AI_Agent")

from app.agents.sql_agent import build_sql_agent_graph

graph = build_sql_agent_graph()

initial_state = {
    "query": "앰플 100ml 2월 매출이 얼마야?",
    "route_type": "text_to_sql",
    "generated_sql": None, "sql_valid": None, "sql_result": None,
    "retrieved_docs": None, "doc_relevance": None, "web_search_results": None,
    "answer": "", "needs_retry": False, "retry_count": 0, "error": None,
    "messages": None, "conversation_context": "", "model_type": "gemini",
}

result = graph.invoke(initial_state)
print("=== SQL ===")
print(result.get("generated_sql"))
print(f"\n=== Rows: {len(result.get('sql_result') or [])} ===")
if result.get("sql_result"):
    for r in result["sql_result"][:10]:
        print(r)
