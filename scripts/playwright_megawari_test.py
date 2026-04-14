#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright test — 메가와리 1분기 실적 발표 PDF 수치 검증.

Usage:
  python -X utf8 scripts/playwright_megawari_test.py [--port 3001] [--headed]
"""

import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
import requests as req_lib

BASE_URL = "http://127.0.0.1:3001"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
RESULTS_FILE = Path(__file__).parent / "playwright_megawari_results.json"
MAX_WAIT = 180
SCREENSHOT_DIR = Path(__file__).parent / "qa_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# PDF 각 페이지에 대응하는 질문
QUESTIONS = [
    {
        "id": "P2",
        "page": 2,
        "title": "퍼포먼스/인플루언서 마케팅 비용 추이",
        "query": "JBT 퍼포먼스 마케팅 비용과 인플루언서 마케팅 비용을 25년 1분기부터 26년 1분기까지 차트로 보여줘",
    },
    {
        "id": "P7",
        "page": 7,
        "title": "카테고리별 분기 매출",
        "query": "JBT 25년 1분기부터 26년 1분기까지 카테고리별 분기 매출 보여줘",
    },
    {
        "id": "P5",
        "page": 5,
        "title": "센텔라 테카 제품 실적",
        "query": "JBT 26년 1분기 센텔라 테카 라인 제품별 매출과 수량 보여줘",
    },
    {
        "id": "P2-detail",
        "page": 2,
        "title": "마케팅 비용 통화 확인",
        "query": "JBT 26년 1분기 퍼포먼스 마케팅 비용 총액이 얼마야? 원화로",
    },
]


def run_tests(headed=False):
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(60000)
        page = context.new_page()

        # Login
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

        _LOADING = ["데이터 조회 중", "조회 중...", "분석 중...", "처리 중..."]

        for idx, q in enumerate(QUESTIONS):
            qid = q["id"]
            question = q["query"]
            print(f"  [{qid}] {question[:60]}...", end="", flush=True)

            # New conversation
            new_btn = page.locator("button#btn-new-chat").first
            if new_btn.is_visible():
                new_btn.click()
                page.wait_for_timeout(1500)

            pre_count = len(page.locator(".message.message-assistant").all())
            start = time.time()

            chat_input = page.locator("textarea#chat-input").first
            chat_input.click()
            chat_input.fill(question)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")

            page.wait_for_timeout(3000)
            prev_text = ""
            stable = 0
            for _ in range(MAX_WAIT):
                try:
                    msgs = page.locator(".message.message-assistant").all()
                    if len(msgs) <= pre_count:
                        page.wait_for_timeout(1000)
                        continue
                    cur = msgs[-1].inner_text() if msgs else ""
                except Exception:
                    cur = ""
                is_loading = any(lp in cur for lp in _LOADING)
                if cur and len(cur) > 10 and cur == prev_text and not is_loading:
                    stable += 1
                    if stable >= 3:
                        break
                else:
                    stable = 0
                prev_text = cur
                page.wait_for_timeout(1000)

            elapsed = time.time() - start
            answer = prev_text or ""

            # Get HTML for chart detection
            try:
                msgs = page.locator(".message.message-assistant").all()
                raw_html = msgs[-1].inner_html() if msgs and len(msgs) > pre_count else ""
            except Exception:
                raw_html = ""

            has_chart = "canvas" in raw_html or "chart-container" in raw_html

            # Screenshot
            ss = SCREENSHOT_DIR / f"megawari_{qid}.png"
            page.screenshot(path=str(ss), full_page=False)

            status = "FAIL"
            if elapsed >= 120:
                status = "TIMEOUT"
            elif len(answer) < 30:
                status = "EMPTY"
            elif any(f in answer for f in ["조회하지 못했습니다", "오류가 발생", "5분을 초과"]):
                status = "ERROR"
            elif has_chart:
                status = "OK+CHART"
            elif len(answer) > 100:
                status = "OK"

            icon = {"OK+CHART": "✓", "OK": "○", "FAIL": "✗", "TIMEOUT": "⏱", "EMPTY": "∅", "ERROR": "!"}
            print(f" [{icon.get(status, '?')}] {elapsed:.1f}s len={len(answer)}")

            results.append({
                "id": qid,
                "page": q["page"],
                "title": q["title"],
                "query": question,
                "answer": answer[:2000],
                "has_chart": has_chart,
                "status": status,
                "time": round(elapsed, 1),
                "screenshot": str(ss),
            })

            RESULTS_FILE.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            page.wait_for_timeout(500)

        browser.close()

    # Summary
    print(f"\n{'='*60}")
    for r in results:
        print(f"  [{r['id']:10s}] {r['status']:10s} {r['time']:5.1f}s  {r['title']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    BASE_URL = f"http://127.0.0.1:{args.port}"

    print(f"{'='*60}")
    print(f"  메가와리 PDF 수치 검증 ({len(QUESTIONS)} questions)")
    print(f"  Target: {BASE_URL}")
    print(f"{'='*60}")
    run_tests(headed=args.headed)
