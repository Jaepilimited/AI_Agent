"""Playwright demo test: CEO 시연용 12개 질문 전수 테스트."""
import asyncio
import sys
import json
import time
import urllib.request
from playwright.async_api import async_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:3000"
USER_NAME = "임재필"
USER_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
PASSWORD = "1234"

DEMO_QUESTIONS = [
    ("매출 TOP10", "이번 달 국가별 매출 TOP 10 알려줘"),
    ("매출 추이", "최근 3개월 월별 매출 추이 보여줘"),
    ("B2B/B2C", "B2B와 B2C 매출 비중 비교해줘"),
    ("플랫폼 비교", "쇼피 vs 아마존 vs 틱톡샵 이번 달 매출 비교해줘"),
    ("제품 TOP5", "이번 달 제품별 판매 수량 순위 TOP 5 보여줘"),
    ("인플루언서", "@@인플루언서 이번 달 팀별 인플루언서 비용과 조회수 비교해줘"),
    ("FB ROAS", "국가별 Facebook ROAS 분석해줘"),
    ("마케팅 ROAS", "@@마케팅비용 Media별 ROAS 비교해줘"),
    ("리뷰 감성", "@@리뷰 아마존 제품별 평균 감성 점수 TOP 5 알려줘"),
    ("CS 성분", "센텔라 앰플의 주요 성분과 피부 효능을 상세히 알려줘"),
    ("Shopify", "@@쇼피파이 이번 달 Shopify 국가별 매출 현황 알려줘"),
    ("종합분석", "동남아시아 시장 매출 현황과 성장 전략 분석해줘"),
]


def get_jwt_token():
    data = json.dumps({"department": USER_DEPT, "name": USER_NAME, "password": PASSWORD}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/api/auth/signin", data=data,
                                headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        cookie_header = resp.headers.get("set-cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("token="):
                return part[len("token="):]
    except Exception as e:
        print(f"[ERROR] Login failed: {e}")
    return None


async def wait_response(page, timeout_sec=120):
    """Wait for AI response to finish streaming."""
    for i in range(timeout_sec):
        typing = await page.locator(".typing-indicator").count()
        streaming = await page.locator(".message-assistant.streaming").count()
        if typing == 0 and streaming == 0 and i > 3:
            return True
        await page.wait_for_timeout(1000)
    return False


async def main():
    token = get_jwt_token()
    if not token:
        print("[FAIL] Cannot get JWT token")
        return

    print(f"[OK] JWT token acquired\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        await context.add_cookies([{
            "name": "token", "value": token,
            "domain": "127.0.0.1", "path": "/",
            "httpOnly": True, "sameSite": "Lax"
        }])

        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)

        try:
            await page.wait_for_selector("#chat-input", state="visible", timeout=30000)
        except:
            print(f"[FAIL] Chat UI not loaded. URL: {page.url}")
            await browser.close()
            return

        print(f"{'#':>2} {'Label':<12} {'Time':>6} {'Chars':>6} {'Table':>5} {'Chart':>5} {'Result':<6} Query")
        print("-" * 90)

        results = []
        for idx, (label, query) in enumerate(DEMO_QUESTIONS, 1):
            # New chat for each question
            new_btn = page.locator("#btn-new-chat")
            await new_btn.click()
            await page.wait_for_timeout(800)

            # Send question
            chat_input = page.locator("#chat-input")
            await chat_input.fill(query)
            await page.wait_for_timeout(200)
            send_btn = page.locator("#btn-send")
            await send_btn.click()

            t0 = time.time()
            await page.wait_for_timeout(3000)
            done = await wait_response(page, timeout_sec=120)
            elapsed = time.time() - t0

            # Get response content
            ai_msgs = page.locator(".message-assistant")
            ai_count = await ai_msgs.count()
            content = ""
            has_table = False
            has_chart = False

            if ai_count > 0:
                msg_el = ai_msgs.last
                try:
                    content = await msg_el.locator(".message-content").text_content(timeout=5000)
                except:
                    content = ""

                # Check for table
                table_count = await msg_el.locator("table").count()
                has_table = table_count > 0

                # Check for chart
                chart_count = await msg_el.locator(".chart-container").count()
                has_chart = chart_count > 0

            content_len = len(content) if content else 0
            has_error = content and ("오류" in content[:100] or "에러" in content[:100])

            if has_error or content_len < 50:
                status = "FAIL"
            elif not done:
                status = "TIMEOUT"
            else:
                status = "PASS"

            icon = {"PASS": "O", "FAIL": "X", "TIMEOUT": "T"}[status]
            tbl = "O" if has_table else "-"
            cht = "O" if has_chart else "-"
            print(f"{idx:>2} {label:<12} {elapsed:>5.0f}s {content_len:>6} {tbl:>5} {cht:>5} {icon:<6} {query[:40]}")

            results.append({
                "label": label, "query": query, "status": status,
                "time": elapsed, "chars": content_len,
                "has_table": has_table, "has_chart": has_chart,
            })

        print("-" * 90)
        passed = sum(1 for r in results if r["status"] == "PASS")
        tables = sum(1 for r in results if r["has_table"])
        charts = sum(1 for r in results if r["has_chart"])
        print(f"TOTAL: {passed}/{len(results)} PASS | Tables: {tables} | Charts: {charts}")

        # Save results
        out_path = "scripts/playwright_demo_results.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nResults saved: {out_path}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
