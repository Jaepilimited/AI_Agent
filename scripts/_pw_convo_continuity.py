"""
Playwright: 대화 연속성 테스트 v3
- Q1: 퍼포먼스 마케팅 성과지표 비교 (5/15~21 vs 5/22~28)
- Q2: 광고비용 원인 분석 (같은 대화 컨텍스트 유지 검증)

버그 수정: 버튼 ID #btn-send (이전 테스트에서 #send-btn 오탐)
"""
import asyncio
import json
import time
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
Q1 = "5/15~21 과 5/22~28일간 퍼포먼스 마케팅 성과지표 비교좀 해줘"
Q2 = "광고비용이 증가한 원인이 뭐야"


async def wait_for_stream_done(page, timeout_s=120):
    """
    #btn-send 가 stop-mode 클래스 없이 존재 = 스트리밍 완료.
    stop-mode 있음 = 스트리밍 중 (stop 버튼), 없음 = 응답 완료 또는 대기.
    """
    deadline = time.time() + timeout_s
    # 먼저 stop-mode 시작을 기다림 (버튼이 stop 버튼으로 전환됐는지)
    stop_mode_seen = False
    while time.time() < deadline:
        state = await page.evaluate("""() => {
            const b = document.querySelector('#btn-send');
            if (!b) return 'no-btn';
            const sm = b.classList.contains('stop-mode');
            const disabled = b.disabled;
            const streaming = document.querySelectorAll('.message.streaming').length;
            return sm + '|' + disabled + '|' + streaming;
        }""")
        if state == 'no-btn':
            await asyncio.sleep(0.5)
            continue
        parts = state.split('|')
        stop_mode = parts[0] == 'true'
        disabled = parts[1] == 'true'
        streaming = int(parts[2])

        if stop_mode:
            stop_mode_seen = True  # 스트리밍 시작 확인

        # 스트리밍이 시작됐고 → stop-mode 없어지고 streaming 메시지도 없으면 완료
        if stop_mode_seen and not stop_mode and streaming == 0:
            return True

        await asyncio.sleep(1.0)
    return False


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

        # 네트워크 캡처
        api_calls = []
        convo_ids_seen = []
        detected_routes = []

        async def on_request(request):
            import re
            # /v1/chat/completions 캡처
            if "/v1/chat/completions" in request.url and request.method == "POST":
                try:
                    body = json.loads(request.post_data or "{}")
                    msgs = body.get("messages", [])
                    entry = {
                        "ts": time.time(),
                        "messages_count": len(msgs),
                        "messages": [
                            {"role": m["role"], "content_preview": str(m.get("content", ""))[:120]}
                            for m in msgs
                        ],
                        "sources": body.get("enabled_sources"),
                    }
                    api_calls.append(entry)
                    print(f"  [NET] API #{len(api_calls)}: {len(msgs)} messages")
                except Exception as e:
                    api_calls.append({"error": str(e)})

            # conversation messages 저장 호출 → conversation_id 추출
            m = re.search(r'/api/conversations/([^/]+)/messages', request.url)
            if m:
                cid = m.group(1)
                if cid not in convo_ids_seen:
                    convo_ids_seen.append(cid)
                    print(f"  [NET] convo_id: {cid}")

        async def on_response(response):
            if "/v1/chat/completions" in response.url:
                try:
                    body_text = await response.text()
                    import re
                    matches = re.findall(r'<!-- source:(\w+)', body_text)
                    if matches:
                        detected_routes.extend(matches)
                        print(f"  [NET] route: {matches}")
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        # 1. 로그인
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
            print("❌ 로그인 실패")
            await browser.close()
            return
        print(f"✅ 로그인: {page.url}")
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        # ─── Q1 전송 ───────────────────────────────────────────────────────────
        print(f"\n2. Q1 전송: {Q1}")
        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(Q1)
        await page.screenshot(path="docs/_cc_01_q1_typed.png")
        t0 = time.time()
        await page.keyboard.press("Enter")

        print("   Q1 응답 대기 (최대 120s)...")
        q1_done = await wait_for_stream_done(page, timeout_s=120)
        elapsed1 = time.time() - t0
        if q1_done:
            print(f"   ✅ Q1 스트리밍 완료: {elapsed1:.1f}초")
        else:
            print(f"   ⚠️  Q1 타임아웃 ({elapsed1:.1f}s)")

        await page.wait_for_timeout(2000)
        await page.screenshot(path="docs/_cc_02_q1_response.png")

        body_q1 = await page.inner_text("body")
        q1_error = any(kw in body_q1 for kw in ["분析이 예상보다", "조회 범위를 좁혀", "multi 서비스가"])
        print(f"   Q1 에러: {'❌ 있음' if q1_error else '✅ 없음'}")
        print(f"   conversation_id (Q1): {convo_ids_seen[-1] if convo_ids_seen else None}")

        # ─── Q2 전송 ───────────────────────────────────────────────────────────
        await page.wait_for_timeout(1000)
        print(f"\n3. Q2 전송 (같은 대화): {Q2}")

        # 버튼 상태 확인
        btn_state = await page.evaluate("""() => {
            const b = document.querySelector('#btn-send');
            if (!b) return 'not found';
            return b.classList.contains('stop-mode') + '|' + b.disabled;
        }""")
        print(f"   #btn-send 상태: {btn_state}")

        input_el2 = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el2.fill(Q2)
        await page.screenshot(path="docs/_cc_03_q2_typed.png")
        t1 = time.time()
        await page.keyboard.press("Enter")

        print("   Q2 응답 대기 (최대 120s)...")
        q2_done = await wait_for_stream_done(page, timeout_s=120)
        elapsed2 = time.time() - t1
        if q2_done:
            print(f"   ✅ Q2 스트리밍 완료: {elapsed2:.1f}초")
        else:
            print(f"   ⚠️  Q2 타임아웃 ({elapsed2:.1f}s)")

        await page.wait_for_timeout(2000)
        await page.screenshot(path="docs/_cc_04_q2_response.png")

        body_final = await page.inner_text("body")

        # ─── 결과 분석 ──────────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("검증 결과")
        print("=" * 60)

        # Conversation ID
        print(f"\n[대화 ID] {convo_ids_seen}")
        unique_ids = list(dict.fromkeys(convo_ids_seen))
        if len(unique_ids) == 1:
            print(f"  ✅ 단일 conversation 유지: {unique_ids[0]}")
        elif len(unique_ids) >= 2:
            print(f"  ❌ 대화 ID 변경됨: {unique_ids}")
        else:
            print("  ⚠️  conversation_id 감지 안 됨")

        # API 호출 분석
        print(f"\n[API 호출] 총 {len(api_calls)}회")
        for i, call in enumerate(api_calls):
            if "error" in call:
                print(f"  #{i+1}: ERROR {call['error']}")
                continue
            print(f"  #{i+1}: messages={call['messages_count']}개, sources={call['sources']}")
            for m in call.get("messages", []):
                print(f"         [{m['role']}] {m['content_preview']}")

        if len(api_calls) >= 2:
            q2_mc = api_calls[-1].get("messages_count", 0)
            if q2_mc >= 3:
                print(f"\n  ✅ Q2 API에 이전 대화 포함: {q2_mc}개 messages")
            elif q2_mc == 2:
                print(f"\n  ⚠️  Q2 API: user만 포함(assistant 미포함): {q2_mc}개 messages")
            else:
                print(f"\n  ❌ Q2 API: 이전 대화 없음 ({q2_mc}개)")

        # 라우트 분석
        print(f"\n[라우트] {detected_routes}")
        if len(detected_routes) >= 2:
            q2_route = detected_routes[-1]
            if q2_route == "bigquery":
                print(f"  ✅ Q2 bigquery 라우트 (원인 키워드 필터 정상)")
            elif q2_route == "multi":
                print(f"  ❌ Q2 multi 라우트 (30s 타임아웃 위험)")
            else:
                print(f"  ℹ️  Q2 라우트: {q2_route}")
        elif len(detected_routes) == 1:
            print(f"  ℹ️  Q1 라우트만 감지: {detected_routes[0]}")

        # 에러 확인
        q2_error = any(kw in body_final for kw in ["분析이 예상보다", "조회 범위를 좁혀", "multi 서비스가"])
        print(f"\n[Q2 에러] {'❌ 있음' if q2_error else '✅ 없음'}")

        # 컨텍스트 참조
        context_clues = ["5/15", "5/22", "15일", "22일", "이전 주", "해당 기간", "직전", "비교 기간"]
        found = [c for c in context_clues if c in body_final[-4000:]]
        if found:
            print(f"[컨텍스트] ✅ Q2 응답이 Q1 날짜 참조: {found}")
        else:
            print(f"[컨텍스트] ⚠️  Q2 응답이 Q1 날짜 미참조")

        # 응답 텍스트
        msgs = await page.eval_on_selector_all(
            ".message-content",
            "els => els.map(e => e.innerText.trim()).filter(t => t.length > 10)"
        )
        print(f"\n[메시지 {len(msgs)}개]")
        for i, m in enumerate(msgs[-2:]):
            label = "Q1 응답" if i == 0 and len(msgs) >= 2 else "Q2 응답"
            print(f"  === {label} ===")
            print(f"  {m[:500]}")

        await page.screenshot(path="docs/_cc_05_final.png")
        await browser.close()
        print("\n스크린샷: docs/_cc_0*.png")


asyncio.run(run())
