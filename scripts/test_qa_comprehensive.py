"""Comprehensive QA Test Suite v6.0.2 — 2026.02.12"""
import requests
import time
import json
import sys

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}

# Test cases: (tag, model, query, expected_route, validation_keywords)
tests = [
    # === BigQuery Sales ===
    ("BQ-01", "skin1004-Search", "2025년 1월 전체 매출 합계 알려줘", "bigquery", ["매출", "원"]),
    ("BQ-02", "skin1004-Search", "2024년 분기별 미국 매출 추이 보여줘", "bigquery", ["분기", "미국"]),
    ("BQ-03", "skin1004-Search", "인도네시아 쇼피 2025년 월별 매출 알려줘", "bigquery", ["인도네시아"]),
    ("BQ-04", "skin1004-Search", "2025년 팀별 매출 순위 top 5", "bigquery", ["팀"]),
    ("BQ-05", "skin1004-Analysis", "2024년 vs 2025년 대륙별 매출 비교해줘", "bigquery", ["대륙"]),
    ("BQ-06", "skin1004-Search", "틱톡샵 국가별 매출 현황 알려줘", "bigquery", ["틱톡"]),
    ("BQ-07", "skin1004-Search", "2025년 상반기 플랫폼별 매출 비중은?", "bigquery", ["플랫폼"]),
    ("BQ-08", "skin1004-Analysis", "아마존 미국 2025년 월별 매출 트렌드", "bigquery", ["아마존"]),
    
    # === BigQuery Product ===
    ("PROD-01", "skin1004-Search", "제품 리스트 알려줘", "bigquery", []),
    ("PROD-02", "skin1004-Search", "전체 제품 목록 보여줘", "bigquery", []),
    ("PROD-03", "skin1004-Search", "제품 종류가 몇 개야?", "bigquery", []),
    ("PROD-04", "skin1004-Search", "센텔라 관련 제품 매출 알려줘", "bigquery", ["센텔라"]),
    
    # === Chart Generation ===
    ("CHART-01", "skin1004-Search", "2025년 팀별 매출 비교 차트 그려줘", "bigquery", []),
    ("CHART-02", "skin1004-Search", "2025년 대륙별 매출 차트로 보여줘", "bigquery", []),
    ("CHART-03", "skin1004-Search", "월별 매출 추이 그래프 보여줘", "bigquery", []),
    
    # === Notion ===
    ("NOTION-01", "skin1004-Search", "노션에서 해외 출장 가이드북 보여줘", "notion", []),
    ("NOTION-02", "skin1004-Search", "노션에서 틱톡샵 접속 방법 알려줘", "notion", []),
    ("NOTION-03", "skin1004-Search", "노션에서 데이터 분석 파트 정보 알려줘", "notion", []),
    ("NOTION-04", "skin1004-Search", "노션에서 EAST 2026 업무파악 보여줘", "notion", []),
    
    # === GWS ===
    ("GWS-01", "skin1004-Search", "오늘 일정 알려줘", "gws", []),
    ("GWS-02", "skin1004-Search", "이번주 남은 일정 보여줘", "gws", []),
    ("GWS-03", "skin1004-Search", "최근 받은 중요 메일 보여줘", "gws", []),
    ("GWS-04", "skin1004-Search", "내 드라이브에서 최근 파일 찾아줘", "gws", []),
    
    # === Direct LLM ===
    ("DIRECT-01", "skin1004-Search", "SKU가 뭐야?", "direct", []),
    ("DIRECT-02", "skin1004-Analysis", "SKIN1004 브랜드에 대해 알려줘", "direct", []),
    ("DIRECT-03", "skin1004-Search", "B2B와 B2C의 차이점은?", "direct", []),
    
    # === Edge Cases ===
    ("EDGE-01", "skin1004-Search", "2025년 각 플랫폼 분기별 매출비중은 얼마야?", "bigquery", []),
    ("EDGE-02", "skin1004-Search", "어떤 제품이 있어?", "bigquery", []),
]

results = []
print(f"\n{'='*70}")
print(f"SKIN1004 AI Agent — Comprehensive QA Test")
print(f"Date: 2026-02-12 | Tests: {len(tests)}")
print(f"{'='*70}")

for i, (tag, model, q, expected_route, keywords) in enumerate(tests, 1):
    print(f"\n[{i}/{len(tests)}] [{tag}] {q}", flush=True)
    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": model,
            "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=180)
        elapsed = time.time() - t0
        
        if resp.status_code != 200:
            print(f"  -> HTTP_ERR {resp.status_code} ({elapsed:.1f}s)", flush=True)
            results.append({"tag": tag, "model": model, "q": q, "status": "HTTP_ERR", "time": elapsed, "chart": False, "answer_len": 0, "route": expected_route})
            continue
            
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        
        # Detect features
        has_chart = "![chart]" in answer or "![Chart]" in answer
        has_error = any(kw in answer for kw in ["오류가 발생", "SQL 실행 실패", "Expected end of input", "Syntax error", "ConnectError"])
        has_connection_error = any(kw in answer for kw in ["ConnectError", "ReadError", "RemoteProtocolError"])
        answer_len = len(answer)
        
        # Status
        if has_error:
            status = "ERROR"
        elif answer_len < 30:
            status = "SHORT"
        else:
            status = "OK"
        
        chart_tag = " [CHART]" if has_chart else ""
        preview = answer[:120].replace('\n', ' ')
        print(f"  -> {status} ({elapsed:.1f}s, {answer_len}chars){chart_tag} {preview}", flush=True)
        results.append({"tag": tag, "model": model, "q": q, "status": status, "time": elapsed, "chart": has_chart, "answer_len": answer_len, "route": expected_route})
    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        print(f"  -> TIMEOUT ({elapsed:.1f}s)", flush=True)
        results.append({"tag": tag, "model": model, "q": q, "status": "TIMEOUT", "time": elapsed, "chart": False, "answer_len": 0, "route": expected_route})
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  -> EXCEPTION ({elapsed:.1f}s) {e}", flush=True)
        results.append({"tag": tag, "model": model, "q": q, "status": "EXCEPTION", "time": elapsed, "chart": False, "answer_len": 0, "route": expected_route})

# === Summary ===
print(f"\n{'='*70}")
print("SUMMARY")
print(f"{'='*70}")

categories = {
    "BigQuery Sales": "BQ-",
    "Product": "PROD-",
    "Chart": "CHART-",
    "Notion": "NOTION-",
    "GWS": "GWS-",
    "Direct": "DIRECT-",
    "Edge Cases": "EDGE-",
}

for cat_name, prefix in categories.items():
    cat_results = [r for r in results if r["tag"].startswith(prefix)]
    if not cat_results:
        continue
    ok = sum(1 for r in cat_results if r["status"] == "OK")
    total = len(cat_results)
    charts = sum(1 for r in cat_results if r["chart"])
    avg_time = sum(r["time"] for r in cat_results) / total
    print(f"\n{cat_name}: {ok}/{total} OK (avg {avg_time:.1f}s, charts: {charts})")
    for r in cat_results:
        mark = "OK" if r["status"] == "OK" else r["status"]
        ct = " [CHART]" if r["chart"] else ""
        print(f"  [{mark}]{ct} ({r['time']:.1f}s, {r['answer_len']}ch) {r['q'][:50]}")

total_ok = sum(1 for r in results if r["status"] == "OK")
total_charts = sum(1 for r in results if r["chart"])
total_time = sum(r["time"] for r in results)
avg_time = total_time / len(results) if results else 0
print(f"\n{'='*70}")
print(f"Total: {total_ok}/{len(results)} OK | Charts: {total_charts} | Avg: {avg_time:.1f}s | Total: {total_time:.0f}s")
print(f"{'='*70}")

# Save results
with open("test_qa_comprehensive_result.txt", "w", encoding="utf-8") as f:
    f.write(f"SKIN1004 AI Agent — Comprehensive QA Test\n")
    f.write(f"Date: 2026-02-12 | Tests: {len(results)}\n")
    f.write(f"Result: {total_ok}/{len(results)} OK | Charts: {total_charts} | Avg: {avg_time:.1f}s\n\n")
    for cat_name, prefix in categories.items():
        cat_results = [r for r in results if r["tag"].startswith(prefix)]
        if not cat_results:
            continue
        ok = sum(1 for r in cat_results if r["status"] == "OK")
        total = len(cat_results)
        f.write(f"{cat_name}: {ok}/{total} OK\n")
        for r in cat_results:
            mark = "OK" if r["status"] == "OK" else r["status"]
            ct = " [CHART]" if r["chart"] else ""
            f.write(f"  [{mark}]{ct} ({r['time']:.1f}s, {r['answer_len']}ch) {r['q']}\n")
        f.write("\n")
    # Also save full answers for review
    f.write("\n" + "="*70 + "\nFULL ANSWERS\n" + "="*70 + "\n")
    for r in results:
        f.write(f"\n[{r['tag']}] {r['q']}\n")
        f.write(f"Status: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['answer_len']}\n")

print(f"\nResults saved to test_qa_comprehensive_result.txt")
