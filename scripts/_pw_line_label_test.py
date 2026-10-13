"""Playwright: 월별 추이 질문 → line 차트 데이터 라벨 표시 검증"""
import asyncio, sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "2025년 월별 매출 추이 보여줘"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

        # 로그인
        await page.goto(f"{URL}/login", wait_until="networkidle", timeout=15000)
        await page.fill("#input-name", "임재필")
        await page.wait_for_selector(".ac-item", timeout=5000)
        await page.click(".ac-item:first-child")
        await page.wait_for_timeout(500)
        await page.fill("#input-password", "1234")
        await page.click("#btn-submit")
        await page.wait_for_timeout(3000)
        if "login" in page.url:
            body = await page.inner_text("body")
            print("로그인 실패:", body[:300])
            await page.screenshot(path="docs/_line_label_login_fail.png")
            await browser.close()
            return

        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        # 질문 전송
        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(QUESTION)
        await page.keyboard.press("Enter")
        print("질문 전송. 응답 대기 중...")

        try:
            await page.wait_for_selector(".chart-container canvas", timeout=300000)
            print("차트 canvas 감지됨")
        except Exception as e:
            print("차트 대기 timeout:", e)
        await page.wait_for_timeout(5000)

        # 차트 canvas 확인 + 차트 타입 확인
        info = await page.evaluate("""() => {
            const canvases = document.querySelectorAll('.chart-container canvas');
            if (!canvases.length) return { found: false };
            const c = canvases[canvases.length - 1];
            // Chart.js 인스턴스에서 타입/플러그인 확인
            const chart = window.Chart ? window.Chart.getChart(c) : null;
            return {
                found: true,
                type: chart ? chart.config.type : null,
                inlinePlugins: chart ? (chart.config.plugins || []).map(p => p.id) : []
            };
        }""")
        print("차트 정보:", info)

        # 차트 영역만 스크린샷
        chart_el = await page.query_selector(".chart-container")
        if chart_el:
            await chart_el.screenshot(path="docs/_line_label_chart.png")
            print("차트 스크린샷: docs/_line_label_chart.png")
        await page.screenshot(path="docs/_line_label_full.png", full_page=False)
        await browser.close()

asyncio.run(run())
