"""Browser tests against a Claré these tests start themselves.

Opt-in, like the network tests, and for the same reason: a suite that reaches
the network should never be what breaks an offline build.

    CLARE_BROWSER_TESTS=1 uv run pytest tests/browser
    uv run python -m app.browser_tests            # same thing, one command

Nothing has to be running first. The session fixture starts the real
application entrypoint on a free port, polls until it answers, and stops it
afterwards — including when a test fails. Set ``CLARE_BROWSER_URL`` to point
at a server you started yourself and the fixture leaves it alone.

Console errors fail the test that produced them. That is the point: a page can
look perfect in a screenshot while its JavaScript has thrown, and every
interaction after that silently does nothing. This project shipped exactly
that bug once.

When a test does fail, everything needed to understand it is written to
``artifacts/browser/`` — screenshot, HTML, console, failed requests, and a
Playwright trace when tracing is on.
"""

from __future__ import annotations

import contextlib
import json
import os
import re

import pytest

from app.browser_tests import ARTIFACTS, serve

#: Warnings that are somebody else's and harmless. Anything not listed here
#: fails the test, so this list stays short and each entry is a decision.
BENIGN = (
    "favicon",
    "download the react devtools",
    "was preloaded using link preload",
)


def _enabled() -> bool:
    return os.environ.get("CLARE_BROWSER_TESTS") == "1"


def _tracing() -> bool:
    return (os.environ.get("CLARE_BROWSER_TRACE") == "1"
            or os.environ.get("CI", "").lower() in {"1", "true", "yes"})


def pytest_collection_modifyitems(config, items):
    if _enabled():
        return
    skip = pytest.mark.skip(
        reason="set CLARE_BROWSER_TESTS=1 for browser tests "
               "(they start the app themselves)")
    for item in items:
        if "browser" in str(item.fspath):
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Record each phase's outcome so fixtures can see it during teardown."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rp_{report.when}", report)


@pytest.fixture(scope="session")
def clare_app():
    """The application under test, started here unless one was given."""
    if not _enabled():
        pytest.skip("browser tests are opt-in")

    external = os.environ.get("CLARE_BROWSER_URL", "").strip()
    if external:
        yield type("Given", (), {"base_url": external,
                                 "output": staticmethod(
                                     lambda limit=60: "(server started "
                                                      "outside the suite)")})()
        return

    try:
        with serve() as server:
            yield server
    except (RuntimeError, TimeoutError) as exc:
        pytest.fail(f"could not start Claré for the browser suite: {exc}")


@pytest.fixture(scope="session")
def browser_context(clare_app):
    if not _enabled():
        pytest.skip("browser tests are opt-in")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed; `uv sync --extra render`")

    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        try:
            context = browser.new_context(locale="en-GB")
            context.set_default_timeout(20_000)
            if _tracing():
                context.tracing.start(screenshots=True, snapshots=True)
            yield context
        finally:
            # Closing from a finally is what keeps a failed run from leaving
            # a Chromium behind holding the profile directory.
            browser.close()


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]


def _save_failure(page, request, clare_app, console, requests_failed) -> list[str]:
    """Everything a person needs to understand a browser failure, on disk."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stem = _slug(request.node.name)
    written: list[str] = []

    def record(suffix: str, write) -> None:
        path = ARTIFACTS / f"{stem}{suffix}"
        try:
            write(path)
        except Exception as exc:  # noqa: BLE001 - a failing dump must not mask the failure
            written.append(f"{path.name}: not saved ({exc})")
        else:
            written.append(str(path))

    record(".png", lambda p: page.screenshot(path=str(p), full_page=True))
    record(".html", lambda p: p.write_text(page.content(), encoding="utf-8"))
    record(".console.txt",
           lambda p: p.write_text("\n".join(console) or "(no console output)",
                                  encoding="utf-8"))
    record(".network.json",
           lambda p: p.write_text(json.dumps(requests_failed, indent=2),
                                  encoding="utf-8"))
    record(".app.log", lambda p: p.write_text(clare_app.output(200),
                                              encoding="utf-8"))
    if _tracing():
        record(".trace.zip",
               lambda p: page.context.tracing.stop_chunk(path=str(p)))
    return written


@pytest.fixture
def page(browser_context, clare_app, request):
    """A page whose console errors fail the test, and which explains itself."""
    page = browser_context.new_page()
    problems: list[str] = []
    console: list[str] = []
    requests_failed: list[dict] = []

    def note(message):
        console.append(f"[{message.type}] {message.text}")
        if message.type != "error":
            return
        text = (message.text or "").lower()
        if any(ok in text for ok in BENIGN):
            return
        problems.append(message.text)

    page.on("console", note)
    page.on("pageerror", lambda exc: problems.append(f"pageerror: {exc}"))
    page.on("requestfailed", lambda req: requests_failed.append(
        {"url": req.url, "method": req.method,
         "failure": (req.failure or "")}))
    page.on("response", lambda res: requests_failed.append(
        {"url": res.url, "status": res.status}) if res.status >= 400 else None)

    if _tracing():
        page.context.tracing.start_chunk(title=request.node.name)

    page.goto(clare_app.base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#rp-question textarea", timeout=20_000)
    page.console_problems = problems

    yield page

    setup = getattr(request.node, "rp_setup", None)
    call = getattr(request.node, "rp_call", None)
    failed = (setup is not None and setup.failed) or \
             (call is not None and call.failed) or bool(problems)

    # Nothing in this teardown may turn a passing test into an error. The one
    # deliberate failure is the console-error assertion at the end; a browser
    # that died, a screenshot that would not save or a closed context are
    # reported and stepped over. (A full run once produced teardown errors on
    # tests that had passed, and a diagnostic that breaks the run it is
    # diagnosing is worse than no diagnostic.)
    if failed:
        try:
            saved = _save_failure(page, request, clare_app, console,
                                  requests_failed)
            print(f"\n--- browser failure: {request.node.name}")
            print(f"    url:        {clare_app.base_url}")
            print(f"    artifacts:  " + "\n                ".join(saved))
            if requests_failed:
                print(f"    requests:   {len(requests_failed)} failed or 4xx/5xx")
                for entry in requests_failed[:5]:
                    print(f"                {entry}")
            print("    app output:")
            for line in clare_app.output(40).splitlines():
                print(f"                {line}")
        except Exception as exc:  # noqa: BLE001 - see note above
            print(f"\n--- browser failure: {request.node.name} "
                  f"(artifacts could not be collected: {exc})")
    elif _tracing():
        with contextlib.suppress(Exception):
            page.context.tracing.stop_chunk()

    with contextlib.suppress(Exception):
        page.close()
    assert not problems, "console errors:\n" + "\n".join(problems[:5])


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
