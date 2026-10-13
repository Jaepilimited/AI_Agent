"""Playwright로 Q1 차트 렌더링 확인"""
import sys, os, time
from pathlib import Path
from playwright.sync_api import sync_playwright

html_path = Path(__file__).parent / "_q1_chart.html"
url = html_path.as_uri()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.goto(url)
    page.wait_for_timeout(2000)

    # 스크린샷
    shot_path = str(Path(__file__).parent.parent / "docs" / "_q1_chart_screenshot.png")
    page.screenshot(path=shot_path, full_page=True)
    print(f"스크린샷 저장: {shot_path}")

    # canvas 존재 확인
    canvas = page.query_selector("canvas#chart")
    print("canvas 존재:", canvas is not None)

    time.sleep(4)
    browser.close()
print("완료")
