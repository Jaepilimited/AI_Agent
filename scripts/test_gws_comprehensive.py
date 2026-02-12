"""
Comprehensive GWS (Google Workspace) test suite for SKIN1004 AI system.
Tests Gmail, Calendar, and Drive queries via the FastAPI API.
"""
import requests
import json
import time
import sys
import io
from datetime import datetime

# Fix console encoding for Korean + emoji output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
TIMEOUT = 180  # 3 minutes to handle slow GWS queries
OUTPUT_FILE = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_results_gws.txt"

# Define test chains: (category, q1, q2_followup)
TEST_CHAINS = [
    (
        "Calendar",
        "오늘 일정 알려줘",
        "이번 주 남은 일정도 보여줘",
    ),
    (
        "Calendar",
        "내일 회의 일정이 있어?",
        "이번 달 회의 일정을 전체적으로 보여줘",
    ),
    (
        "Gmail",
        "최근 받은 메일 중 중요한 것 있어?",
        "안 읽은 메일 중 가장 최근 것 3개만 보여줘",
    ),
    (
        "Drive",
        "드라이브에서 최근 수정된 파일 보여줘",
        "그 파일들 중 공유된 파일만 필터링해줘",
    ),
    (
        "Gmail",
        "지난주에 받은 메일 중 skin1004 관련 메일 찾아줘",
        "그 메일의 첨부파일이 있는 것만 보여줘",
    ),
    (
        "Calendar",
        "다음 주 월요일 일정 확인해줘",
        "다음 주 전체 일정을 요일별로 정리해줘",
    ),
]


def send_query(question: str, conversation_history: list = None) -> dict:
    """Send a query to the API and return the response data."""
    messages = []
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": question})

    payload = {
        "model": "skin1004-Search",
        "messages": messages,
    }

    try:
        resp = requests.post(API_URL, json=payload, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        return {"answer": answer, "status_code": resp.status_code, "error": None}
    except requests.exceptions.Timeout:
        return {"answer": "", "status_code": 0, "error": f"TIMEOUT ({TIMEOUT}s)"}
    except requests.exceptions.ConnectionError:
        return {"answer": "", "status_code": 0, "error": "CONNECTION_ERROR - server not reachable"}
    except Exception as e:
        return {"answer": "", "status_code": 0, "error": str(e)}


def classify_status(result: dict) -> str:
    """Classify the result status."""
    if result["error"]:
        return "ERROR"
    answer_lower = result["answer"].lower()
    # Check for auth-related failures
    auth_keywords = [
        "oauth", "인증", "로그인", "auth", "token", "권한",
        "google 계정", "연동", "access_token", "credential",
        "사용자 인증", "토큰", "google workspace 연동",
        "open webui", "로그인이 필요", "인증이 필요",
    ]
    for kw in auth_keywords:
        if kw in answer_lower:
            return "AUTH_REQUIRED"
    if result["status_code"] == 200:
        return "OK"
    return "ERROR"


def safe_print(text: str):
    """Print text safely, replacing unencodable characters."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def run_tests():
    """Run all GWS test chains and write results."""
    results = []
    summary = {"total": 0, "ok": 0, "error": 0, "auth_required": 0}

    safe_print(f"Starting GWS comprehensive tests at {datetime.now()}")
    safe_print(f"API endpoint: {API_URL}")
    safe_print("=" * 60)

    for idx, (category, q1, q2) in enumerate(TEST_CHAINS, 1):
        safe_print(f"\n--- Test {idx}: {category} ---")

        # Q1
        safe_print(f"  Q1: {q1}")
        t1_start = time.time()
        r1 = send_query(q1)
        t1_elapsed = time.time() - t1_start
        s1 = classify_status(r1)
        safe_print(f"  A1 status: {s1} ({t1_elapsed:.1f}s)")
        if r1["error"]:
            safe_print(f"  Error: {r1['error']}")
        else:
            # Truncate and sanitize for console
            preview = r1['answer'][:120].replace('\n', ' ')
            safe_print(f"  A1 preview: {preview}...")

        summary["total"] += 1
        key = s1.lower()
        if key in summary:
            summary[key] += 1
        else:
            summary[key] = 1

        # Build conversation history for follow-up
        conv_history = [
            {"role": "user", "content": q1},
        ]
        if r1["answer"]:
            conv_history.append({"role": "assistant", "content": r1["answer"]})

        # Q2 follow-up
        safe_print(f"  Q2: {q2}")
        t2_start = time.time()
        r2 = send_query(q2, conversation_history=conv_history if r1["answer"] else None)
        t2_elapsed = time.time() - t2_start
        s2 = classify_status(r2)
        safe_print(f"  A2 status: {s2} ({t2_elapsed:.1f}s)")
        if r2["error"]:
            safe_print(f"  Error: {r2['error']}")
        else:
            preview = r2['answer'][:120].replace('\n', ' ')
            safe_print(f"  A2 preview: {preview}...")

        summary["total"] += 1
        key = s2.lower()
        if key in summary:
            summary[key] += 1
        else:
            summary[key] = 1

        results.append({
            "test_num": idx,
            "category": category,
            "q1": q1,
            "a1": r1["answer"],
            "a1_error": r1["error"],
            "s1": s1,
            "t1": t1_elapsed,
            "q2": q2,
            "a2": r2["answer"],
            "a2_error": r2["error"],
            "s2": s2,
            "t2": t2_elapsed,
        })

        # Small delay between test chains
        time.sleep(2)

    # Write results to file
    write_results(results, summary)
    safe_print("\n" + "=" * 60)
    safe_print(f"Results written to: {OUTPUT_FILE}")
    safe_print(f"Summary: Total={summary['total']}, OK={summary.get('ok',0)}, "
               f"ERROR={summary.get('error',0)}, AUTH_REQUIRED={summary.get('auth_required',0)}")


def write_results(results: list, summary: dict):
    """Write formatted results to file."""
    lines = []
    lines.append("=" * 70)
    lines.append("SKIN1004 AI - GWS (Google Workspace) Comprehensive Test Results")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"API: {API_URL}")
    lines.append(f"Model: skin1004-Search")
    lines.append("=" * 70)
    lines.append("")

    for r in results:
        lines.append("---")
        lines.append(f"### Test {r['test_num']}: {r['category']}")
        lines.append(f"**Q1**: {r['q1']}")
        if r['a1_error']:
            lines.append(f"**A1**: [ERROR: {r['a1_error']}]")
        else:
            lines.append(f"**A1**: {r['a1'][:500]}")
        lines.append(f"**Status**: {r['s1']}")
        lines.append(f"**Response Time**: {r['t1']:.1f}s")
        lines.append("")
        lines.append(f"**Q2 (Follow-up)**: {r['q2']}")
        if r['a2_error']:
            lines.append(f"**A2**: [ERROR: {r['a2_error']}]")
        else:
            lines.append(f"**A2**: {r['a2'][:500]}")
        lines.append(f"**Status**: {r['s2']}")
        lines.append(f"**Response Time**: {r['t2']:.1f}s")
        lines.append("---")
        lines.append("")

    lines.append("=" * 70)
    lines.append("SUMMARY")
    lines.append("=" * 70)
    lines.append(f"Total queries: {summary['total']}")
    lines.append(f"OK (passed): {summary.get('ok', 0)}")
    lines.append(f"ERROR (failed): {summary.get('error', 0)}")
    lines.append(f"AUTH_REQUIRED: {summary.get('auth_required', 0)}")
    lines.append("")

    ok_count = summary.get('ok', 0)
    total = summary['total']
    if total > 0:
        lines.append(f"Success rate (OK only): {ok_count}/{total} ({100*ok_count/total:.1f}%)")
        non_error = ok_count + summary.get('auth_required', 0)
        lines.append(f"Non-error rate (OK + AUTH_REQUIRED): {non_error}/{total} ({100*non_error/total:.1f}%)")
    lines.append("")
    lines.append("Note: AUTH_REQUIRED is expected when no OAuth token is available for the")
    lines.append("current user. The GWS agent requires per-user OAuth2 via Open WebUI.")
    lines.append("=" * 70)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    run_tests()
