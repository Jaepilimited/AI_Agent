"""Qdrant 벡터 검색 메타데이터 테스트 50개.

검증 항목:
- 쿼리별 top_score / hit 여부 (QUALITY_GATE=0.57 기준)
- 반환된 포인트의 team 필드 일치 여부
- 메타데이터 전체 출력: page_title, section_path, page_url, chunk_index, last_edited_time
"""
import asyncio, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / '.env')

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUALITY_GATE = 0.57

# (질문, 팀_key, 기대_팀_값, 예상_HIT)
TESTS = [
    # ── PEOPLE / CRAVER 지식in ──────────────────────────────────────────
    ("채용 담당자가 누구야",               "people",  "PEOPLE",   True),
    ("피플팀 입사 온보딩 담당자",          "people",  "PEOPLE",   True),
    ("안홍비 담당 업무",                   "people",  "PEOPLE",   True),
    ("수습평가 담당자",                    "people",  "PEOPLE",   True),
    ("근태관리 누가 해",                   "people",  "PEOPLE",   True),
    ("외국인 비자 담당",                   "people",  "PEOPLE",   True),
    ("육아휴직 담당자",                    "people",  "PEOPLE",   True),
    ("급여 담당자",                        "people",  "PEOPLE",   True),
    ("퇴사 프로세스 담당자",               "people",  "PEOPLE",   True),
    ("성과평가 담당",                      "people",  "PEOPLE",   True),
    # ── Craver 회사 정보 ────────────────────────────────────────────────
    ("Craver 연매출",                      "craver",  "Craver",   True),
    ("SKIN1004 대표 이메일",               "craver",  "Craver",   True),
    ("회사 주소",                          "craver",  "Craver",   True),
    ("사업자 등록번호",                    "craver",  "Craver",   True),
    ("크레이버 다른 브랜드",               "craver",  "Craver",   True),
    ("SKIN1004 히알루-시카 워터핏 선세럼", "craver",  "Craver",   True),
    # ── B2B2 ───────────────────────────────────────────────────────────
    ("FOC 지원 조건",                      "b2b2",    "B2B2",     True),
    ("ERP 기타출고요청 절차",              "b2b2",    "B2B2",     True),
    ("CPNP 기본 정보",                     "b2b2",    "B2B2",     True),
    ("라벨 제작 발주 방법",                "b2b2",    "B2B2",     True),
    ("출장 가이드라인",                    "b2b2",    "B2B2",     True),
    ("바이어 문의 응대 방법",              "b2b2",    "B2B2",     True),
    ("회계전표 해외출금",                  "b2b2",    "B2B2",     True),
    ("창고 주소",                          "b2b2",    "B2B2",     True),
    ("Advent Calendar 정책",               "b2b2",    "B2B2",     True),
    ("계약서 어디서 받아",                 "b2b2",    "B2B2",     True),
    # ── DB팀 ────────────────────────────────────────────────────────────
    ("인플루언서 마케팅 인사이트 내용",    "db",      "DB",       True),
    ("인플루언서 캠페인 ROI",              "db",      "DB",       True),
    # ── [GM]EAST ────────────────────────────────────────────────────────
    ("시딩 현황 캘린더 어떻게 돼",         None,      "[GM]EAST", True),
    ("2026 Shopee PKG 내용",               None,      "[GM]EAST", True),
    ("EAST PKG 광고 패키지",               None,      "[GM]EAST", True),
    ("말레이시아 PKG",                     None,      "[GM]EAST", True),
    # ── [GM]WEST ────────────────────────────────────────────────────────
    ("이커머스 대시보드 내용",             None,      "[GM]WEST", True),
    ("WEST Ecomm 내용",                    None,      "[GM]WEST", True),
    # ── BCM ─────────────────────────────────────────────────────────────
    ("BCM 업무 내용",                      "bcm",     "BCM",      True),
    # ── IT ──────────────────────────────────────────────────────────────
    ("IT 관련 내용",                       "it",      "IT",       True),
    # ── CS ──────────────────────────────────────────────────────────────
    ("스마트스토어 교환 반품 규정",         "cs",      "CS",       True),
    ("전제품 한 눈에 파악",                "cs",      "CS",       True),
    # ── 팀 필터 없는 전체 검색 ──────────────────────────────────────────
    ("안홍비 담당",                        None,      "PEOPLE",   True),
    ("FOC 현금성 지원",                    None,      "B2B2",     True),
    ("SKIN1004 회사 주소",                 None,      "Craver",   True),
    ("출장 가이드라인 B2B2",               None,      "B2B2",     True),
    ("피플팀 채용 담당",                   None,      "PEOPLE",   True),
    # ── 경계값: 관련 없는 질문 (QUALITY_GATE 0.57에서 차단돼야 함) ──────
    ("오늘 날씨 어때",                     "people",  None,       False),
    ("치킨 맛있는 집 추천",                "b2b2",    None,       False),
    ("파이썬 문법 알려줘",                 "craver",  None,       False),
    ("로또 번호 예측",                     "db",      None,       False),
    ("주식 투자 방법",                     None,      None,       False),
    # ── 추가 2개 ────────────────────────────────────────────────────────
    ("RP 정보 어디서 봐",                  "b2b2",    "B2B2",     True),
    ("소모품 굿즈 샤쉐 코드 생성",         "b2b2",    "B2B2",     True),
]

SEP = "─" * 80

async def main():
    from app.agents.qdrant_agent import _embed_query, _search, TEAM_MAP

    print("=" * 80)
    print(f"Qdrant 메타데이터 테스트 — 총 {len(TESTS)}개  (QUALITY_GATE={QUALITY_GATE})")
    print("=" * 80)

    results = []

    for idx, (query, team_key, expected_team, expect_hit) in enumerate(TESTS):
        try:
            vector = await _embed_query(query)
            team_filter = TEAM_MAP.get(team_key.lower()) if team_key else None
            hits = _search(vector, team_filter=team_filter, top_k=3)

            top = hits[0] if hits else None
            top_score = top["score"] if top else 0.0
            hit = top_score >= QUALITY_GATE
            top_payload = top["payload"] if top else {}
            top_team = top_payload.get("team", "")

            # 판정
            if expect_hit and hit and (expected_team is None or top_team == expected_team):
                status = "✅ PASS"
            elif expect_hit and hit and top_team != expected_team:
                status = f"⚠️  WRONG_TEAM"
            elif expect_hit and not hit:
                status = f"❌ MISS"
            elif not expect_hit and not hit:
                status = "✅ PASS(no-hit)"
            else:
                status = f"⚠️  UNEXPECTED_HIT"

            # 출력
            print(f"\n[{idx+1:02d}] {status}  |  score={top_score:.3f}  |  Q: {query}")
            if top:
                p = top_payload
                print(f"     team         : {p.get('team','')}")
                print(f"     page_title   : {p.get('page_title','')}")
                print(f"     section_path : {p.get('section_path','')}")
                print(f"     breadcrumb   : {p.get('breadcrumb','')}")
                print(f"     chunk_index  : {p.get('chunk_index','')}")
                print(f"     last_edited  : {p.get('last_edited_time','')}")
                print(f"     page_url     : {p.get('page_url','')}")
                print(f"     text_preview : {str(p.get('text',''))[:120]}")
                if len(hits) > 1:
                    print(f"     2nd hit      : score={hits[1]['score']:.3f}  [{hits[1]['payload'].get('page_title','')}]")
                if len(hits) > 2:
                    print(f"     3rd hit      : score={hits[2]['score']:.3f}  [{hits[2]['payload'].get('page_title','')}]")
            else:
                print(f"     → 검색 결과 없음")

            results.append({"status": status, "query": query, "score": top_score})

        except Exception as e:
            print(f"\n[{idx+1:02d}] 💥 ERROR: {e}  |  Q: {query}")
            results.append({"status": f"💥 ERROR", "query": query, "score": 0})

        await asyncio.sleep(0.2)

    # 집계
    print("\n" + "=" * 80)
    total = len(results)
    passed = sum(1 for r in results if r["status"].startswith("✅"))
    warn   = sum(1 for r in results if r["status"].startswith("⚠️"))
    failed = sum(1 for r in results if r["status"].startswith("❌"))
    errors = sum(1 for r in results if r["status"].startswith("💥"))

    print(f"결과 요약: 총 {total}개")
    print(f"  ✅ PASS   : {passed}개 ({passed/total*100:.1f}%)")
    print(f"  ⚠️  WARN   : {warn}개")
    print(f"  ❌ FAIL   : {failed}개")
    print(f"  💥 ERROR  : {errors}개")

    fails = [r for r in results if not r["status"].startswith("✅")]
    if fails:
        print("\n[비정상 목록]")
        for r in fails:
            print(f"  {r['status']} | score={r['score']:.3f} | {r['query']}")


if __name__ == "__main__":
    asyncio.run(main())
