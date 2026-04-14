#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Quick test for quarter formatting fix."""
import json, re, time
from pathlib import Path
from playwright.sync_api import sync_playwright
import requests as req_lib

BASE_URL = "http://127.0.0.1:3001"
RESULTS_FILE = Path(__file__).parent / "playwright_quarter_results.json"
SS_DIR = Path(__file__).parent / "qa_screenshots"
SS_DIR.mkdir(exist_ok=True)

QUESTIONS = [
    ("P2", "JBT 퍼포먼스 마케팅 비용과 인플루언서 마케팅 비용을 25년 1분기부터 26년 1분기까지 분기별로 차트로 보여줘"),
    ("P7", "JBT 25년 1분기부터 26년 1분기까지 분기별 카테고리별 매출 표로 보여줘"),
]

def run():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.set_default_timeout(60000)
        page = ctx.new_page()
        r = req_lib.post(f"{BASE_URL}/api/auth/signin", json={
            "department": "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석",
            "name": "임재필", "password": "1234"})
        ctx.add_cookies([{"name": "token", "value": r.cookies.get("token"), "domain": "127.0.0.1", "path": "/"}])
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        print("  Login OK\n")

        _LOADING = ["데이터 조회 중", "조회 중...", "분석 중...", "처리 중..."]
        for qid, q in QUESTIONS:
            print(f"  [{qid}] {q[:60]}...", end="", flush=True)
            new_btn = page.locator("button#btn-new-chat").first
            if new_btn.is_visible(): new_btn.click(); page.wait_for_timeout(1500)
            pre = len(page.locator(".message.message-assistant").all())
            t0 = time.time()
            inp = page.locator("textarea#chat-input").first
            inp.click(); inp.fill(q); page.wait_for_timeout(300); page.keyboard.press("Enter")
            page.wait_for_timeout(3000)
            prev, stable = "", 0
            for _ in range(180):
                try:
                    msgs = page.locator(".message.message-assistant").all()
                    cur = msgs[-1].inner_text() if len(msgs) > pre else ""
                except: cur = ""
                if cur and len(cur) > 10 and cur == prev and not any(lp in cur for lp in _LOADING):
                    stable += 1
                    if stable >= 3: break
                else: stable = 0
                prev = cur; page.wait_for_timeout(1000)
            elapsed = time.time() - t0
            # Get raw HTML
            try:
                msgs = page.locator(".message.message-assistant").all()
                html = msgs[-1].inner_html() if len(msgs) > pre else ""
            except: html = ""
            has_chart = "canvas" in html
            has_q1 = "2025-Q1" in prev or "Q1" in prev
            page.screenshot(path=str(SS_DIR / f"quarter_{qid}.png"), full_page=False)
            icon = "✓" if has_q1 else "✗"
            print(f" [{icon}] {elapsed:.1f}s chart={has_chart} Q1-found={has_q1}")
            results.append({"id": qid, "answer": prev[:1500], "has_q1": has_q1, "has_chart": has_chart, "time": round(elapsed, 1)})
            RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            page.wait_for_timeout(500)
        browser.close()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3001)
    args = ap.parse_args()
    BASE_URL = f"http://127.0.0.1:{args.port}"
    print(f"  Quarter format test → {BASE_URL}")
    run()
