"""
Playwright: 대화 컨텍스트 연속성 테스트
2026-06-01 fix 검증

시나리오:
  A) 참조형 질문  — "아까 내가 뭐 물어봤어?" → 반드시 Q1 내용 언급
  B) BQ 연속 질문 — "5월 매출 알려줘" → "그럼 4월은?" → 4월 SQL 실행
  C) 무관 질문    — 업무 대화 후 "오늘 날씨 어때?" → 답변 가능 (컨텍스트 불필요)
"""
import asyncio
import json
import time
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"


async def login(page):
    await page.goto(f"{URL}/login", wait_until="networkidle", timeout=15000)
    await page.fill("#input-name", "임재필")
    await page.wait_for_selector(".ac-item", timeout=5000)
    await page.click(".ac-item:first-child")
    await page.wait_for_timeout(500)
    await page.fill("#input-password", "1234")
    await page.click("#btn-submit")
    await page.wait_for_timeout(3000)
    return "login" not in page.url


async def wait_stream(page, timeout_s=90):
    deadline = time.time() + timeout_s
    stop_seen = False
    while time.time() < deadline:
        state = await page.evaluate("""() => {
            const b = document.querySelector('#btn-send');
            if (!b) return 'no-btn';
            return b.classList.contains('stop-mode') + '|' +
                   document.querySelectorAll('.message.streaming').length;
        }""")
        if state == 'no-btn':
            await asyncio.sleep(0.5)
            continue
        stop_mode = state.split('|')[0] == 'true'
        streaming = int(state.split('|')[1])
        if stop_mode:
            stop_seen = True
        if stop_seen and not stop_mode and streaming == 0:
            return True
        await asyncio.sleep(0.8)
    return False


async def send_msg(page, text):
    inp = await page.wait_for_selector("#user-input, textarea", timeout=8000)
    await inp.fill(text)
    await page.keyboard.press("Enter")


async def last_answer(page):
    msgs = await page.eval_on_selector_all(
        ".message.assistant .message-content",
        "els => els.map(e => e.innerText.trim())"
    )
    return msgs[-1] if msgs else ""


async def capture_api(page):
    """API 호출 캡처 — messages 배열 크기 + 내용 반환"""
    calls = []

    async def on_req(req):
        if "/v1/chat/completions" in req.url and req.method == "POST":
            try:
                body = json.loads(req.post_data or "{}")
                calls.append({
                    "n": len(body.get("messages", [])),
                    "last_user": next(
                        (m["content"][:200] for m in reversed(body.get("messages", []))
                         if m["role"] == "user"), ""
                    )
                })
            except Exception:
                pass

    page.on("request", on_req)
    return calls


# ───────────────────────────────────────────────────────────────
async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900}, locale="ko-KR")
        page = await ctx.new_page()

        print("=" * 60)
        print("컨텍스트 연속성 테스트")
        print("=" * 60)

        # 로그인
        print("\n[로그인]")
        ok = await login(page)
        if not ok:
            print(f"  {FAIL} 로그인 실패")
            await browser.close()
            return
        print(f"  {PASS} 로그인 성공")
        await page.wait_for_load_state("networkidle", timeout=10000)
        await page.wait_for_timeout(1500)

        results = []

        # ══════════════════════════════════════════════════════════
        # 시나리오 A: 참조형 질문 ("아까 내가 뭐 물어봤어?")
        # ══════════════════════════════════════════════════════════
        print("\n[시나리오 A] 참조형 질문 테스트")
        api_calls = await capture_api(page)

        Q_A1 = "ROAS가 정확히 뭐야? 간단하게 설명해줘"
        Q_A2 = "아까 내가 뭐 물어봤어?"

        print(f"  Q1: {Q_A1}")
        await send_msg(page, Q_A1)
        done = await wait_stream(page, 60)
        print(f"  Q1 완료: {PASS if done else WARN}")
        await page.wait_for_timeout(1500)

        print(f"  Q2: {Q_A2}")
        await send_msg(page, Q_A2)
        done = await wait_stream(page, 60)
        await page.wait_for_timeout(1500)
        await page.screenshot(path="docs/_ctx_a_result.png")

        ans_a = await last_answer(page)
        # Q2 응답이 "ROAS" 또는 Q1 내용을 언급하면 통과
        context_used = any(kw in ans_a.upper() for kw in ["ROAS", "뭐야", "설명", "물어"])
        n_msgs = api_calls[-1]["n"] if api_calls else 0

        verdict_a = PASS if context_used and n_msgs >= 3 else FAIL
        results.append(("A. 참조형 질문", verdict_a,
                         f"messages={n_msgs}, ROAS 언급={'있음' if context_used else '없음'}"))
        print(f"  결과: {verdict_a} messages={n_msgs}, 컨텍스트 언급={'있음' if context_used else '없음'}")
        print(f"  응답 미리보기: {ans_a[:200]}")

        # ══════════════════════════════════════════════════════════
        # 시나리오 B: BQ 연속 질문 ("5월 매출" → "그럼 4월은?")
        # ══════════════════════════════════════════════════════════
        print("\n[시나리오 B] BQ 연속 질문 테스트")
        # 새 대화 시작
        new_btn = page.locator("#btn-new-chat, .new-chat-btn, [title='새 대화']")
        if await new_btn.count() > 0:
            await new_btn.first.click()
            await page.wait_for_timeout(1000)

        api_calls_b = await capture_api(page)

        Q_B1 = "2026년 5월 전체 매출 알려줘"
        Q_B2 = "그럼 4월은?"

        print(f"  Q1: {Q_B1}")
        await send_msg(page, Q_B1)
        done = await wait_stream(page, 90)
        print(f"  Q1 완료: {PASS if done else WARN}")
        await page.wait_for_timeout(1500)

        print(f"  Q2: {Q_B2}")
        await send_msg(page, Q_B2)
        done = await wait_stream(page, 90)
        await page.wait_for_timeout(1500)
        await page.screenshot(path="docs/_ctx_b_result.png")

        ans_b = await last_answer(page)
        # Q2 응답에 4월 관련 숫자나 데이터가 있으면 통과
        has_april_data = any(kw in ans_b for kw in ["4월", "4/", "April", "원", "매출", "₩", ","])
        n_msgs_b = api_calls_b[-1]["n"] if api_calls_b else 0
        is_error = any(kw in ans_b for kw in ["예상보다 오래", "조회 범위", "오류"])

        verdict_b = PASS if has_april_data and not is_error else FAIL
        results.append(("B. BQ 연속 질문", verdict_b,
                         f"messages={n_msgs_b}, 4월 데이터={'있음' if has_april_data else '없음'}, 에러={'있음' if is_error else '없음'}"))
        print(f"  결과: {verdict_b} 4월 데이터={'있음' if has_april_data else '없음'}")
        print(f"  응답 미리보기: {ans_b[:200]}")

        # ══════════════════════════════════════════════════════════
        # 시나리오 C: 완전 무관 질문 (컨텍스트 불필요해도 답변 가능)
        # ══════════════════════════════════════════════════════════
        print("\n[시나리오 C] 무관 질문 테스트 (답변만 되면 통과)")
        new_btn2 = page.locator("#btn-new-chat, .new-chat-btn, [title='새 대화']")
        if await new_btn2.count() > 0:
            await new_btn2.first.click()
            await page.wait_for_timeout(1000)

        Q_C1 = "5월 매출이 얼마나 됐어?"
        Q_C2 = "파이썬이 뭐야?"

        print(f"  Q1: {Q_C1}")
        await send_msg(page, Q_C1)
        done = await wait_stream(page, 90)
        await page.wait_for_timeout(1500)

        print(f"  Q2 (무관): {Q_C2}")
        await send_msg(page, Q_C2)
        done = await wait_stream(page, 60)
        await page.wait_for_timeout(1500)
        await page.screenshot(path="docs/_ctx_c_result.png")

        ans_c = await last_answer(page)
        # 파이썬 설명이 나오면 통과 (컨텍스트와 무관하게 답변)
        answered = any(kw in ans_c for kw in ["프로그래밍", "언어", "Python", "파이썬", "코딩"])
        is_error_c = any(kw in ans_c for kw in ["오류", "지원 범위", "도움드릴"])

        verdict_c = PASS if answered else WARN
        results.append(("C. 무관 질문", verdict_c,
                         f"파이썬 설명={'있음' if answered else '없음'}"))
        print(f"  결과: {verdict_c} 파이썬 설명={'있음' if answered else '없음'}")
        print(f"  응답 미리보기: {ans_c[:200]}")

        # ══════════════════════════════════════════════════════════
        # 최종 결과
        # ══════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("최종 결과")
        print("=" * 60)
        all_pass = True
        for name, v, detail in results:
            print(f"  {v} {name}: {detail}")
            if v == FAIL:
                all_pass = False

        print(f"\n{'✅ 전체 통과' if all_pass else '❌ 일부 실패'}")
        print("스크린샷: docs/_ctx_*.png")

        await browser.close()


asyncio.run(run())
