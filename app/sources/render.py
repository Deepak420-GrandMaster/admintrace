"""Reading an official site that only exists once its scripts have run.

Several French state services — ANEF, ANTS, France Travail — serve an empty
shell over HTTP and build the page in the browser. Declaring them unreadable
would be wrong; they are official and they are exactly the pages people need.
Rendering every page in a browser would be wrong too: it is slow, and it means
running someone else's code as a matter of routine.

So this is a second path, not a replacement. Static HTML is read first, and
rendering is attempted only when what came back has no usable content *and*
the domain is already registered and approved. The limits below are what make
that acceptable:

* one approved domain — a top-level navigation off it aborts the render;
* no downloads, no uploads, no form submission, no authentication;
* nothing is typed into the page and no cookie is read as data;
* a hard timeout and a size ceiling;
* images, fonts and media never load, because text is all we want.

It is a reader for public pages, not an agent. It never acts on a site.

Requires the optional ``render`` extra::

    uv sync --extra render
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.config import Settings, get_settings
from app.sources.fetch import USER_AGENT, FetchResult
from app.sources.registry import Source, hostname_allowed

RENDER_TIMEOUT_MS = 25_000
#: How long to let scripts settle after load before reading the DOM.
SETTLE_MS = 2_500
MAX_RENDERED_CHARS = 3_000_000
#: Never loaded: none of it can become text.
BLOCKED_RESOURCES = {"image", "media", "font"}


class RenderUnavailable(RuntimeError):
    """Playwright, or its browser, is not installed."""


def available() -> bool:
    """Whether rendering can be attempted at all in this install."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def render(url: str, *, source: Source, settings: Settings | None = None
           ) -> FetchResult:
    """Render one page from an approved domain and return its HTML.

    Returns a :class:`FetchResult` shaped exactly like the static fetcher's,
    so callers and the version store cannot tell the two apart — except by
    ``content_type``, which is marked so the audit log records how a page was
    read.
    """
    settings = settings or get_settings()
    result = FetchResult(
        url=url,
        retrieved_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        source_id=source.id,
    )

    if not url.startswith("https://"):
        result.error = "refused: only https is rendered"
        return result
    if not hostname_allowed(source, url):
        result.error = (f"refused: {urlsplit(url).hostname!r} is not registered "
                        f"for {source.id}")
        return result
    if not available():
        result.error = ("rendering is not installed: add the optional extra "
                        "with `uv sync --extra render`")
        return result

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    off_domain: list[str] = []

    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    java_script_enabled=True,
                    accept_downloads=False,
                    bypass_csp=False,
                    locale="fr-FR",
                )
                context.set_default_timeout(RENDER_TIMEOUT_MS)

                def gate(route, request):
                    # Subresources may come from a CDN — the page cannot render
                    # without them. A *navigation* away from the approved
                    # domain is the thing that must never happen.
                    if request.resource_type in BLOCKED_RESOURCES:
                        return route.abort()
                    if (request.is_navigation_request()
                            and request.frame == page.main_frame
                            and not hostname_allowed(source, request.url)):
                        off_domain.append(request.url)
                        return route.abort()
                    return route.continue_()

                page = context.new_page()
                page.route("**/*", gate)
                # A download is never content; refuse rather than accept it.
                page.on("download", lambda download: download.cancel())

                response = page.goto(url, wait_until="domcontentloaded",
                                     timeout=RENDER_TIMEOUT_MS)
                result.status = response.status if response else 0
                try:
                    page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
                except PlaywrightTimeout:
                    # A page that never goes idle (polling, analytics) is still
                    # readable; take what it has rather than failing.
                    pass

                final = page.url
                if not hostname_allowed(source, final):
                    result.error = (f"refused: rendering ended on "
                                    f"{urlsplit(final).hostname!r}, which is not "
                                    f"registered for {source.id}")
                    return result

                html = page.content()[:MAX_RENDERED_CHARS]
                result.final_url = final
                result.body = html
                result.bytes_read = len(html.encode("utf-8", errors="replace"))
                result.content_hash = hashlib.sha256(
                    html.encode("utf-8", errors="replace")).hexdigest()
                # Marked, so the audit log says how this page was read.
                result.content_type = "text/html; rendered"
                result.redirects = off_domain
                result.ok = 200 <= result.status < 300 and bool(html.strip())
                if not result.ok and not result.error:
                    result.error = f"http {result.status}"
            finally:
                browser.close()
    except PlaywrightTimeout:
        result.error = f"render timed out after {RENDER_TIMEOUT_MS // 1000}s"
    except PlaywrightError as exc:
        result.error = f"render failed: {str(exc).splitlines()[0][:200]}"
    except Exception as exc:  # noqa: BLE001 - a render must never take the app down
        result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    return result
