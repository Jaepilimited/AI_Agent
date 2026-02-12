"""Notion 테스트팀 — 허용 목록 10개 페이지 상세 분석/응용 질문 20개.

각 Notion 허용 페이지를 대상으로 상세 질문:
- 페이지 내용 정확 추출
- 구글시트 포함 페이지 → 시트 데이터까지 읽기 확인
- 응용 질문: 절차 요약, 비교, 조건부 질문
- 크로스 페이지 질문
"""
import requests
import time
import re
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

# ── Notion 허용 페이지 기반 상세 질문 20개 ──
QUESTIONS = [
    # === 1. EAST 해외 출장 가이드북 (상세) ===
    {
        "id": "NT-01",
        "page": "EAST 해외 출장 가이드북",
        "category": "절차 분석",
        "query": "노션에서 해외 출장 시 항공권 예약부터 결제까지의 전체 절차를 단계별로 상세히 알려줘. 특히 FI팀 승인 프로세스를 자세히 설명해줘",
        "expect": "Step별 절차 + FI승인 프로세스 상세",
    },
    {
        "id": "NT-02",
        "page": "EAST 해외 출장 가이드북",
        "category": "응용 질문",
        "query": "노션에서 해외 출장 준비할 때 하나투어에 문의하는 이메일 양식과 연락처를 정리해줘. 항공권, 숙박, 보험 각각 구분해서",
        "expect": "이메일 양식 + 연락처 테이블",
    },

    # === 2. EAST 틱톡샵 접속 방법 ===
    {
        "id": "NT-03",
        "page": "EAST 틱톡샵 접속 방법",
        "category": "절차 분석",
        "query": "노션에서 틱톡샵 셀러센터 접속 방법을 알려줘. 각 국가별(인도네시아, 말레이시아 등) 접속 URL이나 방법이 다른지도 설명해줘",
        "expect": "접속 방법 + 국가별 구분",
    },
    {
        "id": "NT-04",
        "page": "EAST 틱톡샵 접속 방법",
        "category": "심화 질문",
        "query": "노션에서 틱톡샵 운영 시 주의사항이나 팁이 있으면 정리해줘",
        "expect": "운영 팁/주의사항",
    },

    # === 3. WEST 틱톡샵US 대시보드 ===
    {
        "id": "NT-05",
        "page": "WEST 틱톡샵US 대시보드",
        "category": "구글시트 연동",
        "query": "노션에서 WEST 틱톡샵 US 대시보드에 어떤 데이터가 있는지 상세하게 보여줘. 구글시트가 연결되어 있으면 시트 내용도 포함해줘",
        "expect": "대시보드 내용 + 구글시트 데이터",
    },
    {
        "id": "NT-06",
        "page": "WEST 틱톡샵US 대시보드",
        "category": "분석 질문",
        "query": "노션의 WEST 틱톡샵 US 대시보드에서 확인할 수 있는 주요 지표(KPI)가 무엇인지 정리해줘",
        "expect": "KPI 목록 + 설명",
    },

    # === 4. EAST 2026 업무파악 ===
    {
        "id": "NT-07",
        "page": "EAST 2026 업무파악",
        "category": "업무 정리",
        "query": "노션에서 EAST팀 2026년 업무파악 내용을 보여줘. 주요 업무 항목과 담당자 등이 정리되어 있으면 그대로 보여줘",
        "expect": "업무 목록 + 담당자",
    },
    {
        "id": "NT-08",
        "page": "EAST 2026 업무파악",
        "category": "응용 분석",
        "query": "노션에서 EAST팀의 2026년 업무 중 신규 사업이나 중점 추진 과제가 있다면 정리해줘",
        "expect": "신규/중점 과제 요약",
    },

    # === 5. EAST 2팀 가이드 아카이브 ===
    {
        "id": "NT-09",
        "page": "EAST 2팀 가이드 아카이브",
        "category": "아카이브 탐색",
        "query": "노션에서 EAST 2팀 가이드 아카이브에 어떤 가이드 문서들이 있는지 목록을 보여줘",
        "expect": "가이드 문서 목록",
    },
    {
        "id": "NT-10",
        "page": "EAST 2팀 가이드 아카이브",
        "category": "심화 탐색",
        "query": "노션에서 EAST 2팀 가이드 중 동남아 시장 관련 가이드가 있으면 상세 내용을 보여줘",
        "expect": "동남아 관련 가이드 내용",
    },

    # === 6. 데이터 분석 파트 ===
    {
        "id": "NT-11",
        "page": "데이터 분석 파트",
        "category": "구글시트 연동",
        "query": "노션에서 데이터 분석 파트 페이지에 구글시트가 연결되어 있으면 시트 데이터를 보여줘. 어떤 분석 업무들이 정리되어 있는지도 알려줘",
        "expect": "분석 업무 목록 + 구글시트 내용",
    },
    {
        "id": "NT-12",
        "page": "데이터 분석 파트",
        "category": "업무 분석",
        "query": "노션에서 데이터 분석 파트의 주요 업무 범위와 담당 영역을 정리해줘",
        "expect": "업무 범위 + 담당 영역",
    },

    # === 7. DB daily 광고 입력 업무 ===
    {
        "id": "NT-13",
        "page": "DB daily 광고 입력 업무",
        "category": "절차 분석",
        "query": "노션에서 DB daily 광고 입력 업무의 전체 프로세스를 단계별로 설명해줘. 어떤 데이터를 어디에 입력하는지 상세하게",
        "expect": "광고 입력 프로세스 상세",
    },
    {
        "id": "NT-14",
        "page": "DB daily 광고 입력 업무",
        "category": "응용 질문",
        "query": "노션에서 광고 입력 업무 시 주의해야 할 점이나 자주 실수하는 부분이 있다면 알려줘",
        "expect": "주의사항/실수 포인트",
    },

    # === 8. 법인 태블릿 ===
    {
        "id": "NT-15",
        "page": "법인 태블릿",
        "category": "자산 관리",
        "query": "노션에서 법인 태블릿 관리 현황을 보여줘. 어떤 기기들이 등록되어 있고, 사용자나 상태 정보가 있으면 함께 보여줘",
        "expect": "태블릿 목록 + 상태",
    },

    # === 9. KBT 스스 운영방법 ===
    {
        "id": "NT-16",
        "page": "KBT 스스 운영방법",
        "category": "운영 가이드",
        "query": "노션에서 KBT 스마트스토어 운영방법을 상세하게 알려줘. 상품 등록, 주문 처리, 고객 응대 등 주요 업무 절차를 정리해줘",
        "expect": "스스 운영 절차 상세",
    },

    # === 10. 네이버 스스 업무 공유 ===
    {
        "id": "NT-17",
        "page": "네이버 스스 업무 공유",
        "category": "업무 공유",
        "query": "노션에서 네이버 스마트스토어 업무 공유 내용을 보여줘. 팀 내에서 공유하는 업무 프로세스나 가이드라인이 있으면 정리해줘",
        "expect": "스스 업무 프로세스 공유",
    },

    # === 11. 크로스 페이지 / 응용 질문 ===
    {
        "id": "NT-18",
        "page": "크로스 페이지",
        "category": "비교 분석",
        "query": "노션에서 EAST팀의 틱톡샵 접속 방법과 WEST팀의 틱톡샵US 대시보드를 비교해서 각 팀이 틱톡샵을 어떻게 관리하고 있는지 정리해줘",
        "expect": "EAST vs WEST 틱톡샵 운영 비교",
    },
    {
        "id": "NT-19",
        "page": "크로스 페이지",
        "category": "종합 요약",
        "query": "노션에서 현재 등록된 모든 문서 목록과 각 문서의 주요 내용을 한눈에 볼 수 있도록 요약 테이블로 정리해줘",
        "expect": "전체 문서 요약 테이블",
    },
    {
        "id": "NT-20",
        "page": "응용",
        "category": "비즈니스 응용",
        "query": "노션에서 신입 직원이 EAST팀에 합류했을 때 꼭 읽어야 할 필수 문서 순서를 추천해줘. 출장, 틱톡샵, 업무파악 등을 고려해서",
        "expect": "신입 직원 온보딩 문서 추천",
    },
]


def run_test(test_case: dict, idx: int, total: int) -> dict:
    tag = test_case["id"]
    q = test_case["query"]
    cat = test_case["category"]
    page = test_case["page"]

    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} | {page} | {cat}")
    print(f"Q: {q}")
    print(f"{'='*70}")

    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=300)
        elapsed = time.time() - t0
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]

        has_headers = bool(re.findall(r'^#{1,4} ', answer, re.MULTILINE))
        has_bold = '**' in answer
        has_table = '|' in answer and '---' in answer
        has_bullet = bool(re.findall(r'^[\s]*[-*] ', answer, re.MULTILINE))
        has_blockquote = bool(re.findall(r'^> ', answer, re.MULTILINE))
        has_sheet_data = 'google' in answer.lower() or '시트' in answer or 'sheet' in answer.lower()
        has_sections = sum(1 for m in ["요약", "주요 내용", "관련 세부", "출처"] if m in answer)

        # Quality checks
        is_miss = "찾을 수 없" in answer or "검색 결과가 없" in answer
        is_auth_error = "재로그인" in answer or "접근" in answer and "필요" in answer
        is_short = len(answer) < 80

        status = "OK"
        if is_miss:
            status = "MISS"
        elif is_auth_error and len(answer) < 200:
            status = "AUTH_ERR"
        elif is_short:
            status = "SHORT"

        fmt_tags = []
        if has_headers: fmt_tags.append("H")
        if has_bold: fmt_tags.append("B")
        if has_table: fmt_tags.append("T")
        if has_blockquote: fmt_tags.append("Q")
        if has_sheet_data: fmt_tags.append("SHEET")

        print(f"Status: {status} | {elapsed:.1f}s | {len(answer)}ch | fmt={'+'.join(fmt_tags) or 'plain'}")
        print(f"Preview: {answer[:250]}...")

        return {
            "tag": tag, "page": page, "category": cat, "query": q,
            "expect": test_case["expect"],
            "time": elapsed, "chars": len(answer), "status": status,
            "headers": has_headers, "bold": has_bold, "table": has_table,
            "blockquote": has_blockquote, "bullet": has_bullet,
            "sheet_data": has_sheet_data, "sections": has_sections,
            "answer": answer,
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {
            "tag": tag, "page": page, "category": cat, "query": q,
            "expect": test_case["expect"],
            "time": elapsed, "chars": 0, "status": "EXCEPTION",
            "answer": f"EXCEPTION: {e}",
        }


def main():
    print(f"{'='*70}")
    print(f"Notion Test Team - {len(QUESTIONS)} Page-Specific Queries")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    results = []
    for idx, tc in enumerate(QUESTIONS, 1):
        result = run_test(tc, idx, len(QUESTIONS))
        results.append(result)

    # ── Summary ──
    print(f"\n\n{'='*70}")
    print("NOTION TEAM — FINAL SUMMARY")
    print(f"{'='*70}")

    ok = sum(1 for r in results if r["status"] == "OK")
    miss = sum(1 for r in results if r["status"] == "MISS")
    err = sum(1 for r in results if r["status"] in ("EXCEPTION", "AUTH_ERR"))
    avg_time = sum(r["time"] for r in results) / len(results) if results else 0
    avg_chars = sum(r["chars"] for r in results if r["status"] == "OK") / max(ok, 1)
    h_count = sum(1 for r in results if r.get("headers"))
    b_count = sum(1 for r in results if r.get("bold"))
    t_count = sum(1 for r in results if r.get("table"))
    sheet_count = sum(1 for r in results if r.get("sheet_data"))

    for r in results:
        fmts = []
        if r.get("headers"): fmts.append("H")
        if r.get("bold"): fmts.append("B")
        if r.get("table"): fmts.append("T")
        if r.get("blockquote"): fmts.append("Q")
        if r.get("sheet_data"): fmts.append("S")
        fmt_str = "+".join(fmts) if fmts else "plain"
        print(f"  [{r['status']:8s}] {r['tag']:5s} | {r['page']:25s} | {r['time']:6.1f}s | {r['chars']:5d}ch | fmt={fmt_str}")

    print(f"\n{'─'*50}")
    print(f"  Total: {ok}/{len(results)} OK, {miss} MISS, {err} errors")
    print(f"  Avg time: {avg_time:.1f}s | Avg chars: {avg_chars:.0f}")
    print(f"  Headers: {h_count} | Bold: {b_count} | Tables: {t_count} | Sheet data: {sheet_count}")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Save ──
    output_path = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_team_notion_result.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"Notion Test Team Results\n{'='*70}\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Queries: {len(results)}\n\n")

        for r in results:
            f.write(f"{'='*70}\n")
            f.write(f"[{r['tag']}] {r['page']} | {r['category']}\n")
            f.write(f"Q: {r['query']}\n")
            f.write(f"Expected: {r['expect']}\n")
            f.write(f"Status: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n")
            fmts = []
            if r.get("headers"): fmts.append("HEADERS")
            if r.get("bold"): fmts.append("BOLD")
            if r.get("table"): fmts.append("TABLE")
            if r.get("blockquote"): fmts.append("QUOTE")
            if r.get("sheet_data"): fmts.append("SHEET_DATA")
            f.write(f"Formatting: {', '.join(fmts) if fmts else 'plain'}\n")
            f.write(f"{'─'*70}\n")
            f.write(r.get("answer", "N/A"))
            f.write(f"\n\n")

        f.write(f"\n{'='*70}\n")
        f.write(f"SUMMARY\n{'='*70}\n")
        for r in results:
            fmts = []
            if r.get("headers"): fmts.append("H")
            if r.get("bold"): fmts.append("B")
            if r.get("table"): fmts.append("T")
            if r.get("sheet_data"): fmts.append("S")
            fmt_str = "+".join(fmts) if fmts else "plain"
            f.write(f"  [{r['status']:8s}] {r['tag']:5s} | {r['page']:25s} | {r['time']:6.1f}s | {r['chars']:5d}ch | fmt={fmt_str}\n")
        f.write(f"\nTotal: {ok}/{len(results)} OK | MISS: {miss} | Avg: {avg_time:.1f}s | Tables: {t_count}\n")

    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()
