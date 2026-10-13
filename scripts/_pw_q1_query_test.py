"""Playwright: 채팅에서 Q1 국가별 매출 질문 후 SQL 확인"""
import asyncio
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
QUESTION = "2026년 1분기 국가별 매출 비율 보여줘. 국가는 미국,일본,중화권,국내,기타 5개로 나눠줘"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

        # 1. 로그인 페이지
        await page.goto(f"{URL}/login", wait_until="networkidle", timeout=15000)

        # 2. 이름 입력 → autocomplete 클릭
        await page.fill("#input-name", "임재필")
        await page.wait_for_selector(".ac-item", timeout=5000)
        await page.click(".ac-item:first-child")
        await page.wait_for_timeout(500)

        # 3. 부서 드롭다운 확인
        dept_options = await page.eval_on_selector_all(
            "#input-dept option",
            "els => els.map(e => ({value: e.value, text: e.innerText}))"
        )
        print("부서 옵션:", dept_options)
        # 실제 부서 자동 선택됐는지 확인
        selected_dept = await page.eval_on_selector("#input-dept", "el => el.value")
        print("선택된 부서:", selected_dept)

        # 4. 비밀번호 입력
        await page.fill("#input-password", "1234")
        await page.screenshot(path="docs/_pwq_01_before_login.png")

        # 5. 로그인
        await page.click("#btn-submit")
        await page.wait_for_timeout(3000)
        print(f"로그인 후 URL: {page.url}")
        await page.screenshot(path="docs/_pwq_02_after_login.png")

        if "login" in page.url:
            body = await page.inner_text("body")
            print("로그인 실패:", body[:300])
            await browser.close()
            return

        # 6. 채팅 준비 대기
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        # 7. 질문 입력 & 전송
        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(QUESTION)
        await page.screenshot(path="docs/_pwq_03_typed.png")
        await page.keyboard.press("Enter")
        print("질문 전송. 응답 대기 중...")

        # 8. 응답 완료 대기
        try:
            await page.wait_for_function(
                "() => { const btn = document.querySelector('#btn-send'); return btn && !btn.disabled; }",
                timeout=120000
            )
        except Exception as e:
            print("응답 대기 timeout:", e)
        await page.wait_for_timeout(2000)
        await page.screenshot(path="docs/_pwq_04_response.png")

        # 9. details 열고 SQL 추출
        await page.eval_on_selector_all("details", "els => els.forEach(e => e.open = true)")
        await page.wait_for_timeout(500)

        sql_texts = await page.eval_on_selector_all(
            "pre code, code",
            "els => els.map(e => e.innerText.trim()).filter(t => t.toUpperCase().includes('SELECT'))"
        )
        if sql_texts:
            print("\n=== 생성된 SQL ===")
            print(sql_texts[-1][:2000])
        else:
            print("SQL 블록 못 찾음")

        await page.screenshot(path="docs/_pwq_05_final.png")
        print("\n스크린샷 저장: docs/_pwq_04_response.png")
        await page.wait_for_timeout(3000)
        await browser.close()

asyncio.run(run())
