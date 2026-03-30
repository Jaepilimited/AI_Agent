"""Test 100 queries against live server for routing & response quality."""
import asyncio
import time
import httpx

URL = "http://127.0.0.1:3000/v1/chat/completions"
CONCURRENCY = 5

TESTS = [
    # === Web Search (direct) — 20 queries ===
    ("지금 한국대통령", "direct", "이재명"),
    ("오늘 서울 날씨", "direct", ""),
    ("현재 원달러 환율", "direct", ""),
    ("오늘 주요 뉴스", "direct", ""),
    ("지금 일본 총리", "direct", ""),
    ("현재 유가", "direct", ""),
    ("최근 미국 금리", "direct", ""),
    ("올해 한국 GDP 성장률", "direct", ""),
    ("최신 아이폰 모델", "direct", ""),
    ("현재 비트코인 가격", "direct", ""),
    ("오늘 코스피 지수", "direct", ""),
    ("2026년 월드컵 개최국", "direct", ""),
    ("지금 영국 총리", "direct", ""),
    ("최근 테슬라 주가", "direct", ""),
    ("현재 금 시세", "direct", ""),
    ("올해 노벨상 수상자", "direct", ""),
    ("최신 갤럭시 모델", "direct", ""),
    ("지금 중국 주석", "direct", ""),
    ("오늘 프로야구 결과", "direct", ""),
    ("현재 유로 환율", "direct", ""),

    # === BigQuery (sales data) — 25 queries ===
    ("2025년 미국 매출", "bigquery", ""),
    ("이번 달 쇼피 인도네시아 매출", "bigquery", ""),
    ("아마존 미국 Top 10 제품", "bigquery", ""),
    ("국가별 크림 매출", "bigquery", ""),
    ("2025년 일본 매출", "bigquery", ""),
    ("틱톡샵 태국 3월 매출", "bigquery", ""),
    ("동남아 전체 매출 추이", "bigquery", ""),
    ("제품별 수량 순위 2025년", "bigquery", ""),
    ("필리핀 인기 제품 TOP 5", "bigquery", ""),
    ("B2B 신규 업체 매출", "bigquery", ""),
    ("월별 미국 매출 추이", "bigquery", ""),
    ("라자다 말레이시아 매출", "bigquery", ""),
    ("2024년 vs 2025년 매출 비교", "bigquery", ""),
    ("국가별 토너 수량", "bigquery", ""),
    ("쇼피 베트남 분기별 매출", "bigquery", ""),
    ("센텔라 앰플 전년 대비 갯수 비교", "bigquery", ""),
    ("히알루시카 크림 미국 아마존 판매량", "bigquery", ""),
    ("2025년 전체 매출 합계", "bigquery", ""),
    ("싱가포르 베스트셀러 제품", "bigquery", ""),
    ("미국 토너 제품 매출", "bigquery", ""),
    ("광고비 월별 추이", "bigquery", ""),
    ("인플루언서 캠페인 성과", "bigquery", ""),
    ("아마존 리뷰 평점 분석", "bigquery", ""),
    ("쇼피파이 반품 현황", "bigquery", ""),
    ("플랫폼별 매출 순위", "bigquery", ""),

    # === CS (product Q&A) — 15 queries ===
    ("센텔라 크림 성분이 뭐야", "cs", ""),
    ("비건 인증 받았어?", "cs", ""),
    ("임산부가 써도 되나요", "cs", ""),
    ("센텔라 앰플 사용법", "cs", ""),
    ("스킨1004 동물실험 하나요", "cs", ""),
    ("히알루시카 세럼 유통기한", "cs", ""),
    ("민감한 피부에 써도 될까요", "cs", ""),
    ("토너 바르는 순서", "cs", ""),
    ("센텔라 크림 vs 앰플 차이", "cs", ""),
    ("커먼랩스 비타민C 성분", "cs", ""),
    ("좀비뷰티 제품 뭐 있어", "cs", ""),
    ("레티놀 제품 있나요", "cs", ""),
    ("아기 피부에 써도 되나요", "cs", ""),
    ("건성 피부 추천 제품", "cs", ""),
    ("선크림 SPF 몇이야", "cs", ""),

    # === Notion (documents) — 10 queries ===
    ("반품 정책 알려줘", "notion", ""),
    ("쇼피 접속 방법", "notion", ""),
    ("틱톡샵 로그인 방법", "notion", ""),
    ("노션 매뉴얼 찾아줘", "notion", ""),
    ("라자다 셀러센터 가이드", "notion", ""),
    ("사내 문서 검색해줘", "notion", ""),
    ("아마존 셀러센터 접속방법", "notion", ""),
    ("쇼피 프로모션 가이드", "notion", ""),
    ("반품정책 프로세스", "notion", ""),
    ("큐텐 등록 방법 가이드", "notion", ""),

    # === General / Greetings (direct) — 15 queries ===
    ("안녕하세요", "direct", ""),
    ("고마워", "direct", ""),
    ("SKIN1004가 뭐하는 회사야", "direct", "센텔라"),
    ("너 뭐할 수 있어?", "direct", ""),
    ("누가 만들었어?", "direct", "임재필"),
    ("웹검색 되나?", "direct", "웹검색"),
    ("이미지 분석 가능해?", "direct", ""),
    ("차트 그릴 수 있어?", "direct", ""),
    ("구글 웹서치 api 맞아?", "direct", ""),
    ("실시간 정보 줄 수 있어?", "direct", ""),
    ("파이썬이 뭐야", "direct", ""),
    ("마크다운 문법 알려줘", "direct", ""),
    ("1+1은?", "direct", "2"),
    ("영어로 자기소개 해봐", "direct", ""),
    ("ㅎㅎㅎ", "direct", ""),

    # === Edge Cases / Ambiguous — 15 queries ===
    ("올해 미국 매출 추이와 환율 영향", "multi", ""),
    ("날씨가 매출에 영향을 줘?", "multi", ""),
    ("매출 하락 원인 분석", "multi", ""),
    ("아이 피부에 써도 되나요", "cs", ""),
    ("아이폰 최신 가격", "direct", ""),
    ("크림 매출", "bigquery", ""),
    ("크림 성분", "cs", ""),
    ("드라이브에서 파일 찾아줘", "gws", ""),
    ("내 일정 알려줘", "gws", ""),
    ("받은 메일 보여줘", "gws", ""),
    ("센텔라", "cs", ""),
    ("매출", "bigquery", ""),
    ("ㄹㅇ", "direct", ""),
    ("", "direct", ""),
    ("What is SKIN1004?", "direct", ""),
]


async def test_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, idx: int, query: str, expected_route: str, must_contain: str):
    async with sem:
        t0 = time.time()
        try:
            resp = await client.post(URL, json={
                "model": "skin1004-Analysis",
                "messages": [{"role": "user", "content": query or " "}],
                "stream": False,
            })
            elapsed = time.time() - t0
            data = resp.json()
            if "choices" in data:
                answer = data["choices"][0]["message"]["content"]
            elif "detail" in data:
                answer = f"[ERROR: {data['detail']}]"
            else:
                answer = "[NO ANSWER]"

            # Detect actual route from source comment
            source = "unknown"
            if "<!-- source:" in answer:
                source = answer.split("<!-- source:")[1].split(" -->")[0]
                answer = answer.replace(f"<!-- source:{source} -->", "").strip()
            elif "CS Q&A" in answer[:50] or "CS 데이터" in answer[:200]:
                source = "cs"
            elif "SQL" in answer[:100] or "BigQuery" in answer[:100] or ("매출" in answer[:100] and any(c.isdigit() for c in answer[:200])):
                source = "bigquery"
            elif "Notion" in answer[:100] or "문서" in answer[:100]:
                source = "notion"
            elif "Gmail" in answer[:100] or "캘린더" in answer[:100] or "드라이브" in answer[:100]:
                source = "gws"
            else:
                source = "direct"

            # Check must_contain
            content_ok = True
            if must_contain and must_contain not in answer:
                content_ok = False

            short = answer.replace("\n", " ").strip()[:100]
            route_ok = source == expected_route or expected_route == ""

            status = "✅" if route_ok and content_ok else "❌"
            route_mark = "" if route_ok else f" (got:{source})"
            content_mark = "" if content_ok else f" [missing:'{must_contain}']"

            return f"{idx+1:3d}. {status} [{elapsed:5.1f}s] [{expected_route:8s}{route_mark}] {query[:30]:30s} | {short}{content_mark}"
        except Exception as e:
            elapsed = time.time() - t0
            return f"{idx+1:3d}. ❌ [{elapsed:5.1f}s] ERROR: {str(e)[:60]} | {query[:30]}"


async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=180) as client:
        tasks = [
            test_one(client, sem, i, q, route, contain)
            for i, (q, route, contain) in enumerate(TESTS)
        ]
        results = await asyncio.gather(*tasks)

    ok = sum(1 for r in results if "✅" in r)
    fail = sum(1 for r in results if "❌" in r)

    print("=" * 120)
    print(f"SKIN1004 AI Routing & Response Test — {len(TESTS)} queries, concurrency={CONCURRENCY}")
    print("=" * 120)
    for r in results:
        print(r)
    print("=" * 120)
    print(f"PASS: {ok}/{len(TESTS)}  FAIL: {fail}/{len(TESTS)}")

    # Print failures only
    failures = [r for r in results if "❌" in r]
    if failures:
        print(f"\n--- FAILURES ({len(failures)}) ---")
        for f in failures:
            print(f)


if __name__ == "__main__":
    asyncio.run(main())
