"""Speed comparison test for v6.2 optimizations.

Tests representative queries from each domain:
- BigQuery: 2 queries (BQ answer formatting now uses Flash)
- Notion: 2 queries (parallel page reads + parallel sheet reads)
- GWS: 1 query (recursion limit added)
- Direct: 1 query (baseline)

Compares against v6.1 baseline timing from QA rounds.
"""

import json
import time
import requests

API_URL = "http://localhost:8100/v1/chat/completions"

TEST_QUERIES = [
    # BigQuery queries (v6.1 avg: ~50s, target: 25-35s)
    {
        "id": "BQ-1",
        "domain": "BigQuery",
        "query": "2025년 1월 미국 매출 합계 알려줘",
        "baseline_s": 50,
    },
    {
        "id": "BQ-2",
        "domain": "BigQuery",
        "query": "태국 쇼피 1월 매출이 얼마야?",
        "baseline_s": 48,
    },
    # Notion queries (v6.1 avg: ~110s, target: 40-60s)
    {
        "id": "NT-1",
        "domain": "Notion",
        "query": "해외 출장 가이드북 내용 보여줘",
        "baseline_s": 110,
    },
    {
        "id": "NT-2",
        "domain": "Notion",
        "query": "틱톡샵 접속 방법 알려줘",
        "baseline_s": 105,
    },
    # Direct LLM (baseline)
    {
        "id": "DT-1",
        "domain": "Direct",
        "query": "SKIN1004가 어떤 회사야?",
        "baseline_s": 10,
    },
]


def send_query(query: str) -> tuple[str, float]:
    """Send a query and return (answer_preview, elapsed_seconds)."""
    payload = {
        "model": "gemini",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    start = time.time()
    try:
        resp = requests.post(API_URL, json=payload, timeout=300)
        elapsed = time.time() - start
        data = resp.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return answer[:200], elapsed
    except Exception as e:
        elapsed = time.time() - start
        return f"ERROR: {e}", elapsed


def main():
    print("=" * 70)
    print("  SKIN1004 AI Agent — Speed Test v6.2 Optimizations")
    print("=" * 70)
    print()

    results = []
    for test in TEST_QUERIES:
        print(f"[{test['id']}] {test['domain']}: {test['query']}")
        print(f"  Baseline (v6.1): ~{test['baseline_s']}s")

        answer_preview, elapsed = send_query(test["query"])
        improvement = test["baseline_s"] - elapsed
        pct = (improvement / test["baseline_s"]) * 100 if test["baseline_s"] > 0 else 0

        print(f"  Result (v6.2): {elapsed:.1f}s  ({pct:+.0f}%)")
        print(f"  Answer: {answer_preview[:100]}...")
        print()

        results.append({
            "id": test["id"],
            "domain": test["domain"],
            "query": test["query"],
            "baseline_s": test["baseline_s"],
            "v62_s": round(elapsed, 1),
            "improvement_s": round(improvement, 1),
            "improvement_pct": round(pct, 1),
        })

    # Summary
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"{'ID':<8} {'Domain':<10} {'v6.1(s)':<10} {'v6.2(s)':<10} {'Delta':<10} {'%':<8}")
    print("-" * 56)
    for r in results:
        print(
            f"{r['id']:<8} {r['domain']:<10} {r['baseline_s']:<10} "
            f"{r['v62_s']:<10} {r['improvement_s']:>+8.1f}  {r['improvement_pct']:>+6.1f}%"
        )

    # Save results
    with open("test_speed_v62_result.txt", "w", encoding="utf-8") as f:
        f.write("SKIN1004 AI Agent — Speed Test v6.2 Optimizations\n")
        f.write("=" * 70 + "\n\n")
        for r in results:
            f.write(f"[{r['id']}] {r['domain']}: {r['query']}\n")
            f.write(f"  v6.1 Baseline: {r['baseline_s']}s\n")
            f.write(f"  v6.2 Result:   {r['v62_s']}s\n")
            f.write(f"  Improvement:   {r['improvement_s']:+.1f}s ({r['improvement_pct']:+.1f}%)\n\n")

        f.write("\nSUMMARY\n" + "-" * 56 + "\n")
        f.write(f"{'ID':<8} {'Domain':<10} {'v6.1(s)':<10} {'v6.2(s)':<10} {'Delta':<10} {'%':<8}\n")
        for r in results:
            f.write(
                f"{r['id']:<8} {r['domain']:<10} {r['baseline_s']:<10} "
                f"{r['v62_s']:<10} {r['improvement_s']:>+8.1f}  {r['improvement_pct']:>+6.1f}%\n"
            )

    print("\nResults saved to test_speed_v62_result.txt")


if __name__ == "__main__":
    main()
