"""Test product name display format - should show English SET names."""
import requests
import time

time.sleep(6)

test_queries = [
    "앰플 100ml 2월 매출이 얼마야?",
    "커먼랩스 비타민C 제품 매출",
    "센텔라 크림 2025년 제품별 매출",
]

for q in test_queries:
    resp = requests.post("http://localhost:8100/v1/chat/completions", json={
        "model": "skin1004-gemini",
        "messages": [{"role": "user", "content": q}],
        "stream": False,
    }, timeout=120)

    if resp.status_code == 200:
        answer = resp.json()["choices"][0]["message"]["content"]
        with open("C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/test_format_result.txt", "a", encoding="utf-8") as f:
            f.write(f"=== Q: {q} ===\n")
            f.write(f"Answer ({len(answer)} chars):\n{answer}\n\n{'='*60}\n\n")
        print(f"[OK] {q} -> {len(answer)} chars")
    else:
        print(f"[ERR {resp.status_code}] {q}")
