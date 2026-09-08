"""Browser-level tests for Cella's answer loading progress UI."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOADER_SCRIPT = PROJECT_ROOT / "app" / "frontend" / "answer-loading.js"


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    current = context.new_page()
    current.set_content('<div class="typing-indicator"></div>')
    yield current
    context.close()


@pytest.fixture(scope="module")
def app_server():
    handler = partial(QuietStaticHandler, directory=str(PROJECT_ROOT / "app"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def load_progress_renderer(page):
    assert LOADER_SCRIPT.exists(), "answer loading progress renderer is missing"
    page.add_script_tag(path=str(LOADER_SCRIPT))


def test_loading_indicator_is_indeterminate_and_reports_elapsed_time(page):
    """Unknown completion time must not be presented as a real percentage."""
    load_progress_renderer(page)
    page.evaluate(
        """() => window.CellaAnswerLoading.render(
            document.querySelector('.typing-indicator'), 'direct', 15
        )"""
    )

    assert page.locator(".answer-loading-label").inner_text() == "생각하는 중"
    assert page.locator(".answer-loading-percent").count() == 0
    progress = page.get_by_role("progressbar")
    assert progress.get_attribute("aria-valuenow") is None
    assert progress.get_attribute("aria-valuetext") == "답변 생성 중"
    assert progress.locator(".answer-loading-fill").get_attribute("style") is None
    assert page.locator(".answer-loading-elapsed").inner_text() == "0초 경과"


def test_server_route_updates_stage_without_resetting_elapsed_time(page):
    """A server-confirmed route updates the label, not a fake percentage."""
    load_progress_renderer(page)
    indicator = page.locator(".typing-indicator")
    page.evaluate(
        """() => window.CellaAnswerLoading.render(
            document.querySelector('.typing-indicator'), 'direct', 15
        )"""
    )
    page.wait_for_timeout(1100)
    page.evaluate(
        """() => window.CellaAnswerLoading.render(
            document.querySelector('.typing-indicator'), 'bigquery', 60
        )"""
    )

    assert indicator.locator(".answer-loading-label").inner_text() == "데이터 확인 중"
    assert indicator.locator(".answer-loading-percent").count() == 0
    assert indicator.get_by_role("progressbar").get_attribute("aria-valuenow") is None
    elapsed = indicator.locator(".answer-loading-elapsed").inner_text()
    assert int(elapsed.removesuffix("초 경과")) >= 1


def test_destroy_stops_elapsed_clock_without_showing_total_time(page):
    """Finishing an answer leaves neither a timer nor a completion duration."""
    load_progress_renderer(page)
    page.evaluate(
        """() => window.CellaAnswerLoading.render(
            document.querySelector('.typing-indicator'), 'direct', 15
        )"""
    )
    page.wait_for_timeout(1100)
    before = page.locator(".answer-loading-elapsed").inner_text()

    page.evaluate(
        """() => window.CellaAnswerLoading.destroy(
            document.querySelector('.typing-indicator')
        )"""
    )
    page.wait_for_timeout(1100)

    assert page.locator(".answer-loading-elapsed").inner_text() == before
    assert page.locator(".answer-loading-total").count() == 0


def test_cella_chat_page_loads_the_progress_renderer_and_gauge_styles(browser, app_server):
    """The shipped chat page loads the real renderer and visible gauge CSS."""
    context = browser.new_context()
    current = context.new_page()
    current.route("**/frontend/chat.js*", lambda route: route.abort())
    current.goto(f"{app_server}/frontend/chat.html", wait_until="domcontentloaded")

    assert current.evaluate("typeof window.CellaAnswerLoading?.render") == "function"
    current.evaluate(
        """() => {
            const indicator = document.createElement('div');
            indicator.className = 'typing-indicator';
            document.body.appendChild(indicator);
            window.CellaAnswerLoading.render(indicator, 'direct', 15);
        }"""
    )
    track = current.locator(".answer-loading-track")
    fill = current.locator(".answer-loading-fill")
    assert track.evaluate("el => getComputedStyle(el).height") == "4px"
    assert fill.evaluate("el => getComputedStyle(el).backgroundColor") != "rgba(0, 0, 0, 0)"

    context.close()
