"""Capture the corrected states and compare 401 behavior with preserved scripts."""
import hashlib
import importlib.util
import json
import subprocess

from playwright.sync_api import expect, sync_playwright

from probe import OUT, ROOT, SNAPSHOT, production_payload, run


spec = importlib.util.spec_from_file_location("notification_test_harness", ROOT / "tests/frontend/test_notifications_drawer.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class ScriptNotificationPage(harness.NotificationPage):
    def __init__(self, page, script):
        self.script = script
        super().__init__(page)

    def route(self, route):
        if route.request.url == "http://notifications.test/frontend/chat.js":
            route.fulfill(status=200, content_type="application/javascript", body=self.script)
            return
        super().route(route)


def main():
    historical = subprocess.check_output(["git", "show", "865df99^:app/frontend/chat.js"], cwd=ROOT)
    baseline = (OUT.parent / "baseline/app/frontend/chat.js").read_bytes()
    fixed = (ROOT / "app/frontend/chat.js").read_bytes()
    actual_payload = production_payload()
    evidence = {"corrected_states": [], "auth_matrix": []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        ui = harness.NotificationPage(page)
        ui.open("pending")
        expect(page.locator("#notif-items")).to_contain_text("불러오는 중")
        page.screenshot(path=str(OUT / "fixed-loading.png"), animations="disabled")
        evidence["corrected_states"].append({"state": "loading", "viewed": list(ui.viewed), "snapshot": page.evaluate(SNAPSHOT)})
        ui.pending.popleft().fulfill(status=503, body="Unavailable")
        expect(page.locator("#notif-items")).to_contain_text("알림을 불러오지 못했습니다")
        page.screenshot(path=str(OUT / "fixed-error.png"), animations="disabled")
        evidence["corrected_states"].append({"state": "error", "viewed": list(ui.viewed), "snapshot": page.evaluate(SNAPSHOT)})
        page.locator("#notif-items").get_by_role("button", name="다시 시도").click()
        expect(page.locator("#notif-items .notif-item")).to_have_count(2)
        ui.settle()
        page.screenshot(path=str(OUT / "fixed-retry-success.png"), animations="disabled")
        evidence["corrected_states"].append({"state": "retry-success", "viewed": list(ui.viewed), "snapshot": page.evaluate(SNAPSHOT)})
        page.unroute_all(behavior="wait")
        context.close()

        for label, script in (("historical-pre-entra", historical), ("baseline-with-wrapper", baseline), ("fixed", fixed)):
            for failure in ("401", "401-header"):
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                ui = ScriptNotificationPage(page, script)
                ui.open(failure)
                expected_login = label == "fixed" or (label == "baseline-with-wrapper" and failure == "401-header")
                if expected_login:
                    expect(page).to_have_url("http://notifications.test/login")
                else:
                    ui.settle()
                    expect(page.locator("#notif-drawer")).to_have_class("open")
                page.screenshot(path=str(OUT / ("auth-" + label + "-" + failure + ".png")), animations="disabled")
                evidence["auth_matrix"].append({
                    "script": label, "script_sha256": hashlib.sha256(script).hexdigest(), "failure": failure,
                    "url": page.url, "viewed": list(ui.viewed), "snapshot": page.evaluate(SNAPSHOT),
                })
                page.unroute_all(behavior="wait")
                context.close()

        evidence["production20"] = run(browser, "fixed-production20-desktop", height=720,
                                        items=actual_payload["items"])
        browser.close()
    (OUT / "fixed-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"auth_matrix": [{key: row[key] for key in ("script", "failure", "url", "viewed")}
                                     for row in evidence["auth_matrix"]],
                      "fixed_states": [{key: row[key] for key in ("state", "viewed")} for row in evidence["corrected_states"]],
                      "production20_item_count": evidence["production20"]["item_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
