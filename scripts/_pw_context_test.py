"""
Playwright: 대화 컨텍스트 정밀 테스트
3턴 연속 대화에서 각 질문이 이전 컨텍스트를 올바르게 참조하는지 검증

Q1: 퍼포먼스 지표 비교 (5/15~21 vs 5/22~28)
Q2: 채널별 세분화 (암시적 기간 참조) ← 컨텍스트 필요
Q3: 특정 지표 추이 (Q2 결과 참조) ← 컨텍스트 누적 필요

검증:
- 각 질문이 올바른 기간(5/15~28) 사용하는지
- bigquery 라우트로 가는지
- 응답에 관련 날짜/채널이 포함되는지
"""
import asyncio
import json
import time
import re
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
Q1 = "5/15~21 과 5/22~28일간 퍼포먼스 마케팅 성과지표 비교좀 해줘"
Q2 = "그 기간을 채널별로 세분화해서 보여줘"
Q3 = "그중에서 ROAS가 가장 낮은 채널만 따로 3개월 추이 보여줘"


async def wait_stream_done(page, timeout_s=120):
    deadline = time.time() + timeout_s
    stop_seen = False
    while time.time() < deadline:
        state = await page.evaluate("""() => {
            const b = document.querySelector('#btn-send');
            if (!b) return 'no-btn';
            return b.classList.contains('stop-mode') + '|' + b.disabled +
                   '|' + document.querySelectorAll('.message.streaming').length;
        }""")
        if state == 'no-btn':
            await asyncio.sleep(0.5)
            continue
        parts = state.split('|')
        stop_mode = parts[0] == 'true'
        streaming = int(parts[2])
        if stop_mode:
            stop_seen = True
        if stop_seen and not stop_mode and streaming == 0:
            return True
        await asyncio.sleep(1.0)
    return False


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

        api_calls = []
        detected_routes = []

        async def on_request(request):
            if "/v1/chat/completions" in request.url and request.method == "POST":
                try:
                    body = json.loads(request.post_data or "{}")
                    msgs = body.get("messages", [])
                    api_calls.append({
                        "ts": time.time(),
                        "msg_count": len(msgs),
                        "msgs": [{"role": m["role"], "preview": str(m.get("content",""))[:150]}
                                 for m in msgs],
                    })
                    print(f"  [API#{len(api_calls)}] {len(msgs)} messages")
                except Exception:
                    pass

        async def on_response(response):
            if "/v1/chat/completions" in response.url:
                try:
                    text = await response.text()
                    matches = re.findall(r'<!-- source:(\w+)', text)
                    if matches:
                        detected_routes.extend(matches)
                        print(f"  [ROUTE] {matches}")
                    # SQL 추출
                    sql_matches = re.findall(r'```sql\\s*\\n(.*?)\\n```', text, re.DOTALL)
                    if not sql_matches:
                        # Try from SSE data
                        raw_data = re.findall(r'data: ({.*?})\n', text)
                        for d in raw_data:
                            try:
                                parsed = json.loads(d)
                                delta = parsed.get('choices', [{}])[0].get('delta', {})
                                content = delta.get('content', '')
                                if 'SELECT' in content.upper()[:50]:
                                    print(f"  [SQL snippet] {content[:100]}")
                            except Exception:
                                pass
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        # 로그인
        print("1. 로그인...")
        await page.goto(f"{URL}/login", wait_until="networkidle", timeout=15000)
        await page.fill("#input-name", "임재필")
        await page.wait_for_selector(".ac-item", timeout=5000)
        await page.click(".ac-item:first-child")
        await page.wait_for_timeout(500)
        await page.fill("#input-password", "1234")
        await page.click("#btn-submit")
        await page.wait_for_timeout(3000)
        if "login" in page.url:
            print("❌ 로그인 실패"); await browser.close(); return
        print(f"✅ 로그인")
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        results = []

        async def send_question(q, q_num, timeout_s=90):
            print(f"\n{q_num+2}. Q{q_num+1} 전송: {q}")
            inp = await page.wait_for_selector("#user-input, textarea", timeout=8000)
            await inp.fill(q)
            await page.screenshot(path=f"docs/_ctx_{q_num+1:02d}_typed.png")
            t0 = time.time()
            await page.keyboard.press("Enter")
            done = await wait_stream_done(page, timeout_s=timeout_s)
            elapsed = time.time() - t0
            status = "✅" if done else "⚠️ timeout"
            print(f"   {status}: {elapsed:.1f}초")
            await page.wait_for_timeout(1500)
            await page.screenshot(path=f"docs/_ctx_{q_num+1:02d}_response.png")

            body = await page.inner_text("body")
            has_error = any(kw in body for kw in ["분析이 예상보다", "multi 서비스가", "오류가 발생"])
            return {"done": done, "elapsed": elapsed, "has_error": has_error, "body_tail": body[-3000:]}

        # Q1
        r1 = await send_question(Q1, 0, timeout_s=90)
        results.append(r1)

        # Q2
        r2 = await send_question(Q2, 1, timeout_s=90)
        results.append(r2)

        # Q3
        r3 = await send_question(Q3, 2, timeout_s=90)
        results.append(r3)

        # ─── 최종 분석 ──────────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("컨텍스트 검증 결과")
        print("=" * 60)

        for i, (q, r) in enumerate(zip([Q1, Q2, Q3], results)):
            print(f"\n[Q{i+1}] {q}")
            print(f"  완료: {'✅' if r['done'] else '⚠️ 타임아웃'} ({r['elapsed']:.1f}s)")
            print(f"  에러: {'❌' if r['has_error'] else '✅ 없음'}")

        # API 호출 분석
        print(f"\n[API 호출] 총 {len(api_calls)}회")
        for i, call in enumerate(api_calls):
            print(f"  #{i+1}: messages={call['msg_count']}개")
            for m in call.get('msgs', []):
                print(f"         [{m['role']}] {m['preview'][:100]}")

        # 라우트
        print(f"\n[라우트] {detected_routes}")
        route_issues = []
        for i, route in enumerate(detected_routes):
            q_label = f"Q{i+1}"
            if route == "bigquery":
                print(f"  {q_label}: ✅ bigquery")
            elif route == "multi":
                print(f"  {q_label}: ❌ multi (30s 타임아웃 위험)")
                route_issues.append(q_label)
            else:
                print(f"  {q_label}: ℹ️ {route}")

        # 컨텍스트 참조 검증
        print("\n[컨텍스트 참조 검증]")
        # Q2는 Q1의 기간(5/15~28)을 참조해야 함
        date_clues = ["5/15", "5/22", "15일", "22일", "5월 15", "5월 22"]
        q2_body = results[1]["body_tail"] if len(results) > 1 else ""
        found_in_q2 = [c for c in date_clues if c in q2_body]
        if found_in_q2:
            print(f"  Q2: ✅ Q1 기간 참조됨: {found_in_q2}")
        else:
            print(f"  Q2: ❌ Q1 기간 미참조 (잘못된 기간으로 답변 가능성)")

        # Q3는 특정 채널을 참조해야 함 (Q2에서 채널별 분류 후)
        channel_clues = ["Meta", "TikTok", "Google", "SingleONE", "Amazon", "Shopify", "X "]
        q3_body = results[2]["body_tail"] if len(results) > 2 else ""
        found_channels = [c for c in channel_clues if c in q3_body]
        if found_channels:
            print(f"  Q3: ✅ 채널 참조됨: {found_channels}")
        else:
            print(f"  Q3: ⚠️ 채널 명시 없음 (일반적인 ROAS 조회일 수 있음)")

        # Q2 messages 수 확인 (컨텍스트 전달)
        if len(api_calls) >= 2:
            q2_msgs = api_calls[1]["msg_count"]
            q3_msgs = api_calls[2]["msg_count"] if len(api_calls) >= 3 else 0
            print(f"\n[messages 수]")
            print(f"  Q1 API: {api_calls[0]['msg_count']}개 (1=정상)")
            print(f"  Q2 API: {q2_msgs}개 (3=정상: Q1user+Q1ai+Q2user)")
            print(f"  Q3 API: {q3_msgs}개 (5=정상: Q1user+Q1ai+Q2user+Q2ai+Q3user)")
            if q2_msgs == 3:
                print("  ✅ Q2 컨텍스트 정상 전달")
            elif q2_msgs < 3:
                print(f"  ❌ Q2 컨텍스트 부족: {q2_msgs}개")
            if q3_msgs == 5:
                print("  ✅ Q3 컨텍스트 정상 전달 (3턴 모두)")
            elif q3_msgs < 5:
                print(f"  ⚠️ Q3 컨텍스트 부족: {q3_msgs}개")

        # 응답 미리보기
        msgs = await page.eval_on_selector_all(
            ".message-content",
            "els => els.map(e => e.innerText.trim()).filter(t => t.length > 20)"
        )
        print(f"\n[응답 미리보기] 총 {len(msgs)}개 메시지")
        for i, msg in enumerate(msgs):
            role = "user" if i % 2 == 0 else "AI"
            print(f"  [{role}] {msg[:200]}")

        await page.screenshot(path="docs/_ctx_final.png")
        await browser.close()
        print("\n스크린샷: docs/_ctx_*.png")


asyncio.run(run())
