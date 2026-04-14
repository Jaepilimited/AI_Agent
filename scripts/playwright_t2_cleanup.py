"""T2 cleanup test — switch conversations, verify no JS errors."""
import requests as r
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3001"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context()
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    resp = r.post(
        f"{BASE}/api/auth/signin",
        json={
            "department": "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석",
            "name": "임재필",
            "password": "1234",
        },
    )
    print(f"signin status: {resp.status_code}")
    tok = resp.cookies.get("token")
    if not tok:
        print("No token obtained")
        print(resp.text[:500])
        b.close()
        raise SystemExit(1)
    ctx.add_cookies(
        [{"name": "token", "value": tok, "domain": "127.0.0.1", "path": "/"}]
    )
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(5000)
    convos = pg.locator("#convo-list .convo-item, #convo-list li")
    count = convos.count()
    print(f"convo count: {count}")
    if count > 1:
        convos.nth(0).click()
        pg.wait_for_timeout(2000)
        convos.nth(1).click()
        pg.wait_for_timeout(2000)
        # Switch back and forth a few more times to stress-test cleanup
        convos.nth(0).click()
        pg.wait_for_timeout(1500)
        convos.nth(1).click()
        pg.wait_for_timeout(1500)
    print(f"Errors: {len(errs)}")
    for e in errs[:10]:
        print(f"  {e}")
    b.close()
