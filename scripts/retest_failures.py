"""Re-test all previously failed/error queries from marketing QA 500."""
import json
import requests
import time
from collections import Counter
from pathlib import Path

API = "http://localhost:3001/v1/chat/completions"
MODEL = "skin1004-Analysis"
RESULT_FILE = Path(__file__).resolve().parent.parent / "test_results_marketing.json"
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "retest_results.json"

def main():
    data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    fails = [r for r in data if r["status"] in ("FAIL", "ERROR")]

    print(f"Re-testing {len(fails)} previously failed queries...")
    results = []

    for i, r in enumerate(fails):
        q = r["query"]
        start = time.time()
        try:
            resp = requests.post(
                API,
                json={"model": MODEL, "messages": [{"role": "user", "content": q}], "stream": False},
                timeout=120,
            )
            elapsed = time.time() - start
            ans = resp.json()["choices"][0]["message"]["content"]
            ans_len = len(ans)
            if ans_len < 20:
                status = "EMPTY"
            elif elapsed >= 90:
                status = "FAIL"
            elif elapsed >= 60:
                status = "WARN"
            else:
                status = "OK"
        except Exception as e:
            elapsed = time.time() - start
            status = "ERROR"
            ans_len = 0
            ans = str(e)

        entry = {
            "id": r["id"],
            "query": q,
            "status": status,
            "time": round(elapsed, 1),
            "ans_len": ans_len,
            "old_status": r["status"],
            "old_time": r["time"],
        }
        results.append(entry)
        icon = {"OK": "+", "WARN": "!", "FAIL": "X", "ERROR": "E", "EMPTY": "0"}.get(status, "?")
        print(f"  [{i+1}/{len(fails)}] [{r['id']}] {icon} {status} {elapsed:.1f}s (was {r['status']} {r['time']}s) | {q[:40]}")

    # Save results
    OUTPUT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # Summary
    stats = Counter(r["status"] for r in results)
    old_stats = Counter(r["old_status"] for r in results)
    avg_before = sum(r["old_time"] for r in results) / len(results)
    avg_after = sum(r["time"] for r in results) / len(results)

    print(f"\n=== BEFORE: {dict(old_stats)}")
    print(f"=== AFTER:  {dict(stats)}")
    print(f"Avg time: {avg_before:.1f}s -> {avg_after:.1f}s ({(avg_before-avg_after)/avg_before*100:.0f}% faster)")
    print(f"Results saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
