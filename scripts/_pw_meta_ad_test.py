"""Playwright: 메타 광고 소재수 + 매출 상관관계 질문 테스트 (라우팅 버그 수정 검증)"""
import asyncio
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
QUESTION = "26년 메타 광고 소재수와 매출의 상관관계를 월별로 보여줘"


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

        console_errors = []
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)

        # 1. 로그인
        await page.goto(f"{URL}/login", wait_until="networkidle", timeout=15000)
        await page.fill("#input-name", "임재필")
        await page.wait_for_selector(".ac-item", timeout=5000)
        await page.click(".ac-item:first-child")
        await page.wait_for_timeout(500)
        await page.fill("#input-password", "1234")
        await page.click("#btn-submit")
        await page.wait_for_timeout(3000)

        if "login" in page.url:
            print("❌ 로그인 실패")
            await browser.close()
            return

        print(f"✅ 로그인 성공: {page.url}")

        # 2. 채팅 준비 대기
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        # 3. 질문 입력 & 전송
        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(QUESTION)
        await page.screenshot(path="docs/_meta_test_01_typed.png")
        print(f"✉️  전송: {QUESTION}")

        import time
        t_start = time.time()
        await page.keyboard.press("Enter")

        # 4. 응답 완료 대기 (최대 120초)
        try:
            await page.wait_for_function(
                "() => { const btn = document.querySelector('#btn-send'); return btn && !btn.disabled; }",
                timeout=120000
            )
            elapsed = time.time() - t_start
            print(f"⏱️  응답 완료: {elapsed:.1f}초")
        except Exception as e:
            elapsed = time.time() - t_start
            print(f"⚠️  응답 대기 timeout ({elapsed:.1f}s): {e}")

        await page.wait_for_timeout(1500)
        await page.screenshot(path="docs/_meta_test_02_response.png")

        # 5. 응답 텍스트 추출
        msgs = await page.eval_on_selector_all(
            ".message-content, .assistant-message, [class*='assistant'] .message-content",
            "els => els.map(e => e.innerText.trim())"
        )

        # fallback: 마지막 메시지 전체
        if not msgs:
            msgs = await page.eval_on_selector_all(
                ".message",
                "els => els.map(e => ({cls: e.className, txt: e.innerText.trim().slice(0, 500)}))"
            )
            print("\n=== 메시지 목록 (fallback) ===")
            for m in msgs:
                print(f"[{m.get('cls','')}] {m.get('txt','')}")
        else:
            print("\n=== 어시스턴트 응답 ===")
            for m in msgs:
                print(m[:600])
                print("---")

        # 6. 타임아웃 에러 메시지 포함 여부 확인
        full_text = await page.inner_text("body")
        if "분析이 예상보다 오래" in full_text or "조회 범위를 좁혀" in full_text:
            print("\n❌ 타임아웃 에러 메시지 감지 — 라우팅 버그 미수정")
        elif "상관" in full_text and ("소재" in full_text or "creative" in full_text.lower() or "SELECT" in full_text):
            print("\n✅ 정상 응답 수신 — 라우팅 버그 수정 확인됨")
        else:
            print("\n⚠️  응답 내용 확인 필요 — 스크린샷 참조")

        # 7. SQL 블록 추출
        sql_texts = await page.eval_on_selector_all(
            "pre code, code",
            "els => els.map(e => e.innerText.trim()).filter(t => t.toUpperCase().includes('SELECT'))"
        )
        if sql_texts:
            print("\n=== 생성된 SQL ===")
            print(sql_texts[-1][:1500])
        else:
            print("\nSQL 블록 없음 (직접 답변 형식)")

        await page.screenshot(path="docs/_meta_test_03_final.png")

        if console_errors:
            print(f"\n콘솔 에러 {len(console_errors)}개: {console_errors[:5]}")

        await browser.close()
        print("\n스크린샷: docs/_meta_test_0*.png")


asyncio.run(run())
