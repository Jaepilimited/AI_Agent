import time, json, sys, io
import requests as r
from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
BASE = "http://127.0.0.1:3001"
MAX_WAIT = 150

results = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width":1280,"height":900})
    ctx.set_default_timeout(60000)
    pg = ctx.new_page()
    resp = r.post(f"{BASE}/api/auth/signin", json={
        "department":"Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석",
        "name":"임재필",
        "password":"1234"
    })
    print(f"Login status: {resp.status_code}")
    token = resp.cookies.get("token")
    if not token:
        print("NO TOKEN - aborting")
        sys.exit(1)
    ctx.add_cookies([{"name":"token","value":token,"domain":"127.0.0.1","path":"/"}])
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(5000)

    def test(q, label):
        print(f"\n=== {label}: {q} ===")
        try:
            pg.locator("#btn-new-chat").click()
            pg.wait_for_timeout(1500)
        except Exception as e:
            print(f"new-chat click err: {e}")
        inp = pg.locator("textarea#chat-input").first
        inp.click()
        inp.fill(q)
        pg.wait_for_timeout(300)
        pg.locator("#btn-send").click(force=True)
        pg.wait_for_timeout(3000)
        start = time.time()
        prev, stable = "", 0
        ans = None
        while time.time() - start < MAX_WAIT:
            msgs = pg.locator(".message.message-assistant").all()
            if msgs:
                try:
                    cur = msgs[-1].inner_text()
                except Exception:
                    cur = ""
                if cur == prev and len(cur) > 10:
                    stable += 1
                    if stable >= 3:
                        ans = cur
                        break
                else:
                    stable = 0
                prev = cur
            pg.wait_for_timeout(2000)
        entry = {"label": label, "q": q}
        if ans:
            chips = pg.locator(".followup-chip").all()
            chip_texts = []
            for c in chips:
                try:
                    chip_texts.append(c.inner_text())
                except Exception:
                    pass
            entry["chars"] = len(ans)
            entry["chip_count"] = len(chip_texts)
            entry["chips"] = chip_texts
            entry["has_placeholder"] = ("[후속 질문" in ans) or ("[플레이스홀더" in ans) or ("[구체적 후속" in ans)
            entry["answer_tail"] = ans[-800:]
            print(f"Chars: {len(ans)}, Chips: {len(chip_texts)}")
            for i, t in enumerate(chip_texts):
                print(f"  Chip{i+1}: {t}")
            if entry["has_placeholder"]:
                print("  !! PLACEHOLDER LEAK")
        else:
            entry["error"] = "NO RESPONSE"
            print("  NO RESPONSE")
        results.append(entry)

    test("@@매출 이번달 총 매출 알려줘", "BQ")
    test("@@CS 센텔라 앰플 사용법", "CS")
    test("SKIN1004 본사 위치", "Direct")
    b.close()

with open("scripts/playwright_followup_results.json","w",encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("\n=== SAVED to scripts/playwright_followup_results.json ===")
