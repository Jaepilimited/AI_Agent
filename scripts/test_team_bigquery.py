"""BigQuery 테스트팀 — 상세 분석/응용 질문 20개.

각 질문은 단순 조회가 아닌 분석적 관점에서 설계:
- 비교 분석 (YoY, MoM, 국가간, 브랜드간)
- 트렌드 분석 (추이, 성장률)
- 제품 분석 (라인별, SKU별)
- 비즈니스 인사이트 도출
"""
import requests
import time
import json
import re
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

# ── 상세 분석 질문 20개 ──
QUESTIONS = [
    # === 1. 매출 트렌드 & 비교 분석 ===
    {
        "id": "BQ-01",
        "category": "트렌드 분석",
        "query": "2025년 월별 총 매출 추이를 차트로 보여주고, 전년 동기 대비 성장률이 가장 높았던 달은 언제인지 분석해줘",
        "expect": "월별 추이 + 성장률 분석",
    },
    {
        "id": "BQ-02",
        "category": "YoY 비교",
        "query": "2024년 대비 2025년 분기별 매출을 비교 분석해줘. 어느 분기에 가장 큰 성장이 있었는지 인사이트도 포함해줘",
        "expect": "분기별 비교 테이블 + 인사이트",
    },
    {
        "id": "BQ-03",
        "category": "국가 비교",
        "query": "2025년 동남아시아 국가별(인도네시아, 말레이시아, 필리핀, 싱가포르, 베트남, 태국) 매출 순위를 분석하고, 각 국가의 주요 판매 채널(쇼피, 라자다, 틱톡 등)도 함께 보여줘",
        "expect": "국가별 매출 + 채널 분석",
    },
    {
        "id": "BQ-04",
        "category": "플랫폼 분석",
        "query": "쇼피, 라자다, 틱톡샵, 아마존 4대 플랫폼의 2025년 매출을 비교하고, 각 플랫폼의 전년 대비 성장률을 분석해줘",
        "expect": "플랫폼별 매출 비교 + 성장률",
    },

    # === 2. 제품 심층 분석 ===
    {
        "id": "BQ-05",
        "category": "제품 분석",
        "query": "센텔라 라인 제품들의 2025년 매출 순위 TOP 10을 보여주고, 센텔라 앰플과 센텔라 크림의 매출 비중을 비교 분석해줘",
        "expect": "라인별 제품 순위 + 비중 분석",
    },
    {
        "id": "BQ-06",
        "category": "제품 트렌드",
        "query": "2025년 센텔라 앰플 100ml의 월별 판매 추이를 차트로 보여줘. 어떤 시기에 매출이 집중되는지 분석해줘",
        "expect": "월별 판매 추이 차트 + 시즌 분석",
    },
    {
        "id": "BQ-07",
        "category": "제품 다각도",
        "query": "스킨1004 브랜드의 2025년 제품 라인별(Centella, Hyalucica, Poremizing, Tone_Brightening 등) 매출을 비교하고, 각 라인의 핵심 베스트셀러 제품도 함께 알려줘",
        "expect": "라인별 매출 + 베스트셀러",
    },
    {
        "id": "BQ-08",
        "category": "제품-국가 크로스",
        "query": "센텔라 앰플이 어느 국가에서 가장 많이 팔리는지 국가별 매출 TOP 10을 보여주고, 각 국가에서의 주요 판매 플랫폼도 분석해줘",
        "expect": "국가-플랫폼 크로스 분석",
    },

    # === 3. B2B vs B2C 분석 ===
    {
        "id": "BQ-09",
        "category": "채널 분석",
        "query": "2025년 B2B와 B2C의 매출 비중을 분석하고, B2B 내에서 국내 도매 vs 해외 도매의 비율도 함께 보여줘",
        "expect": "B2B/B2C 비교 + 하위 채널 분석",
    },
    {
        "id": "BQ-10",
        "category": "B2B 심층",
        "query": "2025년 해외 B2B 거래처별(Company_Name) 매출 TOP 10을 보여줘. 어떤 거래처가 가장 큰 비중을 차지하는지 분석해줘",
        "expect": "거래처별 매출 순위 + 집중도 분석",
    },

    # === 4. 팀 성과 분석 ===
    {
        "id": "BQ-11",
        "category": "팀 분석",
        "query": "2025년 각 팀(Team_NEW)별 매출과 주문 건수를 동시에 보여주고, 건당 매출 효율이 가장 높은 팀은 어디인지 분석해줘",
        "expect": "팀별 매출+주문 + 효율 분석",
    },
    {
        "id": "BQ-12",
        "category": "팀 트렌드",
        "query": "GM_EAST1 팀과 GM_EAST2 팀의 2025년 월별 매출 추이를 비교해줘. 어느 팀이 더 빠르게 성장하고 있는지 분석해줘",
        "expect": "팀간 월별 비교 + 성장 분석",
    },

    # === 5. 브랜드 분석 ===
    {
        "id": "BQ-13",
        "category": "브랜드 비교",
        "query": "SKIN1004(SK)와 CommonLabs(CL) 브랜드의 2025년 매출을 비교하고, 각 브랜드의 주요 판매 국가 TOP 5도 함께 분석해줘",
        "expect": "브랜드 매출 비교 + 국가 분석",
    },
    {
        "id": "BQ-14",
        "category": "브랜드-플랫폼",
        "query": "커먼랩스(CL) 브랜드의 2025년 동남아 플랫폼별(쇼피, 라자다, 틱톡) 매출을 비교해줘",
        "expect": "서브브랜드 플랫폼 분석",
    },

    # === 6. 수량 & 제품 분석 ===
    {
        "id": "BQ-15",
        "category": "수량 분석",
        "query": "2025년 가장 많이 팔린 제품 TOP 15를 수량 기준으로 보여줘. 매출 순위와 수량 순위가 다른 제품이 있다면 그것도 분석해줘",
        "expect": "수량 TOP + 매출 순위 교차 분석",
    },
    {
        "id": "BQ-16",
        "category": "제품-시즌",
        "query": "선크림(Sun) 제품의 2024년 vs 2025년 월별 판매량 추이를 비교해줘. 여름 시즌 매출 패턴이 어떻게 다른지 분석해줘",
        "expect": "시즌 패턴 YoY 비교",
    },

    # === 7. 심화 응용 분석 ===
    {
        "id": "BQ-17",
        "category": "시장 점유율",
        "query": "2025년 일본 시장에서 아마존, 라쿠텐, 큐텐 3개 플랫폼의 매출 비중을 분석하고, 어떤 플랫폼에 더 집중해야 할지 인사이트를 제공해줘",
        "expect": "일본 플랫폼 비중 + 전략 인사이트",
    },
    {
        "id": "BQ-18",
        "category": "복합 분석",
        "query": "2025년 미국 시장 전체 매출과 주요 채널(아마존, 자사몰, 틱톡)별 매출 비중을 보여주고, 전년 대비 가장 빠르게 성장한 채널이 어디인지 분석해줘",
        "expect": "미국 채널별 매출 + 성장 분석",
    },
    {
        "id": "BQ-19",
        "category": "FOC 분석",
        "query": "2025년 FOC(무상 제공) 금액이 가장 큰 국가 TOP 5와 전체 매출 대비 FOC 비율을 분석해줘",
        "expect": "FOC 분석 + 비율 계산",
    },
    {
        "id": "BQ-20",
        "category": "종합 대시보드",
        "query": "2025년 1월 전체 실적 요약을 대시보드 형태로 정리해줘: 총 매출, 총 주문 건수, 국가별 TOP 5, 플랫폼별 TOP 5, 제품별 TOP 5를 한눈에 보여줘",
        "expect": "종합 대시보드 스타일 요약",
    },
]

def run_test(test_case: dict, idx: int, total: int) -> dict:
    """Run a single test query and return result dict."""
    tag = test_case["id"]
    q = test_case["query"]
    cat = test_case["category"]

    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} | {cat}")
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

        # Formatting quality checks
        has_headers = bool(re.findall(r'^#{1,4} ', answer, re.MULTILINE))
        has_bold = '**' in answer
        has_table = '|' in answer and '---' in answer
        has_bullet = bool(re.findall(r'^[\s]*[-*] ', answer, re.MULTILINE))
        has_blockquote = bool(re.findall(r'^> ', answer, re.MULTILINE))
        has_chart = '![chart]' in answer.lower() or '![Chart]' in answer
        has_numbers = bool(re.findall(r'[\d,]+[억만원]', answer))
        has_sections = sum(1 for m in ["요약", "상세 데이터", "분석", "인사이트"] if m in answer)

        # Content quality checks
        is_error = "오류" in answer and len(answer) < 100
        is_too_short = len(answer) < 50
        is_korean = any(c >= '\uac00' and c <= '\ud7a3' for c in answer[:200])

        status = "OK"
        if is_error:
            status = "ERROR"
        elif is_too_short:
            status = "SHORT"

        fmt_tags = []
        if has_headers: fmt_tags.append("H")
        if has_bold: fmt_tags.append("B")
        if has_table: fmt_tags.append("T")
        if has_chart: fmt_tags.append("C")
        if has_blockquote: fmt_tags.append("Q")

        print(f"Status: {status} | {elapsed:.1f}s | {len(answer)}ch | fmt={'+'.join(fmt_tags) or 'plain'} | sections={has_sections}")
        print(f"Preview: {answer[:200]}...")

        return {
            "tag": tag, "category": cat, "query": q, "expect": test_case["expect"],
            "time": elapsed, "chars": len(answer), "status": status,
            "headers": has_headers, "bold": has_bold, "table": has_table,
            "chart": has_chart, "blockquote": has_blockquote, "bullet": has_bullet,
            "has_numbers": has_numbers, "sections": has_sections,
            "answer": answer,
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {
            "tag": tag, "category": cat, "query": q, "expect": test_case["expect"],
            "time": elapsed, "chars": 0, "status": "EXCEPTION",
            "answer": f"EXCEPTION: {e}",
        }


def main():
    print(f"{'='*70}")
    print(f"BigQuery Test Team - {len(QUESTIONS)} Analytical Queries")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    results = []
    for idx, tc in enumerate(QUESTIONS, 1):
        result = run_test(tc, idx, len(QUESTIONS))
        results.append(result)

    # ── Summary ──
    print(f"\n\n{'='*70}")
    print("BIGQUERY TEAM — FINAL SUMMARY")
    print(f"{'='*70}")

    ok = sum(1 for r in results if r["status"] == "OK")
    err = sum(1 for r in results if r["status"] in ("ERROR", "EXCEPTION"))
    avg_time = sum(r["time"] for r in results) / len(results) if results else 0
    avg_chars = sum(r["chars"] for r in results if r["status"] == "OK") / max(ok, 1)
    h_count = sum(1 for r in results if r.get("headers"))
    b_count = sum(1 for r in results if r.get("bold"))
    t_count = sum(1 for r in results if r.get("table"))
    c_count = sum(1 for r in results if r.get("chart"))
    q_count = sum(1 for r in results if r.get("blockquote"))
    n_count = sum(1 for r in results if r.get("has_numbers"))

    for r in results:
        fmts = []
        if r.get("headers"): fmts.append("H")
        if r.get("bold"): fmts.append("B")
        if r.get("table"): fmts.append("T")
        if r.get("chart"): fmts.append("C")
        if r.get("blockquote"): fmts.append("Q")
        fmt_str = "+".join(fmts) if fmts else "plain"
        print(f"  [{r['status']:5s}] {r['tag']:6s} | {r['category']:12s} | {r['time']:6.1f}s | {r['chars']:5d}ch | fmt={fmt_str}")

    print(f"\n{'─'*50}")
    print(f"  Total: {ok}/{len(results)} OK, {err} errors")
    print(f"  Avg time: {avg_time:.1f}s | Avg chars: {avg_chars:.0f}")
    print(f"  Headers: {h_count} | Bold: {b_count} | Tables: {t_count} | Charts: {c_count}")
    print(f"  Blockquotes: {q_count} | Numbers(억/만원): {n_count}")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Save Full Results ──
    output_path = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_team_bigquery_result.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"BigQuery Test Team Results\n{'='*70}\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Queries: {len(results)}\n\n")

        for r in results:
            f.write(f"{'='*70}\n")
            f.write(f"[{r['tag']}] {r['category']}\n")
            f.write(f"Q: {r['query']}\n")
            f.write(f"Expected: {r['expect']}\n")
            f.write(f"Status: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n")
            fmts = []
            if r.get("headers"): fmts.append("HEADERS")
            if r.get("bold"): fmts.append("BOLD")
            if r.get("table"): fmts.append("TABLE")
            if r.get("chart"): fmts.append("CHART")
            if r.get("blockquote"): fmts.append("QUOTE")
            f.write(f"Formatting: {', '.join(fmts) if fmts else 'plain'}\n")
            f.write(f"Sections detected: {r.get('sections', 0)}\n")
            f.write(f"{'─'*70}\n")
            f.write(r.get("answer", "N/A"))
            f.write(f"\n\n")

        # Summary at end
        f.write(f"\n{'='*70}\n")
        f.write(f"SUMMARY\n{'='*70}\n")
        for r in results:
            fmts = []
            if r.get("headers"): fmts.append("H")
            if r.get("bold"): fmts.append("B")
            if r.get("table"): fmts.append("T")
            if r.get("chart"): fmts.append("C")
            if r.get("blockquote"): fmts.append("Q")
            fmt_str = "+".join(fmts) if fmts else "plain"
            f.write(f"  [{r['status']:5s}] {r['tag']:6s} | {r['category']:12s} | {r['time']:6.1f}s | {r['chars']:5d}ch | fmt={fmt_str}\n")
        f.write(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_time:.1f}s | Charts: {c_count} | Tables: {t_count}\n")

    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()
