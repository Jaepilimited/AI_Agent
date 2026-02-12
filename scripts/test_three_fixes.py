"""Test 3 fixes: SQL CTE, Product routing, Chart generation."""
import requests
import time
import json

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}

tests = [
    # Issue 1: SQL CTE — queries that previously triggered CTE generation
    ("SQL-CTE-1", "각 플랫폼 분기별 매출비중은 얼마야?"),
    ("SQL-CTE-2", "2025년 대륙별 매출 비중을 보여줘"),
    ("SQL-CTE-3", "올해 각 국가 매출에서 상위 3개 국가의 주요 판매 플랫폼은?"),

    # Issue 2: Product routing — should go to BigQuery, not direct
    ("ROUTE-1", "제품 리스트 알려줘"),
    ("ROUTE-2", "전체 제품 목록 보여줘"),
    ("ROUTE-3", "어떤 제품이 있어?"),
    ("ROUTE-4", "제품 종류가 몇 개야?"),

    # Issue 3: Chart — queries that previously caused string→float error
    ("CHART-1", "2025년 팀별 매출 비교 차트 그려줘"),
    ("CHART-2", "인도네시아 제품별 매출 top 5 보여줘"),
    ("CHART-3", "2025년 대륙별 매출 차트로 보여줘"),
]

results = []
for tag, q in tests:
    print(f"\n[{tag}] {q}", flush=True)
    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": "skin1004-Search",
            "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=180)
        elapsed = time.time() - t0
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]

        # Detect issues
        has_sql_error = any(kw in answer for kw in [
            "Expected end of input", "Syntax error", "SQL 실행 실패",
        ])
        has_notion_content = "Notion" in answer
        has_bigquery = "BigQuery" in answer or "총" in answer or "매출" in answer
        has_chart = "![chart]" in answer
        has_connection_error = any(kw in answer for kw in [
            "오류가 발생", "ConnectError", "조회할 수 없",
        ])

        # Determine result status
        if tag.startswith("SQL-CTE"):
            status = "FAIL" if has_sql_error else "OK"
        elif tag.startswith("ROUTE"):
            # Should NOT be generic direct LLM response
            is_direct = "일반적인" in answer or "도움이 필요" in answer or len(answer) < 100
            status = "FAIL" if (is_direct and not has_bigquery) else "OK"
        elif tag.startswith("CHART"):
            status = "OK"  # As long as no crash
            if has_sql_error:
                status = "SQL_ERR"

        preview = answer[:150].replace('\n', ' ')
        chart_tag = " [CHART]" if has_chart else ""
        print(f"  -> {status} ({elapsed:.1f}s){chart_tag} {preview}", flush=True)
        results.append({"tag": tag, "q": q, "status": status, "time": elapsed, "chart": has_chart, "answer_len": len(answer)})
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  -> EXCEPTION ({elapsed:.1f}s) {e}", flush=True)
        results.append({"tag": tag, "q": q, "status": "EXCEPTION", "time": elapsed, "chart": False, "answer_len": 0})

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

for category in ["SQL-CTE", "ROUTE", "CHART"]:
    cat_results = [r for r in results if r["tag"].startswith(category)]
    ok = sum(1 for r in cat_results if r["status"] == "OK")
    total = len(cat_results)
    charts = sum(1 for r in cat_results if r["chart"])
    avg_time = sum(r["time"] for r in cat_results) / total if total > 0 else 0
    print(f"\n{category}: {ok}/{total} OK (avg {avg_time:.1f}s, charts: {charts})")
    for r in cat_results:
        mark = "OK" if r["status"] == "OK" else "FAIL"
        chart_tag = " [CHART]" if r["chart"] else ""
        print(f"  [{mark}]{chart_tag} ({r['time']:.1f}s) {r['q'][:50]}")

total_ok = sum(1 for r in results if r["status"] == "OK")
print(f"\nTotal: {total_ok}/{len(results)} OK")
