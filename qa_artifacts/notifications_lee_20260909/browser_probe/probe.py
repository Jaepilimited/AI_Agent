"""Isolated browser evidence for the notification drawer. No production calls."""
import hashlib
import io
import json
import mimetypes
import sys
import ast
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ITEMS = [
    {"type": "announcement", "id": 1, "title": "공지 알림 재현", "note": "공지 내용이 표시되는지 확인합니다.", "seen": False,
     "url": "", "created_at": "2026-09-09T08:00:00+09:00"},
    {"type": "briefing", "id": 2, "title": "업무 브리핑 재현", "note": "브리핑 내용이 표시되는지 확인합니다.", "seen": False,
     "url": "", "follow_up": "이번 주 매출을 알려줘", "created_at": "2026-09-09T08:10:00+09:00"},
]
ME = {"id": 4242, "email": "notification-probe@example.invalid", "name": "알림 검사", "department": "OP",
      "role": "user", "can_view_fi": False, "must_change_password": False, "allowed_models": ["skin1004-Analysis"],
      "brand_filters": [], "my_brand_filter": None, "group_assignment_required": False}
SNAPSHOT = """() => {
  const describe = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    const x = Math.max(0, Math.min(innerWidth - 1, r.x + r.width / 2));
    const y = Math.max(0, Math.min(innerHeight - 1, r.y + r.height / 2));
    const hit = document.elementFromPoint(x, y);
    return {selector, className: el.className, text: el.innerText, html: el.innerHTML,
      rect: {x:r.x,y:r.y,width:r.width,height:r.height},
      dimensions:{clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,scrollTop:el.scrollTop},
      style:{display:s.display,visibility:s.visibility,opacity:s.opacity,transform:s.transform,zIndex:s.zIndex,
        color:s.color,background:s.backgroundColor,pointerEvents:s.pointerEvents,overflowY:s.overflowY,
        height:s.height,minHeight:s.minHeight,flexShrink:s.flexShrink,flexBasis:s.flexBasis},
      centerHit: hit ? {tag:hit.tagName,id:hit.id,className:hit.className} : null};
  };
  return {url:location.href,viewport:{width:innerWidth,height:innerHeight},
    elements:['#notif-badge','#btn-notifications','#notif-overlay','#notif-drawer','#notif-items','.notif-item','.notif-note'].map(describe)};
}"""


def run(browser, name, width=1440, height=1000, theme="light", fail_second=False, items=None):
    items = ITEMS if items is None else items
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script("localStorage.setItem('skin1004-cella-origin-invited-v1','1'); localStorage.setItem('theme'," + json.dumps(theme) + ");")
    page = context.new_page()
    page.set_default_timeout(8000)
    calls, errors, console = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: console.append({"type": message.type, "text": message.text}))
    count = 0

    def handler(route):
        nonlocal count
        req = route.request
        url = urlsplit(req.url)
        path = url.path
        calls.append({"method": req.method, "path": path, "type": req.resource_type})
        if url.netloc == "notification-probe.invalid" and path in ("/", "/chat"):
            route.fulfill(status=200, content_type="text/html", body=(ROOT / "app/frontend/chat.html").read_bytes())
            return
        if url.netloc == "notification-probe.invalid" and path.startswith(("/static/", "/frontend/")):
            asset = ROOT / "app" / path.lstrip("/")
            if asset.is_file():
                route.fulfill(status=200, content_type=mimetypes.guess_type(path)[0] or "application/octet-stream", body=asset.read_bytes())
                return
        if path == "/api/notifications":
            count += 1
            if fail_second and count > 1:
                calls[-1]["status"] = 503
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail":"Controlled notification request failure"}))
                return
            calls[-1]["status"] = 200
            data = {"unseen": sum(not item["seen"] for item in items), "items": items}
        elif path == "/api/auth/me":
            data = ME
        elif path in ("/api/conversations", "/api/datasources", "/api/saved-questions", "/api/conversations/feedback/replies"):
            data = []
        elif path == "/api/notifications/viewed":
            data = {"ok": True}
        elif req.resource_type == "stylesheet":
            route.fulfill(status=200, content_type="text/css", body="")
            return
        elif req.resource_type == "script":
            route.fulfill(status=200, content_type="application/javascript", body="")
            return
        else:
            data = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    page.route("**/*", handler)
    page.goto("http://notification-probe.invalid/chat", wait_until="domcontentloaded")
    page.wait_for_function("document.getElementById('notif-badge').textContent === '2'")
    if width < 768:
        page.locator("#btn-menu").click()
    before = page.evaluate(SNAPSHOT)
    page.screenshot(path=str(OUT / (name + "-before.png")))
    page.locator("#btn-notifications").click()
    page.wait_for_function("document.getElementById('notif-drawer').classList.contains('open')")
    if not fail_second:
        page.wait_for_function("n => document.querySelectorAll('#notif-items .notif-item').length === n", arg=len(items))
    page.wait_for_timeout(400)
    after = page.evaluate(SNAPSHOT)
    page.screenshot(path=str(OUT / (name + "-after.png")))
    result = {"scenario": name, "fail_second": fail_second, "before": before, "after": after,
              "calls": calls, "page_errors": errors, "console": console,
              "item_count": page.locator("#notif-items .notif-item").count(),
              "items_text": page.locator("#notif-items").inner_text()}
    if not fail_second:
        before_calls = len(calls)
        before_pages = len(context.pages)
        page.locator("#notif-items .notif-text").first.click()
        page.wait_for_timeout(100)
        result["click_first_title"] = {"new_calls": calls[before_calls:], "new_pages": len(context.pages) - before_pages,
                                         "snapshot": page.evaluate(SNAPSHOT)}
        page.locator("#notif-items").evaluate("el => el.scrollTop = el.scrollHeight")
        result["scrolled_to_bottom"] = page.evaluate(SNAPSHOT)
        page.screenshot(path=str(OUT / (name + "-bottom.png")))
    context.close()
    return result


def production_payload():
    """Run only the exact production response mapping over captured rows, without importing app code."""
    evidence = json.loads((OUT.parent / "production_evidence.json").read_text(encoding="utf-8"))
    records = evidence["notifications"]["34"]
    for rows in records.values():
        for row in rows:
            for key in ("created_at", "handled_at", "seen_at"):
                if row.get(key):
                    row[key] = datetime.fromisoformat(row[key])
    source = (OUT.parent / "baseline/app/api/notifications_api.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("_neg", "list_notifications"):
            node.decorator_list = []
            selected.append(node)
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_FB_LABEL" for target in node.targets):
            selected.append(node)
    namespace = {
        "asyncio": asyncio, "User": object, "Depends": lambda _arg: None, "get_current_user": lambda: None,
        "store": SimpleNamespace(list_notifications=lambda _uid: records["shares"]),
        "feedback_inbox": SimpleNamespace(my_feedback=lambda _uid: records["feedback"]),
        "announcements": SimpleNamespace(for_user=lambda _uid: records["announcements"]),
        "briefing": SimpleNamespace(for_user=lambda _uid: records["briefings"]),
        "group_alerts": SimpleNamespace(for_user=lambda _uid: []),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "production-response-mapping", "exec"), namespace)
    payload = asyncio.run(namespace["list_notifications"](SimpleNamespace(id=34)))
    assert payload["unseen"] == 2 and len(payload["items"]) == 20
    return payload


def main():
    actual = production_payload()
    evidence = {"assets": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                             for name in ("app/frontend/chat.html", "app/frontend/chat.js", "app/static/style.css")},
                "results": []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, options in [
                ("desktop-light", {}),
                ("desktop-dark", {"theme": "dark"}),
                ("desktop-click-api-failure", {"fail_second": True}),
                ("production20-desktop-light", {"items": actual["items"]}),
                ("production20-desktop-short", {"items": actual["items"], "height": 720}),
                ("production20-mobile390", {"items": actual["items"], "width": 390, "height": 844}),
                ("production20-mobile360", {"items": actual["items"], "width": 360, "height": 780}),
        ]:
            evidence["results"].append(run(browser, name, **options))
        browser.close()
    (OUT / "evidence-extended.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    for result in evidence["results"]:
        print(json.dumps({key: result[key] for key in ("scenario", "item_count", "page_errors", "console")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
