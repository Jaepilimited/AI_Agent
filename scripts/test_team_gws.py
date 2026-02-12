"""GWS 테스트팀 — Gmail/Calendar/Drive 상세 분석/응용 질문 15개.

각 서비스별 심화 질문:
- Gmail: 복합 검색, 날짜 범위, 발신자 필터, 분석 요청
- Calendar: 기간별 일정, 일정 분석, 충돌 확인
- Drive: 파일 검색, 유형별, 최근 수정
- 크로스 서비스: 메일+일정+파일 복합 질문
"""
import requests
import time
import re
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

# ── GWS 상세 질문 15개 ──
QUESTIONS = [
    # === Gmail 심화 ===
    {
        "id": "GWS-01",
        "service": "Gmail",
        "category": "복합 검색",
        "query": "지난 일주일간 받은 메일 중 마케팅 관련 메일만 모아서 보여줘. 제안서, 협업, 프로모션 관련 메일을 분류해서 정리해줘",
        "expect": "마케팅 메일 분류 테이블",
    },
    {
        "id": "GWS-02",
        "service": "Gmail",
        "category": "발신자 분석",
        "query": "최근 받은 메일에서 외부 업체(사내 메일 제외)로부터 온 비즈니스 제안 메일을 정리해줘. 업체명, 제안 내용, 날짜를 표로 만들어줘",
        "expect": "외부 업체 제안 메일 테이블",
    },
    {
        "id": "GWS-03",
        "service": "Gmail",
        "category": "키워드 검색",
        "query": "메일에서 'SKIN1004' 또는 '스킨1004' 관련 최근 메일을 검색해줘",
        "expect": "브랜드 관련 메일 목록",
    },
    {
        "id": "GWS-04",
        "service": "Gmail",
        "category": "분석 요청",
        "query": "최근 2주간 받은 메일 중 결제/입금 관련 메일을 모아서 금액과 날짜를 정리해줘",
        "expect": "결제 관련 메일 + 금액 정리",
    },
    {
        "id": "GWS-05",
        "service": "Gmail",
        "category": "보안 분석",
        "query": "최근 받은 로그인 알림 메일(Shopee, Shopify, 카카오 등)을 모아서 어떤 서비스에서 로그인 시도가 있었는지 정리해줘",
        "expect": "로그인 알림 정리",
    },

    # === Calendar 심화 ===
    {
        "id": "GWS-06",
        "service": "Calendar",
        "category": "주간 일정",
        "query": "이번 주와 다음 주 2주간의 전체 일정을 요일별로 정리해줘",
        "expect": "2주간 일정 요일별 정리",
    },
    {
        "id": "GWS-07",
        "service": "Calendar",
        "category": "일정 분석",
        "query": "앞으로 한달간 예정된 회의나 미팅 일정이 있으면 보여줘. 일정이 특정 요일에 집중되어 있는지도 분석해줘",
        "expect": "1개월 미팅 일정 + 패턴 분석",
    },
    {
        "id": "GWS-08",
        "service": "Calendar",
        "category": "특정 일정",
        "query": "캘린더에서 '매출' 또는 '보고' 키워드가 포함된 일정을 검색해줘",
        "expect": "키워드 기반 일정 검색",
    },
    {
        "id": "GWS-09",
        "service": "Calendar",
        "category": "일정 확인",
        "query": "오늘 남은 일정과 내일 일정을 한번에 보여줘. 준비해야 할 것이 있으면 알려줘",
        "expect": "오늘+내일 일정 + 준비사항",
    },

    # === Drive 심화 ===
    {
        "id": "GWS-10",
        "service": "Drive",
        "category": "파일 검색",
        "query": "구글 드라이브에서 '매출' 관련 파일을 검색해줘. 스프레드시트, 문서, 프레젠테이션 종류별로 분류해서 보여줘",
        "expect": "매출 파일 유형별 분류",
    },
    {
        "id": "GWS-11",
        "service": "Drive",
        "category": "최근 파일",
        "query": "구글 드라이브에서 최근 수정된 파일 10개를 보여줘. 파일명, 유형, 수정일, 링크를 표로 정리해줘",
        "expect": "최근 파일 테이블 + 링크",
    },
    {
        "id": "GWS-12",
        "service": "Drive",
        "category": "키워드 검색",
        "query": "드라이브에서 'TikTok' 또는 '틱톡' 관련 파일을 찾아줘",
        "expect": "틱톡 관련 파일 목록",
    },

    # === 크로스 서비스 & 응용 ===
    {
        "id": "GWS-13",
        "service": "크로스",
        "category": "종합 검색",
        "query": "오늘 일정을 확인하고, 관련된 최근 메일이 있으면 함께 보여줘",
        "expect": "일정 + 관련 메일 연결",
    },
    {
        "id": "GWS-14",
        "service": "크로스",
        "category": "업무 브리핑",
        "query": "오늘의 업무 브리핑을 해줘: 오늘 일정, 중요 메일 요약, 최근 공유된 파일 등을 종합해서 정리해줘",
        "expect": "종합 업무 브리핑",
    },
    {
        "id": "GWS-15",
        "service": "Drive",
        "category": "비즈니스 응용",
        "query": "드라이브에서 보고서나 리포트 형태의 파일을 찾아줘. 특히 2026년에 생성된 것 위주로",
        "expect": "2026년 보고서 파일 목록",
    },
]


def run_test(test_case: dict, idx: int, total: int) -> dict:
    tag = test_case["id"]
    q = test_case["query"]
    cat = test_case["category"]
    svc = test_case["service"]

    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} | {svc} | {cat}")
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
        has_link = 'http' in answer

        # Quality checks
        is_auth_error = "재로그인" in answer or "접근하려면" in answer
        is_no_result = "검색 결과가 없" in answer or "일정이 없" in answer
        is_error = "오류 발생" in answer and len(answer) < 100
        is_short = len(answer) < 30

        status = "OK"
        if is_auth_error:
            status = "AUTH"
        elif is_error:
            status = "ERROR"
        elif is_no_result and len(answer) < 100:
            status = "EMPTY"
        elif is_short:
            status = "SHORT"

        fmt_tags = []
        if has_headers: fmt_tags.append("H")
        if has_bold: fmt_tags.append("B")
        if has_table: fmt_tags.append("T")
        if has_blockquote: fmt_tags.append("Q")
        if has_link: fmt_tags.append("L")

        print(f"Status: {status} | {elapsed:.1f}s | {len(answer)}ch | fmt={'+'.join(fmt_tags) or 'plain'}")
        print(f"Preview: {answer[:250]}...")

        return {
            "tag": tag, "service": svc, "category": cat, "query": q,
            "expect": test_case["expect"],
            "time": elapsed, "chars": len(answer), "status": status,
            "headers": has_headers, "bold": has_bold, "table": has_table,
            "blockquote": has_blockquote, "bullet": has_bullet, "link": has_link,
            "answer": answer,
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {
            "tag": tag, "service": svc, "category": cat, "query": q,
            "expect": test_case["expect"],
            "time": elapsed, "chars": 0, "status": "EXCEPTION",
            "answer": f"EXCEPTION: {e}",
        }


def main():
    print(f"{'='*70}")
    print(f"GWS Test Team - {len(QUESTIONS)} Queries (Gmail/Calendar/Drive)")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    results = []
    for idx, tc in enumerate(QUESTIONS, 1):
        result = run_test(tc, idx, len(QUESTIONS))
        results.append(result)

    # ── Summary ──
    print(f"\n\n{'='*70}")
    print("GWS TEAM — FINAL SUMMARY")
    print(f"{'='*70}")

    ok = sum(1 for r in results if r["status"] == "OK")
    empty = sum(1 for r in results if r["status"] == "EMPTY")
    auth = sum(1 for r in results if r["status"] == "AUTH")
    err = sum(1 for r in results if r["status"] in ("ERROR", "EXCEPTION"))
    avg_time = sum(r["time"] for r in results) / len(results) if results else 0
    avg_chars = sum(r["chars"] for r in results if r["status"] == "OK") / max(ok, 1)

    # Service breakdown
    gmail_ok = sum(1 for r in results if r["service"] == "Gmail" and r["status"] == "OK")
    gmail_total = sum(1 for r in results if r["service"] == "Gmail")
    cal_ok = sum(1 for r in results if r["service"] == "Calendar" and r["status"] in ("OK", "EMPTY"))
    cal_total = sum(1 for r in results if r["service"] == "Calendar")
    drive_ok = sum(1 for r in results if r["service"] == "Drive" and r["status"] == "OK")
    drive_total = sum(1 for r in results if r["service"] == "Drive")
    cross_ok = sum(1 for r in results if r["service"] == "크로스" and r["status"] == "OK")
    cross_total = sum(1 for r in results if r["service"] == "크로스")

    h_count = sum(1 for r in results if r.get("headers"))
    b_count = sum(1 for r in results if r.get("bold"))
    t_count = sum(1 for r in results if r.get("table"))
    l_count = sum(1 for r in results if r.get("link"))

    for r in results:
        fmts = []
        if r.get("headers"): fmts.append("H")
        if r.get("bold"): fmts.append("B")
        if r.get("table"): fmts.append("T")
        if r.get("blockquote"): fmts.append("Q")
        if r.get("link"): fmts.append("L")
        fmt_str = "+".join(fmts) if fmts else "plain"
        print(f"  [{r['status']:5s}] {r['tag']:6s} | {r['service']:8s} | {r['category']:12s} | {r['time']:6.1f}s | {r['chars']:5d}ch | fmt={fmt_str}")

    print(f"\n{'─'*50}")
    print(f"  Total: {ok}/{len(results)} OK, {empty} EMPTY, {auth} AUTH, {err} errors")
    print(f"  Gmail: {gmail_ok}/{gmail_total} | Calendar: {cal_ok}/{cal_total} | Drive: {drive_ok}/{drive_total} | Cross: {cross_ok}/{cross_total}")
    print(f"  Avg time: {avg_time:.1f}s | Avg chars: {avg_chars:.0f}")
    print(f"  Headers: {h_count} | Bold: {b_count} | Tables: {t_count} | Links: {l_count}")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Save ──
    output_path = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_team_gws_result.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"GWS Test Team Results\n{'='*70}\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Queries: {len(results)}\n\n")

        for r in results:
            f.write(f"{'='*70}\n")
            f.write(f"[{r['tag']}] {r['service']} | {r['category']}\n")
            f.write(f"Q: {r['query']}\n")
            f.write(f"Expected: {r['expect']}\n")
            f.write(f"Status: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n")
            fmts = []
            if r.get("headers"): fmts.append("HEADERS")
            if r.get("bold"): fmts.append("BOLD")
            if r.get("table"): fmts.append("TABLE")
            if r.get("blockquote"): fmts.append("QUOTE")
            if r.get("link"): fmts.append("LINK")
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
            if r.get("link"): fmts.append("L")
            fmt_str = "+".join(fmts) if fmts else "plain"
            f.write(f"  [{r['status']:5s}] {r['tag']:6s} | {r['service']:8s} | {r['time']:6.1f}s | {r['chars']:5d}ch | fmt={fmt_str}\n")
        f.write(f"\nTotal: {ok}/{len(results)} OK | Gmail: {gmail_ok}/{gmail_total} | Cal: {cal_ok}/{cal_total} | Drive: {drive_ok}/{drive_total}\n")

    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()
