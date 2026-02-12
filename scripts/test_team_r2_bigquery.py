"""BigQuery Round 2 - 심화 분석 + Round 1 약점 보완 15개.

Round 1 발견사항:
- BQ-14: CL(CommonLabs) SE Asia 데이터 없음 -> 데이터 있는 범위로 재질문
- BQ-20: 복합 대시보드 쿼리 실패 -> 단계적 접근
- BQ-07: 짧은 응답 -> 더 구체적 질문
- 차트 생성 12/20 -> 차트 요청 더 다양하게
- 성장률/비중 분석 강화
"""
import requests
import time
import re
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

QUESTIONS = [
    # === Round 1 약점 보완 ===
    {
        "id": "R2-BQ-01",
        "category": "약점 보완",
        "query": "커먼랩스(CL) 브랜드의 2024년 전체 매출과 국가별 판매 현황을 보여줘. 주요 판매 채널도 함께 분석해줘",
        "expect": "CL 브랜드 분석 (기간 확대)",
    },
    {
        "id": "R2-BQ-02",
        "category": "약점 보완",
        "query": "2024년 전체 실적 요약: 총 매출, B2B vs B2C 비중, 대륙별 TOP 3, 팀별 TOP 3를 정리해줘",
        "expect": "종합 대시보드 (기간 변경)",
    },
    {
        "id": "R2-BQ-03",
        "category": "약점 보완",
        "query": "스킨1004 제품 라인별(Line 컬럼) 2025년 매출 비중을 파이차트로 보여줘. 각 라인에서 가장 잘 팔리는 제품도 알려줘",
        "expect": "라인별 매출 비중 + 베스트셀러",
    },

    # === 심화 분석: 성장률/전환 ===
    {
        "id": "R2-BQ-04",
        "category": "성장률 분석",
        "query": "2024년과 2025년의 국가별 매출을 비교해서 성장률이 가장 높은 국가 TOP 10과 성장률이 마이너스인 국가를 모두 보여줘",
        "expect": "국가별 YoY 성장률 + 마이너스 국가",
    },
    {
        "id": "R2-BQ-05",
        "category": "시장 침투",
        "query": "인도네시아 시장의 쇼피, 라자다, 틱톡, 토코피디아 4개 플랫폼 매출 추이를 2024년 하반기부터 2025년까지 분기별로 보여줘",
        "expect": "인도네시아 플랫폼별 분기 추이",
    },
    {
        "id": "R2-BQ-06",
        "category": "제품 포트폴리오",
        "query": "2025년 신제품(2024년에는 판매 기록이 없고 2025년에 처음 판매된 제품)이 있으면 리스트와 매출을 보여줘",
        "expect": "신제품 목록 + 매출",
    },

    # === 복합 비즈니스 인사이트 ===
    {
        "id": "R2-BQ-07",
        "category": "비즈니스 인사이트",
        "query": "2025년 아마존 채널의 국가별(미국, 일본, 캐나다, 호주) 매출을 비교하고, 아마존 전체가 회사 매출에서 차지하는 비중을 분석해줘",
        "expect": "아마존 국가별 + 전체 비중",
    },
    {
        "id": "R2-BQ-08",
        "category": "비즈니스 인사이트",
        "query": "2025년 틱톡샵(Tiktok)의 국가별 매출을 보여주고, 틱톡샵이 전체 B2C 매출에서 차지하는 비중의 변화를 2024년과 비교해줘",
        "expect": "틱톡 성장 분석 + B2C 비중",
    },
    {
        "id": "R2-BQ-09",
        "category": "수익성 분석",
        "query": "2025년 제품별 매출 대비 제조원가(Production_Cost) 비율이 가장 높은(마진이 낮은) 제품 TOP 10을 보여줘",
        "expect": "제품별 마진율 분석",
    },

    # === 시각화 중심 ===
    {
        "id": "R2-BQ-10",
        "category": "시각화",
        "query": "2025년 월별 B2B와 B2C 매출 추이를 동시에 비교하는 차트를 보여줘",
        "expect": "B2B vs B2C 월별 비교 차트",
    },
    {
        "id": "R2-BQ-11",
        "category": "시각화",
        "query": "2025년 브랜드별(SK, CL, DD) 분기 매출 추이를 차트로 비교해줘",
        "expect": "브랜드별 분기 차트",
    },
    {
        "id": "R2-BQ-12",
        "category": "시각화",
        "query": "센텔라 앰플 제품들의 국가별 판매 비중을 파이 차트로 보여줘",
        "expect": "제품-국가 파이 차트",
    },

    # === 고급 분석 ===
    {
        "id": "R2-BQ-13",
        "category": "고급 분석",
        "query": "2025년 국가별 건당 평균 매출(AOV)이 가장 높은 국가 TOP 10을 보여줘. 건당 매출 = 총매출 / 주문건수로 계산해줘",
        "expect": "국가별 AOV 분석",
    },
    {
        "id": "R2-BQ-14",
        "category": "고급 분석",
        "query": "2025년 할인/쿠폰(Discount_Coupon) 사용 금액이 가장 큰 플랫폼 TOP 5와 할인율(할인금액/매출)을 분석해줘",
        "expect": "할인 분석 + 비율",
    },
    {
        "id": "R2-BQ-15",
        "category": "고급 분석",
        "query": "2025년 각 대륙(Continent1)에서 가장 잘 팔리는 제품 1위를 보여줘. 대륙마다 선호 제품이 다른지 분석해줘",
        "expect": "대륙별 인기 제품 분석",
    },
]

def run_test(tc, idx, total):
    tag = tc["id"]
    q = tc["query"]
    cat = tc["category"]
    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} | {cat}")
    print(f"Q: {q}")
    print(f"{'='*70}")
    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": MODEL, "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=300)
        elapsed = time.time() - t0
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        has_h = bool(re.findall(r'^#{1,4} ', answer, re.MULTILINE))
        has_b = '**' in answer
        has_t = '|' in answer and '---' in answer
        has_c = '![chart]' in answer.lower() or '![Chart]' in answer
        has_q = bool(re.findall(r'^> ', answer, re.MULTILINE))
        is_err = ("오류" in answer and len(answer) < 100) or len(answer) < 50
        status = "ERROR" if is_err else "OK"
        fmts = []
        if has_h: fmts.append("H")
        if has_b: fmts.append("B")
        if has_t: fmts.append("T")
        if has_c: fmts.append("C")
        if has_q: fmts.append("Q")
        print(f"Status: {status} | {elapsed:.1f}s | {len(answer)}ch | fmt={'+'.join(fmts) or 'plain'}")
        return {"tag": tag, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": len(answer), "status": status,
                "h": has_h, "b": has_b, "t": has_t, "c": has_c, "q": has_q,
                "answer": answer}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {"tag": tag, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": 0, "status": "EXCEPTION", "answer": str(e)}

def main():
    print(f"{'='*70}")
    print(f"BigQuery R2 - {len(QUESTIONS)} Advanced Queries")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    results = [run_test(tc, i+1, len(QUESTIONS)) for i, tc in enumerate(QUESTIONS)]

    ok = sum(1 for r in results if r["status"] == "OK")
    avg_t = sum(r["time"] for r in results) / len(results)
    print(f"\n{'='*70}\nBQ R2 SUMMARY\n{'='*70}")
    for r in results:
        f = "+".join([k for k in ["H","B","T","C","Q"] if r.get(k.lower() if k!="Q" else "q")]) or "plain"
        print(f"  [{r['status']:5s}] {r['tag']:10s} | {r['category']:12s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}")
    print(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    out = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_team_r2_bigquery_result.txt"
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(f"BigQuery Round 2 Results\n{'='*70}\n")
        fp.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nQueries: {len(results)}\n\n")
        for r in results:
            fp.write(f"{'='*70}\n[{r['tag']}] {r['category']}\nQ: {r['query']}\nExpected: {r['expect']}\n")
            fp.write(f"Status: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n{'_'*70}\n")
            fp.write(r.get("answer","N/A") + "\n\n")
        fp.write(f"\n{'='*70}\nSUMMARY\n{'='*70}\n")
        for r in results:
            f = "+".join([k for k in ["H","B","T","C","Q"] if r.get(k.lower() if k!="Q" else "q")]) or "plain"
            fp.write(f"  [{r['status']:5s}] {r['tag']:10s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}\n")
        fp.write(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s\n")
    print(f"\nSaved to {out}")

if __name__ == "__main__":
    main()
