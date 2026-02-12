"""Comprehensive test runner for SKIN1004 AI Agent - All 4 domains"""
import requests
import json
import time
import sys

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
TIMEOUT = 180

results = []

def query(q: str, model: str = "skin1004-Search") -> dict:
    """Send a query and return (answer, elapsed, status)"""
    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": model,
            "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=TIMEOUT)
        elapsed = time.time() - t0
        if resp.status_code != 200:
            return {"answer": f"HTTP {resp.status_code}: {resp.text[:300]}", "elapsed": elapsed, "status": "ERROR"}
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        return {"answer": answer, "elapsed": elapsed, "status": "OK"}
    except Exception as e:
        return {"answer": str(e)[:300], "elapsed": time.time() - t0, "status": "ERROR"}

def run_test(domain: str, test_num: int, category: str, q1: str, q2: str):
    """Run a test pair (main + follow-up)"""
    print(f"  [{domain}] Test {test_num}: {category}...", flush=True)
    r1 = query(q1)
    print(f"    Q1: {r1['status']} ({r1['elapsed']:.1f}s)", flush=True)
    r2 = query(q2)
    print(f"    Q2: {r2['status']} ({r2['elapsed']:.1f}s)", flush=True)
    results.append({
        "domain": domain,
        "test_num": test_num,
        "category": category,
        "q1": q1, "a1": r1["answer"], "s1": r1["status"], "t1": r1["elapsed"],
        "q2": q2, "a2": r2["answer"], "s2": r2["status"], "t2": r2["elapsed"],
    })

# ========== DOMAIN 1: Notion ==========
print("\n=== DOMAIN 1: Notion ===", flush=True)

run_test("Notion", 1, "해외 출장 가이드",
    "노션에서 해외 출장 가이드북 내용 알려줘",
    "출장 시 필요한 서류와 준비사항은 뭐야?")

run_test("Notion", 2, "틱톡샵 접속",
    "노션에서 틱톡샵 접속 방법 알려줘",
    "틱톡샵 셀러센터 로그인 계정 정보도 있어?")

run_test("Notion", 3, "법인 태블릿",
    "노션에서 법인 태블릿 정보 알려줘",
    "태블릿 원격 접속 방법은?")

run_test("Notion", 4, "EAST 업무파악",
    "노션에서 EAST 2026 업무파악 내용 보여줘",
    "EAST팀의 주요 담당 업무는 뭐야?")

run_test("Notion", 5, "광고 입력 업무",
    "노션에서 DB daily 광고 입력 업무 알려줘",
    "광고 데이터 입력 주기와 방법은?")

run_test("Notion", 6, "데이터 분석 파트",
    "노션에서 데이터 분석 파트 정보 보여줘",
    "데이터 분석팀의 주요 도구와 프로세스는?")

run_test("Notion", 7, "WEST 대시보드 (Sheets)",
    "노션에서 WEST 틱톡샵US 대시보드 보여줘",
    "틱톡샵 US에 등록된 제품 목록은?")

run_test("Notion", 8, "LLM 폴백 검색",
    "노션에서 zombiepack 번들 제품 정보 알려줘",
    "번들 제품의 할인율 기준은?")

# ========== DOMAIN 2: Sales (BigQuery) ==========
print("\n=== DOMAIN 2: Sales (BigQuery) ===", flush=True)

run_test("Sales", 1, "월별 매출 추이",
    "2025년 하반기 월별 전체 매출 추이를 보여줘",
    "그 중에서 매출이 가장 높았던 달과 가장 낮았던 달은?")

run_test("Sales", 2, "플랫폼 비교",
    "2025년 아마존과 쇼피의 연간 매출 비교해줘",
    "두 플랫폼의 분기별 매출 추이는 어떻게 돼?")

run_test("Sales", 3, "국가별 분석",
    "동남아시아 국가별 2025년 총 매출 순위 알려줘",
    "그 중 매출이 가장 높은 상위 3개 국가의 주요 판매 플랫폼은?")

run_test("Sales", 4, "제품 분석",
    "2025년 가장 많이 팔린 제품 TOP 5 알려줘",
    "그 제품들의 월별 판매 추이는?")

run_test("Sales", 5, "팀별 실적",
    "2025년 팀별 매출 실적을 비교해줘",
    "EAST팀과 WEST팀의 월별 매출 비교해줘")

run_test("Sales", 6, "틱톡샵 분석",
    "틱톡샵 US의 2025년 월별 매출 알려줘",
    "틱톡샵 전체 국가별 매출 순위는?")

run_test("Sales", 7, "대륙별 분석",
    "2025년 대륙별 매출 비중을 보여줘",
    "북미 매출에서 아마존이 차지하는 비중은?")

run_test("Sales", 8, "전년 대비",
    "2024년 대비 2025년 연간 총 매출 비교해줘",
    "가장 큰 폭으로 성장한 플랫폼은?")

# ========== DOMAIN 3: Product ==========
print("\n=== DOMAIN 3: Product ===", flush=True)

run_test("Product", 1, "전체 제품 수",
    "현재 등록된 전체 제품 수는 몇 개야?",
    "그 중 활성 상태인 제품은 몇 개야?")

run_test("Product", 2, "좀비팩 제품",
    "zombie pack 관련 제품 리스트 알려줘",
    "zombie pack 제품들의 2025년 총 매출은?")

run_test("Product", 3, "센텔라 제품",
    "센텔라 관련 제품 목록 보여줘",
    "센텔라 앰플의 2025년 월별 매출 추이는?")

run_test("Product", 4, "마다가스카르 앰플",
    "마다가스카르 센텔라 앰플의 2025년 총 매출 알려줘",
    "그 제품의 국가별 판매 비중은?")

run_test("Product", 5, "번들 제품",
    "번들 제품 리스트를 보여줘",
    "번들 제품의 2025년 총 매출은?")

run_test("Product", 6, "제품 카테고리",
    "제품 카테고리별 등록 수를 보여줘",
    "가장 많은 제품이 등록된 카테고리는?")

# ========== DOMAIN 4: GWS ==========
print("\n=== DOMAIN 4: GWS ===", flush=True)

run_test("GWS", 1, "오늘 일정",
    "오늘 일정 알려줘",
    "이번 주 남은 일정도 보여줘")

run_test("GWS", 2, "내일 회의",
    "내일 회의 일정이 있어?",
    "이번 달 회의 일정을 전체적으로 보여줘")

run_test("GWS", 3, "최근 메일",
    "최근 받은 메일 중 중요한 것 있어?",
    "안 읽은 메일 중 가장 최근 것 3개만 보여줘")

run_test("GWS", 4, "드라이브 파일",
    "드라이브에서 최근 수정된 파일 보여줘",
    "그 파일들 중 공유된 파일만 보여줘")

run_test("GWS", 5, "메일 검색",
    "지난주에 받은 메일 중 skin1004 관련 메일 찾아줘",
    "그 메일 중 첨부파일이 있는 것만 보여줘")

run_test("GWS", 6, "다음 주 일정",
    "다음 주 월요일 일정 확인해줘",
    "다음 주 전체 일정을 요일별로 정리해줘")

# ========== WRITE RESULTS ==========
output_path = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_results_all.txt"
with open(output_path, "w", encoding="utf-8") as f:
    for domain in ["Notion", "Sales", "Product", "GWS"]:
        domain_results = [r for r in results if r["domain"] == domain]
        f.write(f"\n{'='*60}\n")
        f.write(f"  DOMAIN: {domain}\n")
        f.write(f"{'='*60}\n\n")

        ok = sum(1 for r in domain_results if r["s1"] == "OK")
        ok2 = sum(1 for r in domain_results if r["s2"] == "OK")
        total = len(domain_results)
        f.write(f"Summary: Q1 {ok}/{total} OK, Q2 {ok2}/{total} OK\n\n")

        for r in domain_results:
            f.write(f"---\n### Test {r['test_num']}: {r['category']}\n")
            f.write(f"**Q1**: {r['q1']}\n")
            f.write(f"**Status**: {r['s1']} ({r['t1']:.1f}s)\n")
            f.write(f"**A1**: {r['a1'][:800]}\n\n")
            f.write(f"**Q2 (Follow-up)**: {r['q2']}\n")
            f.write(f"**Status**: {r['s2']} ({r['t2']:.1f}s)\n")
            f.write(f"**A2**: {r['a2'][:800]}\n\n")

    # Overall summary
    f.write(f"\n{'='*60}\n")
    f.write(f"  OVERALL SUMMARY\n")
    f.write(f"{'='*60}\n\n")
    total_q = len(results) * 2
    total_ok = sum(1 for r in results if r["s1"] == "OK") + sum(1 for r in results if r["s2"] == "OK")
    f.write(f"Total queries: {total_q}\n")
    f.write(f"Passed: {total_ok}\n")
    f.write(f"Failed: {total_q - total_ok}\n")
    f.write(f"Pass rate: {total_ok/total_q*100:.1f}%\n")

    avg_time = sum(r["t1"] + r["t2"] for r in results) / len(results)
    f.write(f"Avg response time per pair: {avg_time:.1f}s\n")

print(f"\nAll tests complete! Results written to {output_path}")
print(f"Total: {len(results)} test pairs ({len(results)*2} queries)")
total_ok = sum(1 for r in results if r["s1"] == "OK") + sum(1 for r in results if r["s2"] == "OK")
print(f"Passed: {total_ok}/{len(results)*2}")
