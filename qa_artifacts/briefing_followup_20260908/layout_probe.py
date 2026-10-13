"""Capture the mail scroll regression using the production frontend files."""
import argparse
import importlib.util
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("before", "after"))
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "briefing_layout_tests", ROOT / "tests/frontend/test_briefing_document.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    measurements = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for name, width, mail_count in (("desktop", 1400, 24), ("mobile", 390, 24),
                                         ("short_mail", 1400, 1)):
            page = browser.new_page(viewport={"width": width, "height": 1200})
            fixtures._mount(page, fixtures._mail_scroll_payload(mail_count))
            if args.phase == "after" and name == "desktop":
                fixtures._wait_for_mail_to_match_right(page)
            page.locator("#personal-briefing").screenshot(path=str(OUT / f"{name}_{args.phase}.png"))
            measurements[name] = fixtures._mail_geometry(page)
            page.close()
        browser.close()
    (OUT / f"layout_{args.phase}.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
    print(json.dumps(measurements))


if __name__ == "__main__":
    main()
