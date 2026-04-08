"""Playwright Live Demo v2 — CEO 시연용 풀 기능 데모 + 자막.

Features:
  - Light 모드 전환
  - 대시보드 기능
  - @@ 데이터소스 선택
  - 시스템 스테이터스 (데이터 업데이트 체크)
  - Google Workspace (Gmail, Calendar)
  - 매출 드릴다운 + 후속 질문
  - 마케팅/리뷰/CS 전환
  - 종합 분석
"""
import asyncio
import sys
import json
import time
import threading
import urllib.request
from playwright.async_api import async_playwright

# ── 시연 제어 ──
_paused = threading.Event()
_paused.set()  # 시작은 실행 상태
_skip_to_next = threading.Event()
_jump_to = [None]  # [scene_number] or [None]

def _input_listener():
    """Space=일시정지/재개, N=다음 장면 스킵, 숫자=특정 장면 점프, Q=종료."""
    while True:
        try:
            line = input().strip().lower()
            if line == 'q':
                print("\n[종료]")
                import os; os._exit(0)
            elif line == 'n':
                _skip_to_next.set()
                if not _paused.is_set():
                    _paused.set()
                print("[>> 다음 장면으로 스킵]")
            elif line == '' or line == ' ':
                if _paused.is_set():
                    _paused.clear()
                    print("[⏸ 일시정지] Space/Enter=재개, N=다음, 숫자=점프, Q=종료")
                else:
                    _paused.set()
                    print("[▶ 재개]")
            elif line.isdigit():
                _jump_to[0] = int(line)
                _skip_to_next.set()
                if not _paused.is_set():
                    _paused.set()
                print(f"[>> 장면 {line}으로 점프]")
        except:
            break

threading.Thread(target=_input_listener, daemon=True).start()

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 접속 설정 (다른 PC에서 실행 시 --url로 지정) ──
import argparse
_parser = argparse.ArgumentParser(description="SKIN1004 AI Demo")
_parser.add_argument("--url", default="http://127.0.0.1:3000", help="서버 URL (예: http://172.16.1.250:3000)")
_parser.add_argument("--name", default="임재필", help="로그인 이름")
_parser.add_argument("--dept", default="Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석", help="부서")
_parser.add_argument("--pw", default="1234", help="비밀번호")
_args = _parser.parse_args()

BASE_URL = _args.url
USER_NAME = _args.name
USER_DEPT = _args.dept
PASSWORD = _args.pw

SUBTITLE_CSS = """
#demo-subtitle {
  position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%);
  background: rgba(0,0,0,0.88); color: #fff; padding: 14px 36px;
  border-radius: 14px; font-size: 17px; font-weight: 600;
  z-index: 99999; text-align: center; max-width: 85%;
  backdrop-filter: blur(16px); border: 1px solid rgba(232,146,0,0.3);
  font-family: 'Noto Sans KR', sans-serif; line-height: 1.6;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  transition: opacity 0.4s; pointer-events: none;
}
#demo-subtitle .hi { color: #e89200; font-weight: 800; }
#demo-subtitle .dim { font-size: 13px; color: #aaa; margin-top: 6px; }
#demo-phase {
  position: fixed; top: 14px; left: 50%; transform: translateX(-50%);
  background: linear-gradient(135deg, #e89200, #ff6b35); color: #fff;
  padding: 7px 22px; border-radius: 18px; font-size: 13px; font-weight: 700;
  z-index: 99999; font-family: 'Noto Sans KR', sans-serif;
  box-shadow: 0 4px 16px rgba(232,146,0,0.4);
  transition: opacity 0.4s; pointer-events: none;
  letter-spacing: 0.03em;
}
"""

# ── Full Demo Script ──
SCRIPT = [
    # ─── INTRO ───
    {"type": "sub", "phase": "SKIN1004 AI Agent", "text": '사내 AI 에이전트 <span class="hi">풀 기능 시연</span>을 시작합니다<div class="dim">매출 · 마케팅 · 리뷰 · 문서 · CS · Google Workspace</div>', "wait": 4},

    # ─── 1. LIGHT MODE ───
    {"type": "sub", "phase": "UI · Light Mode", "text": '<span class="hi">라이트 모드</span>로 전환합니다', "wait": 2},
    {"type": "action", "action": "light_mode"},
    {"type": "pause", "wait": 2},

    # ─── 2. GOOGLE 계정 소개 (로그인 스킵) ───
    {"type": "sub", "phase": "Google 계정 연결", "text": 'Google 연결 버튼으로 <span class="hi">OAuth 개인 인증</span> 가능<div class="dim">Gmail · Calendar · Drive · 대시보드(Looker Studio)</div>', "wait": 4},
    {"type": "sub", "text": '개인 메일/일정만 접근 · <span class="hi">다른 사람 데이터 접근 불가</span><div class="dim">per-user 토큰 방식 · 로그아웃 시 토큰 삭제</div>', "wait": 4},

    # ─── 3. DASHBOARD ───
    {"type": "sub", "phase": "대시보드", "text": '<span class="hi">대시보드</span> — 전체 시스템 현황을 한눈에 확인', "wait": 2},
    {"type": "action", "action": "open_dashboard"},
    {"type": "pause", "wait": 5},
    {"type": "sub", "text": '매출 추이, 국가별 분포, 플랫폼 현황 등 <span class="hi">Looker Studio 실시간 대시보드</span>', "wait": 4},
    {"type": "action", "action": "close_dashboard"},
    {"type": "pause", "wait": 1},

    # ─── 4. SYSTEM STATUS ───
    {"type": "sub", "phase": "시스템 스테이터스", "text": '<span class="hi">시스템 상태 모니터링</span> — 데이터 테이블 업데이트 실시간 감지', "wait": 2},
    {"type": "action", "action": "open_status"},
    {"type": "pause", "wait": 3},
    {"type": "sub", "text": '매출 테이블이 업데이트 중이면 <span class="hi">자동 점검 모드</span> 활성화<div class="dim">잘못된 수치 응답 방지 · 업데이트 완료 시 자동 해제</div>', "wait": 5},
    {"type": "action", "action": "close_status"},
    {"type": "pause", "wait": 1},

    # ─── 5. @@ 데이터소스 목록 ───
    {"type": "sub", "phase": "@@ 데이터소스 선택", "text": '<span class="hi">@@목록</span> 으로 사용 가능한 데이터소스를 확인합니다', "wait": 2},
    {"type": "send", "q": "@@목록", "sub": '<span class="hi">13개 데이터 테이블</span> + 팀별 Notion 문서 + CS + Google Workspace<div class="dim">@@매출, @@광고, @@인플루언서, @@리뷰, @@노션 등</div>'},
    {"type": "pause", "wait": 3},

    # ─── 5. 매출 드릴다운 (후속 질문) ───
    {"type": "new", "phase": "DEMO 1 · 매출 드릴다운", "sub": '질문 한 줄로 매출 현황 → <span class="hi">후속 질문으로 드릴다운</span>'},
    {"type": "send", "q": "이번 달 국가별 매출 TOP 10 알려줘", "sub": '<span class="hi">자연어 → SQL 자동 변환</span> → BigQuery 실행 → 표 + 차트 생성'},
    {"type": "send", "q": "여기서 인도네시아만 플랫폼별로 상세하게 보여줘", "sub": '"여기서 인도네시아만" — <span class="hi">이전 대화 맥락을 기억</span>하고 드릴다운'},
    {"type": "send", "q": "인도네시아 최근 3개월 월별 매출 추이도 보여줘", "sub": '월별 추이 → <span class="hi">자동 라인 차트</span> 생성'},

    # ─── 6. 마케팅 + @@ 지정 ───
    {"type": "new", "phase": "DEMO 2 · 마케팅 효율", "sub": 'ROAS 분석 → <span class="hi">@@인플루언서</span>로 데이터소스 직접 지정'},
    {"type": "send", "q": "국가별 Facebook ROAS 분석해줘", "sub": '13개 테이블에서 <span class="hi">광고 테이블을 자동 선택</span>하여 ROAS 계산'},
    {"type": "send", "q": "@@인플루언서 이번 달 팀별 비용과 조회수 비교해줘", "sub": '<span class="hi">@@인플루언서</span> — 데이터소스 직접 지정으로 정확한 검색'},

    # ─── 7. 제품 → 리뷰 → CS (3개 라우트 전환) ───
    {"type": "new", "phase": "DEMO 3 · 제품 · 리뷰 · CS", "sub": '<span class="hi">3개 데이터 소스</span>를 자연스럽게 전환하는 대화'},
    {"type": "send", "q": "이번 달 제품별 판매 수량 순위 TOP 5 보여줘", "sub": '<span class="hi">BigQuery 제품 테이블</span>에서 자동 조회'},
    {"type": "send", "q": "@@리뷰 아마존 제품별 평균 감성 점수 TOP 5 알려줘", "sub": '<span class="hi">리뷰 감성 분석</span> — AI가 sentiment score 자동 분석'},
    {"type": "send", "q": "센텔라 앰플의 주요 성분과 피부 효능을 상세히 알려줘", "sub": '<span class="hi">CS Q&A 데이터베이스</span> — 739건 제품 전문 지식 즉시 답변'},

    # ─── 8. Google Workspace (소개) ───
    {"type": "sub", "phase": "DEMO 4 · Google Workspace", "text": 'Google 연결 후 사용 가능한 질문 예시:<div class="dim">"오늘 일정 알려줘" · "안 읽은 메일 3개 요약" · "드라이브에서 보고서 찾아줘"</div>', "wait": 5},
    {"type": "sub", "text": '<span class="hi">ReAct 에이전트</span> — 복잡한 요청도 단계별로 처리<div class="dim">"다음주 화요일 오후 2시에 마케팅 미팅 잡아줘" → Calendar 자동 생성</div>', "wait": 5},

    # ─── 9. 종합 분석 ───
    {"type": "new", "phase": "DEMO 5 · 종합 분석", "sub": '내부 데이터 + 외부 검색을 <span class="hi">종합</span>하여 전략 제안'},
    {"type": "send", "q": "동남아시아 시장 매출 현황과 성장 전략 분석해줘", "sub": '<span class="hi">Multi 라우트</span> — BigQuery 매출 데이터 + Google 검색 종합 분석'},

    # ─── CLOSING ───
    {"type": "sub", "phase": "DEMO COMPLETE", "text": '시연 완료<div class="dim">자연어 한 줄로 13개 DB · 808 문서 · 739 CS · Gmail/Calendar에 즉시 접근</div>', "wait": 8},
]


def get_jwt_token():
    data = json.dumps({"department": USER_DEPT, "name": USER_NAME, "password": PASSWORD}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/api/auth/signin", data=data,
                                headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        for part in resp.headers.get("set-cookie", "").split(";"):
            p = part.strip()
            if p.startswith("token="):
                return p[len("token="):]
    except Exception as e:
        print(f"[ERROR] Login: {e}")
    return None


async def inject_overlay(page):
    await page.evaluate("""() => {
        if (document.getElementById('demo-subtitle')) return;
        var s = document.createElement('style');
        s.textContent = `""" + SUBTITLE_CSS + """`;
        document.head.appendChild(s);
        var sub = document.createElement('div'); sub.id = 'demo-subtitle'; sub.style.opacity = '0';
        document.body.appendChild(sub);
        var ph = document.createElement('div'); ph.id = 'demo-phase'; ph.style.opacity = '0';
        document.body.appendChild(ph);
    }""")


async def show_sub(page, text, phase=None):
    await page.evaluate("(t) => { var e = document.getElementById('demo-subtitle'); if(e){e.innerHTML=t;e.style.opacity='1';} }", text)
    if phase:
        await page.evaluate("(p) => { var e = document.getElementById('demo-phase'); if(e){e.textContent=p;e.style.opacity='1';} }", phase)


async def hide_sub(page):
    await page.evaluate("() => { var e = document.getElementById('demo-subtitle'); if(e) e.style.opacity='0'; }")


async def wait_resp(page, timeout_sec=120):
    for i in range(timeout_sec):
        try:
            t = await page.locator(".typing-indicator").count()
            s = await page.locator(".message-assistant.streaming").count()
            if t == 0 and s == 0 and i > 3:
                return True
            await page.wait_for_timeout(1000)
        except:
            return False
    return False


async def main():
    token = get_jwt_token()
    if not token:
        print("[FAIL] JWT 획득 실패"); return
    print("[OK] 로그인 성공\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized", "--window-size=1920,1080"])
        ctx = await browser.new_context(no_viewport=True)
        from urllib.parse import urlparse
        _host = urlparse(BASE_URL).hostname
        await ctx.add_cookies([{"name": "token", "value": token, "domain": _host, "path": "/", "httpOnly": True, "sameSite": "Lax"}])

        page = await ctx.new_page()
        await page.goto(BASE_URL, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)

        try:
            await page.wait_for_selector("#chat-input", state="visible", timeout=30000)
        except:
            print("[FAIL] Chat UI 로드 실패"); await browser.close(); return

        print("[OK] Chat UI 로드\n")
        await inject_overlay(page)
        await page.wait_for_timeout(500)

        # 장면 번호 할당 (new/sub with phase → 새 장면)
        scene_num = 0
        for s in SCRIPT:
            if s["type"] in ("new",) or (s["type"] == "sub" and s.get("phase")):
                scene_num += 1
                s["_scene"] = scene_num
            else:
                s["_scene"] = scene_num

        print(f"\n[조작] Space/Enter=일시정지/재개, N=다음 장면, 숫자=점프, Q=종료")
        print(f"[장면] 총 {scene_num}개 장면\n")

        step_idx = 0
        while step_idx < len(SCRIPT):
            _paused.wait()  # 일시정지 상태면 대기

            # 점프 요청 체크
            if _jump_to[0] is not None:
                target = _jump_to[0]
                _jump_to[0] = None
                _skip_to_next.clear()
                # 해당 장면 번호의 첫 step으로 점프
                for i, s in enumerate(SCRIPT):
                    if s.get("_scene") == target:
                        step_idx = i
                        break
                continue

            # 스킵 요청 체크
            if _skip_to_next.is_set():
                _skip_to_next.clear()
                current_scene = SCRIPT[step_idx].get("_scene", 0)
                while step_idx < len(SCRIPT) and SCRIPT[step_idx].get("_scene", 0) == current_scene:
                    step_idx += 1
                continue

            step = SCRIPT[step_idx]
            step_idx += 1
            t = step["type"]

            # ── 자막 표시 ──
            if t == "sub":
                try:
                    sc = step.get('_scene', 0)
                    print(f"[{sc}/{scene_num}] {step.get('phase','')} | {step['text'][:50]}...")
                    await show_sub(page, step["text"], step.get("phase"))
                    await page.wait_for_timeout(step.get("wait", 3) * 1000)
                except:
                    print("[스킵] 브라우저 닫힘"); break

            # ── 대기 ──
            elif t == "pause":
                await page.wait_for_timeout(step["wait"] * 1000)

            # ── 새 대화 ──
            elif t == "new":
                try:
                    await hide_sub(page)
                    btn = page.locator("#btn-new-chat")
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    sc = step.get('_scene', 0)
                    print(f"\n[{sc}/{scene_num}] {step.get('phase','')}")
                    await show_sub(page, step.get("sub", ""), step.get("phase"))
                    await page.wait_for_timeout(3000)
                except:
                    print(f"\n[스킵] 브라우저 닫힘")
                    break

            # ── 액션 (UI 조작) ──
            elif t == "action":
                act = step["action"]

                if act == "light_mode":
                    print("[액션] Light 모드 전환")
                    btn = page.locator("#skin-theme-toggle")
                    # 다크 모드이면 클릭하여 라이트로 전환
                    is_dark = await page.evaluate("() => document.documentElement.classList.contains('dark')")
                    if is_dark:
                        await btn.click()
                    await page.wait_for_timeout(500)

                elif act == "open_dashboard":
                    print("[액션] 대시보드 열기")
                    btn = page.locator("#btn-dashboard")
                    await btn.click()
                    await page.wait_for_timeout(1500)

                elif act == "close_dashboard":
                    print("[액션] 대시보드 닫기")
                    await page.evaluate("""() => {
                        var o = document.getElementById('skin-dashboard-overlay');
                        var d = document.getElementById('skin-dashboard-drawer');
                        if (o) o.classList.remove('open');
                        if (o) o.classList.add('closed');
                        if (d) d.classList.remove('open');
                        if (d) d.classList.add('closed');
                    }""")
                    await page.wait_for_timeout(800)

                elif act == "open_status":
                    print("[액션] 시스템 스테이터스 열기")
                    btn = page.locator("#btn-system-status")
                    await btn.click(timeout=5000)
                    await page.wait_for_timeout(1500)

                elif act == "close_status":
                    print("[액션] 시스템 스테이터스 닫기")
                    await page.evaluate("""() => {
                        // Close ALL drawers/overlays
                        document.querySelectorAll('.open').forEach(function(el) {
                            el.classList.remove('open');
                            el.classList.add('closed');
                        });
                    }""")
                    await page.wait_for_timeout(800)

                elif act == "google_login":
                    print("[액션] Google 로그인 시작")
                    btn = page.locator("#btn-gws-connect")

                    # Listen for popup (OAuth window)
                    async with ctx.expect_page(timeout=15000) as popup_info:
                        await btn.click(timeout=5000)
                    popup = await popup_info.value
                    await popup.wait_for_load_state("domcontentloaded")
                    print("[액션] OAuth 팝업 열림")

                    try:
                        # Email input
                        email_input = popup.locator('input[type="email"]')
                        await email_input.wait_for(state="visible", timeout=10000)
                        await email_input.fill("jeffrey@skin1004korea.com")
                        await popup.wait_for_timeout(500)
                        # Click Next
                        next_btn = popup.locator('#identifierNext, button:has-text("다음"), button:has-text("Next")')
                        await next_btn.click()
                        print("[액션] 이메일 입력 완료")
                        await popup.wait_for_timeout(3000)

                        # Password input
                        pwd_input = popup.locator('input[type="password"]')
                        await pwd_input.wait_for(state="visible", timeout=10000)
                        await pwd_input.fill("skin1004!")
                        await popup.wait_for_timeout(500)
                        # Click Next
                        pwd_next = popup.locator('#passwordNext, button:has-text("다음"), button:has-text("Next")')
                        await pwd_next.click()
                        print("[액션] 비밀번호 입력 완료")

                        # Wait for popup to close or redirect
                        await popup.wait_for_timeout(5000)

                        # If "Allow" consent screen appears, click it
                        try:
                            allow_btn = popup.locator('button:has-text("허용"), button:has-text("Allow"), button:has-text("Continue")')
                            if await allow_btn.count() > 0:
                                await allow_btn.first.click()
                                print("[액션] 권한 허용 클릭")
                                await popup.wait_for_timeout(3000)
                        except:
                            pass

                    except Exception as e:
                        print(f"[액션] 자동 로그인 중 대기: {e}")
                        # Fallback: wait for manual login
                        await page.wait_for_timeout(20000)

                    # Wait for main page to reflect login
                    await page.wait_for_timeout(3000)
                    print("[액션] Google 로그인 완료")

            # ── 메시지 전송 ──
            elif t == "send":
                q = step["q"]
                sub_text = step.get("sub", "")
                try:
                    await show_sub(page, sub_text)
                    await page.wait_for_timeout(1500)

                    chat_input = page.locator("#chat-input")
                    await chat_input.fill(q)
                    await page.wait_for_timeout(400)

                    t0 = time.time()
                    send_btn = page.locator("#btn-send")
                    await send_btn.click()
                    print(f"  [전송] {q}")
                except Exception as e:
                    print(f"  [스킵] 브라우저 닫힘: {q[:30]}")
                    continue

                await page.wait_for_timeout(3000)
                done = await wait_resp(page, timeout_sec=120)
                elapsed = time.time() - t0

                # 결과 확인
                ai = page.locator(".message-assistant")
                cnt = await ai.count()
                content_len = 0
                has_table = False
                has_chart = False
                if cnt > 0:
                    try:
                        c = await ai.last.locator(".message-content").text_content(timeout=5000)
                        content_len = len(c) if c else 0
                    except:
                        pass
                    has_table = await ai.last.locator("table").count() > 0
                    has_chart = await ai.last.locator(".chart-container").count() > 0

                extras = []
                if has_table: extras.append("표")
                if has_chart: extras.append("차트")
                ex = f" [{', '.join(extras)}]" if extras else ""
                print(f"  [응답] {elapsed:.0f}초, {content_len}자{ex}")

                result_sub = f'응답 완료: <span class="hi">{elapsed:.0f}초</span>'
                if has_table: result_sub += ' · 표'
                if has_chart: result_sub += ' · 차트'
                await show_sub(page, result_sub)
                await page.wait_for_timeout(2500)

        try:
            await hide_sub(page)
        except:
            pass
        print("\n[완료] 시연 종료")

        try:
            await page.wait_for_timeout(300000)
        except:
            pass
        try:
            await browser.close()
        except:
            pass


if __name__ == "__main__":
    asyncio.run(main())
