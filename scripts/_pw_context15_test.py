"""Playwright: 한 대화에서 15개 연속 질문 — 컨텍스트 연속성 테스트
참조 유형: 직전 참조(그거/방금), 다중 참조(둘 중/세 나라), 장거리 참조(처음 질문), 세션 전체 참조
"""
import asyncio, sys, time
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"

QUESTIONS = [
    "2026년 1분기 미국 매출 알려줘",                                  # 1 BQ 기준점
    "일본은?",                                                        # 2 직전 참조 (조건 승계)
    "둘 중 어디가 더 높아?",                                          # 3 2개 턴 참조
    "미국 매출을 월별로 나눠서 보여줘",                               # 4 토픽 유지
    "그 중에 2월 수치만 다시 말해줘",                                  # 5 직전 답변 참조
    "한국 1분기 매출은 얼마야?",                                       # 6 새 국가 (조건 승계)
    "지금까지 나온 세 나라 매출 합치면 얼마야?",                       # 7 다중 턴 참조
    "그 합계에서 미국이 차지하는 비중은 몇 퍼센트야?",                 # 8 직전 참조 + 계산
    "센텔라 앰플은 1분기에 몇 개 팔렸어?",                             # 9 토픽 전환
    "그 제품 작년 같은 기간이랑 비교하면 늘었어 줄었어?",              # 10 직전 참조
    "아까 처음에 물어본 미국 매출 얼마였는지 기억해?",                 # 11 장거리 참조 (턴 1)
    "그 수치랑 일본 수치를 표로 비교해줘",                             # 12 장거리 2개 참조
    "지금까지 이 대화에서 조회한 국가를 전부 나열해봐",                # 13 세션 전체 참조
    "그 국가들 중에 매출이 가장 낮았던 곳은 어디야?",                  # 14 직전 + 세션 참조
    "오늘 대화 내용을 3줄로 요약해줘",                                 # 15 세션 전체 요약
]

async def wait_answer(page, prev_count, max_loops=120):
    """새 assistant 메시지가 나타나고 6초간 안정될 때까지 대기"""
    prev, stable, text = "", 0, ""
    for _ in range(max_loops):
        await page.wait_for_timeout(2000)
        result = await page.evaluate("""() => {
            const msgs = document.querySelectorAll('.message.assistant, .msg.assistant, [class*="assistant"]');
            return { count: msgs.length, text: msgs.length ? (msgs[msgs.length-1].innerText || '') : '' };
        }""")
        if result["count"] <= prev_count:
            continue
        text = result["text"]
        if text and len(text) > 40 and text == prev:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        prev = text
    return text

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

        await page.goto(f"{URL}/login", wait_until="networkidle", timeout=15000)
        await page.fill("#input-name", "임재필")
        await page.wait_for_selector(".ac-item", timeout=5000)
        await page.click(".ac-item:first-child")
        await page.wait_for_timeout(500)
        await page.fill("#input-password", "1234")
        await page.click("#btn-submit")
        await page.wait_for_timeout(3000)
        if "login" in page.url:
            print("로그인 실패")
            await browser.close()
            return
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        for i, q in enumerate(QUESTIONS, 1):
            count = await page.evaluate("""() => document.querySelectorAll('.message.assistant, .msg.assistant, [class*="assistant"]').length""")
            input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
            await input_el.fill(q)
            await page.keyboard.press("Enter")
            t0 = time.time()
            ans = await wait_answer(page, count)
            elapsed = time.time() - t0
            # 노이즈 제거: 줄바꿈 압축
            snippet = " ".join(ans.split())[:400]
            print(f"\n{'='*70}")
            print(f"[Q{i}] {q}  ({elapsed:.0f}초)")
            print(f"[A{i}] {snippet}")
            sys.stdout.flush()
            await page.wait_for_timeout(1500)

        await page.screenshot(path="docs/_ctx15_final.png", full_page=False)
        await browser.close()
        print("\n테스트 완료")

asyncio.run(run())
