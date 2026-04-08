"""QA Test: CS / IT / PEOPLE 팀별 50문항 × 3 = 150문항.

Usage:
    python -X utf8 scripts/qa_team_150.py --port 3002
    python -X utf8 scripts/qa_team_150.py --port 3002 --team PEOPLE
"""
import argparse
import json
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

# ── Questions ──

PEOPLE_QUESTIONS = [
    # 성과급/보상 (10)
    "성과급대상자는 누구지",
    "성과금 지급 대상자는 누구야",
    "성과급 언제 줘",
    "성과급은 어떤 방식으로 지급돼",
    "인센티브 지급 기준이 뭐야",
    "성과급 얼마나 받을 수 있어",
    "반기별 성과급 시기",
    "성과급 지급 조건",
    "성과급 차등 분배 기준",
    "보상 정책 알려줘",
    # 퇴사 (5)
    "퇴사절차알려줘",
    "퇴사 프로세스가 어떻게 돼",
    "퇴직금 어떻게 받아",
    "퇴사할때 인수인계 어떻게 해",
    "퇴사 시 연차 산정 기준",
    # 연차/휴가 (10)
    "연차 며칠이야",
    "경조휴가 며칠이야",
    "출산휴가 몇일",
    "배우자 출산휴가",
    "생일 휴가 있어?",
    "건강검진 휴가",
    "전사휴무일 언제야",
    "연차촉진제가 뭐야",
    "졸업휴가 있어?",
    "휴일대체 사용 방법",
    # 복지 (5)
    "복지포인트얼마야",
    "복지포인트 사용처",
    "복지카드 어디서 써",
    "사내근로복지기금이 뭐야",
    "명절상여금 지급 시기",
    # 명함/서류 (5)
    "명함신청어떻게해",
    "재직증명서 발급 방법",
    "급여명세서 어디서 봐",
    "법인서류 신청",
    "계약서 날인 절차",
    # 채용/교육 (5)
    "채용 요청 프로세스",
    "면접 프로세스가 어떻게 돼",
    "외부 교육 신청 방법",
    "채용요청서 승인 후 이력서 언제 받아",
    "교육 보고서 어떻게 올려",
    # 업무툴/시설 (5)
    "다우오피스 결재 올리는법",
    "잔디 이름 변경 방법",
    "회의실 예약 어떻게 해",
    "커피머신 사용법",
    "분리수거 어떻게 해",
    # 핵심가치 (5)
    "공통 역량이 뭐야",
    "핵심 가치 알려줘",
    "크레이버 핵심 가치",
    "평가요소가 뭐야",
    "문제해결 역량이 뭐야",
]

IT_QUESTIONS = [
    # VPN (10)
    "VPN설정방법",
    "VPN 사용 가이드 알려줘",
    "VPN 설치 파일 어디서 받아",
    "Mac VPN 설정 방법",
    "VPN 토큰 뭐야",
    "VPN 접속이 안돼",
    "FortiClient 설치 방법",
    "VPN 사용 신청서 어디서 써",
    "SSL-VPN 신청 방법",
    "재택근무할때 VPN 필요해?",
    # 네트워크/Wi-Fi (10)
    "와이파이비번",
    "사내 와이파이 연결 방법",
    "방문객 와이파이 안내",
    "CRAVER GUEST 비번",
    "Wi-Fi SSID가 뭐야",
    "네트워크 연결이 안돼",
    "임직원용 와이파이",
    "방문객 전용 와이파이",
    "wifi 비밀번호 알려줘",
    "사내 네트워크 접속 방법",
    # 프린터 (10)
    "프린터연결방법",
    "프린터 설정 어떻게 해",
    "팩스 사용 방법",
    "출력물 회수 규정",
    "프린터 드라이버 설치",
    "컬러 프린팅 가능해?",
    "복합기 사용법",
    "스캔 방법 알려줘",
    "프린터 연결 안될때",
    "Craver 네트워크에서만 프린터 돼?",
    # 메일/캘린더 (10)
    "메일 서명 설정 방법",
    "Gmail 서명 추가",
    "캘린더 공유 방법",
    "메일 자동응답 설정",
    "Google Workspace 사용법",
    "이메일 서명 양식",
    "캘린더 일정 등록",
    "메일 전달 설정",
    "Gmail 설정 어디서 해",
    "서명 복사 방법",
    # 계정/시스템 (10)
    "계정 접속 방법",
    "Microsoft 365 어떻게 써",
    "업무 툴 목록",
    "시스템 접속 안될때",
    "Google Workspace 계정",
    "Flex 어떻게 써",
    "카카오T 비즈니스 사용법",
    "다우오피스 접속 방법",
    "업무 시스템 가이드",
    "IT 관련 문의처",
]

CS_QUESTIONS = [
    # 제품 성분/설명 (15)
    "센텔라 앰플 성분이 뭐야",
    "히알루시카 크림 특징",
    "톤브라이트닝 앰플 효과",
    "프로바이오시카 앰플 성분",
    "포어마이징 선스크린 SPF",
    "티트리카 크림 어떤 피부에 좋아",
    "센텔라 수딩크림 사용법",
    "히알루테카 퍼밍크림 성분",
    "톤브라이트닝 패드 사용법",
    "스팟커버패치 사용방법",
    "센텔라 토닝토너 성분",
    "클렌징오일 사용법",
    "센텔라 테카 크림이 뭐야",
    "포어마이징 클레이 스틱 마스크 사용법",
    "좀비팩이 뭐야",
    # 교환/반품 (10)
    "교환 반품 규정 알려줘",
    "반품 배송비 누가 내",
    "교환 신청 방법",
    "환불 처리 기간",
    "불량품 교환 절차",
    "반품 주소 알려줘",
    "네이버 스마트스토어 반품 규정",
    "교환 접수 방법",
    "반품 가능 기간",
    "미개봉 반품 가능?",
    # 제품 라인 (10)
    "센텔라 라인 제품 뭐있어",
    "톤브라이트닝 라인 종류",
    "히알루시카 라인 전제품",
    "프로바이오시카 라인 리스트",
    "포어마이징 라인 제품 목록",
    "티트리카 라인 제품",
    "SKIN1004 전제품 리스트",
    "베스트셀러 제품 뭐야",
    "센텔라 테카 라인 제품",
    "히알루테카 라인 제품",
    # 사용법/가격 (10)
    "앰플 사용 순서",
    "선크림 언제 발라",
    "마스크팩 몇분 붙여",
    "토너 사용법",
    "클렌징폼 사용법",
    "제품 가격 알려줘",
    "센텔라 앰플 용량",
    "앰플 유통기한",
    "피부타입별 추천 제품",
    "민감성 피부 추천",
    # 고객 문의 (5)
    "고객센터 전화번호",
    "제품 성분 알러지 문의",
    "비건 인증 받았어?",
    "파라벤 들어있어?",
    "동물실험 하나요",
]

# ── Test Logic ──

FAIL_KEYWORDS = ["찾을 수 없습니다", "검색 결과가 없습니다", "확인할 수 없습니다",
                  "정보가 없습니다", "데이터가 없습니다"]

print_lock = Lock()

def run_one(session, base_url, query, team_label, idx):
    """Run a single query and return result dict."""
    start = time.time()
    try:
        resp = session.post(
            f"{base_url}/v1/chat/completions",
            json={"model": "gemini", "messages": [{"role": "user", "content": query}], "stream": False},
            timeout=120,
        )
        elapsed = time.time() - start
        if resp.status_code != 200:
            return {"id": f"{team_label}-{idx:03d}", "query": query, "team": team_label,
                    "status": "FAIL", "time": elapsed, "answer_len": 0, "reason": f"HTTP {resp.status_code}"}
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Evaluate
        has_fail_kw = any(kw in content for kw in FAIL_KEYWORDS)
        too_short = len(content) < 80
        status = "FAIL" if (has_fail_kw and len(content) < 300) or too_short else "WARN" if has_fail_kw else "OK"
        if elapsed >= 90:
            status = "FAIL"
        elif elapsed >= 60:
            status = "WARN" if status == "OK" else status

        return {"id": f"{team_label}-{idx:03d}", "query": query, "team": team_label,
                "status": status, "time": round(elapsed, 1), "answer_len": len(content),
                "answer_preview": content[:300], "reason": "timeout" if elapsed >= 90 else ""}
    except Exception as e:
        elapsed = time.time() - start
        return {"id": f"{team_label}-{idx:03d}", "query": query, "team": team_label,
                "status": "FAIL", "time": round(elapsed, 1), "answer_len": 0, "reason": str(e)[:100]}


def run_team(base_url, questions, team_label, concurrency=3):
    """Run all questions for a team with limited concurrency."""
    session = requests.Session()
    # Login
    session.post(f"{base_url}/api/auth/signin", json={
        "department": "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석",
        "name": "임재필", "password": "1234"
    })

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(run_one, session, base_url, q, team_label, i+1): i
                   for i, q in enumerate(questions)}
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            with print_lock:
                icon = "✓" if r["status"] == "OK" else "!" if r["status"] == "WARN" else "✗"
                print(f"  {icon} {r['id']} | {r['time']:5.1f}s | {r['answer_len']:4d}자 | {r['query'][:40]}")

    results.sort(key=lambda x: x["id"])
    return results


def print_summary(all_results):
    """Print summary table."""
    teams = {}
    for r in all_results:
        t = r["team"]
        if t not in teams:
            teams[t] = {"OK": 0, "WARN": 0, "FAIL": 0, "total": 0, "times": []}
        teams[t][r["status"]] += 1
        teams[t]["total"] += 1
        teams[t]["times"].append(r["time"])

    print("\n" + "=" * 70)
    print(f"{'Team':<10} {'Total':>6} {'OK':>6} {'WARN':>6} {'FAIL':>6} {'Pass%':>7} {'Avg(s)':>7}")
    print("-" * 70)
    total_ok = total_all = 0
    for t in sorted(teams.keys()):
        s = teams[t]
        avg = sum(s["times"]) / len(s["times"]) if s["times"] else 0
        pct = (s["OK"] / s["total"] * 100) if s["total"] else 0
        print(f"{t:<10} {s['total']:>6} {s['OK']:>6} {s['WARN']:>6} {s['FAIL']:>6} {pct:>6.1f}% {avg:>6.1f}s")
        total_ok += s["OK"]
        total_all += s["total"]
    print("-" * 70)
    pct_all = (total_ok / total_all * 100) if total_all else 0
    print(f"{'TOTAL':<10} {total_all:>6} {total_ok:>6} {'':>6} {'':>6} {pct_all:>6.1f}%")
    print("=" * 70)

    # Print failures
    fails = [r for r in all_results if r["status"] == "FAIL"]
    if fails:
        print(f"\n{'='*70}")
        print(f"FAILURES ({len(fails)}건)")
        print(f"{'='*70}")
        for r in fails:
            print(f"\n{r['id']} | {r['query']}")
            print(f"  time={r['time']}s | len={r['answer_len']} | reason={r.get('reason','')}")
            if r.get("answer_preview"):
                print(f"  answer: {r['answer_preview'][:200]}")

    warns = [r for r in all_results if r["status"] == "WARN"]
    if warns:
        print(f"\n{'='*70}")
        print(f"WARNINGS ({len(warns)}건)")
        print(f"{'='*70}")
        for r in warns:
            print(f"  {r['id']} | {r['time']:.1f}s | {r['query'][:40]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3002)
    parser.add_argument("--team", choices=["PEOPLE", "IT", "CS"], help="Run only one team")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}"

    # Health check
    try:
        r = requests.get(f"{base_url}/health", timeout=5)
        assert r.status_code == 200
    except Exception:
        print(f"ERROR: Server not reachable at {base_url}")
        sys.exit(1)

    team_map = {
        "PEOPLE": PEOPLE_QUESTIONS,
        "IT": IT_QUESTIONS,
        "CS": CS_QUESTIONS,
    }

    if args.team:
        team_map = {args.team: team_map[args.team]}

    all_results = []
    total_start = time.time()

    for team_label, questions in team_map.items():
        print(f"\n{'='*50}")
        print(f"  {team_label} ({len(questions)} questions)")
        print(f"{'='*50}")
        results = run_team(base_url, questions, team_label, concurrency=args.concurrency)
        all_results.extend(results)

    total_elapsed = time.time() - total_start
    print(f"\nTotal time: {total_elapsed:.0f}s")
    print_summary(all_results)

    # Save results
    out_path = f"scripts/qa_team_150_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    main()
