"""GWS Round 2 - 약점 보완 + 복합 분석 + 업무 응용 10개.

Round 1 발견사항:
- GWS-05: 보안 메일 검색 타임아웃 (302s) -> 더 구체적 키워드
- Gmail 평균 140s -> 단일 키워드 검색은 더 빠를 수 있음
- Drive 검색 빠르고 안정적 (21-46s)
- Calendar 비어있는 경우 적절한 EMPTY 응답
- Cross-service 질문 잘 처리됨
"""
import requests
import time
import re
from datetime import datetime

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL = "skin1004-Search"

QUESTIONS = [
    # === Gmail 약점 보완 ===
    {
        "id": "R2-GWS-01",
        "service": "Gmail",
        "category": "보안 재테스트",
        "query": "최근 Shopee 관련 로그인 알림 메일을 찾아줘",
        "expect": "Shopee 로그인 메일 (구체적 키워드)",
    },
    {
        "id": "R2-GWS-02",
        "service": "Gmail",
        "category": "날짜 범위",
        "query": "오늘 받은 메일만 보여줘. 시간순으로 정리해줘",
        "expect": "오늘 메일 시간순 정리",
    },
    {
        "id": "R2-GWS-03",
        "service": "Gmail",
        "category": "특정 발신자",
        "query": "하나투어에서 온 메일이 있으면 보여줘",
        "expect": "하나투어 메일 검색",
    },

    # === Calendar 심화 ===
    {
        "id": "R2-GWS-04",
        "service": "Calendar",
        "category": "장기 일정",
        "query": "앞으로 30일간 예정된 모든 일정을 주차별로 정리해줘",
        "expect": "30일 일정 주차별 그룹핑",
    },

    # === Drive 심화 ===
    {
        "id": "R2-GWS-05",
        "service": "Drive",
        "category": "유형별 검색",
        "query": "드라이브에서 프레젠테이션(PPT) 파일만 검색해줘",
        "expect": "PPT 파일만 필터링",
    },
    {
        "id": "R2-GWS-06",
        "service": "Drive",
        "category": "키워드 심화",
        "query": "드라이브에서 '인도네시아' 또는 'Indonesia' 관련 파일을 찾아줘",
        "expect": "인도네시아 관련 파일",
    },
    {
        "id": "R2-GWS-07",
        "service": "Drive",
        "category": "업무 파일",
        "query": "드라이브에서 KOL이나 인플루언서 관련 파일을 찾아줘",
        "expect": "KOL/인플루언서 파일 목록",
    },

    # === 크로스 서비스 심화 ===
    {
        "id": "R2-GWS-08",
        "service": "Cross",
        "category": "업무 검색",
        "query": "TikTok 관련된 내용을 전부 찾아줘. 메일, 파일, 일정 모두 포함해서",
        "expect": "TikTok 관련 전체 검색",
    },
    {
        "id": "R2-GWS-09",
        "service": "Cross",
        "category": "종합 분석",
        "query": "이번 달에 받은 광고/마케팅 관련 제안 메일을 분석하고, 관련 파일이 드라이브에 있는지도 확인해줘",
        "expect": "마케팅 제안 종합 분석",
    },
    {
        "id": "R2-GWS-10",
        "service": "Cross",
        "category": "일일 리포트",
        "query": "내 업무 상태를 종합적으로 알려줘: 오늘 메일 요약, 이번 주 남은 일정, 최근 수정한 파일 5개",
        "expect": "업무 상태 종합 리포트",
    },
]

def run_test(tc, idx, total):
    tag = tc["id"]
    q = tc["query"]
    cat = tc["category"]
    svc = tc["service"]
    print(f"\n{'='*70}")
    print(f"[{idx}/{total}] {tag} | {svc} | {cat}")
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
        has_l = 'http' in answer
        is_empty = ("없습니다" in answer and len(answer) < 100) or len(answer) < 30
        is_auth = "재로그인" in answer
        status = "AUTH" if is_auth else ("EMPTY" if is_empty else "OK")
        fmts = []
        if has_h: fmts.append("H")
        if has_b: fmts.append("B")
        if has_t: fmts.append("T")
        if has_l: fmts.append("L")
        print(f"Status: {status} | {elapsed:.1f}s | {len(answer)}ch | fmt={'+'.join(fmts) or 'plain'}")
        return {"tag": tag, "service": svc, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": len(answer), "status": status,
                "h": has_h, "b": has_b, "t": has_t, "l": has_l, "answer": answer}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        return {"tag": tag, "service": svc, "category": cat, "query": q, "expect": tc["expect"],
                "time": elapsed, "chars": 0, "status": "EXCEPTION", "answer": str(e)}

def main():
    print(f"{'='*70}")
    print(f"GWS R2 - {len(QUESTIONS)} Queries")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    results = [run_test(tc, i+1, len(QUESTIONS)) for i, tc in enumerate(QUESTIONS)]

    ok = sum(1 for r in results if r["status"] == "OK")
    avg_t = sum(r["time"] for r in results) / len(results)
    print(f"\n{'='*70}\nGWS R2 SUMMARY\n{'='*70}")
    for r in results:
        f = "+".join([k for k in ["H","B","T","L"] if r.get(k.lower())]) or "plain"
        print(f"  [{r['status']:5s}] {r['tag']:12s} | {r['service']:8s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}")
    print(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    out = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_team_r2_gws_result.txt"
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(f"GWS Round 2 Results\n{'='*70}\n")
        fp.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nQueries: {len(results)}\n\n")
        for r in results:
            fp.write(f"{'='*70}\n[{r['tag']}] {r['service']} | {r['category']}\nQ: {r['query']}\n")
            fp.write(f"Expected: {r['expect']}\nStatus: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n{'_'*70}\n")
            fp.write(r.get("answer","N/A") + "\n\n")
        fp.write(f"\n{'='*70}\nSUMMARY\n{'='*70}\n")
        for r in results:
            f = "+".join([k for k in ["H","B","T","L"] if r.get(k.lower())]) or "plain"
            fp.write(f"  [{r['status']:5s}] {r['tag']:12s} | {r['time']:6.1f}s | {r['chars']:5d}ch | {f}\n")
        fp.write(f"\nTotal: {ok}/{len(results)} OK | Avg: {avg_t:.1f}s\n")
    print(f"\nSaved to {out}")

if __name__ == "__main__":
    main()
