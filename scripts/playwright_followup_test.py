"""Test follow-up question quality on port 3001 (dev)."""
import json
import time
import requests as r
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3001"
RESULTS = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    ctx.set_default_timeout(60000)
    pg = ctx.new_page()
    resp = r.post(
        f"{BASE}/api/auth/signin",
        json={
            "department": "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석",
            "name": "임재필",
            "password": "1234",
        },
    )
    print(f"Login status: {resp.status_code}")
    token = resp.cookies.get("token")
    if not token:
        print("LOGIN FAILED")
        print(resp.text)
        b.close()
        raise SystemExit(1)
    ctx.add_cookies(
        [
            {
                "name": "token",
                "value": token,
                "domain": "127.0.0.1",
                "path": "/",
            }
        ]
    )
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(5000)

    def test_q(q, route_hint):
        print(f"\n=== {route_hint}: {q} ===")
        try:
            pg.locator("#btn-new-chat").click()
        except Exception as e:
            print(f"new-chat click error: {e}")
        pg.wait_for_timeout(1500)
        inp = pg.locator("textarea#chat-input").first
        inp.click()
        inp.fill(q)
        pg.wait_for_timeout(300)
        pg.locator("#btn-send").click(force=True)

        pg.wait_for_timeout(3000)
        start = time.time()
        prev, stable = "", 0
        ans = None
        timeout_s = 180 if "매출" in q else 90
        while time.time() - start < timeout_s:
            msgs = pg.locator(".message.message-assistant").all()
            if msgs:
                cur = msgs[-1].inner_text()
                if cur == prev and len(cur) > 10:
                    stable += 1
                    if stable >= 3:
                        ans = cur
                        break
                else:
                    stable = 0
                prev = cur
            pg.wait_for_timeout(2000)

        record = {"route": route_hint, "query": q, "answer": ans or "", "chips": []}
        if ans:
            chips = pg.locator(".followup-chip, .followup-suggestions button").all()
            print(f"Answer length: {len(ans)} chars")
            print(f"Answer tail:\n{ans[-500:]}")
            print(f"Chips count: {len(chips)}")
            for i, chip in enumerate(chips):
                try:
                    txt = chip.inner_text().strip()
                except Exception:
                    txt = "(unreadable)"
                print(f"  Chip {i+1}: {txt}")
                record["chips"].append(txt)
            has_placeholder = (
                "[후속 질문" in ans
                or "[followup" in ans.lower()
                or "[구체적 후속" in ans
            )
            print(f"Has placeholder in answer: {has_placeholder}")
            record["has_placeholder"] = has_placeholder
        else:
            print("NO RESPONSE")
            record["has_placeholder"] = None
        RESULTS.append(record)

    test_q("@@매출 이번달 총 매출 알려줘", "BQ route")
    test_q("@@CS 센텔라 앰플 사용법", "CS route")
    test_q("SKIN1004 본사 위치", "Direct route")

    b.close()

with open(
    r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\scripts\playwright_followup_results.json",
    "w",
    encoding="utf-8",
) as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2)

print("\n=== DONE ===")
print(f"Saved results to scripts/playwright_followup_results.json")
