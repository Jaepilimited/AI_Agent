"""
SKIN1004 AI System - Product Table Domain Comprehensive Test
Tests 6 unique query chains with follow-up questions against the BigQuery Product table.
"""
import requests
import json
import time
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
TIMEOUT = 120
OUTPUT_FILE = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_results_product.txt"

# Define test chains: (category, q1, q2_followup)
TEST_CHAINS = [
    (
        "Product Count",
        "현재 등록된 전체 제품 수는 몇 개야?",
        "그 중 활성 상태인 제품은 몇 개야?"
    ),
    (
        "Zombie Pack Products",
        "zombie pack 관련 제품 리스트 알려줘",
        "zombie pack 제품들의 판매 현황은?"
    ),
    (
        "Centella Products",
        "센텔라 관련 제품 목록 보여줘",
        "센텔라 앰플이 가장 많이 팔린 국가는?"
    ),
    (
        "New Products 2025",
        "2025년 신규 등록된 제품이 있어?",
        "최근 출시된 제품들의 초기 판매 실적은?"
    ),
    (
        "Madagascar Centella Ampoule Revenue",
        "마다가스카르 센텔라 앰플의 총 매출 알려줘",
        "그 제품의 월별 매출 추이는?"
    ),
    (
        "Bundle Products",
        "번들 제품 리스트를 보여줘",
        "번들 제품과 단품의 매출 비교해줘"
    ),
]


def send_query(question: str) -> dict:
    """Send a query to the API and return the response data."""
    payload = {
        "model": "skin1004-Search",
        "messages": [{"role": "user", "content": question}],
    }
    try:
        resp = requests.post(API_URL, json=payload, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        return {"answer": answer, "status": "OK", "error": None}
    except requests.exceptions.Timeout:
        return {"answer": "", "status": "ERROR", "error": "Request timed out (120s)"}
    except requests.exceptions.ConnectionError:
        return {"answer": "", "status": "ERROR", "error": "Connection refused - is server running on port 8100?"}
    except Exception as e:
        return {"answer": "", "status": "ERROR", "error": str(e)}


def truncate(text: str, max_len: int = 500) -> str:
    """Truncate text to max_len characters."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def main():
    results = []
    total_tests = len(TEST_CHAINS) * 2  # Each chain has 2 questions
    passed = 0
    failed = 0

    print(f"Starting Product Domain Tests at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total test chains: {len(TEST_CHAINS)}, Total questions: {total_tests}")
    print("=" * 70)

    for idx, (category, q1, q2) in enumerate(TEST_CHAINS, 1):
        print(f"\n--- Test {idx}: {category} ---")

        # Question 1
        print(f"  Q1: {q1}")
        start = time.time()
        r1 = send_query(q1)
        elapsed1 = time.time() - start
        print(f"  A1 Status: {r1['status']} ({elapsed1:.1f}s)")
        if r1["status"] == "OK":
            passed += 1
            print(f"  A1 Preview: {truncate(r1['answer'], 100)}")
        else:
            failed += 1
            print(f"  A1 Error: {r1['error']}")

        # Small delay between requests
        time.sleep(1)

        # Question 2 (Follow-up)
        print(f"  Q2: {q2}")
        start = time.time()
        r2 = send_query(q2)
        elapsed2 = time.time() - start
        print(f"  A2 Status: {r2['status']} ({elapsed2:.1f}s)")
        if r2["status"] == "OK":
            passed += 1
            print(f"  A2 Preview: {truncate(r2['answer'], 100)}")
        else:
            failed += 1
            print(f"  A2 Error: {r2['error']}")

        results.append({
            "test_num": idx,
            "category": category,
            "q1": q1,
            "a1": r1["answer"] if r1["status"] == "OK" else f"[ERROR] {r1['error']}",
            "s1": r1["status"],
            "t1": elapsed1,
            "q2": q2,
            "a2": r2["answer"] if r2["status"] == "OK" else f"[ERROR] {r2['error']}",
            "s2": r2["status"],
            "t2": elapsed2,
        })

        # Delay between test chains
        time.sleep(1)

    # Write results to file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("SKIN1004 AI System - Product Table Domain Test Results\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"API Endpoint: {API_URL}\n")
        f.write(f"Model: skin1004-Search\n")
        f.write("=" * 70 + "\n\n")

        for r in results:
            f.write("---\n")
            f.write(f"### Test {r['test_num']}: {r['category']}\n")
            f.write(f"**Q1**: {r['q1']}\n")
            f.write(f"**A1**: {truncate(r['a1'])}\n")
            f.write(f"**Status**: {r['s1']} (Response time: {r['t1']:.1f}s)\n")
            f.write(f"**Q2 (Follow-up)**: {r['q2']}\n")
            f.write(f"**A2**: {truncate(r['a2'])}\n")
            f.write(f"**Status**: {r['s2']} (Response time: {r['t2']:.1f}s)\n")
            f.write("---\n\n")

        f.write("=" * 70 + "\n")
        f.write("SUMMARY\n")
        f.write("=" * 70 + "\n")
        f.write(f"Total Questions: {total_tests}\n")
        f.write(f"Passed (OK):    {passed}\n")
        f.write(f"Failed (ERROR): {failed}\n")
        f.write(f"Pass Rate:      {passed/total_tests*100:.1f}%\n")
        f.write("=" * 70 + "\n")

    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {passed}/{total_tests} passed, {failed}/{total_tests} failed")
    print(f"Results written to: {OUTPUT_FILE}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
