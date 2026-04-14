#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright @@ datasource selection test.

Tests:
1. @@ autocomplete dropdown appears
2. @@매출 single source query works
3. @@매출 @@CS multi source query works

Usage:
  python -X utf8 scripts/playwright_at_test.py [--headed]
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
import requests as req_lib

BASE_URL = "http://127.0.0.1:3001"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
SCREENSHOT_DIR = Path(__file__).parent / "qa_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
MAX_WAIT = 120


def run_test(headed: bool = False):
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(60000)
        page = context.new_page()

        # Collect console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type in ("error",) else None)

        # Login
        print("=" * 60)
        print("@@ Datasource Selection Test")
        print("=" * 60)
        print("\n[1/4] Logging in...")
        login_resp = req_lib.post(f"{BASE_URL}/api/auth/signin", json={
            "department": LOGIN_DEPT, "name": LOGIN_NAME, "password": LOGIN_PW,
        })
        if login_resp.status_code != 200:
            print(f"  FAIL: Login failed: {login_resp.text}")
            return
        token = login_resp.cookies.get("token")
        context.add_cookies([{"name": "token", "value": token, "domain": "127.0.0.1", "path": "/"}])
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        print("  OK: Logged in")

        # Check for JS errors on load
        if console_errors:
            print(f"\n  ⚠️  Console errors on load:")
            for e in console_errors:
                print(f"    {e}")
        page.screenshot(path=str(SCREENSHOT_DIR / "at_test_loaded.png"), full_page=False)

        # Test 1: @@ autocomplete dropdown
        print("\n[2/4] Testing @@ autocomplete dropdown...")
        chat_input = page.locator("textarea#chat-input").first
        chat_input.click()
        chat_input.fill("@@")
        page.wait_for_timeout(1000)

        # Check if dropdown appeared
        dropdown = page.locator(".db-autocomplete-dropdown")
        dropdown_visible = dropdown.is_visible() if dropdown.count() > 0 else False
        page.screenshot(path=str(SCREENSHOT_DIR / "at_test_dropdown.png"), full_page=False)

        if dropdown_visible:
            items = page.locator(".db-autocomplete-dropdown .db-ac-item, .db-autocomplete-dropdown .db-group-item")
            count = items.count()
            print(f"  OK: Dropdown visible with {count} items")
            results.append({"test": "autocomplete_dropdown", "status": "PASS", "items": count})
        else:
            print(f"  FAIL: Dropdown NOT visible")
            results.append({"test": "autocomplete_dropdown", "status": "FAIL"})

        # Clear input
        chat_input.fill("")
        page.wait_for_timeout(500)

        # Test 2: @@매출 single source query
        print("\n[3/4] Testing @@매출 single source query...")
        console_errors.clear()
        pre_count = len(page.locator(".message.message-assistant").all())

        question = "@@매출 이번달 총 매출 알려줘"
        chat_input.click()
        chat_input.fill(question)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SCREENSHOT_DIR / "at_test_before_send.png"), full_page=False)
        page.keyboard.press("Enter")

        print(f"  Sent: {question}")
        print(f"  Waiting for response (max {MAX_WAIT}s)...")

        # Wait for new assistant message
        page.wait_for_timeout(3000)
        start = time.time()
        prev_text = ""
        stable_count = 0
        answer = ""
        while time.time() - start < MAX_WAIT:
            msgs = page.locator(".message.message-assistant").all()
            if len(msgs) > pre_count:
                last_msg = msgs[-1]
                cur_text = last_msg.inner_text()
                if cur_text == prev_text and len(cur_text) > 10:
                    stable_count += 1
                    if stable_count >= 3:
                        answer = cur_text
                        break
                else:
                    stable_count = 0
                prev_text = cur_text
            page.wait_for_timeout(2000)

        elapsed = time.time() - start
        page.screenshot(path=str(SCREENSHOT_DIR / "at_test_single_result.png"), full_page=False)

        if answer:
            is_error = "오류" in answer or "error" in answer.lower() or "검색 결과가 없습니다" in answer
            status = "WARN" if is_error else "PASS"
            print(f"  {status}: Got response in {elapsed:.1f}s ({len(answer)} chars)")
            print(f"  Preview: {answer[:200]}...")
            results.append({"test": "single_source_query", "status": status, "elapsed": round(elapsed, 1), "chars": len(answer)})
        else:
            print(f"  FAIL: No response after {MAX_WAIT}s")
            results.append({"test": "single_source_query", "status": "FAIL", "elapsed": MAX_WAIT})

        if console_errors:
            print(f"  Console errors:")
            for e in console_errors[:5]:
                print(f"    {e}")

        # Test 3: New conversation + @@매출 @@CS multi source
        print("\n[4/4] Testing multi source @@매출 @@CS...")
        console_errors.clear()

        # Click new conversation button
        new_btn = page.locator("#new-chat-btn, .new-chat-btn, button:has-text('새 대화')").first
        if new_btn.count() > 0:
            new_btn.click()
            page.wait_for_timeout(2000)

        pre_count2 = len(page.locator(".message.message-assistant").all())
        question2 = "@@매출 @@CS 센텔라 앰플 관련 정보"
        chat_input = page.locator("textarea#chat-input").first
        chat_input.click()
        chat_input.fill(question2)
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")

        print(f"  Sent: {question2}")
        print(f"  Waiting for response (max {MAX_WAIT}s)...")

        page.wait_for_timeout(3000)
        start2 = time.time()
        prev_text2 = ""
        stable_count2 = 0
        answer2 = ""
        while time.time() - start2 < MAX_WAIT:
            msgs2 = page.locator(".message.message-assistant").all()
            if len(msgs2) > pre_count2:
                last_msg2 = msgs2[-1]
                cur_text2 = last_msg2.inner_text()
                if cur_text2 == prev_text2 and len(cur_text2) > 10:
                    stable_count2 += 1
                    if stable_count2 >= 3:
                        answer2 = cur_text2
                        break
                else:
                    stable_count2 = 0
                prev_text2 = cur_text2
            page.wait_for_timeout(2000)

        elapsed2 = time.time() - start2
        page.screenshot(path=str(SCREENSHOT_DIR / "at_test_multi_result.png"), full_page=False)

        if answer2:
            is_error2 = "오류" in answer2 or "error" in answer2.lower()
            status2 = "WARN" if is_error2 else "PASS"
            print(f"  {status2}: Got response in {elapsed2:.1f}s ({len(answer2)} chars)")
            print(f"  Preview: {answer2[:200]}...")
            results.append({"test": "multi_source_query", "status": status2, "elapsed": round(elapsed2, 1), "chars": len(answer2)})
        else:
            print(f"  FAIL: No response after {MAX_WAIT}s")
            results.append({"test": "multi_source_query", "status": "FAIL", "elapsed": MAX_WAIT})

        if console_errors:
            print(f"  Console errors:")
            for e in console_errors[:5]:
                print(f"    {e}")

        browser.close()

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    for r in results:
        icon = "✅" if r["status"] == "PASS" else "⚠️" if r["status"] == "WARN" else "❌"
        print(f"  {icon} {r['test']}: {r['status']}")

    out_path = Path(__file__).parent / "playwright_at_test_result.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    headed = "--headed" in sys.argv
    run_test(headed=headed)
