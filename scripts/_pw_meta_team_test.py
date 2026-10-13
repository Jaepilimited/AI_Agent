"""Playwright: 메타광고+매출 팀별 효율 질문 테스트 (프로덕션 3000)"""
import asyncio, time
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3000"
QUESTION = (
    "26년 메타 광고 소재수와 매출의 상관관계를 월별로 보여주고 나온 결과를 권역별로 분리해서 "
    "각 팀별로 어떤 효율을 뽑아냈는지 보여줘. 그리고 가능하면 팀별로 효율 집계한 데이터를 시각화해주셈. "
    "팀을 내가 명시해줄게 해당 국가로 팀을 나눠주면 됨 "
    "EAST1 - 인도네시아 ,필리핀 EAST2 - 말레이시아,싱가포르 "
    "WEST - 미국, 호주 , 영국, 멕시코,폴란드 JBT - 일본, KBT -한국 임. "
    "명시한 팀을 기준으로 팀별로 효율 집계한 데이터를 시각화해줘봐"
)

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

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

        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        # 2. 질문 전송
        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(QUESTION)
        await page.screenshot(path="docs/_meta_team_01_typed.png")
        print("✉️  질문 전송 중...")
        t0 = time.time()
        await page.keyboard.press("Enter")

        # 3. 응답 대기 (최대 180초 — BigQuery 복잡 쿼리)
        try:
            await page.wait_for_function(
                "() => { const b = document.querySelector('#btn-send'); return b && !b.disabled; }",
                timeout=180000
            )
            print(f"⏱️  응답 완료: {time.time()-t0:.1f}초")
        except Exception as e:
            print(f"⚠️  대기 timeout ({time.time()-t0:.1f}s): {e}")

        await page.wait_for_timeout(1500)
        await page.screenshot(path="docs/_meta_team_02_response.png")

        # 4. 에러 여부 확인
        body = await page.inner_text("body")
        if "multi 서비스가 일시적으로 불안정" in body:
            print("❌ circuit breaker 에러 — multi 라우팅 여전히 발생")
        elif "분析이 예상보다 오래" in body or "조회 범위를 좁혀" in body:
            print("❌ 타임아웃 에러")
        else:
            print("✅ 에러 없음 — 정상 응답")

        # 5. 팀별 키워드 포함 여부
        for team in ["EAST1", "EAST2", "WEST", "JBT", "KBT"]:
            found = team in body
            mark = "✅" if found else "❌"
            print(f"  {mark} {team} 포함: {found}")

        # 6. 응답 텍스트 출력
        msgs = await page.eval_on_selector_all(
            ".message-content, .assistant-message",
            "els => els.map(e => e.innerText.trim())"
        )
        if msgs:
            print("\n=== 어시스턴트 응답 (마지막) ===")
            print(msgs[-1][:800])
        else:
            # fallback
            all_msgs = await page.eval_on_selector_all(
                ".message",
                "els => els.slice(-2).map(e => ({cls: e.className.slice(0,30), txt: e.innerText.trim().slice(0,400)}))"
            )
            for m in all_msgs:
                print(f"[{m.get('cls')}] {m.get('txt')}")

        # 7. SQL 추출
        sql_texts = await page.eval_on_selector_all(
            "pre code, code",
            "els => els.map(e => e.innerText.trim()).filter(t => t.toUpperCase().includes('SELECT'))"
        )
        if sql_texts:
            print("\n=== 생성된 SQL ===")
            print(sql_texts[-1][:1200])

        await page.screenshot(path="docs/_meta_team_03_final.png")
        await browser.close()
        print("\n스크린샷: docs/_meta_team_0*.png")

asyncio.run(run())
