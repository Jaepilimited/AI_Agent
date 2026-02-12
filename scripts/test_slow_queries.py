"""Re-test the slowest queries (100s+) on v6.2 to measure improvement.

Criteria:
- >= 200s = FAILURE (red)
- >= 100s = WARNING (yellow)
- < 100s  = OK (green)
"""
import json
import time
import requests

API_URL = "http://localhost:8100/v1/chat/completions"

# Top slowest queries from each domain
SLOW_QUERIES = [
    # Notion 200s+ (were 213-302s)
    {"id": "NT-01", "domain": "Notion", "query": "노션에서 해외 출장 시 항공권 예약부터 결제까지의 전체 절차를 단계별로 상세히 알려줘", "prev_s": 213.1},
    {"id": "NT-04", "domain": "Notion", "query": "노션에서 틱톡샵 운영 시 주의사항이나 팁이 있으면 정리해줘", "prev_s": 269.7},
    {"id": "R2-NT-02", "domain": "Notion", "query": "노션의 틱톡샵US 대시보드에서 어필리에잇(affiliate) 운영 전략을 자세히 설명해줘", "prev_s": 205.6},
    # Notion 100-200s
    {"id": "R2-NT-03", "domain": "Notion", "query": "노션에서 데이터 분석 파트에 대한 정보를 알려줘. VM 인스턴스 사용법이나 자동화 코드 실행 방법", "prev_s": 174.5},
    {"id": "R2-NT-12", "domain": "Notion", "query": "노션 문서 전체를 기반으로, EAST팀과 WEST팀의 업무 범위와 관리 플랫폼/국가의 차이점을 분석해줘", "prev_s": 127.6},
    # GWS 100-170s
    {"id": "GWS-06", "domain": "GWS", "query": "이번 주와 다음 주 2주간의 전체 일정을 요일별로 정리해줘", "prev_s": 176.8},
    {"id": "GWS-03", "domain": "GWS", "query": "메일에서 'SKIN1004' 또는 '스킨1004' 관련 최근 메일을 검색해줘", "prev_s": 169.9},
    # BigQuery 100-170s
    {"id": "BQ-17", "domain": "BigQuery", "query": "2025년 일본 시장에서 아마존, 라쿠텐, 큐텐 3개 플랫폼의 매출 비중을 분석해줘", "prev_s": 171.1},
    {"id": "R2-BQ-05", "domain": "BigQuery", "query": "인도네시아 시장의 쇼피, 라자다, 틱톡, 토코피디아 4개 플랫폼 매출 추이를 2024년 하반기부터 2025년까지 분기별로 보여줘", "prev_s": 157.7},
    {"id": "BQ-16", "domain": "BigQuery", "query": "선크림(Sun) 제품의 2024년 vs 2025년 월별 판매량 추이를 비교해줘", "prev_s": 130.2},
]


def send_query(query: str) -> tuple[str, float]:
    payload = {
        "model": "gemini",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    start = time.time()
    try:
        resp = requests.post(API_URL, json=payload, timeout=320)
        elapsed = time.time() - start
        data = resp.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return answer[:150], elapsed
    except Exception as e:
        elapsed = time.time() - start
        return f"ERROR: {e}", elapsed


def classify(seconds: float) -> str:
    if seconds >= 200:
        return "FAIL"
    elif seconds >= 100:
        return "WARN"
    return "OK"


def main():
    print("=" * 75)
    print("  Slow Query Re-test on v6.2")
    print("  Criteria: >= 200s = FAIL, >= 100s = WARN, < 100s = OK")
    print("=" * 75)
    print()

    results = []
    for q in SLOW_QUERIES:
        print(f"[{q['id']}] {q['domain']}: {q['query'][:60]}...")
        print(f"  Previous: {q['prev_s']:.1f}s ({classify(q['prev_s'])})")

        answer, elapsed = send_query(q["query"])
        grade = classify(elapsed)
        delta = q["prev_s"] - elapsed
        pct = (delta / q["prev_s"]) * 100

        marker = {"OK": "OK", "WARN": "!! WARN", "FAIL": "*** FAIL"}[grade]
        print(f"  v6.2:     {elapsed:.1f}s ({marker}) | Delta: {delta:+.1f}s ({pct:+.0f}%)")
        print(f"  Answer:   {answer[:80]}...")
        print()

        results.append({
            "id": q["id"],
            "domain": q["domain"],
            "query": q["query"][:60],
            "prev_s": q["prev_s"],
            "v62_s": round(elapsed, 1),
            "grade": grade,
            "delta": round(delta, 1),
        })

    # Summary
    print("=" * 75)
    print("  SUMMARY")
    print("=" * 75)
    print(f"{'ID':<12} {'Domain':<10} {'Prev(s)':<10} {'v6.2(s)':<10} {'Grade':<8} {'Delta':<10}")
    print("-" * 60)

    ok_count = warn_count = fail_count = 0
    for r in results:
        print(f"{r['id']:<12} {r['domain']:<10} {r['prev_s']:<10} {r['v62_s']:<10} {r['grade']:<8} {r['delta']:>+8.1f}s")
        if r["grade"] == "OK":
            ok_count += 1
        elif r["grade"] == "WARN":
            warn_count += 1
        else:
            fail_count += 1

    print("-" * 60)
    print(f"OK: {ok_count}, WARN: {warn_count}, FAIL: {fail_count}")

    # Save
    with open("test_slow_queries_result.txt", "w", encoding="utf-8") as f:
        f.write("Slow Query Re-test on v6.2\n")
        f.write(f"Criteria: >= 200s = FAIL, >= 100s = WARN, < 100s = OK\n")
        f.write("=" * 75 + "\n\n")
        for r in results:
            f.write(f"[{r['id']}] {r['domain']}: {r['query']}\n")
            f.write(f"  Previous: {r['prev_s']}s\n")
            f.write(f"  v6.2:     {r['v62_s']}s ({r['grade']})\n")
            f.write(f"  Delta:    {r['delta']:+.1f}s\n\n")
        f.write(f"\nSummary: OK={ok_count}, WARN={warn_count}, FAIL={fail_count}\n")

    print(f"\nSaved to test_slow_queries_result.txt")

    # Desktop notification
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app.core.notify import notify
        notify(
            "AI Agent 테스트 완료",
            f"Slow Query 테스트: OK={ok_count} | WARN={warn_count} | FAIL={fail_count}",
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
