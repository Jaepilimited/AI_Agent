#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""팀명·본부 매핑 실 UI 테스트 (2026-08-11).

골든셋은 비스트리밍 API(`stream: False`)로 돈다. 실사용자는 **스트리밍 경로**를 타고
`@@` 파싱도 프론트가 따로 한다 — 즉 골든 44/44 여도 UI 동작은 검증되지 않는다.
이 스크립트는 그 차이를 메운다:

  1. 한글 팀명으로 필터되는가        (SQL 에 코드가 들어갔는가)
  2. 표시가 한글 팀명인가            (_relabel_team_values 가 스트리밍에도 걸리는가)
  3. 차트 라벨도 한글인가            (라벨은 결과 값을 그대로 쓴다)
  4. 본부별로 묶이는가               (구 '사업부' 명칭 회귀 감시)
  5. 코드로 물어도 통하는가          (혼용)
  6. @@ 소스 격리가 프론트 경로에서도 지켜지는가

사용:
  python -X utf8 scripts/playwright_team_division_test.py [--headed] [--base URL]
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests as req_lib
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:3001"
SHOT_DIR = Path(__file__).parent / "qa_screenshots"
MAX_WAIT = 150

# 코드가 한글명 없이 홀로 쓰였는지 검사할 때 쓴다 ('영업1팀(B2B1)' 은 정상)
TEAM_CODES = ["B2B1", "B2B2", "DT1", "DT2", "EAST1", "EAST2",
              "WEST_MKT", "WEST_Ecomm", "CBT", "JBT", "KBT", "BCM"]

CASES = [
    {
        "id": "kr_name_filter",
        "q": "올해 영업1팀 매출 알려줘",
        "must": ["영업1팀"],
        "must_sql": ["'B2B1'"],
        "never": ["존재하지 않", "찾을 수 없"],
        "why": "한글 팀명으로 물어도 SQL 은 코드여야 한다 (Team_NEW='영업1팀' 이면 0건)",
    },
    {
        "id": "team_rollup_display",
        "q": "올해 팀별 매출 알려줘",
        "must": ["영업1팀", "동남아시아1팀"],
        "must_any": ["서구권마케팅팀", "일본사업팀", "중국사업팀"],
        "never": ["GM_EAST1", "DD_DT1", "GM_Ecomm"],
        "no_bare_code": True,
        "chart_korean": True,
        "why": "스트리밍 경로에서도 표·차트 라벨이 공식 한글 팀명이어야 한다",
    },
    {
        "id": "division_rollup",
        "q": "올해 본부별 매출 알려줘",
        "must": ["글로벌마케팅본부"],
        "must_any": ["영업1본부", "유통1본부", "유통2본부", "상품본부"],
        "never": ["GM 사업부", "PR 사업부", "DD 사업부"],
        "why": "본부 5개로 묶여야 한다. 구 '사업부' 명칭은 회귀",
    },
    {
        "id": "code_still_works",
        "q": "JBT 매출 올해 얼마야?",
        "must_any": ["일본사업팀", "JBT"],
        "never": ["좀비뷰티", "존재하지 않"],
        "must_sql": ["'JBT'"],
        "why": "코드로 물어도 통해야 한다(혼용)",
    },
    {
        "id": "at_source_isolation",
        "q": "@@메타광고 광고 플랫폼별 분포 알려줘",
        "must_any": ["INSTAGRAM", "FACEBOOK", "인스타", "페이스북"],
        "never": ["언제인가요", "오류가 발생"],
        "must_sql": ["meta data_test"],
        "never_sql": ["integrated_ad", "SALES_ALL_Backup"],
        "why": "프론트 @@ 경로에서도 화이트리스트 밖 테이블을 읽으면 안 된다",
    },
]


def mint_admin_token() -> str:
    """admin 토큰을 직접 발급한다.

    비밀번호로 로그인하면 계정·부서 문자열이 바뀔 때마다 스크립트가 깨진다
    (실제로 AD 부서명이 영문→한글로 바뀌어 깨졌다). 골든 러너와 같은 방식으로
    JWT 를 만들어 붙인다 — 이 테스트의 관심사는 인증이 아니라 UI 동작이다.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from datetime import datetime, timedelta, timezone

    import jwt as pyjwt
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
    from app.config import get_settings
    from app.db.mariadb import fetch_one

    row = fetch_one("SELECT id, email FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    if not row:
        raise SystemExit("admin 사용자가 없습니다")
    return pyjwt.encode(
        {"user_id": row["id"], "email": row["email"], "role": "admin",
         "brand_filter": "",
         "exp": datetime.now(timezone.utc) + timedelta(hours=2)},
        get_settings().jwt_secret_key, algorithm="HS256")


def extract_sql(page, msg_index: int) -> str:
    """assistant 메시지의 <details> 안 SQL 을 꺼낸다.

    ⚠️ `inner_text()` 는 **보이는 텍스트만** 준다 — <details> 는 접혀 있어서 빈 문자열이
    돌아온다. 접힌 내용까지 읽으려면 `text_content()` 여야 한다.
    """
    try:
        blocks = (page.locator(".message.message-assistant").nth(msg_index)
                  .locator("details pre, details code"))
        if blocks.count() == 0:
            return ""
        return "\n".join(blocks.nth(i).text_content() or "" for i in range(blocks.count()))
    except Exception:
        return ""


def chart_labels(page, msg_index: int) -> list:
    """렌더된 차트의 라벨. Chart.js 인스턴스 → 실패 시 chart-config JSON 순으로 시도."""
    try:
        labels = page.evaluate(
            """(idx) => {
                const msgs = document.querySelectorAll('.message.message-assistant');
                const el = msgs[idx];
                if (!el) return null;
                const cv = el.querySelector('canvas');
                if (!cv || typeof Chart === 'undefined' || !Chart.getChart) return null;
                const ch = Chart.getChart(cv);
                return ch && ch.data ? (ch.data.labels || []) : null;
            }""",
            msg_index,
        )
        if labels:
            return labels
    except Exception:
        pass
    # 폴백: 원본 chart-config 블록 (접혀 있어도 text_content 로 읽힌다)
    try:
        raw = (page.locator(".message.message-assistant").nth(msg_index)
               .text_content() or "")
        m = re.search(r'"labels"\s*:\s*(\[[^\]]*\])', raw)
        if m:
            return json.loads(m.group(1))
    except Exception:
        pass
    return []


def ask(page, question: str, pre_count: int) -> tuple:
    """질문을 보내고 답변이 안정될 때까지 기다린다 (스트리밍이라 증가가 멈춰야 완료)."""
    chat_input = page.locator("textarea#chat-input").first
    chat_input.click()
    chat_input.fill(question)
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")

    start = time.time()
    prev, stable, answer = "", 0, ""
    page.wait_for_timeout(2500)
    while time.time() - start < MAX_WAIT:
        msgs = page.locator(".message.message-assistant").all()
        if len(msgs) > pre_count:
            cur = msgs[-1].inner_text()
            if cur == prev and len(cur) > 30:
                stable += 1
                if stable >= 3:
                    answer = cur
                    break
            else:
                stable = 0
            prev = cur
        page.wait_for_timeout(1500)
    return answer, time.time() - start


def check(case: dict, answer: str, sql: str, labels: list) -> list:
    """실패 사유 목록 (빈 목록 = 통과)."""
    # 본문 키워드는 **대소문자 무시**로 본다. LLM 이 'INSTAGRAM' 을 'Instagram' 으로
    # 표기하는 것은 정답 여부와 무관한데, 구분하면 멀쩡한 답변이 실패로 잡힌다.
    fails = []
    low = answer.lower()
    for kw in case.get("must", []):
        if kw.lower() not in low:
            fails.append(f"필수 누락: {kw!r}")
    any_kws = case.get("must_any", [])
    if any_kws and not any(k.lower() in low for k in any_kws):
        fails.append(f"다음 중 하나 필요: {any_kws}")
    for kw in case.get("never", []):
        if kw.lower() in low:
            fails.append(f"금지 문구: {kw!r}")

    for kw in case.get("must_sql", []):
        if kw.lower() not in sql.lower():
            fails.append(f"SQL 에 {kw!r} 없음 (SQL={sql[:120]!r})")
    for kw in case.get("never_sql", []):
        if kw.lower() in sql.lower():
            fails.append(f"SQL 에 금지 테이블 {kw!r} 등장")

    if case.get("no_bare_code"):
        # '영업1팀(B2B1)' 은 허용, 한글명 없이 코드만 나온 경우만 잡는다
        bare = [c for c in TEAM_CODES
                if re.search(rf"(?<![가-힣(]){re.escape(c)}\b", answer)
                and f"({c})" not in answer]
        if bare:
            fails.append(f"한글명 없이 코드만 표기: {bare}")

    # 차트 생성은 LLM 이 chart-config JSON 을 제대로 뱉어야 일어난다 — 간헐적으로
    # 실패한다(chart_generation_skipped). 차트 유무를 단정하지 말고,
    # **차트가 있을 때 라벨이 한글인지**만 본다. 없으면 경고로 남긴다.
    if case.get("chart_korean") and labels:
        if not any(re.search(r"[가-힣]", str(l)) for l in labels):
            fails.append(f"차트 라벨이 한글이 아님: {labels[:4]}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--base", default=BASE_URL)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    SHOT_DIR.mkdir(exist_ok=True)

    results = []
    print("=" * 72)
    print(f"팀명·본부 UI 테스트 (스트리밍 경로)  대상: {base}")
    print("=" * 72)

    token = mint_admin_token()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1400, "height": 950})
        ctx.set_default_timeout(60000)
        page = ctx.new_page()
        console_errors = []
        page.on("console", lambda m: console_errors.append(f"[{m.type}] {m.text}")
                if m.type == "error" else None)

        ctx.add_cookies([{"name": "token", "value": token,
                          "domain": "127.0.0.1", "path": "/"}])
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        print("로그인 완료\n")

        for i, case in enumerate(CASES, 1):
            print(f"[{i}/{len(CASES)}] {case['id']}: {case['q']}")
            print(f"        ({case['why']})")
            console_errors.clear()
            pre = len(page.locator(".message.message-assistant").all())
            answer, elapsed = ask(page, case["q"], pre)

            if not answer:
                print(f"  FAIL: {MAX_WAIT}s 안에 응답 없음\n")
                results.append({"id": case["id"], "status": "FAIL",
                                "fails": ["무응답"], "elapsed": MAX_WAIT})
                continue

            idx = len(page.locator(".message.message-assistant").all()) - 1
            page.wait_for_timeout(1200)   # 차트 렌더 여유
            sql = extract_sql(page, idx)
            labels = chart_labels(page, idx) if case.get("chart_korean") else []
            fails = check(case, answer, sql, labels)

            shot = SHOT_DIR / f"team_{case['id']}.png"
            page.screenshot(path=str(shot), full_page=False)

            status = "PASS" if not fails else "FAIL"
            print(f"  {status} ({elapsed:.1f}s, {len(answer)}자)")
            if case.get("chart_korean"):
                if labels:
                    print(f"  차트 라벨: {list(labels)[:3]} ...")
                else:
                    print("  (차트 없음 — LLM chart-config 생성 실패 시 정상 발생)")
            for f in fails:
                print(f"    - {f}")
            if console_errors:
                print(f"    콘솔 오류 {len(console_errors)}건: {console_errors[:2]}")
            print()

            results.append({
                "id": case["id"], "status": status, "fails": fails,
                "elapsed": round(elapsed, 1), "chars": len(answer),
                "sql": sql[:400], "chart_labels": list(labels)[:14],
                "console_errors": console_errors[:3],
                "answer_head": answer[:300],
            })

        browser.close()

    out = Path(__file__).parent / "playwright_team_division_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    passed = sum(1 for r in results if r["status"] == "PASS")
    print("=" * 72)
    print(f"결과: {passed}/{len(results)} 통과   (상세: {out.name}, 스크린샷: {SHOT_DIR.name}/)")
    for r in results:
        mark = "OK  " if r["status"] == "PASS" else "FAIL"
        print(f"  {mark} {r['id']:<22} {r['elapsed']:>6.1f}s")
        for f in r["fails"]:
            print(f"        - {f}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
