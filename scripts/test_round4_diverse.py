"""Round 4 Diverse QA Test — New variables not covered in R1-R3 (112 queries).

Focus:
- BigQuery: untested columns, product lines, countries, metrics, time patterns
- Notion: different search patterns, cross-page, ambiguous queries
- GWS: different search terms, date ranges, file types

Criteria: >= 200s = FAIL, >= 100s = WARN, < 100s = OK
"""
import json
import time
import os
import sys
import requests
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"

# ═══════════════════════════════════════════════════════════════════
# BIGQUERY — 25 queries with NEW variables
# ═══════════════════════════════════════════════════════════════════
BQ_QUERIES = [
    # --- Product Line diversity (untested lines) ---
    {
        "id": "R4-BQ-01",
        "query": "히알루시카(Hyalucica) 라인 제품들의 2025년 월별 매출 추이를 보여줘",
        "expected": "Hyalucica 라인 월별 매출 데이터",
        "variable": "product_line=Hyalucica",
    },
    {
        "id": "R4-BQ-02",
        "query": "프로바이오시카(Probiocica) 라인 vs 티트리카(Teatrica) 라인의 2025년 매출 비교",
        "expected": "두 제품 라인 매출 비교",
        "variable": "product_line_comparison",
    },
    {
        "id": "R4-BQ-03",
        "query": "톤브라이트닝(Tone_Brightening) 라인의 국가별 매출 TOP 5를 보여줘",
        "expected": "Tone_Brightening 국가별 매출 순위",
        "variable": "product_line=Tone_Brightening",
    },
    {
        "id": "R4-BQ-04",
        "query": "포어마이징(Poremizing) 제품이 가장 많이 팔리는 플랫폼을 분석해줘",
        "expected": "Poremizing 플랫폼별 매출 분석",
        "variable": "product_line=Poremizing",
    },
    # --- Country diversity (untested countries) ---
    {
        "id": "R4-BQ-05",
        "query": "2025년 태국 시장 매출 현황을 플랫폼별로 보여줘",
        "expected": "태국 플랫폼별 매출",
        "variable": "country=태국",
    },
    {
        "id": "R4-BQ-06",
        "query": "베트남에서 가장 많이 팔리는 SKIN1004 제품 TOP 10을 수량 기준으로 보여줘",
        "expected": "베트남 제품 판매량 순위",
        "variable": "country=베트남, metric=Total_Qty",
    },
    {
        "id": "R4-BQ-07",
        "query": "2025년 유럽(서유럽, 북유럽) 매출을 국가별로 분석해줘",
        "expected": "유럽 국가별 매출",
        "variable": "continent=서유럽+북유럽",
    },
    {
        "id": "R4-BQ-08",
        "query": "중동 지역 2024년 vs 2025년 매출 비교",
        "expected": "중동 연도별 매출 비교",
        "variable": "continent=중동, YoY",
    },
    {
        "id": "R4-BQ-09",
        "query": "대만과 싱가포르의 2025년 분기별 매출 추이를 비교해줘",
        "expected": "대만/싱가포르 분기별 매출",
        "variable": "country=대만+싱가포르",
    },
    # --- Metric diversity (untested metrics) ---
    {
        "id": "R4-BQ-10",
        "query": "2025년 제품별 판매 수량(Total_Qty) TOP 20을 보여줘. 매출이 아닌 순수 수량 기준으로",
        "expected": "수량 기준 제품 순위",
        "variable": "metric=Total_Qty",
    },
    {
        "id": "R4-BQ-11",
        "query": "2025년 FOC(무료 제공) 금액이 가장 큰 국가 TOP 5를 보여줘",
        "expected": "FOC 금액 국가 순위",
        "variable": "metric=FOC",
    },
    {
        "id": "R4-BQ-12",
        "query": "2025년 주문 건수(Order_Count)가 가장 많은 플랫폼 TOP 10",
        "expected": "주문 건수 기준 플랫폼 순위",
        "variable": "metric=Order_Count",
    },
    # --- Team / Manager diversity ---
    {
        "id": "R4-BQ-13",
        "query": "GM_EAST1 팀과 GM_EAST2 팀의 2025년 월별 매출 추이를 비교해줘",
        "expected": "두 팀 월별 매출 비교",
        "variable": "team=GM_EAST1+GM_EAST2",
    },
    {
        "id": "R4-BQ-14",
        "query": "KBT 팀이 관리하는 국가와 플랫폼별 2025년 매출을 보여줘",
        "expected": "KBT 팀 국가/플랫폼 분석",
        "variable": "team=KBT",
    },
    {
        "id": "R4-BQ-15",
        "query": "JBT 팀의 2025년 월별 매출 추이를 보여줘",
        "expected": "JBT 팀 월별 매출",
        "variable": "team=JBT",
    },
    # --- Time period diversity ---
    {
        "id": "R4-BQ-16",
        "query": "이번 달 매출을 지난 달과 비교해줘. MoM 증감률도 계산해줘",
        "expected": "월간 비교 매출",
        "variable": "time=MoM(relative)",
    },
    {
        "id": "R4-BQ-17",
        "query": "2025년 1월 첫째 주(1일~7일)의 일별 매출 추이를 보여줘",
        "expected": "특정 주간 일별 매출",
        "variable": "time=weekly_daily",
    },
    {
        "id": "R4-BQ-18",
        "query": "2024년 4분기 vs 2025년 1분기 매출을 대륙별로 비교해줘",
        "expected": "분기 간 대륙별 비교",
        "variable": "time=QoQ, group=Continent1",
    },
    # --- B2B specific ---
    {
        "id": "R4-BQ-19",
        "query": "2025년 해외 B2B 거래처(Company_Name) 매출 TOP 10을 보여줘",
        "expected": "B2B 거래처 매출 순위",
        "variable": "type=B2B, column=Company_Name",
    },
    {
        "id": "R4-BQ-20",
        "query": "B2B1 팀과 B2B2 팀의 2025년 월별 매출을 비교해줘",
        "expected": "B2B 팀 간 매출 비교",
        "variable": "team=B2B1+B2B2",
    },
    # --- Product type diversity ---
    {
        "id": "R4-BQ-21",
        "query": "토너(Toner) 제품들의 2025년 국가별 매출 TOP 5",
        "expected": "토너 제품 국가별 매출",
        "variable": "product_type=Toner",
    },
    {
        "id": "R4-BQ-22",
        "query": "클렌징 오일과 폼클렌저의 2025년 매출을 비교해줘",
        "expected": "클렌징 제품 타입 비교",
        "variable": "product_type=Cleansing_Oil+Foam",
    },
    {
        "id": "R4-BQ-23",
        "query": "마스크(Mask)와 패드(Pad) 제품의 2025년 매출을 국가별로 비교해줘",
        "expected": "마스크/패드 국가별 매출",
        "variable": "product_type=Mask+Pad",
    },
    # --- Complex aggregation ---
    {
        "id": "R4-BQ-24",
        "query": "2025년 쇼피(Shopee) 채널에서 국가별, 제품 라인별 매출 TOP 3를 교차 분석해줘",
        "expected": "쇼피 국가×라인 교차 분석",
        "variable": "cross_tab=Country×Line",
    },
    {
        "id": "R4-BQ-25",
        "query": "커먼랩스(CL) 브랜드 전체 제품 목록과 각 제품의 2025년 매출을 보여줘",
        "expected": "CL 브랜드 전제품 매출",
        "variable": "brand=CL, full_list",
    },
]

# ═══════════════════════════════════════════════════════════════════
# NOTION — 15 queries with different search patterns
# ═══════════════════════════════════════════════════════════════════
NT_QUERIES = [
    # --- Different question formats ---
    {
        "id": "R4-NT-01",
        "query": "노션에서 법인 태블릿 현재 사용 현황과 기기 목록을 테이블로 보여줘",
        "expected": "법인 태블릿 목록/현황",
        "variable": "page=법인태블릿, format=table",
    },
    {
        "id": "R4-NT-02",
        "query": "노션의 DB daily 광고 입력 업무에서 구글 애즈(Google Ads) 데이터 입력 방법을 알려줘",
        "expected": "구글 애즈 광고 입력 절차",
        "variable": "page=DB daily, topic=Google Ads",
    },
    {
        "id": "R4-NT-03",
        "query": "노션에서 EAST 2026 업무파악 중 말레이시아 담당 업무를 보여줘",
        "expected": "말레이시아 담당 업무 내용",
        "variable": "page=EAST 2026, filter=말레이시아",
    },
    {
        "id": "R4-NT-04",
        "query": "노션에서 WEST 틱톡샵US 대시보드의 주간 리포트 양식이나 템플릿이 있으면 보여줘",
        "expected": "WEST 틱톡샵 리포트 관련 내용",
        "variable": "page=WEST 틱톡샵US, topic=리포트",
    },
    # --- Cross-page queries ---
    {
        "id": "R4-NT-05",
        "query": "노션에서 광고 관련 업무를 다루는 모든 문서를 찾아서 핵심 내용을 정리해줘",
        "expected": "광고 관련 문서 종합 (DB daily 등)",
        "variable": "cross_page=광고",
    },
    {
        "id": "R4-NT-06",
        "query": "노션에서 인도네시아 관련 내용이 있는 문서를 모두 찾아줘",
        "expected": "인도네시아 관련 문서 종합",
        "variable": "cross_page=인도네시아",
    },
    # --- Ambiguous / LLM fallback ---
    {
        "id": "R4-NT-07",
        "query": "노션에서 신입사원 온보딩에 필요한 정보를 정리해줘",
        "expected": "온보딩 관련 문서 추천 (LLM fallback)",
        "variable": "ambiguous=온보딩",
    },
    {
        "id": "R4-NT-08",
        "query": "노션에서 해외 배송이나 물류 관련 내용이 있으면 알려줘",
        "expected": "물류 관련 문서 검색 (있으면 내용, 없으면 없음 안내)",
        "variable": "ambiguous=물류",
    },
    # --- Specific detail extraction ---
    {
        "id": "R4-NT-09",
        "query": "노션의 해외 출장 가이드에서 숙박 예약 관련 내용만 따로 정리해줘",
        "expected": "출장 가이드 중 숙박 부분",
        "variable": "page=출장가이드, filter=숙박",
    },
    {
        "id": "R4-NT-10",
        "query": "노션에서 틱톡샵 접속 시 2단계 인증(2FA)이나 보안 관련 내용이 있으면 알려줘",
        "expected": "틱톡샵 보안/인증 관련",
        "variable": "page=틱톡샵접속, filter=보안",
    },
    {
        "id": "R4-NT-11",
        "query": "노션에서 데이터 분석 파트의 BigQuery 관련 업무나 SQL 사용 가이드가 있으면 보여줘",
        "expected": "데이터분석 파트 BQ 관련",
        "variable": "page=데이터분석, filter=BigQuery",
    },
    # --- Format variation ---
    {
        "id": "R4-NT-12",
        "query": "EAST 2팀 가이드 아카이브에서 쇼피(Shopee) 관련 가이드를 찾아줘",
        "expected": "EAST 2팀 쇼피 관련 가이드",
        "variable": "page=EAST2팀가이드, filter=쇼피",
    },
    {
        "id": "R4-NT-13",
        "query": "법인 태블릿 중에서 Anydesk 원격 접속이 가능한 기기 목록을 보여줘",
        "expected": "Anydesk 가능 태블릿 목록",
        "variable": "page=법인태블릿, filter=Anydesk",
    },
    {
        "id": "R4-NT-14",
        "query": "노션 문서 중에서 플랫폼별 운영 가이드가 있는 것을 모두 찾아서 플랫폼명과 함께 정리해줘",
        "expected": "플랫폼별 운영 가이드 목록",
        "variable": "cross_page=플랫폼 가이드",
    },
    {
        "id": "R4-NT-15",
        "query": "노션의 EAST 2026 업무파악에서 각 담당자별 관리 국가와 플랫폼을 매트릭스로 정리해줘",
        "expected": "담당자×국가×플랫폼 매트릭스",
        "variable": "page=EAST2026, format=matrix",
    },
]

# ═══════════════════════════════════════════════════════════════════
# GWS — 15 queries with different patterns
# ═══════════════════════════════════════════════════════════════════
GWS_QUERIES = [
    # --- Email search diversity ---
    {
        "id": "R4-GWS-01",
        "query": "최근 일주일간 읽지 않은 메일이 몇 개인지 알려줘",
        "expected": "읽지 않은 메일 수",
        "variable": "email=unread_count",
    },
    {
        "id": "R4-GWS-02",
        "query": "메일에서 '인보이스' 또는 'invoice' 관련 메일을 찾아줘",
        "expected": "인보이스 관련 메일 검색",
        "variable": "email=search_invoice",
    },
    {
        "id": "R4-GWS-03",
        "query": "지난달에 보낸 메일 중에서 첨부파일이 있는 메일을 보여줘",
        "expected": "첨부파일 포함 발신 메일",
        "variable": "email=sent+attachment",
    },
    # --- Calendar diversity ---
    {
        "id": "R4-GWS-04",
        "query": "이번 달 남은 일정을 주차별로 분류해서 보여줘",
        "expected": "이번 달 주차별 일정 정리",
        "variable": "calendar=monthly_weekly",
    },
    {
        "id": "R4-GWS-05",
        "query": "캘린더에서 반복 일정(recurring)이 있으면 목록을 보여줘",
        "expected": "반복 일정 목록",
        "variable": "calendar=recurring",
    },
    {
        "id": "R4-GWS-06",
        "query": "내일 일정이 있는지 확인하고, 준비할 내용을 정리해줘",
        "expected": "내일 일정 확인",
        "variable": "calendar=tomorrow",
    },
    # --- Drive diversity ---
    {
        "id": "R4-GWS-07",
        "query": "드라이브에서 '예산' 또는 'budget' 관련 파일을 찾아줘",
        "expected": "예산 관련 파일 검색",
        "variable": "drive=search_budget",
    },
    {
        "id": "R4-GWS-08",
        "query": "드라이브에서 이미지 파일(png, jpg)을 최근 수정순으로 보여줘",
        "expected": "이미지 파일 검색",
        "variable": "drive=image_files",
    },
    {
        "id": "R4-GWS-09",
        "query": "드라이브에서 '2026'이 포함된 파일명을 가진 문서를 찾아줘",
        "expected": "2026 포함 파일 검색",
        "variable": "drive=filename_2026",
    },
    # --- Cross-service queries ---
    {
        "id": "R4-GWS-10",
        "query": "이번 주 회의 일정을 확인하고, 회의 관련 메일도 함께 보여줘",
        "expected": "회의 일정 + 관련 메일",
        "variable": "cross=calendar+email",
    },
    {
        "id": "R4-GWS-11",
        "query": "Shopee 관련된 모든 것을 찾아줘 — 메일, 파일, 일정 전부",
        "expected": "Shopee 관련 전체 검색",
        "variable": "cross=all_services_shopee",
    },
    # --- Specific patterns ---
    {
        "id": "R4-GWS-12",
        "query": "메일에서 Amazon 관련 최근 메일을 찾아줘",
        "expected": "Amazon 관련 메일",
        "variable": "email=search_amazon",
    },
    {
        "id": "R4-GWS-13",
        "query": "드라이브에서 공유받은 스프레드시트 파일을 보여줘",
        "expected": "공유 스프레드시트 목록",
        "variable": "drive=shared_sheets",
    },
    {
        "id": "R4-GWS-14",
        "query": "지난주에 생성되거나 수정된 드라이브 파일을 모두 보여줘",
        "expected": "지난주 수정 파일 목록",
        "variable": "drive=last_week_modified",
    },
    {
        "id": "R4-GWS-15",
        "query": "구글 캘린더에서 다음 달 일정을 미리 보여줘",
        "expected": "다음 달 일정 목록",
        "variable": "calendar=next_month",
    },
]

ALL_QUERIES = (
    [{"domain": "BigQuery", **q} for q in BQ_QUERIES]
    + [{"domain": "Notion", **q} for q in NT_QUERIES]
    + [{"domain": "GWS", **q} for q in GWS_QUERIES]
)


def send_query(query: str, timeout: int = 320) -> tuple:
    payload = {
        "model": "gemini",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    start = time.time()
    try:
        resp = requests.post(API_URL, json=payload, timeout=timeout)
        elapsed = time.time() - start
        data = resp.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return answer, elapsed, None
    except Exception as e:
        elapsed = time.time() - start
        return "", elapsed, str(e)


def classify(seconds: float) -> str:
    if seconds >= 200:
        return "FAIL"
    elif seconds >= 100:
        return "WARN"
    return "OK"


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_file = os.path.join(base_dir, "test_round4_diverse_result.txt")

    total = len(ALL_QUERIES)
    print(f"Round 4 Diverse QA Test — {total} queries")
    print(f"Criteria: >= 200s = FAIL, >= 100s = WARN, < 100s = OK")
    print("=" * 75)

    results = []
    ok = warn = fail = err = 0
    domain_stats = {}

    for i, q in enumerate(ALL_QUERIES, 1):
        qid = q["id"]
        domain = q["domain"]
        query = q["query"]
        expected = q["expected"]
        variable = q["variable"]

        print(f"\n[{i}/{total}] [{qid}] {domain}: {query[:60]}...")
        answer, elapsed, error = send_query(query)

        grade = classify(elapsed)
        has_chart = "![chart]" in answer if answer else False
        has_table = "|" in answer and "---" in answer if answer else False
        answer_len = len(answer)

        # Check if answer seems relevant
        is_error = bool(error) or "오류" in answer[:100] or "죄송" in answer[:50]

        if error:
            status = f"EXCEPTION ({elapsed:.1f}s) — {error[:60]}"
            err += 1
        else:
            status = f"{grade} ({elapsed:.1f}s) | len={answer_len} | chart={has_chart} | table={has_table}"
            if grade == "OK":
                ok += 1
            elif grade == "WARN":
                warn += 1
            else:
                fail += 1

        print(f"  Status: {status}")
        print(f"  Answer: {answer[:100]}..." if answer else "  Answer: (empty)")

        # Domain stats
        if domain not in domain_stats:
            domain_stats[domain] = {"ok": 0, "warn": 0, "fail": 0, "err": 0, "total_time": 0, "count": 0}
        ds = domain_stats[domain]
        ds["count"] += 1
        ds["total_time"] += elapsed
        if error:
            ds["err"] += 1
        elif grade == "OK":
            ds["ok"] += 1
        elif grade == "WARN":
            ds["warn"] += 1
        else:
            ds["fail"] += 1

        results.append({
            "id": qid,
            "domain": domain,
            "query": query,
            "expected": expected,
            "variable": variable,
            "time": round(elapsed, 1),
            "grade": grade if not error else "ERR",
            "answer_len": answer_len,
            "has_chart": has_chart,
            "has_table": has_table,
            "is_error": is_error,
            "answer": answer,
            "status_line": status,
        })

    # ═══════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print("SUMMARY")
    print("=" * 75)
    print(f"Total: {total} | OK: {ok} | WARN: {warn} | FAIL: {fail} | ERR: {err}")
    print(f"Success Rate: {(ok / total) * 100:.1f}% (OK only), {((ok + warn) / total) * 100:.1f}% (OK+WARN)")
    print()

    print(f"{'Domain':<12} {'Count':<8} {'OK':<6} {'WARN':<6} {'FAIL':<6} {'ERR':<6} {'Avg(s)':<10}")
    print("-" * 60)
    for domain, ds in domain_stats.items():
        avg = ds["total_time"] / ds["count"] if ds["count"] > 0 else 0
        print(f"{domain:<12} {ds['count']:<8} {ds['ok']:<6} {ds['warn']:<6} {ds['fail']:<6} {ds['err']:<6} {avg:<10.1f}")

    # ═══════════════════════════════════════════════════
    # SAVE DETAILED RESULT FILE
    # ═══════════════════════════════════════════════════
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"Round 4 Diverse QA Test — {total} queries\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Criteria: >= 200s = FAIL, >= 100s = WARN, < 100s = OK\n")
        f.write("=" * 75 + "\n\n")

        current_domain = ""
        for r in results:
            if r["domain"] != current_domain:
                current_domain = r["domain"]
                f.write(f"\n{'=' * 75}\n")
                f.write(f" {current_domain} TESTS\n")
                f.write(f"{'=' * 75}\n\n")

            f.write(f"[{r['id']}] {r['domain']} — {r['variable']}\n")
            f.write(f"Q: {r['query']}\n")
            f.write(f"Expected: {r['expected']}\n")
            f.write(f"Status: {r['status_line']}\n")
            if r['is_error']:
                f.write(f"Fix: NEEDS_REVIEW\n")
            f.write("_" * 40 + "\n")
            f.write(r["answer"][:2000] + "\n" if r["answer"] else "(no answer)\n")
            f.write("=" * 75 + "\n\n")

        # Summary section
        f.write(f"\n{'=' * 75}\n")
        f.write("SUMMARY\n")
        f.write(f"{'=' * 75}\n\n")
        f.write(f"Total: {total} | OK: {ok} | WARN: {warn} | FAIL: {fail} | ERR: {err}\n")
        f.write(f"Success Rate: {(ok / total) * 100:.1f}% (OK), {((ok + warn) / total) * 100:.1f}% (OK+WARN)\n\n")

        f.write(f"{'Domain':<12} {'Count':<8} {'OK':<6} {'WARN':<6} {'FAIL':<6} {'ERR':<6} {'Avg(s)':<10}\n")
        f.write("-" * 60 + "\n")
        for domain, ds in domain_stats.items():
            avg = ds["total_time"] / ds["count"] if ds["count"] > 0 else 0
            f.write(f"{domain:<12} {ds['count']:<8} {ds['ok']:<6} {ds['warn']:<6} {ds['fail']:<6} {ds['err']:<6} {avg:<10.1f}\n")

        # Individual results table
        f.write(f"\n\n{'ID':<12} {'Domain':<10} {'Time':<8} {'Grade':<6} {'Len':<8} {'Chart':<6} {'Table':<6} {'Variable'}\n")
        f.write("-" * 90 + "\n")
        for r in results:
            f.write(f"{r['id']:<12} {r['domain']:<10} {r['time']:<8} {r['grade']:<6} {r['answer_len']:<8} "
                    f"{'Y' if r['has_chart'] else 'N':<6} {'Y' if r['has_table'] else 'N':<6} {r['variable']}\n")

    print(f"\nSaved to {out_file}")

    # Desktop notification
    try:
        sys.path.insert(0, os.path.dirname(base_dir) if base_dir else '.')
        sys.path.insert(0, base_dir)
        from app.core.notify import notify
        notify(
            "AI Agent 테스트 완료",
            f"R4 다양성 테스트: {total}개 | OK: {ok} | WARN: {warn} | FAIL: {fail} | 평균: {sum(r['time'] for r in results)/len(results):.1f}s",
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
