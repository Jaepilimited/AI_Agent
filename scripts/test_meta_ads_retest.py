"""Re-test 15 meta advertising failure patterns against the live SKIN1004 AI system.

Each query is sent to /v1/chat/completions, and the response SQL is checked
against the expected fix rules from the QA sheet.
"""

import json
import re
import sys
import time

import requests

BASE = "http://127.0.0.1:3000"
TIMEOUT = 120

# ── Sign in ──────────────────────────────────────────────────────────
print("Signing in...")

# First, find the right department for 임재필
try:
    depts_r = requests.get(f"{BASE}/api/auth/departments", timeout=10)
    depts = depts_r.json()
    print(f"Available departments ({len(depts)}):")
    for d in depts[:10]:
        print(f"  - {d['department']} ({d['cnt']} users)")
except Exception as e:
    print(f"Could not list departments: {e}")

# Try to find 임재필 by searching
try:
    search_r = requests.get(f"{BASE}/api/auth/search-name", params={"name": "임재필"}, timeout=10)
    found = search_r.json()
    print(f"Search '임재필': {json.dumps(found, ensure_ascii=False)}")
    if found:
        dept = found[0].get("department", "경영기획")
        name = found[0].get("display_name", "임재필")
        print(f"Using: department='{dept}', name='{name}'")
    else:
        dept = "경영기획"
        name = "임재필"
except Exception as e:
    print(f"Search failed: {e}")
    dept = "경영기획"
    name = "임재필"

r = requests.post(
    f"{BASE}/api/auth/signin",
    json={"department": dept, "name": name, "password": "1234"},
    timeout=15,
)
if r.status_code != 200:
    print(f"SIGN-IN FAILED: {r.status_code}")
    print(f"Response: {r.text}")
    # Try alternative approach - maybe the user can sign in differently
    # Try each department that has users
    for d in depts[:20]:
        try:
            users_r = requests.get(f"{BASE}/api/auth/users-by-dept", params={"dept": d["department"]}, timeout=10)
            users = users_r.json()
            for u in users:
                if "재필" in u.get("display_name", ""):
                    print(f"Found matching user: {u}")
        except:
            pass
    sys.exit(1)
cookies = r.cookies
print(f"Sign-in OK (status {r.status_code})")


def ask(query: str) -> dict:
    """Send a query to the AI and return {answer, elapsed_s}."""
    t0 = time.time()
    resp = requests.post(
        f"{BASE}/v1/chat/completions",
        json={
            "model": "gemini",
            "messages": [{"role": "user", "content": query}],
            "stream": False,
        },
        cookies=cookies,
        timeout=TIMEOUT,
    )
    elapsed = round(time.time() - t0, 1)
    if resp.status_code != 200:
        return {"answer": f"HTTP {resp.status_code}: {resp.text[:200]}", "elapsed_s": elapsed}
    data = resp.json()
    # OpenAI-compatible format
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        answer = json.dumps(data, ensure_ascii=False)[:500]
    return {"answer": answer, "elapsed_s": elapsed}


def extract_sql(answer: str) -> str:
    """Extract SQL from code blocks in the answer."""
    blocks = re.findall(r"```sql\s*\n(.*?)\n```", answer, re.DOTALL | re.IGNORECASE)
    if blocks:
        return "\n".join(blocks)
    # Fallback: find SELECT statements
    selects = re.findall(r"(SELECT\s+[\s\S]*?(?:LIMIT\s+\d+|$))", answer, re.IGNORECASE)
    if selects:
        return selects[0]
    return ""


# ── Test Definitions ─────────────────────────────────────────────────

TESTS = [
    {
        "id": 1,
        "query": "브랜드별 활성 메타 광고 수",
        "checks": [
            ("should use brand as-is, no UPPER", lambda sql: "UPPER" not in sql.upper() or "UPPER(BRAND)" not in sql.upper()),
            ("should GROUP BY brand", lambda sql: "GROUP BY" in sql.upper() and "BRAND" in sql.upper()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 2,
        "query": "한국 SKIN1004 메타 광고",
        "checks": [
            ("brand = 'skin1004' (lowercase)", lambda sql: "'skin1004'" in sql.lower() and "'SKIN1004'" not in sql),
            ("simple SELECT * or SELECT columns", lambda sql: "SELECT" in sql.upper()),
            ("country_name = 'South Korea'", lambda sql: "'south korea'" in sql.lower()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 3,
        "query": "Facebook 플랫폼 메타 광고 수",
        "checks": [
            ("should use LIKE '%facebook%'", lambda sql: "LIKE" in sql.upper() and "facebook" in sql.lower()),
            ("should NOT use = 'Facebook'", lambda sql: "= 'Facebook'" not in sql and "= 'facebook'" not in sql),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 4,
        "query": "국가별 메타 광고 비율",
        "checks": [
            ("should NOT have WHERE brand = 'skin1004'", lambda sql: "brand = 'skin1004'" not in sql.lower() or "where" not in sql.lower().split("brand = 'skin1004'")[0][-50:]),
            ("should GROUP BY country_name", lambda sql: "country_name" in sql.lower()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 5,
        "query": "메타 광고 전체 현황",
        "checks": [
            ("should be total aggregate, NOT grouped by brand", lambda sql: "GROUP BY" not in sql.upper() or "BRAND" not in sql.upper().split("GROUP BY")[1][:30] if "GROUP BY" in sql.upper() else True),
            ("should NOT have WHERE brand", lambda sql: "WHERE" not in sql.upper() or "BRAND" not in sql.upper().split("WHERE")[1][:100] if "WHERE" in sql.upper() else True),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 6,
        "query": "최근 7일 시작된 메타 광고",
        "checks": [
            ("should use DATE(start_date_formatted)", lambda sql: "date(start_date_formatted)" in sql.lower().replace(" ", "").replace("\n", "") or "DATE(start_date_formatted)" in sql),
            ("should use DATE_SUB", lambda sql: "DATE_SUB" in sql.upper()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 7,
        "query": "비디오 유형 메타 광고 수",
        "checks": [
            ("should use JSON_VALUE(snapshot, '$.display_format')", lambda sql: "json_value" in sql.lower() and "snapshot" in sql.lower() and "display_format" in sql.lower()),
            ("should check = 'VIDEO'", lambda sql: "'VIDEO'" in sql or "'video'" in sql),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 8,
        "query": "Instagram 플랫폼 광고 수",
        "checks": [
            ("should use LIKE '%instagram%'", lambda sql: "LIKE" in sql.upper() and "instagram" in sql.lower()),
            ("should NOT use = 'Instagram'", lambda sql: "= 'Instagram'" not in sql and "= 'instagram'" not in sql),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 9,
        "query": "활성/비활성 광고 건수 비교",
        "checks": [
            ("should NOT have WHERE brand", lambda sql: not re.search(r"WHERE\s+.*brand", sql, re.IGNORECASE)),
            ("should reference is_active", lambda sql: "is_active" in sql.lower()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 10,
        "query": "베트남 메타 광고",
        "checks": [
            ("should have country_name = 'Vietnam'", lambda sql: "'vietnam'" in sql.lower()),
            ("should NOT have brand filter", lambda sql: "brand" not in sql.lower() or "brand" in sql.lower().split("select")[1].split("from")[0].lower() if "select" in sql.lower() else True),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 11,
        "query": "SKIN1004 활성 광고 국가 분포",
        "checks": [
            ("should use brand = 'skin1004' (lowercase!)", lambda sql: "'skin1004'" in sql.lower()),
            ("should NOT use 'SKIN1004' (uppercase)", lambda sql: "'SKIN1004'" not in sql),
            ("should GROUP BY country_name", lambda sql: "country_name" in sql.lower()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 12,
        "query": "메타 광고 유형별 분포",
        "checks": [
            ("ad_type should be official/partnership (GROUP BY ad_type)", lambda sql: "ad_type" in sql.lower()),
            ("should NOT filter by platform type", lambda sql: "publisher_platform" not in sql.lower()),
            ("should NOT have WHERE brand", lambda sql: not re.search(r"WHERE\s+.*brand", sql, re.IGNORECASE)),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 13,
        "query": "최근 광고 20건 보여줘",
        "checks": [
            ("should exclude null/unknown brand", lambda sql: ("brand IS NOT NULL" in sql or "brand is not null" in sql.lower()) or ("'unknown'" in sql.lower())),
            ("should have LIMIT 20", lambda sql: "LIMIT 20" in sql.upper()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 14,
        "query": "2025년 시작된 메타 광고 수",
        "checks": [
            ("should use start_date_formatted, NOT start_date", lambda sql: "start_date_formatted" in sql.lower()),
            ("should use EXTRACT or DATE() for year", lambda sql: "extract" in sql.lower() or "date(" in sql.lower() or "'2025" in sql),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
    {
        "id": 15,
        "query": "publisher_platform 목록 알려줘",
        "checks": [
            ("should use UNNEST pattern", lambda sql: "UNNEST" in sql.upper()),
            ("should use meta data_test table", lambda sql: "meta data_test" in sql.lower()),
        ],
    },
]


# ── Run Tests ────────────────────────────────────────────────────────

results = []
for t in TESTS:
    print(f"\n{'='*70}")
    print(f"Test {t['id']}: {t['query']}")
    print(f"{'='*70}")

    resp = ask(t["query"])
    answer = resp["answer"]
    elapsed = resp["elapsed_s"]
    sql = extract_sql(answer)

    print(f"  Elapsed: {elapsed}s")
    if sql:
        # Show first 300 chars of SQL
        sql_preview = sql[:400].replace("\n", "\n    ")
        print(f"  SQL found:\n    {sql_preview}")
    else:
        print(f"  SQL: (not found in answer)")
        print(f"  Answer preview: {answer[:300]}")

    check_results = []
    all_pass = True
    for desc, check_fn in t["checks"]:
        if not sql:
            passed = False
            reason = "No SQL found in answer"
        else:
            try:
                passed = check_fn(sql)
                reason = "" if passed else f"SQL did not match rule"
            except Exception as e:
                passed = False
                reason = f"Check error: {e}"

        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        check_results.append({"desc": desc, "passed": passed, "reason": reason})
        print(f"  [{status}] {desc}" + (f" -- {reason}" if reason else ""))

    results.append({
        "id": t["id"],
        "query": t["query"],
        "elapsed_s": elapsed,
        "sql_found": bool(sql),
        "sql": sql[:500] if sql else "",
        "all_pass": all_pass,
        "checks": check_results,
    })


# ── Summary Table ────────────────────────────────────────────────────

print("\n\n")
print("=" * 90)
print("SUMMARY TABLE")
print("=" * 90)
print(f"{'#':<4} {'Query':<45} {'Time':>6} {'SQL':>4} {'Result':>8}")
print("-" * 90)

pass_count = 0
fail_count = 0
for r in results:
    status = "PASS" if r["all_pass"] else "FAIL"
    sql_mark = "Y" if r["sql_found"] else "N"
    q_short = r["query"][:43]
    print(f"{r['id']:<4} {q_short:<45} {r['elapsed_s']:>5}s {sql_mark:>4} {status:>8}")
    if r["all_pass"]:
        pass_count += 1
    else:
        fail_count += 1
        # Show which checks failed
        for ck in r["checks"]:
            if not ck["passed"]:
                print(f"     -> FAIL: {ck['desc']}")

print("-" * 90)
print(f"TOTAL: {pass_count} PASS / {fail_count} FAIL / {len(results)} TESTS")
print(f"Pass rate: {pass_count/len(results)*100:.1f}%")
print("=" * 90)
