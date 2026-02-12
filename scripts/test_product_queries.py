"""Test various Korean product queries to verify SET mapping works."""
import requests
import json
import time

time.sleep(5)  # Wait for server

test_queries = [
    "앰플 100ml 매출이 해외B2B top5업체 나열해줘",
    "센텔라 크림 2025년 매출",
    "히알루시카 선세럼 국가별 매출 top5",
    "좀비팩 전체 매출",
    "커머넬리 비타민C 앰플 매출",
]

results = []
for q in test_queries:
    try:
        resp = requests.post("http://localhost:8100/v1/chat/completions", json={
            "model": "skin1004-gemini",
            "messages": [{"role": "user", "content": q}],
            "stream": False,
        }, timeout=120)

        if resp.status_code == 200:
            data = resp.json()
            answer = data["choices"][0]["message"]["content"]
            has_data = "결과가 없습니다" not in answer
            results.append((q, "OK" if has_data else "NO DATA", len(answer)))
        else:
            results.append((q, f"ERROR {resp.status_code}", 0))
    except Exception as e:
        results.append((q, f"EXCEPTION: {e}", 0))

with open("C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/test_product_results.txt", "w", encoding="utf-8") as f:
    for q, status, length in results:
        f.write(f"[{status}] ({length} chars) {q}\n")

    # Re-run first query to get full answer
    f.write("\n\n=== Full answer for query 1 ===\n")
    resp = requests.post("http://localhost:8100/v1/chat/completions", json={
        "model": "skin1004-gemini",
        "messages": [{"role": "user", "content": test_queries[0]}],
        "stream": False,
    }, timeout=120)
    if resp.status_code == 200:
        f.write(resp.json()["choices"][0]["message"]["content"])

for q, status, length in results:
    print(f"[{status}] ({length} chars) {q}")
