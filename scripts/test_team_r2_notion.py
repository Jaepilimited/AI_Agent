"""Notion Round 2 - 구글시트 심화 + 약점 보완 + 응용 질문 12개.

Round 1 발견사항:
- NT-03: 틱톡샵 접속방법 타임아웃 (페이지 크기 문제)
- NT-04: 클라이언트 닫힘 (연속 요청 시 공유 클라이언트 이슈)
- NT-11: MISS (데이터분석파트 - 있지만 MISS 판정)
- NT-16/17: KBT/네이버 스스 접근 불가 -> 다른 페이지 내용으로 대체 응답
- 구글시트 데이터 읽기 추가 테스트
- 더 세밀한 내용 추출 테스트
"""
import requests
import time
import re
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

QUESTIONS = [
    # === 구글시트 연동 심화 ===
    {
        "id": "R2-NT-01",
        "page": "WEST 틱톡샵US",
        "category": "시트 데이터 분석",
        "query": "노션의 WEST 틱톡샵 US 대시보드에 연결된 구글시트에서 어떤 제품들이 등록되어 있는지 상세하게 보여줘. 제품 카테고리나 가격 정보가 있으면 정리해줘",
        "expect": "구글시트 제품 데이터 상세",
    },
    {
        "id": "R2-NT-02",
        "page": "WEST 틱톡샵US",
        "category": "시트 기반 인사이트",
        "query": "노션의 틱톡샵US 대시보드에서 어필리에잇(affiliate) 운영 전략을 자세히 설명해줘. 크리에이터 관리 기준이나 GMV 목표가 있으면 알려줘",
        "expect": "어필리에잇 전략 + GMV 기준",
    },

    # === 약점 보완: 접근 불가 페이지 재테스트 ===
    {
        "id": "R2-NT-03",
        "page": "데이터 분석 파트",
        "category": "재테스트",
        "query": "노션에서 데이터 분석 파트에 대한 정보를 알려줘. VM 인스턴스 사용법이나 자동화 코드 실행 방법이 있으면 상세하게",
        "expect": "VM 사용법 + 자동화 상세",
    },
    {
        "id": "R2-NT-04",
        "page": "법인 태블릿",
        "category": "재테스트",
        "query": "노션에서 법인 태블릿의 원격 접속(Anydesk) 방법과 계정 정보를 알려줘",
        "expect": "원격 접속 방법 상세",
    },

    # === 내용 심화 추출 ===
    {
        "id": "R2-NT-05",
        "page": "EAST 2026 업무파악",
        "category": "세부 추출",
        "query": "노션에서 EAST팀 2026년 업무 중 인도네시아 담당자의 업무 내용을 상세하게 보여줘. 어떤 플랫폼을 관리하고 있는지도 알려줘",
        "expect": "인도네시아 담당자 업무 상세",
    },
    {
        "id": "R2-NT-06",
        "page": "EAST 2026 업무파악",
        "category": "세부 추출",
        "query": "노션에서 EAST팀의 2026년 라마단 캠페인 계획이 있으면 상세히 알려줘. 일정, 대상 국가, 전략 등을 정리해줘",
        "expect": "라마단 캠페인 상세 계획",
    },
    {
        "id": "R2-NT-07",
        "page": "DB daily 광고",
        "category": "세부 추출",
        "query": "노션에서 네이버 GFA(성과형 디스플레이 광고) 데이터 입력 절차를 단계별로 상세하게 알려줘. 로그인 정보, 다운로드 방법, 시트 입력 위치 등 포함해서",
        "expect": "GFA 데이터 입력 완전 가이드",
    },

    # === 응용/분석 질문 ===
    {
        "id": "R2-NT-08",
        "page": "크로스 페이지",
        "category": "업무 효율",
        "query": "노션에서 EAST팀이 일상적으로 수행하는 반복 업무(daily task)를 모든 문서에서 찾아서 정리해줘. 광고 입력, 데이터 확인 등",
        "expect": "일상 반복 업무 종합",
    },
    {
        "id": "R2-NT-09",
        "page": "크로스 페이지",
        "category": "비즈니스 분석",
        "query": "노션 문서에서 확인할 수 있는 SKIN1004가 진출한 이커머스 플랫폼 목록을 정리해줘. 쇼피, 틱톡샵, 아마존 등 각 플랫폼의 운영 국가도 함께",
        "expect": "이커머스 플랫폼 + 국가 매핑",
    },

    # === 조건부/비교 질문 ===
    {
        "id": "R2-NT-10",
        "page": "해외 출장 가이드북",
        "category": "조건부 질문",
        "query": "노션에서 해외 출장 시 법인카드 사용 방법과 비즈플레이 정산 절차를 알려줘. 항공권 외에 현지에서 사용하는 비용 처리 방법도 있으면 알려줘",
        "expect": "법인카드 + 비즈플레이 + 현지비용",
    },
    {
        "id": "R2-NT-11",
        "page": "EAST 2팀 가이드",
        "category": "요약 질문",
        "query": "노션에서 EAST 2팀의 가이드 아카이브에 있는 모든 문서의 핵심 포인트를 각각 3줄로 요약해줘",
        "expect": "각 문서 3줄 요약",
    },
    {
        "id": "R2-NT-12",
        "page": "크로스",
        "category": "종합 인사이트",
        "query": "노션 문서 전체를 기반으로, EAST팀과 WEST팀의 업무 범위와 관리 플랫폼/국가의 차이점을 분석해줘",
        "expect": "EAST vs WEST 업무 범위 비교",
    },
]

def run_test(tc, idx, total):
    tag = tc["id"]
    q = tc["query"]
    cat = tc["category"]
    page = tc["page"]
    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} | {page} | {cat}")
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
        has_s = 'sheet' in answer.lower() or 'google' in answer.lower()
        is_miss = len(answer) < 80 or ("오류" in answer and "closed" in answer)
        status = "OK" if not is_miss else "MISS"
        fmts = []
        if has_h: fmts.append("H")
        if has_b: fmts.append("B")
        if has_t: fmts.append("T")
        if has_s: fmts.append("S")
        print(f"Status: {status} | {elapsed:.1f}s | {len(answer)}ch | fmt={'+'.join(fmts) or 'plain'}")
        return {"tag": tag, "page": page, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": len(answer), "status": status,
                "h": has_h, "b": has_b, "t": has_t, "s": has_s, "answer": answer}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {"tag": tag, "page": page, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": 0, "status": "EXCEPTION", "answer": str(e)}

def main():
    print(f"{'='*70}")
    print(f"Notion R2 - {len(QUESTIONS)} Advanced Queries")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    results = [run_test(tc, i+1, len(QUESTIONS)) for i, tc in enumerate(QUESTIONS)]

    ok = sum(1 for r in results if r["status"] == "OK")
    avg_t = sum(r["time"] for r in results) / len(results)
    print(f"\n{'='*70}\nNOTION R2 SUMMARY\n{'='*70}")
    for r in results:
        f = "+".join([k for k in ["H","B","T","S"] if r.get(k.lower())]) or "plain"
        print(f"  [{r['status']:5s}] {r['tag']:10s} | {r['page']:20s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}")
    print(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    out = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_team_r2_notion_result.txt"
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(f"Notion Round 2 Results\n{'='*70}\n")
        fp.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nQueries: {len(results)}\n\n")
        for r in results:
            fp.write(f"{'='*70}\n[{r['tag']}] {r['page']} | {r['category']}\nQ: {r['query']}\n")
            fp.write(f"Expected: {r['expect']}\nStatus: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n{'_'*70}\n")
            fp.write(r.get("answer","N/A") + "\n\n")
        fp.write(f"\n{'='*70}\nSUMMARY\n{'='*70}\n")
        for r in results:
            f = "+".join([k for k in ["H","B","T","S"] if r.get(k.lower())]) or "plain"
            fp.write(f"  [{r['status']:5s}] {r['tag']:10s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}\n")
        fp.write(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s\n")
    print(f"\nSaved to {out}")

if __name__ == "__main__":
    main()
