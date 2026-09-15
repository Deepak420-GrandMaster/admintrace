"""Browser tests against a running Claré.

Opt-in, like the network tests, and for the same reason: a suite that fails
because a server was not running teaches nobody anything. Start the app, then

    CLARE_BROWSER_TESTS=1 uv run pytest tests/browser

These drive the real interface with the Playwright already installed for
rendered sources — there is deliberately no second browser stack.

Console errors fail the test that produced them. That is the point: a page can
look perfect in a screenshot while its JavaScript has thrown, and every
interaction after that silently does nothing. This project shipped exactly
that bug once.
"""

from __future__ import annotations

import os

import pytest

BASE_URL = os.environ.get("CLARE_BROWSER_URL", "http://127.0.0.1:7860")

#: Warnings that are somebody else's and harmless. Anything not listed here
#: fails the test, so this list stays short and each entry is a decision.
BENIGN = (
    "favicon",
    "download the react devtools",
    "was preloaded using link preload",
)


def _enabled() -> bool:
    return os.environ.get("CLARE_BROWSER_TESTS") == "1"


def pytest_collection_modifyitems(config, items):
    if _enabled():
        return
    skip = pytest.mark.skip(
        reason="set CLARE_BROWSER_TESTS=1 (and run the app) for browser tests")
    for item in items:
        if "browser" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def browser_context():
    if not _enabled():
        pytest.skip("browser tests are opt-in")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed; `uv sync --extra render`")

    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        context = browser.new_context(locale="en-GB")
        context.set_default_timeout(20_000)
        yield context
        browser.close()


@pytest.fixture
def page(browser_context):
    """A page whose console errors fail the test."""
    page = browser_context.new_page()
    problems: list[str] = []

    def note(message):
        if message.type != "error":
            return
        text = (message.text or "").lower()
        if any(ok in text for ok in BENIGN):
            return
        problems.append(message.text)

    page.on("console", note)
    page.on("pageerror", lambda exc: problems.append(f"pageerror: {exc}"))

    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_selector("#rp-question textarea", timeout=20_000)
    page.console_problems = problems
    yield page

    assert not problems, "console errors:\n" + "\n".join(problems[:5])
    page.close()


@pytest.fixture
def sized(page):
    """Resize helper that waits for the layout to settle."""
    def resize(width: int, height: int = 900):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(350)
        return page
    return resize


def no_horizontal_overflow(page) -> tuple[bool, str]:
    result = page.evaluate("""() => {
        const d = document.documentElement;
        const over = [...document.querySelectorAll('*')].filter(e => {
            const r = e.getBoundingClientRect();
            return r.right > d.clientWidth + 1 || r.left < -1;
        }).slice(0, 5).map(e => e.tagName + '.' + (e.className || '').toString().slice(0, 30));
        return {scrolls: d.scrollWidth > d.clientWidth, offscreen: over};
    }""")
    ok = not result["scrolls"] and not result["offscreen"]
    return ok, f"scrolls={result['scrolls']} offscreen={result['offscreen']}"


def small_touch_targets(page) -> list[dict]:
    """Controls a finger has to hit, measured on the element it actually hits.

    In a Gradio radio group that is the ``<label>``; the span inside it is
    text and is always smaller. Measuring the span reports a failure that is
    not there, and hides the one that is.
    """
    return page.evaluate("""() => [...document.querySelectorAll(
        'button, a[href], [role="tab"], .rp-switch label, .rp-cat')]
        .filter(e => {
            const style = getComputedStyle(e);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.height < 44 && r.width < 44;
        })
        .map(e => ({tag: e.tagName, cls: (e.className || '').toString().slice(0, 30),
                    h: Math.round(e.getBoundingClientRect().height),
                    w: Math.round(e.getBoundingClientRect().width)}))""")
