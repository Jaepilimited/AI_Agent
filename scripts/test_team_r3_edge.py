"""Round 3 - Edge Cases & Stress Test (15 queries).

모든 도메인 혼합 엣지케이스:
- 모호한 질문
- 잘못된 전제의 질문
- 매우 긴/복잡한 질문
- 도메인 경계 질문 (라우팅 정확도)
- 한영 혼합, 줄임말
- 멀티턴 시뮬레이션
"""
import requests
import time
import re
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

QUESTIONS = [
    # === 모호한 질문 ===
    {"id": "E-01", "domain": "routing", "category": "모호",
     "query": "매출 좀 알려줘",
     "expect": "전체 매출 요약 or 기간 요청"},
    {"id": "E-02", "domain": "routing", "category": "모호",
     "query": "노션에 뭐 있어?",
     "expect": "등록된 문서 목록 요약"},
    {"id": "E-03", "domain": "routing", "category": "모호",
     "query": "최근에 뭐 왔어?",
     "expect": "최근 메일 or 알림 (GWS 라우팅)"},

    # === 잘못된 전제 ===
    {"id": "E-04", "domain": "bigquery", "category": "잘못된 전제",
     "query": "2030년 매출 보여줘",
     "expect": "미래 데이터 없음 안내"},
    {"id": "E-05", "domain": "bigquery", "category": "잘못된 전제",
     "query": "나이키 브랜드 매출 알려줘",
     "expect": "해당 브랜드 없음 안내"},
    {"id": "E-06", "domain": "notion", "category": "잘못된 전제",
     "query": "노션에서 인사규정 보여줘",
     "expect": "해당 문서 없음 안내 (허용 목록에 없음)"},

    # === 복잡한/긴 질문 ===
    {"id": "E-07", "domain": "bigquery", "category": "복잡한 질문",
     "query": "2025년 동남아시아 시장에서 스킨1004 센텔라 라인의 월별 매출 추이를 분석하되, 국가별(인도네시아, 말레이시아, 필리핀, 싱가포르)로 세분화하고, 쇼피와 틱톡 두 플랫폼의 매출 비중 변화도 함께 보여줘. 가능하면 차트로 시각화해줘",
     "expect": "복잡한 다차원 분석 or 부분 답변"},

    # === 도메인 경계 ===
    {"id": "E-08", "domain": "multi", "category": "도메인 경계",
     "query": "센텔라 앰플 매출이랑 노션에 센텔라 관련 문서도 찾아줘",
     "expect": "multi 라우팅 (BQ + Notion)"},
    {"id": "E-09", "domain": "multi", "category": "도메인 경계",
     "query": "오늘 매출 보고 일정 있어? 관련 메일도 확인해줘",
     "expect": "GWS 라우팅 (Calendar + Gmail)"},
    {"id": "E-10", "domain": "direct", "category": "도메인 경계",
     "query": "스킨1004가 뭐하는 회사야?",
     "expect": "direct 라우팅 (일반 지식)"},

    # === 한영 혼합/줄임말 ===
    {"id": "E-11", "domain": "bigquery", "category": "한영 혼합",
     "query": "SK brand의 Centella Ampoule 100ml 2025 sales를 알려줘",
     "expect": "한영 혼합 질문 처리"},
    {"id": "E-12", "domain": "bigquery", "category": "줄임말",
     "query": "25년 SEA 쇼피 매출",
     "expect": "줄임말/약어 해석"},

    # === 연속 질문 (context 없이) ===
    {"id": "E-13", "domain": "bigquery", "category": "후속 질문",
     "query": "그럼 그 중에서 인도네시아만 따로 보여줘",
     "expect": "컨텍스트 없는 후속질문 -> 재질문 요청 or 인니 전체"},

    # === 특수 문자/이모지 ===
    {"id": "E-14", "domain": "direct", "category": "특수 입력",
     "query": "SKIN1004 화이팅!!! 오늘 매출 궁금해요~ ^^",
     "expect": "비격식체 처리 + 매출 조회"},

    # === 매우 긴 출력 요구 ===
    {"id": "E-15", "domain": "bigquery", "category": "긴 출력",
     "query": "2025년 모든 국가의 매출을 하나도 빠짐없이 다 보여줘",
     "expect": "전체 국가 매출 테이블 (LIMIT 적용)"},
]

def run_test(tc, idx, total):
    tag = tc["id"]
    q = tc["query"]
    cat = tc["category"]
    dom = tc["domain"]
    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} | {dom} | {cat}")
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
        has_c = '![chart]' in answer.lower()
        is_err = len(answer) < 20
        status = "ERROR" if is_err else "OK"
        fmts = []
        if has_h: fmts.append("H")
        if has_b: fmts.append("B")
        if has_t: fmts.append("T")
        if has_c: fmts.append("C")
        print(f"Status: {status} | {elapsed:.1f}s | {len(answer)}ch | fmt={'+'.join(fmts) or 'plain'}")
        print(f"Preview: {answer[:200]}...")
        return {"tag": tag, "domain": dom, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": len(answer), "status": status,
                "h": has_h, "b": has_b, "t": has_t, "c": has_c, "answer": answer}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {"tag": tag, "domain": dom, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": 0, "status": "EXCEPTION", "answer": str(e)}

def main():
    print(f"{'='*70}")
    print(f"Round 3 Edge Cases - {len(QUESTIONS)} Queries")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    results = [run_test(tc, i+1, len(QUESTIONS)) for i, tc in enumerate(QUESTIONS)]

    ok = sum(1 for r in results if r["status"] == "OK")
    avg_t = sum(r["time"] for r in results) / len(results)
    print(f"\n{'='*70}\nROUND 3 EDGE CASE SUMMARY\n{'='*70}")
    for r in results:
        f = "+".join([k for k in ["H","B","T","C"] if r.get(k.lower())]) or "plain"
        print(f"  [{r['status']:5s}] {r['tag']:5s} | {r['domain']:8s} | {r['category']:10s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}")
    print(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    out = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_team_r3_edge_result.txt"
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(f"Round 3 Edge Case Results\n{'='*70}\n")
        fp.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nQueries: {len(results)}\n\n")
        for r in results:
            fp.write(f"{'='*70}\n[{r['tag']}] {r['domain']} | {r['category']}\nQ: {r['query']}\n")
            fp.write(f"Expected: {r['expect']}\nStatus: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n{'_'*70}\n")
            fp.write(r.get("answer","N/A") + "\n\n")
        fp.write(f"\n{'='*70}\nSUMMARY\n{'='*70}\n")
        for r in results:
            f = "+".join([k for k in ["H","B","T","C"] if r.get(k.lower())]) or "plain"
            fp.write(f"  [{r['status']:5s}] {r['tag']:5s} | {r['domain']:8s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}\n")
        fp.write(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s\n")
    print(f"\nSaved to {out}")

if __name__ == "__main__":
    main()
