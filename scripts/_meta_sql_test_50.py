"""Meta 광고 라이브러리 SQL Agent 테스트 50개.

검증 항목:
- SQL 생성 여부 (meta data_test 테이블 사용)
- BigQuery 실행 성공 여부
- 결과 행 수 / 샘플 데이터
- 필수 패턴 준수 (brand 소문자, publisher_platform LIKE, DATE() 래핑 등)
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "─" * 80
TARGET_TABLE = "meta data_test"
ENABLED = ["메타광고"]

# (질문, 예상_패턴_in_SQL, 예상_결과_있음)
# expected_pattern: None이면 패턴 검사 생략
# expect_rows: True=행 있어야, False=행 없어야, None=모름
TESTS = [
    # ── SKIN1004 기본 조회 ────────────────────────────────────────────────────
    ("한국 SKIN1004 메타 광고 현황",              "skin1004",               True),
    ("SKIN1004 활성 광고 몇 개야",                "is_active",              True),
    ("SKIN1004 페이스북 광고 최근 목록",          "facebook",               True),
    ("SKIN1004 인스타그램 광고 현황",             "instagram",              True),
    ("SKIN1004 공식 광고(Official_Ad) 목록",      "Official_Ad",            True),
    ("SKIN1004 파트너십 광고 몇 개야",            "Partnership_Ad",         True),
    ("SKIN1004 최근 30일 광고 시작 건수",         "INTERVAL 30 DAY",        True),
    ("SKIN1004 비디오 광고 몇 개야",              "VIDEO",                  True),
    ("SKIN1004 이미지 광고 vs 비디오 광고 비율",  "IMAGE",                  True),
    ("SKIN1004 광고 페이지명 목록",               "page_name",              True),
    # ── 국가별 분석 ─────────────────────────────────────────────────────────
    ("메타 광고 국가별 분포 top 10",              "country_name",           True),
    ("미국 SKIN1004 메타 광고",                   "United States",          True),
    ("베트남 메타 광고 현황",                     "Vietnam",                True),
    ("한국 vs 미국 SKIN1004 광고 수 비교",        "South Korea",            True),
    ("가장 광고 많은 국가 top 10",                "country_name",           True),
    ("태국 SKIN1004 광고 목록",                   "Thailand",               True),
    ("일본 SKIN1004 메타 광고",                   "Japan",                  True),
    ("싱가포르 SKIN1004 광고 몇 개야",            "Singapore",              True),
    # ── 브랜드 비교 ────────────────────────────────────────────────────────
    ("메타 광고 브랜드별 광고 수",                "brand",                  True),
    ("medicube 메타 광고 현황",                   "medicube",               True),
    ("cosrx 메타 광고 몇 개야",                   "cosrx",                  True),
    ("beauty_of_joseon 페이스북 광고",            "beauty_of_joseon",       True),
    ("SKIN1004 vs anua 광고 수 비교",             "anua",                   True),
    ("가장 광고 많은 브랜드 top 5",               "brand",                  True),
    ("dr_melaxin 활성 광고",                      "dr_melaxin",             True),
    ("laneige 메타 광고 국가별",                  "laneige",                True),
    # ── 날짜/시간 분석 ──────────────────────────────────────────────────────
    ("올해 시작된 SKIN1004 광고",                 "2026",                   True),
    ("최근 7일 시작된 광고",                      "INTERVAL 7 DAY",         True),
    ("2025년 SKIN1004 광고 현황",                 "2025",                   True),
    ("SKIN1004 광고 월별 시작 건수",              "month",                  True),
    ("가장 오래된 SKIN1004 광고",                 "ASC",                    True),
    ("현재 활성 중인 SKIN1004 광고",              "is_active",              True),
    ("최근 90일 내 종료된 SKIN1004 광고",         "INTERVAL 90 DAY",        True),
    ("올해 파트너십 광고 건수",                   "Partnership_Ad",         True),
    # ── 플랫폼 분석 ────────────────────────────────────────────────────────
    ("SKIN1004 플랫폼별 광고 분포",               "publisher_platform",     True),
    ("인스타그램에만 집행된 SKIN1004 광고",       "instagram",              True),
    ("페이스북 광고 SKIN1004",                    "facebook",               True),
    ("SKIN1004 플랫폼별 광고 유형 분포",          "ad_type",                True),
    ("메신저 광고 포함된 SKIN1004 광고",          "messenger",              True),
    ("플랫폼별 활성 광고 수 (SKIN1004)",          "is_active",              True),
    ("SKIN1004 페이스북+인스타 동시 집행 광고",   "facebook",               True),
    ("audience_network 포함 광고",                "audience_network",       True),
    # ── 고급 분석 ──────────────────────────────────────────────────────────
    ("SKIN1004 페이지별 최신 광고",               "page_name",              True),
    ("한국 파트너십 광고 최근 목록",              "South Korea",            True),
    ("메타 광고 라이브러리 전체 현황 요약",       "COUNT",                  True),
    ("SKIN1004 가장 오래 집행 중인 활성 광고",    "is_active",              True),
    ("공식광고 vs 파트너십 광고 비율",            "ad_type",                True),
    ("국가별 파트너십 광고 분포",                 "Partnership_Ad",         True),
    ("전체 브랜드 활성 광고 현황",                "is_active",              True),
]


async def main():
    from app.agents.sql_agent import run_sql_agent
    # SQL 생성+실행만 검사 — 내부적으로 generate_sql+execute 노드 실행

    print("=" * 80)
    print(f"Meta 광고 SQL Agent 테스트 — 총 {len(TESTS)}개")
    print(f"테이블: skin1004-319714.ad_data.meta data_test")
    print("=" * 80)

    results = []

    for idx, (query, expected_pattern, expect_rows) in enumerate(TESTS, 1):
        try:
            # 실제 SQL Agent 실행 (generate → validate → execute → format)
            answer = await run_sql_agent(
                query=query,
                enabled_sources=ENABLED,
                can_view_fi=True,
            )

            # 응답에서 SQL 포함 여부 & 패턴 검사
            answer_lower = answer.lower()
            has_table   = TARGET_TABLE in answer_lower or "meta" in answer_lower
            has_pattern = (
                expected_pattern is None
                or expected_pattern.lower() in answer_lower
                or expected_pattern.lower() in answer_lower
            )
            has_error   = any(k in answer_lower for k in ["실패", "오류", "error", "failed", "실행할 수 없"])
            has_rows    = not any(k in answer_lower for k in ["결과가 없", "데이터가 없", "조회되지 않", "없습니다"])

            if has_error:
                status = "❌ ERROR"
            elif expect_rows and not has_rows:
                status = "⚠️  NO_ROWS"
            else:
                status = "✅ PASS"

            preview = answer.replace("\n", " ")[:120]
            print(f"\n[{idx:02d}] {status}  |  Q: {query}")
            print(f"     preview: {preview}")
            if expected_pattern and expected_pattern.lower() not in answer_lower:
                print(f"     ⚠️  패턴 누락: '{expected_pattern}'")

            results.append({
                "idx": idx, "status": status,
                "query": query, "has_error": has_error,
            })

        except Exception as e:
            print(f"\n[{idx:02d}] 💥 EXCEPTION  |  Q: {query}")
            print(f"     error: {str(e)[:200]}")
            results.append({"idx": idx, "status": "💥 EXCEPTION", "query": query, "has_error": True})

        await asyncio.sleep(0.3)  # API 호출 간 짧은 딜레이

    # 집계
    print("\n" + "=" * 80)
    total   = len(results)
    passed  = sum(1 for r in results if r["status"].startswith("✅"))
    warned  = sum(1 for r in results if r["status"].startswith("⚠️"))
    failed  = sum(1 for r in results if r["status"].startswith("❌"))
    excepts = sum(1 for r in results if r["status"].startswith("💥"))

    print(f"결과 요약: 총 {total}개")
    print(f"  ✅ PASS      : {passed}개 ({passed/total*100:.1f}%)")
    print(f"  ⚠️  NO_ROWS   : {warned}개")
    print(f"  ❌ ERROR     : {failed}개")
    print(f"  💥 EXCEPTION : {excepts}개")

    issues = [r for r in results if not r["status"].startswith("✅")]
    if issues:
        print("\n[비정상 목록]")
        for r in issues:
            print(f"  {r['status']} | [{r['idx']:02d}] {r['query']}")


if __name__ == "__main__":
    asyncio.run(main())
