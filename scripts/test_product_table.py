"""Test Product table queries - quantity questions should use Product table."""
import requests
import time

time.sleep(7)

test_queries = [
    ("수량 질문 → Product", "센텔라 앰플 100ml 2월 수량이 몇 개야?"),
    ("매출 질문 → SALES_ALL", "센텔라 앰플 100ml 2월 매출이 얼마야?"),
    ("제품 수량 top → Product", "인도네시아에서 가장 많이 팔린 제품 TOP 5 수량"),
    ("커먼랩스 수량 → Product", "커먼랩스 비타민C 앰플 2025년 수량"),
]

for label, q in test_queries:
    try:
        resp = requests.post("http://localhost:8100/v1/chat/completions", json={
            "model": "skin1004-gemini",
            "messages": [{"role": "user", "content": q}],
            "stream": False,
        }, timeout=120)

        if resp.status_code == 200:
            answer = resp.json()["choices"][0]["message"]["content"]
            has_data = "결과가 없습니다" not in answer
            with open("C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/test_product_table_result.txt", "a", encoding="utf-8") as f:
                f.write(f"=== [{label}] Q: {q} ===\n")
                f.write(f"Status: {'OK' if has_data else 'NO DATA'} ({len(answer)} chars)\n")
                f.write(f"{answer[:800]}\n\n{'='*60}\n\n")
            print(f"[{'OK' if has_data else 'NO DATA'}] ({len(answer)}) {label}")
        else:
            print(f"[ERR {resp.status_code}] {label}")
    except Exception as e:
        print(f"[EXCEPTION] {label}: {e}")
