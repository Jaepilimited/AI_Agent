#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""멀티턴 컨텍스트 테스트 — 팀·본부 어휘가 후속 질문에서 유지되는가 (2026-08-11).

어제 만든 컨텍스트 무손실 아키텍처(직전 SQL 원문 보존 + 실행 테이블 앵커) 위에
오늘 팀·본부 어휘가 얹혔다. 후속 질문에서 **필터·지표·데이터소스**가 유지돼야 한다.

각 시나리오는 새 대화(#btn-new-chat)에서 시작해 이전 시나리오가 섞이지 않게 한다.
검증은 답변 본문뿐 아니라 **실행된 SQL** 로 한다 — 본문은 그럴듯해도 SQL 이
다른 테이블·지표로 새는 경우를 잡아야 하기 때문이다.

사용:
  python -X utf8 scripts/playwright_context_team_test.py [--headed] [--base URL]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from playwright_team_division_test import ask, extract_sql, mint_admin_token

BASE_URL = "http://127.0.0.1:3001"
SHOT_DIR = Path(__file__).parent / "qa_screenshots"

SCENARIOS = [
    {
        "id": "team_filter_carryover",
        "why": "팀 필터가 후속 기간 질문에서도 유지되는가",
        "turns": [
            {"q": "올해 영업1팀 매출 알려줘", "must_sql": ["'B2B1'"]},
            {"q": "작년은?",
             "must_sql": ["'B2B1'", "2025"],
             "never_sql": ["Team_NEW IS NULL"],
             "note": "기간만 바뀌고 팀 필터는 유지돼야 한다"},
        ],
    },
    {
        "id": "division_drilldown",
        "why": "본부 답변 뒤 '그 본부만 팀별로' 가 해당 본부로 좁혀지는가",
        "turns": [
            {"q": "올해 본부별 매출 알려줘", "must": ["글로벌마케팅본부"]},
            {"q": "글로벌마케팅본부만 팀별로 나눠줘",
             "must_any": ["중국사업팀", "일본사업팀", "서구권마케팅팀", "동남아시아1팀"],
             "must_sql": ["WEST_MKT", "CBT"],
             "never": ["영업1팀", "유통1팀"],
             "note": "GM 7개 팀만 나와야 하고 영업·유통이 섞이면 안 된다"},
        ],
    },
    {
        "id": "qty_metric_carryover",
        "why": "수량 지표가 후속 질문에서 매출로 회귀하지 않는가",
        "turns": [
            {"q": "올해 팀별 판매수량 알려줘",
             "must_sql": ["Total_Qty", "Product"]},
            {"q": "영업2팀만 보여줘",
             "must_sql": ["Total_Qty", "'B2B2'"],
             "never_sql": ["Sales1_R"],
             "note": "수량 질문의 후속인데 매출(Sales1_R)로 돌아가면 회귀"},
        ],
    },
    {
        "id": "at_source_carryover",
        "why": "@@ 로 좁힌 소스가 후속 질문에서도 유지되는가",
        "turns": [
            {"q": "@@메타광고 광고 플랫폼별 분포 알려줘",
             "must_sql": ["meta data_test"]},
            {"q": "인스타그램만 보여줘",
             "must_sql": ["meta data_test"],
             "never_sql": ["SALES_ALL_Backup", "integrated_ad"],
             "note": "후속에서 판매 테이블로 새면 소스 격리가 턴 단위로 무너진 것"},
        ],
    },
    {
        "id": "pronoun_reference",
        "why": "'그거' 같은 대명사가 직전 팀을 가리키는가",
        "turns": [
            {"q": "올해 일본사업팀 매출 알려줘", "must_sql": ["'JBT'"]},
            {"q": "그거 월별로 보여줘",
             "must_sql": ["'JBT'"],
             "must_sql_any": ["FORMAT_DATETIME", "EXTRACT(MONTH", "EXTRACT(month"],
             "note": "월별로 쪼개되 팀은 그대로여야 한다"},
        ],
    },
]


def check_turn(spec: dict, answer: str, sql: str) -> list:
    """실패 사유 목록 (빈 목록 = 통과). 본문은 대소문자 무시, SQL 도 무시."""
    fails = []
    low, slow = answer.lower(), sql.lower()

    for kw in spec.get("must", []):
        if kw.lower() not in low:
            fails.append(f"본문 누락: {kw!r}")
    anys = spec.get("must_any", [])
    if anys and not any(k.lower() in low for k in anys):
        fails.append(f"본문에 다음 중 하나 필요: {anys}")
    for kw in spec.get("never", []):
        if kw.lower() in low:
            fails.append(f"본문 금지어: {kw!r}")

    if not sql.strip() and (spec.get("must_sql") or spec.get("must_sql_any")):
        fails.append("SQL 을 찾지 못함 (SQL 을 안 탄 답변일 수 있다)")
    for kw in spec.get("must_sql", []):
        if kw.lower() not in slow:
            fails.append(f"SQL 누락: {kw!r}")
    sql_anys = spec.get("must_sql_any", [])
    if sql_anys and not any(k.lower() in slow for k in sql_anys):
        fails.append(f"SQL 에 다음 중 하나 필요: {sql_anys}")
    for kw in spec.get("never_sql", []):
        if kw.lower() in slow:
            fails.append(f"SQL 금지 문구: {kw!r}")
    return fails


def new_chat(page) -> None:
    btn = page.locator("#btn-new-chat")
    if btn.count():
        btn.first.click()
        page.wait_for_timeout(1500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--base", default=BASE_URL)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    SHOT_DIR.mkdir(exist_ok=True)

    print("=" * 72)
    print(f"멀티턴 컨텍스트 테스트 (팀·본부)  대상: {base}")
    print("=" * 72)

    token = mint_admin_token()
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1400, "height": 950})
        ctx.set_default_timeout(60000)
        page = ctx.new_page()
        ctx.add_cookies([{"name": "token", "value": token,
                          "domain": "127.0.0.1", "path": "/"}])
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        print("로그인 완료\n")

        for si, sc in enumerate(SCENARIOS, 1):
            print(f"[{si}/{len(SCENARIOS)}] {sc['id']} — {sc['why']}")
            new_chat(page)
            turn_rows = []
            scenario_ok = True

            for ti, spec in enumerate(SCENARIOS[si - 1]["turns"], 1):
                pre = len(page.locator(".message.message-assistant").all())
                answer, elapsed = ask(page, spec["q"], pre)
                idx = len(page.locator(".message.message-assistant").all()) - 1
                sql = extract_sql(page, idx) if idx >= 0 else ""

                if not answer:
                    fails = ["무응답"]
                else:
                    fails = check_turn(spec, answer, sql)
                scenario_ok &= not fails

                mark = "OK  " if not fails else "FAIL"
                print(f"    T{ti} {mark} {elapsed:5.1f}s  {spec['q']}")
                if spec.get("note") and fails:
                    print(f"         (기대: {spec['note']})")
                for f in fails:
                    print(f"         - {f}")

                turn_rows.append({
                    "turn": ti, "q": spec["q"], "fails": fails,
                    "elapsed": round(elapsed, 1),
                    "sql": " ".join(sql.split())[:400],
                    "answer_head": (answer or "")[:250],
                })

            page.screenshot(path=str(SHOT_DIR / f"ctx_{sc['id']}.png"), full_page=False)
            results.append({"id": sc["id"], "status": "PASS" if scenario_ok else "FAIL",
                            "turns": turn_rows})
            print()

        browser.close()

    out = Path(__file__).parent / "playwright_context_team_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    passed = sum(1 for r in results if r["status"] == "PASS")
    print("=" * 72)
    print(f"결과: {passed}/{len(results)} 시나리오 통과   (상세: {out.name})")
    for r in results:
        print(f"  {'OK  ' if r['status'] == 'PASS' else 'FAIL'} {r['id']}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
