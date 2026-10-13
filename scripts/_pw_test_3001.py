"""Playwright headless test for dev server port 3001."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
        )
        page = await ctx.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: console_errors.append(f"[pageerror] {e}"))

        results = []

        # 1. 루트 → 로그인 리다이렉트 확인
        await page.goto("http://127.0.0.1:3001/", wait_until="networkidle", timeout=15000)
        results.append(f"1. GET / → 최종 URL: {page.url}")

        # 2. 로그인 페이지 로드
        await page.goto("http://127.0.0.1:3001/login", wait_until="networkidle", timeout=15000)
        title = await page.title()
        results.append(f"2. 로그인 페이지 title='{title}'")
        await page.screenshot(path="docs/_pw_01_login.png")

        # 3. 이름 검색 → autocomplete에서 임재필 선택
        await page.fill("#input-name", "임재필")
        await page.wait_for_timeout(400)  # debounce 150ms + network

        # autocomplete 드롭다운 대기
        try:
            await page.wait_for_selector(".ac-dropdown .ac-item, .ac-dropdown div", timeout=5000)
            ac_items = await page.eval_on_selector_all(
                ".ac-dropdown .ac-item, .ac-dropdown div",
                "els => els.map(e => e.innerText.trim())"
            )
            results.append(f"3. autocomplete 항목: {ac_items[:5]}")
            # 첫 번째 항목 클릭
            await page.click(".ac-dropdown .ac-item:first-child, .ac-dropdown div:first-child")
            await page.wait_for_timeout(300)
        except Exception as e:
            results.append(f"3. autocomplete 실패: {e} — 직접 API 확인")
            # API로 확인
            resp = await page.evaluate("fetch('/api/auth/search-name?name=임재필').then(r=>r.json())")
            results.append(f"   search-name API 결과: {resp}")

        await page.screenshot(path="docs/_pw_02_after_name.png")

        # 4. 비밀번호 입력 & 제출
        await page.fill("#input-password", "YOUR_PASSWORD_HERE")
        await page.screenshot(path="docs/_pw_03_before_submit.png")
        await page.click("#btn-submit")
        await page.wait_for_timeout(2500)
        after_url = page.url
        results.append(f"4. 로그인 후 URL: {after_url}")
        await page.screenshot(path="docs/_pw_04_after_login.png")

        # 5. 채팅 UI 검증
        logged_in = "login" not in after_url
        if logged_in:
            # 페이지 완전 로드 대기
            await page.wait_for_load_state("networkidle", timeout=10000)
            await page.wait_for_timeout(1000)

            # 실제 DOM 스캔
            all_ids = await page.eval_on_selector_all("[id]", "els => els.map(e => e.id).filter(Boolean)")
            results.append(f"5. 채팅 페이지 IDs: {all_ids[:30]}")

            # textarea, input[type=text] 탐색
            textareas = await page.eval_on_selector_all(
                "textarea, input[type='text']:not([id='input-name'])",
                "els => els.map(e => ({tag: e.tagName, id: e.id, cls: e.className.slice(0,40), placeholder: e.placeholder}))"
            )
            results.append(f"   textareas/inputs: {textareas}")

            sidebar   = await page.query_selector(".sidebar")
            model_sel = await page.query_selector("#model-select")
            results.append(f"   sidebar={sidebar is not None} model={model_sel is not None}")

            # 입력창 찾기 (id 무관)
            input_el = await page.query_selector("textarea, #user-input, #chat-input")
            send_btn = await page.query_selector("#send-btn, button[type='submit']:not(#btn-submit), .send-btn")

            results.append(f"   input_el={input_el is not None} send_btn={send_btn is not None}")

            await page.screenshot(path="docs/_pw_05_chat.png")

            # 6. 메시지 전송 테스트
            if input_el:
                input_selector = await page.evaluate("el => el.id ? '#'+el.id : el.tagName.toLowerCase()", input_el)
                await page.fill(input_selector, "안녕, 1+1 알려줘")

                if send_btn:
                    await send_btn.click()
                else:
                    await page.keyboard.press("Enter")

                # 어시스턴트 응답 대기
                try:
                    await page.wait_for_function(
                        """() => {
                            const msgs = document.querySelectorAll('.message, [class*="message"], [class*="assistant"], [class*="bot"]');
                            for (const m of msgs) {
                                const c = m.className || '';
                                if (c.includes('assistant') || c.includes('bot')) return true;
                            }
                            return false;
                        }""",
                        timeout=20000,
                    )
                    await page.wait_for_timeout(5000)  # 스트리밍 완료 대기
                    msgs = await page.eval_on_selector_all(
                        ".message, [class*='message']",
                        "els => els.map(e => ({cls: e.className.trim().split(' ').slice(-2).join(' '), txt: e.innerText.trim().slice(0,120)}))"
                    )
                    results.append(f"6. 메시지 목록:")
                    for m in msgs:
                        results.append(f"   [{m['cls']}] {m['txt']}")
                except Exception as e:
                    results.append(f"6. 응답 대기 실패: {e}")
                    # 현재 DOM 상태 덤프
                    body_txt = await page.inner_text("body")
                    results.append(f"   body 일부: {body_txt[:300]}")

                await page.screenshot(path="docs/_pw_06_after_msg.png")
            else:
                results.append("6. 입력창 없음 — 스킵")
        else:
            err_el = await page.query_selector("#error-msg")
            err_txt = await err_el.inner_text() if err_el else "(없음)"
            results.append(f"5. 로그인 실패 — 에러: {err_txt}")

        # 7. /safety/status API 직접 확인
        status = await page.evaluate("fetch('/safety/status').then(r=>r.json())")
        results.append(f"7. /safety/status maintenance.active={status.get('maintenance',{}).get('active')}")

        await browser.close()

    print("=== Playwright 검토 결과 ===")
    for r in results:
        print(r)

    if console_errors:
        print(f"\n=== 콘솔 오류 ({len(console_errors)}개) ===")
        for e in console_errors[:20]:
            print(e)
    else:
        print("\n콘솔 오류 없음 ✅")


asyncio.run(run())
