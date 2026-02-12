"""Notion Agent 30-query diversity test."""
import requests
import time
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

URL = "http://localhost:8100/v1/chat/completions"
MODEL = "skin1004-search"

QUERIES = [
    # 직접 검색 가능한 페이지들
    "노션에서 전사 조직도 보여줘",
    "노션에서 경비 규정 내용 알려줘",
    "노션 신규 입사자 체크리스트 가져와",
    "노션에서 해외 출장 가이드북 내용 알려줘",
    "노션 틱톡샵 접속 방법 알려줘",
    "노션 휴가신청 방법 알려줘",
    # 데이터비즈니스 관련
    "노션에서 데이터 분석 파트 내용 가져와줘",
    "노션에서 법인 태블릿 안내 내용 알려줘",
    "노션 통합 데이터 정보 페이지 보여줘",
    # 크레이버 관련
    "노션 크레이버 회의실 예약 시스템 알려줘",
    "노션에서 크레이버 회사 주소 알려줘",
    "노션 상품 기획 챕터 내용 보여줘",
    "노션 운영 챕터 내용 알려줘",
    "노션 컨텐츠 챕터 정보 알려줘",
    # EAST 관련
    "노션에서 EAST 2팀 가이드 아카이브 내용 보여줘",
    "노션 EAST 2026 업무파악 내용 가져와",
    # 마케팅/분석 관련
    "노션에서 마케팅 효율 분석 내용 알려줘",
    "노션 소시올라 2023년 데이터 보여줘",
    "노션 틱톡 시딩 데이터 관리 내용 알려줘",
    # BA/계약 관련
    "노션에서 BA 계약서 내용 알려줘",
    "노션 기자간담회 진행내용 보여줘",
    # 제품/SCM 관련
    "노션에서 데이터 생성 원료목록보고 내용 알려줘",
    "노션 CL 데이터베이스 구축 내용 보여줘",
    # 일반 검색
    "노션에서 skin1004 ai agent 페이지 읽어줘",
    "노션 벡터 임베딩 관련 내용 알려줘",
    "노션에서 게더타운 정보 알려줘",
    "노션 인사관련 정보 보여줘",
    "노션에서 FI관련 정보 알려줘",
    # 팀/조직 관련
    "노션 팀 구성원 정보 보여줘",
    "노션에서 그로스 실험실 내용 알려줘",
]

print(f"=== Notion Agent 30-Query Test ===")
print(f"Total queries: {len(QUERIES)}")
print()

results = []
for i, query in enumerate(QUERIES, 1):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    start = time.time()
    try:
        resp = requests.post(URL, json=payload, timeout=180)
        elapsed = time.time() - start
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # Determine if it found relevant content
        fail_markers = [
            "페이지를 찾을 수 없습니다",
            "관련 페이지를 찾을 수 없습니다",
            "내용을 읽을 수 없습니다",
            "오류가 발생",
            "검색어를 바꿔서",
        ]
        success = not any(m in content for m in fail_markers)
        status = "OK" if success else "MISS"

        results.append({
            "query": query,
            "status": status,
            "time": elapsed,
            "answer_len": len(content),
            "preview": content[:150].replace("\n", " "),
        })
        print(f"[{i:2d}/30] {status:4s} | {elapsed:5.1f}s | {query}")
        if not success:
            print(f"         -> {content[:100].replace(chr(10), ' ')}")

    except Exception as e:
        elapsed = time.time() - start
        results.append({
            "query": query,
            "status": "ERR",
            "time": elapsed,
            "answer_len": 0,
            "preview": str(e)[:100],
        })
        print(f"[{i:2d}/30] ERR  | {elapsed:5.1f}s | {query} -> {e}")

# Summary
print()
print("=" * 60)
print("=== SUMMARY ===")
ok = sum(1 for r in results if r["status"] == "OK")
miss = sum(1 for r in results if r["status"] == "MISS")
err = sum(1 for r in results if r["status"] == "ERR")
times = [r["time"] for r in results]
avg_time = sum(times) / len(times) if times else 0

print(f"OK: {ok}/30 | MISS: {miss}/30 | ERR: {err}/30")
print(f"Avg time: {avg_time:.1f}s | Min: {min(times):.1f}s | Max: {max(times):.1f}s")
print(f"Total time: {sum(times):.0f}s")
print()
print("--- Failed queries ---")
for r in results:
    if r["status"] != "OK":
        print(f"  {r['status']:4s} | {r['query']}")
        print(f"         {r['preview'][:120]}")
