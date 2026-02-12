"""Regression test - targets the 5 specific failures from comprehensive QA.

Failures to retest:
1. NT-03: 틱톡샵 접속방법 타임아웃 (302s) - now has sheet read timeout (30s)
2. NT-04: httpx 클라이언트 닫힘 - fixed client re-creation
3. R2-NT-01: 대형 구글시트 타임아웃 - now has sheet read timeout (30s)
4. R2-NT-09: "이커머스 플랫폼" 미라우팅 - fixed punctuation in search
5. GWS-05: 보안 메일 ReAct 타임아웃 (302s) - now has 120s timeout
"""
import requests
import time
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

TESTS = [
    {
        "id": "REG-01",
        "original": "NT-03",
        "fix": "Sheet read timeout (30s)",
        "query": "노션에서 틱톡샵 접속 방법을 알려줘",
        "expect": "300s+ timeout -> now should respond within 60s or show timeout msg",
        "max_time": 120,
    },
    {
        "id": "REG-02",
        "original": "NT-04 (runs right after REG-01)",
        "fix": "httpx client re-creation",
        "query": "노션에서 법인 태블릿 Anydesk 원격 접속 방법 알려줘",
        "expect": "Should work even if REG-01 had timeout (client re-created)",
        "max_time": 120,
    },
    {
        "id": "REG-03",
        "original": "R2-NT-01",
        "fix": "Sheet read timeout (30s) + max_rows=50",
        "query": "노션의 WEST 틱톡샵US 대시보드에 연결된 구글시트에서 어떤 제품들이 있는지 보여줘",
        "expect": "300s+ timeout -> now should respond within 60s with partial data",
        "max_time": 120,
    },
    {
        "id": "REG-04",
        "original": "R2-NT-09",
        "fix": "Search punctuation cleanup",
        "query": "노션 문서에서 확인할 수 있는 SKIN1004가 진출한 이커머스 플랫폼 목록을 정리해줘. 쇼피, 틱톡샵, 아마존 등 각 플랫폼의 운영 국가도 함께",
        "expect": "Should route to Notion and find relevant pages (not MISS)",
        "max_time": 120,
    },
    {
        "id": "REG-05",
        "original": "GWS-05",
        "fix": "ReAct agent 120s timeout",
        "query": "최근 받은 보안 관련 메일을 찾아서 분석해줘. 로그인 알림, 비밀번호 변경, 보안 경고 등을 분류해줘",
        "expect": "300s+ timeout -> now max 120s, returns timeout msg if needed",
        "max_time": 150,
    },
]


def run_test(tc, idx, total):
    tag = tc["id"]
    q = tc["query"]
    orig = tc["original"]
    fix = tc["fix"]
    max_t = tc["max_time"]
    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} (orig: {orig})")
    print(f"Fix: {fix}")
    print(f"Q: {q}")
    print(f"{'='*70}")
    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": MODEL, "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=max_t + 30)
        elapsed = time.time() - t0
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]

        is_timeout_msg = "시간 초과" in answer or "timeout" in answer.lower()
        is_error = len(answer) < 30 or ("오류" in answer and "closed" in answer)
        within_time = elapsed <= max_t

        if is_error:
            status = "FAIL"
        elif is_timeout_msg and within_time:
            status = "OK-TIMEOUT"  # Graceful timeout (improvement over 300s hang)
        elif within_time:
            status = "OK"
        else:
            status = "SLOW"

        print(f"Status: {status} | {elapsed:.1f}s (limit: {max_t}s) | {len(answer)}ch")
        print(f"Preview: {answer[:300]}...")
        return {
            "tag": tag, "original": orig, "fix": fix, "query": q,
            "time": elapsed, "chars": len(answer), "status": status,
            "within_time": within_time, "answer": answer,
        }
    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        print(f"HTTP TIMEOUT ({elapsed:.1f}s)")
        return {
            "tag": tag, "original": orig, "fix": fix, "query": q,
            "time": elapsed, "chars": 0, "status": "HTTP_TIMEOUT",
            "within_time": False, "answer": "HTTP request timeout",
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {
            "tag": tag, "original": orig, "fix": fix, "query": q,
            "time": elapsed, "chars": 0, "status": "EXCEPTION",
            "within_time": False, "answer": str(e),
        }


def main():
    print(f"{'='*70}")
    print(f"Regression Test - {len(TESTS)} targeted queries")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    results = []
    for i, tc in enumerate(TESTS):
        results.append(run_test(tc, i + 1, len(TESTS)))

    ok = sum(1 for r in results if r["status"] in ("OK", "OK-TIMEOUT"))
    avg_t = sum(r["time"] for r in results) / len(results)

    print(f"\n{'='*70}")
    print(f"REGRESSION TEST SUMMARY")
    print(f"{'='*70}")
    for r in results:
        print(f"  [{r['status']:12s}] {r['tag']:7s} (was: {r['original']:10s}) | {r['time']:6.1f}s | {r['chars']:5d}ch | {r['fix']}")
    print(f"\nFixed: {ok}/{len(results)} | Avg: {avg_t:.1f}s")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    out = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_regression_result.txt"
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(f"Regression Test Results\n{'='*70}\n")
        fp.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for r in results:
            fp.write(f"{'='*70}\n[{r['tag']}] Original: {r['original']}\n")
            fp.write(f"Fix: {r['fix']}\nQ: {r['query']}\n")
            fp.write(f"Status: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n")
            fp.write(f"{'_'*70}\n{r['answer']}\n\n")
        fp.write(f"\n{'='*70}\nSUMMARY\n{'='*70}\n")
        for r in results:
            fp.write(f"  [{r['status']:12s}] {r['tag']:7s} (was: {r['original']:10s}) | {r['time']:6.1f}s | {r['chars']:5d}ch\n")
        fp.write(f"\nFixed: {ok}/{len(results)} | Avg: {avg_t:.1f}s\n")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
