"""
Comprehensive Sales_ALL_Backup domain test script.
Sends 8 unique query chains (main + follow-up) to the FastAPI server.
"""
import requests
import json
import time
import sys

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
TIMEOUT = 120
OUTPUT_FILE = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_results_sales.txt"

# Define test chains: (category, main_question, follow_up_question)
TEST_CHAINS = [
    (
        "월별 매출 추이",
        "2025년 하반기 월별 전체 매출 추이를 보여줘",
        "그 중에서 매출이 가장 높았던 달과 가장 낮았던 달은?"
    ),
    (
        "플랫폼 비교",
        "2025년 아마존과 쇼피의 연간 매출 비교해줘",
        "두 플랫폼의 분기별 성장률은 어떻게 돼?"
    ),
    (
        "국가별 분석",
        "동남아시아 국가별 2025년 총 매출 순위 알려줘",
        "그 중 전년 대비 성장률이 가장 높은 국가는?"
    ),
    (
        "제품 분석",
        "2025년 가장 많이 팔린 제품 TOP 10 알려줘",
        "그 제품들의 평균 판매 단가는 얼마야?"
    ),
    (
        "팀별 실적",
        "2025년 팀별 매출 실적을 비교해줘",
        "EAST팀과 WEST팀의 분기별 매출 추이는?"
    ),
    (
        "시계열 분석",
        "2024년 대비 2025년 월별 매출 증감률을 보여줘",
        "가장 큰 폭으로 증가한 월은 언제야?"
    ),
    (
        "틱톡샵 분석",
        "틱톡샵 US의 2025년 월별 매출과 주문건수 알려줘",
        "틱톡샵 US의 평균 객단가 추이는?"
    ),
    (
        "대륙별 분석",
        "2025년 대륙별 매출 비중을 보여줘",
        "북미 매출에서 아마존이 차지하는 비중은?"
    ),
]


def send_query(question: str) -> tuple:
    """Send a query to the API and return (answer, status)."""
    try:
        resp = requests.post(
            API_URL,
            json={
                "model": "skin1004-Search",
                "messages": [{"role": "user", "content": question}]
            },
            headers=HEADERS,
            timeout=TIMEOUT
        )
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}: {resp.text[:300]}", "ERROR"

        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        return answer, "OK"
    except requests.exceptions.Timeout:
        return "TIMEOUT after 120s", "ERROR"
    except requests.exceptions.ConnectionError:
        return "CONNECTION ERROR - server not reachable", "ERROR"
    except Exception as e:
        return f"EXCEPTION: {str(e)[:300]}", "ERROR"


def run_all_tests():
    results = []
    total = 0
    passed = 0
    failed = 0

    print(f"Starting {len(TEST_CHAINS)} test chains...\n")

    for i, (category, q1, q2) in enumerate(TEST_CHAINS, 1):
        print(f"=== Test {i}: {category} ===")

        # Main question
        print(f"  Q1: {q1}")
        a1, s1 = send_query(q1)
        total += 1
        if s1 == "OK":
            passed += 1
            print(f"  A1 Status: OK ({len(a1)} chars)")
        else:
            failed += 1
            print(f"  A1 Status: ERROR - {a1[:100]}")

        # Small delay between requests
        time.sleep(2)

        # Follow-up question
        print(f"  Q2: {q2}")
        a2, s2 = send_query(q2)
        total += 1
        if s2 == "OK":
            passed += 1
            print(f"  A2 Status: OK ({len(a2)} chars)")
        else:
            failed += 1
            print(f"  A2 Status: ERROR - {a2[:100]}")

        results.append((i, category, q1, a1, s1, q2, a2, s2))

        # Delay between test chains
        time.sleep(2)
        print()

    # Write results to file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("SKIN1004 AI Agent - BigQuery Sales_ALL_Backup Comprehensive Test Results\n")
        f.write(f"Test Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"API Endpoint: {API_URL}\n")
        f.write("=" * 80 + "\n\n")

        for (idx, cat, q1, a1, s1, q2, a2, s2) in results:
            f.write("---\n")
            f.write(f"### Test {idx}: {cat}\n")
            f.write(f"**Q1**: {q1}\n")
            f.write(f"**A1**: {a1[:500]}\n")
            f.write(f"**Status**: {s1}\n")
            f.write(f"**Q2 (Follow-up)**: {q2}\n")
            f.write(f"**A2**: {a2[:500]}\n")
            f.write(f"**Status**: {s2}\n")
            f.write("---\n\n")

        f.write("=" * 80 + "\n")
        f.write("### Summary\n")
        f.write(f"- Total individual queries: {total}\n")
        f.write(f"- Passed (OK): {passed}\n")
        f.write(f"- Failed (ERROR): {failed}\n")
        f.write(f"- Success rate: {passed/total*100:.1f}%\n")
        f.write(f"- Test chains: {len(TEST_CHAINS)}\n")
        f.write("=" * 80 + "\n")

        # Also write full answers for reference
        f.write("\n\n" + "=" * 80 + "\n")
        f.write("FULL ANSWERS (for detailed review)\n")
        f.write("=" * 80 + "\n\n")

        for (idx, cat, q1, a1, s1, q2, a2, s2) in results:
            f.write(f"\n{'='*60}\n")
            f.write(f"Test {idx}: {cat}\n")
            f.write(f"{'='*60}\n\n")
            f.write(f"Q1: {q1}\n")
            f.write(f"Status: {s1}\n")
            f.write(f"Full Answer:\n{a1}\n\n")
            f.write(f"Q2: {q2}\n")
            f.write(f"Status: {s2}\n")
            f.write(f"Full Answer:\n{a2}\n\n")

    print(f"\nResults written to: {OUTPUT_FILE}")
    print(f"\n{'='*50}")
    print(f"SUMMARY: {passed}/{total} passed ({passed/total*100:.1f}%)")
    print(f"{'='*50}")

    return passed, failed


if __name__ == "__main__":
    passed, failed = run_all_tests()
    sys.exit(0 if failed == 0 else 1)
