"""Test Notion Agent with retry logic - sequential 8 queries"""
import requests
import time

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}

queries = [
    "노션에서 해외 출장 가이드북 내용 알려줘",
    "노션에서 틱톡샵 접속 방법 알려줘",
    "노션에서 법인 태블릿 정보 알려줘",
    "노션에서 EAST 2026 업무파악 내용 보여줘",
    "노션에서 DB daily 광고 입력 업무 알려줘",
    "노션에서 데이터 분석 파트 정보 보여줘",
    "노션에서 WEST 틱톡샵US 대시보드 보여줘",
    "노션에서 zombiepack 번들 제품 정보 알려줘",
]

results = []
for i, q in enumerate(queries):
    print(f"\n[{i+1}/8] {q[:40]}...", flush=True)
    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": "skin1004-Search",
            "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=180)
        elapsed = time.time() - t0
        answer = resp.json()["choices"][0]["message"]["content"]

        # Check if answer contains error
        has_error = any(kw in answer for kw in [
            "오류가 발생", "ConnectError", "ReadError",
            "RemoteProtocolError", "ConnectTimeout",
        ])
        status = "ERROR" if has_error else "OK"

        # Check if answer has actual Notion content
        has_notion = "Notion 문서" in answer or "네, Notion" in answer
        source = "NOTION" if has_notion else ("ERROR" if has_error else "FALLBACK")

        print(f"  → {status} ({elapsed:.1f}s) [{source}] {answer[:100]}", flush=True)
        results.append({"q": q, "status": status, "source": source, "time": elapsed})
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  → EXCEPTION ({elapsed:.1f}s) {e}", flush=True)
        results.append({"q": q, "status": "EXCEPTION", "source": "N/A", "time": elapsed})

# Summary
print("\n" + "="*60)
print("SUMMARY")
print("="*60)
ok = sum(1 for r in results if r["status"] == "OK")
notion = sum(1 for r in results if r["source"] == "NOTION")
errors = sum(1 for r in results if r["status"] == "ERROR")
avg_time = sum(r["time"] for r in results) / len(results)
print(f"Total: {len(results)}")
print(f"OK: {ok} / ERROR: {errors}")
print(f"Notion content: {notion}/{len(results)}")
print(f"Avg time: {avg_time:.1f}s")

for r in results:
    mark = "✓" if r["status"] == "OK" else "✗"
    print(f"  {mark} [{r['source']:8s}] ({r['time']:5.1f}s) {r['q'][:50]}")
