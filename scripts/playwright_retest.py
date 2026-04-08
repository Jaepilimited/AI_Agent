#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright retest — jp2 시트 실패 항목 + 수정 검증.

수정된 규칙 검증:
- CTE 허용 (이동평균, 누적, 중간값, YoY)
- SKU + Product_Name 함께 표시
- "최근" = 최근 3개월
- 서론 간결화
- 복잡 쿼리 SQL 생성 강제

Usage:
  python -X utf8 scripts/playwright_retest.py
"""

import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
import requests as req_lib

BASE_URL = "http://127.0.0.1:3000"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
RESULTS_FILE = Path(__file__).parent / "playwright_retest_results.json"
MAX_WAIT = 180

# jp2 시트 실패 항목 재현 + 수정 검증 질문
QUESTIONS = [
    # === CTE 허용 검증 (이동평균, 누적, 중간값, YoY) ===
    "월별 매출액의 3개월 이동평균을 계산해줘",
    "전체 제품의 중간값(Median) 매출액은 얼마야",
    "Brand별 월별 누적 매출액을 계산해줘",
    "전년 동기 대비 국가별 총 수량 증감률 비교해줘",
    "전년 대비 Category별 수량 변화 분석해줘",

    # === SQL 생성 실패했던 질문 재시도 ===
    "연도별 Centella 라인 제품 판매 수량 합계 보여줘",
    "지난달 대비 이번 달 총 판매 수량 성장률",
    "Brand CL 제외하고 2023년 4분기에 가장 많이 팔린 SET 제품",
    "각 Country별로 가장 많이 팔린 Category",
    "각 SKU별 평균 일별 판매 수량",
    "한국의 Toner 카테고리 3개월 이동평균 판매 수량",
    "각 Country에서 가장 많이 팔린 Line의 평균 판매 수량",
    "각 Country별 누적 판매 수량 추이",
    "2023년 1분기 동안 판매 수량이 300개 미만이었던 Line별 총 판매 수량",
    "2023년 11월 15일의 모든 판매 기록 상세하게 보여줘",
    "2023년 각 월별 TOP 5 SKU의 판매 수량 비교",
    "최근 일본에서 인기 있는 Line 제품",
    "2023년 전체 판매 수량 상위 10% SKU들의 월별 추이",
    "Hyalucica 라인 크림 카테고리 2023년 10월 상세 매출 내역",
    "가장 매출액이 높았던 단일 거래의 모든 상세 정보",
    "상위 5개 Category에 대해 국가별 수량 교차 분석",
    "요즘 전체 매출 실적 어떤가요",
    "가장 잘 팔리는 제품은 뭐야",
    "전체 매출 데이터 바탕으로 성장 가능성 높은 국가 추천",
    "전체 평균 단가보다 높은 단가 제품들의 총 매출액",

    # === SKU + Product_Name 검증 ===
    "매출액 기준 TOP 10 SKU 리스트 알려줘",

    # === "최근" 해석 검증 ===
    "SK 브랜드의 최근 판매 동향 알려줘",

    # === 서론 간결화 검증 ===
    "모든 제품의 평균 판매 수량",

    # === 속도 느린 쿼리 검증 ===
    "Country별 판매 수량 점유율 파이차트로",

    # === 사업부 매핑 검증 ===
    "사업부별 매출액과 주문수량",
]


def extract_sql(answer: str) -> str:
    m = re.search(r'<details>.*?```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def run_retest():
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(60000)
        page = context.new_page()

        # Login via API cookie
        print("  Logging in...")
        login_resp = req_lib.post(f"{BASE_URL}/api/auth/signin", json={
            "department": LOGIN_DEPT, "name": LOGIN_NAME, "password": LOGIN_PW,
        })
        if login_resp.status_code != 200:
            print(f"  Login failed: {login_resp.text}")
            return
        token = login_resp.cookies.get("token")
        context.add_cookies([{"name": "token", "value": token, "domain": "127.0.0.1", "path": "/"}])
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        print("  Login OK\n")

        total = len(QUESTIONS)
        for idx, question in enumerate(QUESTIONS):
            qnum = idx + 1
            print(f"  [{qnum:2d}/{total}] {question[:55]}...", end="", flush=True)

            # New conversation every 3 questions
            if idx > 0 and idx % 3 == 0:
                new_btn = page.locator("button#btn-new-chat").first
                if new_btn.is_visible():
                    new_btn.click()
                    page.wait_for_timeout(1000)

            # Count existing assistant messages BEFORE sending
            pre_count = len(page.locator(".message.message-assistant").all())

            start_time = time.time()

            chat_input = page.locator("textarea#chat-input").first
            chat_input.click()
            chat_input.fill(question)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")

            # Wait for a NEW assistant message to appear (count > pre_count)
            page.wait_for_timeout(3000)
            prev_text = ""
            stable_count = 0
            for _ in range(MAX_WAIT):
                try:
                    msgs = page.locator(".message.message-assistant").all()
                    if len(msgs) <= pre_count:
                        # New message hasn't appeared yet
                        page.wait_for_timeout(1000)
                        continue
                    cur_text = msgs[-1].inner_text() if msgs else ""
                    if cur_text and "\n" in cur_text:
                        cur_text = cur_text.split("\n", 1)[1].strip()
                except Exception:
                    cur_text = ""
                if cur_text and len(cur_text) > 10 and cur_text == prev_text:
                    stable_count += 1
                    if stable_count >= 3:
                        break
                else:
                    stable_count = 0
                prev_text = cur_text
                page.wait_for_timeout(1000)

            elapsed = time.time() - start_time
            answer = prev_text or ""
            used_sql = extract_sql(answer)
            answer_len = len(answer)

            # Check if SQL was generated (key metric for retest)
            has_sql = bool(used_sql)
            has_data = "조회하지 못했습니다" not in answer and "조회 안내" not in answer

            if elapsed >= 90:
                status = "FAIL"
            elif answer_len < 20:
                status = "EMPTY"
            elif not has_data:
                status = "NO_DATA"
            elif elapsed >= 60:
                status = "WARN"
            else:
                status = "OK"

            icon = {"OK": "+", "WARN": "!", "FAIL": "X", "EMPTY": "0", "NO_DATA": "N"}.get(status, "?")
            sql_mark = "SQL" if has_sql else "---"
            print(f" [{icon}] {elapsed:.1f}s len={answer_len:4d} [{sql_mark}]")

            results.append({
                "id": f"RT-{qnum:03d}",
                "query": question,
                "answer_preview": answer[:500].replace("\n", " "),
                "used_sql": used_sql,
                "has_sql": has_sql,
                "has_data": has_data,
                "status": status,
                "time": round(elapsed, 1),
                "answer_len": answer_len,
            })

            RESULTS_FILE.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            page.wait_for_timeout(500)

        browser.close()

    # Summary
    print("\n" + "=" * 70)
    print("  RETEST SUMMARY")
    print("=" * 70)
    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    no_data = sum(1 for r in results if r["status"] == "NO_DATA")
    fail = sum(1 for r in results if r["status"] in ("FAIL", "EMPTY"))
    sql_gen = sum(1 for r in results if r["has_sql"])
    data_ok = sum(1 for r in results if r["has_data"])
    avg_t = sum(r["time"] for r in results) / len(results) if results else 0

    print(f"  OK={ok} WARN={warn} NO_DATA={no_data} FAIL={fail}")
    print(f"  SQL Generated: {sql_gen}/{len(results)}")
    print(f"  Data Returned: {data_ok}/{len(results)}")
    print(f"  avg={avg_t:.1f}s")

    # Show NO_DATA items
    nd = [r for r in results if r["status"] == "NO_DATA"]
    if nd:
        print(f"\n  Still failing ({len(nd)}):")
        for r in nd:
            print(f"    {r['id']}: {r['query'][:60]}")

    print("=" * 70)


if __name__ == "__main__":
    print("=" * 70)
    print(f"  PLAYWRIGHT RETEST — {len(QUESTIONS)} questions (jp2 fixes)")
    print("=" * 70)
    run_retest()
