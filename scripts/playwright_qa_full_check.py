#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright full QA check — tests all major features one by one.

Usage: python -X utf8 scripts/playwright_qa_full_check.py [--port 3000] [--headed]
"""

import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
import requests as req_lib

PORT = 3000
for a in sys.argv:
    if a.startswith("--port"):
        idx = sys.argv.index(a)
        if idx + 1 < len(sys.argv):
            PORT = int(sys.argv[idx + 1])

BASE = f"http://127.0.0.1:{PORT}"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
SS = Path(__file__).parent / "qa_screenshots"
SS.mkdir(exist_ok=True)
MAX_WAIT = 90
RESULTS = []


def wait_response(pg, pre_count, max_wait=MAX_WAIT):
    """Wait for assistant response to stabilize."""
    pg.wait_for_timeout(3000)
    start = time.time()
    prev, stable = "", 0
    while time.time() - start < max_wait:
        msgs = pg.locator(".message.message-assistant").all()
        if len(msgs) > pre_count:
            cur = msgs[-1].inner_text()
            if cur == prev and len(cur) > 10:
                stable += 1
                if stable >= 3:
                    return cur, time.time() - start
            else:
                stable = 0
            prev = cur
        pg.wait_for_timeout(2000)
    return None, max_wait


def send_msg(pg, text):
    """Send a message via button click."""
    inp = pg.locator("textarea#chat-input").first
    inp.click()
    inp.fill(text)
    pg.wait_for_timeout(500)
    pg.locator("#btn-send").click(force=True)


def new_chat(pg):
    """Click new chat button."""
    pg.locator("#btn-new-chat").click()
    pg.wait_for_timeout(2000)


def log_result(name, status, detail="", elapsed=0):
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(status, "?")
    RESULTS.append({"test": name, "status": status, "detail": detail, "elapsed": round(elapsed, 1)})
    e = f" ({elapsed:.0f}s)" if elapsed else ""
    d = f" — {detail}" if detail else ""
    print(f"  {icon} {name}: {status}{e}{d}")


def run():
    with sync_playwright() as p:
        headed = "--headed" in sys.argv
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.set_default_timeout(30000)
        page = ctx.new_page()

        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        print("=" * 60)
        print(f"SKIN1004 AI — Full QA Check (port {PORT})")
        print("=" * 60)

        # ─── 1. Login ───
        print("\n[1/12] Login")
        try:
            resp = req_lib.post(f"{BASE}/api/auth/signin", json={
                "department": LOGIN_DEPT, "name": LOGIN_NAME, "password": LOGIN_PW
            }, timeout=10)
            if resp.status_code == 200:
                token = resp.cookies.get("token")
                ctx.add_cookies([{"name": "token", "value": token, "domain": "127.0.0.1", "path": "/"}])
                page.goto(BASE, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                welcome = page.locator("#welcome-user-name").inner_text() if page.locator("#welcome-user-name").count() > 0 else ""
                log_result("Login", "PASS", f"user={welcome}")
            else:
                log_result("Login", "FAIL", f"HTTP {resp.status_code}")
                browser.close()
                return
        except Exception as e:
            log_result("Login", "FAIL", str(e))
            browser.close()
            return

        page.screenshot(path=str(SS / "qa_01_loaded.png"))

        # ─── 2. Page load — no JS errors ───
        print("\n[2/12] Page load errors")
        init_errs = [e for e in console_errors if "favicon" not in e.lower()]
        if not init_errs:
            log_result("Page load", "PASS", "0 JS errors")
        else:
            log_result("Page load", "WARN", f"{len(init_errs)} errors: {init_errs[0][:80]}")
        console_errors.clear()

        # ─── 3. Sidebar elements ───
        print("\n[3/12] Sidebar elements")
        checks = {
            "Logo": "#sidebar .sidebar-header",
            "Search": "#convo-search",
            "New chat btn": "#btn-new-chat",
            "Dashboard btn": "#dashboard-btn, .dashboard-btn, button:has-text('Dashboard')",
            "System Status": "#system-status-btn, .system-status-btn, button:has-text('System Status')",
            "User name": "#user-name",
        }
        missing = []
        for name, sel in checks.items():
            if page.locator(sel).count() == 0:
                missing.append(name)
        if not missing:
            log_result("Sidebar elements", "PASS", f"all {len(checks)} found")
        else:
            log_result("Sidebar elements", "FAIL", f"missing: {', '.join(missing)}")
        page.screenshot(path=str(SS / "qa_03_sidebar.png"))

        # ─── 4. Model selector ───
        print("\n[4/12] Model selector")
        model_sel = page.locator("#model-select, select.model-select")
        if model_sel.count() > 0:
            options = model_sel.locator("option").all()
            opt_texts = [o.inner_text() for o in options]
            log_result("Model selector", "PASS", f"{len(options)} models: {', '.join(opt_texts)}")
        else:
            log_result("Model selector", "FAIL", "selector not found")

        # ─── 5. Plain query (direct route) ───
        print("\n[5/12] Plain query")
        console_errors.clear()
        pre = len(page.locator(".message.message-assistant").all())
        send_msg(page, "SKIN1004 본사 위치가 어디야?")
        ans, t = wait_response(page, pre, 60)
        if ans:
            has_addr = "강남" in ans or "테헤란" in ans or "서울" in ans
            log_result("Plain query", "PASS" if has_addr else "WARN",
                       f"{len(ans)} chars, addr={'Y' if has_addr else 'N'}", t)
        else:
            log_result("Plain query", "FAIL", "no response", MAX_WAIT)
        page.screenshot(path=str(SS / "qa_05_plain.png"))
        if console_errors:
            log_result("Plain query JS errors", "WARN", console_errors[0][:80])

        # ─── 6. @@매출 (BQ route) ───
        print("\n[6/12] @@매출 query")
        console_errors.clear()
        new_chat(page)
        pre2 = len(page.locator(".message.message-assistant").all())
        send_msg(page, "@@매출 이번달 총 매출")
        ans2, t2 = wait_response(page, pre2)
        if ans2:
            has_data = "매출" in ans2 or "원" in ans2 or "₩" in ans2
            log_result("@@매출 query", "PASS" if has_data else "WARN",
                       f"{len(ans2)} chars, data={'Y' if has_data else 'N'}", t2)
        else:
            log_result("@@매출 query", "FAIL", "no response", MAX_WAIT)
        page.screenshot(path=str(SS / "qa_06_bq.png"))
        if console_errors:
            log_result("@@매출 JS errors", "WARN", console_errors[0][:80])

        # ─── 7. @@CS (CS route) ───
        print("\n[7/12] @@CS query")
        console_errors.clear()
        new_chat(page)
        pre3 = len(page.locator(".message.message-assistant").all())
        send_msg(page, "@@CS 센텔라 앰플 사용법")
        ans3, t3 = wait_response(page, pre3)
        if ans3:
            log_result("@@CS query", "PASS", f"{len(ans3)} chars", t3)
        else:
            log_result("@@CS query", "FAIL", "no response", MAX_WAIT)
        page.screenshot(path=str(SS / "qa_07_cs.png"))

        # ─── 8. Follow-up chips ───
        print("\n[8/12] Follow-up chips")
        chips = page.locator(".followup-chip, .followup-suggestions button")
        chip_count = chips.count()
        if chip_count > 0:
            log_result("Follow-up chips", "PASS", f"{chip_count} chips found")
            # Click first chip
            try:
                pre_chip = len(page.locator(".message.message-assistant").all())
                chips.first.click()
                page.wait_for_timeout(5000)
                post_chip = len(page.locator(".message.message-assistant").all())
                if post_chip > pre_chip:
                    log_result("Chip click sends msg", "PASS")
                else:
                    log_result("Chip click sends msg", "WARN", "no new response after 5s")
            except:
                log_result("Chip click sends msg", "WARN", "click failed")
        else:
            log_result("Follow-up chips", "WARN", "no chips found")

        # ─── 9. Conversation switching ───
        print("\n[9/12] Conversation switching")
        convos = page.locator("#convo-list .convo-item, #convo-list li")
        convo_count = convos.count()
        if convo_count > 1:
            convos.nth(1).click()
            page.wait_for_timeout(3000)
            msgs = page.locator(".message").all()
            log_result("Conversation switch", "PASS" if len(msgs) > 0 else "WARN",
                       f"{convo_count} convos, {len(msgs)} msgs loaded")
        else:
            log_result("Conversation switch", "WARN", f"only {convo_count} conversations")
        page.screenshot(path=str(SS / "qa_09_convo_switch.png"))

        # ─── 10. Theme toggle ───
        print("\n[10/12] Theme toggle")
        theme_btn = page.locator("#skin-theme-toggle")
        if theme_btn.count() > 0:
            before_class = page.locator("html").get_attribute("class")
            theme_btn.click()
            page.wait_for_timeout(500)
            after_class = page.locator("html").get_attribute("class")
            changed = before_class != after_class
            log_result("Theme toggle", "PASS" if changed else "FAIL",
                       f"{before_class} → {after_class}")
            # Toggle back
            theme_btn.click()
            page.wait_for_timeout(300)
        else:
            log_result("Theme toggle", "FAIL", "button not found")
        page.screenshot(path=str(SS / "qa_10_theme.png"))

        # ─── 11. Sidebar collapse ───
        print("\n[11/12] Sidebar collapse")
        sidebar = page.locator("#sidebar")
        collapse_btn = page.locator("#btn-menu, .btn-menu, .sidebar-toggle")
        if collapse_btn.count() > 0:
            collapse_btn.click()
            page.wait_for_timeout(500)
            is_collapsed = "collapsed" in (sidebar.get_attribute("class") or "")
            log_result("Sidebar collapse", "PASS" if is_collapsed else "WARN",
                       f"collapsed={is_collapsed}")
            # Expand back
            collapse_btn.click()
            page.wait_for_timeout(300)
        else:
            log_result("Sidebar collapse", "WARN", "toggle button not found")

        # ─── 12. Admin panel ───
        print("\n[12/12] Admin panel")
        admin_btn = page.locator("#admin-btn, .admin-btn, button:has-text('Admin')")
        if admin_btn.count() > 0:
            admin_btn.click()
            page.wait_for_timeout(1500)
            admin_drawer = page.locator("#skin-admin-drawer, .admin-drawer")
            is_open = admin_drawer.count() > 0 and admin_drawer.is_visible()
            log_result("Admin panel", "PASS" if is_open else "WARN",
                       f"drawer visible={is_open}")
            page.screenshot(path=str(SS / "qa_12_admin.png"))
            # Close
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        else:
            log_result("Admin panel", "WARN", "admin button not found")

        browser.close()

    # Summary
    print("\n" + "=" * 60)
    print("QA RESULTS SUMMARY")
    print("=" * 60)
    pass_count = sum(1 for r in RESULTS if r["status"] == "PASS")
    fail_count = sum(1 for r in RESULTS if r["status"] == "FAIL")
    warn_count = sum(1 for r in RESULTS if r["status"] == "WARN")
    total = len(RESULTS)
    print(f"\n  PASS: {pass_count}/{total}  |  FAIL: {fail_count}  |  WARN: {warn_count}")
    print()
    for r in RESULTS:
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(r["status"], "?")
        print(f"  {icon} {r['test']}: {r['status']}")

    score = round(pass_count / total * 100) if total else 0
    print(f"\n  Health Score: {score}/100")

    out = Path(__file__).parent / "playwright_qa_full_result.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Results: {out}")


if __name__ == "__main__":
    run()
