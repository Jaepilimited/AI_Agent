#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright single question test.

Usage:
  python -X utf8 scripts/playwright_single_test.py [--port 3001] [--headed]
"""

import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
import requests as req_lib

BASE_URL = "http://127.0.0.1:3001"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
RESULTS_FILE = Path(__file__).parent / "playwright_single_test_result.json"
MAX_WAIT = 180  # seconds
SCREENSHOT_DIR = Path(__file__).parent / "qa_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

QUESTION = "@@BP 센텔라 앰플 주요 성분 알려줘"


def extract_sql(answer: str) -> str:
    m = re.search(r'<details>.*?```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def has_chart(answer: str) -> bool:
    return "```chart-config" in answer or "chart-container" in answer


def run_test(headed: bool = False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
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

        # Count existing assistant messages BEFORE sending
        pre_count = len(page.locator(".message.message-assistant").all())

        print(f"  Q: {QUESTION}")
        start_time = time.time()

        chat_input = page.locator("textarea#chat-input").first
        chat_input.click()
        chat_input.fill(QUESTION)
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")

        # Wait for assistant response to stabilize
        _LOADING_PATTERNS = ["데이터 조회 중", "조회 중...", "분석 중...", "처리 중..."]
        page.wait_for_timeout(3000)
        prev_text = ""
        stable_count = 0
        for _ in range(MAX_WAIT):
            try:
                msgs = page.locator(".message.message-assistant").all()
                if len(msgs) <= pre_count:
                    page.wait_for_timeout(1000)
                    continue
                cur_text = msgs[-1].inner_text() if msgs else ""
            except Exception:
                cur_text = ""
            is_loading = any(lp in cur_text for lp in _LOADING_PATTERNS)
            if cur_text and len(cur_text) > 10 and cur_text == prev_text and not is_loading:
                stable_count += 1
                if stable_count >= 3:
                    break
            else:
                stable_count = 0
            prev_text = cur_text
            page.wait_for_timeout(1000)

        elapsed = time.time() - start_time
        answer = prev_text or ""

        # Get raw HTML for chart detection
        try:
            msgs = page.locator(".message.message-assistant").all()
            raw_html = msgs[-1].inner_html() if msgs and len(msgs) > pre_count else ""
        except Exception:
            raw_html = ""

        used_sql = extract_sql(raw_html)
        chart_found = has_chart(raw_html) or "canvas" in raw_html or "<canvas" in raw_html

        # Check for visible chart canvas
        chart_visible = False
        try:
            chart_canvas = page.locator(".message.message-assistant canvas").last
            if chart_canvas.is_visible():
                chart_visible = True
        except Exception:
            pass

        # Take screenshot
        ss_path = SCREENSHOT_DIR / "single_test_result.png"
        page.screenshot(path=str(ss_path), full_page=False)
        print(f"\n  Screenshot: {ss_path}")

        # Determine status
        fail_indicators = [
            "조회하지 못했습니다", "오류가 발생", "데이터를 찾을 수 없",
            "분석이 예상보다 오래", "서비스가 일시적으로 불안정",
        ]
        if elapsed >= 90:
            status = "FAIL (TIMEOUT)"
        elif len(answer) < 20:
            status = "FAIL (EMPTY)"
        elif any(fi in answer for fi in fail_indicators):
            status = "FAIL (ERROR)"
        elif chart_found or chart_visible:
            status = "OK (CHART)"
        elif len(answer) > 100:
            status = "WARN (NO CHART)"
        else:
            status = "FAIL"

        print(f"\n{'=' * 70}")
        print(f"  STATUS : {status}")
        print(f"  TIME   : {elapsed:.1f}s")
        print(f"  LENGTH : {len(answer)} chars")
        print(f"  SQL    : {'Yes' if used_sql else 'No'}")
        print(f"  CHART  : {'Yes (canvas visible)' if chart_visible else 'Yes (in HTML)' if chart_found else 'No'}")
        print(f"{'=' * 70}")
        print(f"\n  Answer preview (first 800 chars):")
        print(f"  {answer[:800]}")
        print(f"{'=' * 70}")

        result = {
            "question": QUESTION,
            "answer_preview": answer[:1000].replace("\n", " "),
            "answer_full_length": len(answer),
            "raw_html_preview": raw_html[:2000],
            "used_sql": used_sql,
            "has_chart_html": chart_found,
            "has_chart_canvas": chart_visible,
            "status": status,
            "time": round(elapsed, 1),
            "screenshot": str(ss_path),
        }
        RESULTS_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  Results saved: {RESULTS_FILE}")

        browser.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    BASE_URL = f"http://127.0.0.1:{args.port}"

    print("=" * 70)
    print(f"  PLAYWRIGHT SINGLE QUESTION TEST")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)
    run_test(headed=args.headed)
