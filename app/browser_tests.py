"""Run the browser suite against an application this module starts itself.

A test suite that fails because somebody forgot to start a server teaches
nobody anything, and it is the reason browser QA stayed manual here longer
than it should have. So the lifecycle lives in one place and both entry
points use it:

    uv run python -m app.browser_tests      # start, test, stop
    CLARE_BROWSER_TESTS=1 uv run pytest tests/browser

The second works because the session fixture in ``tests/browser/conftest.py``
calls :func:`serve` when no external URL was given. Point
``CLARE_BROWSER_URL`` at a server you started yourself and nothing here runs —
useful when you want to keep one app up across several runs.

The application started is the real entrypoint, ``app.ui.app``, on a free
port. There is no second server implementation to drift from production.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from app.config import PROJECT_ROOT

#: How long to wait for the app to answer before giving up. Polled, never
#: slept through: a fixed sleep is either a slow test or a flaky one.
START_TIMEOUT_S = int(os.environ.get("APP_START_TIMEOUT_SECONDS", "60"))
#: How long a terminate is given before the process group is killed outright.
STOP_GRACE_S = 10
ARTIFACTS = PROJECT_ROOT / "artifacts" / "browser"


def free_port() -> int:
    """A port the OS says is free right now.

    Racy in principle — something could take it between here and launch — but
    far better than a hardcoded port that is reliably already in use on a
    developer's machine or busy on a CI runner.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def chosen_port() -> int:
    configured = os.environ.get("CLARE_TEST_PORT", "").strip()
    return int(configured) if configured else free_port()


@dataclass
class Server:
    """A running Claré, and everything needed to explain it if it misbehaves."""

    base_url: str
    process: subprocess.Popen
    log_path: Path
    _extra: list[str] = field(default_factory=list)

    def output(self, limit: int = 60) -> str:
        """The app's own stdout and stderr, tail-first for a failure report."""
        try:
            lines = self.log_path.read_text(encoding="utf-8",
                                            errors="replace").splitlines()
        except OSError as exc:
            return f"(could not read app log: {exc})"
        return "\n".join(lines[-limit:]) or "(the app printed nothing)"


def _responds(url: str) -> bool:
    try:
        with urlopen(url, timeout=3) as reply:  # noqa: S310 - our own localhost
            return reply.status == 200
    except (URLError, OSError, ValueError):
        return False


def wait_until_ready(base_url: str, process: subprocess.Popen,
                     timeout: float = START_TIMEOUT_S) -> None:
    """Poll until the app serves, or explain why it never did.

    Polling rather than sleeping is the difference between a suite that takes
    as long as the app needs and one that is both slow and occasionally wrong.
    A process that has already exited is reported immediately rather than
    waited out for the full timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"the application exited with code {process.returncode} "
                f"before it began serving")
        if _responds(base_url):
            return
        time.sleep(0.25)
    raise TimeoutError(
        f"the application did not answer on {base_url} within {timeout:g}s "
        f"(raise APP_START_TIMEOUT_SECONDS if this machine is just slow)")


def stop(server: Server) -> None:
    """Shut the app down and leave nothing behind.

    The process is started in its own session so the whole group can be
    signalled: Gradio's reloader and any worker it spawned would otherwise
    outlive the terminate and keep the port. Kill follows terminate because a
    wedged process that holds the port is worse than an abrupt one.
    """
    process = server.process
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        process.terminate()
    try:
        process.wait(timeout=STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=STOP_GRACE_S)


@contextlib.contextmanager
def serve(port: int | None = None, timeout: float = START_TIMEOUT_S):
    """Start the real application, yield a :class:`Server`, always stop it.

    The stop runs from a ``finally``, so a failing test, a raised exception
    and a keyboard interrupt all leave the port free.
    """
    port = port or chosen_port()
    host = os.environ.get("CLARE_APP_HOST", "127.0.0.1")
    base_url = f"http://{host}:{port}"

    environment = dict(os.environ, CLARE_APP_HOST=host, CLARE_APP_PORT=str(port))
    # Gradio's own analytics call would be a network request on every start.
    environment.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    # Without this the app's startup lines sit in a pipe buffer and a failure
    # report says "the app printed nothing" when the app printed plenty.
    environment["PYTHONUNBUFFERED"] = "1"

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    handle, log_name = tempfile.mkstemp(prefix="clare-app-", suffix=".log",
                                        dir=str(ARTIFACTS))
    log_path = Path(log_name)
    server = None
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as sink:
            process = subprocess.Popen(
                [sys.executable, "-m", "app.ui.app"],
                cwd=str(PROJECT_ROOT), env=environment,
                stdout=sink, stderr=subprocess.STDOUT,
                start_new_session=True)
            server = Server(base_url=base_url, process=process,
                            log_path=log_path)
            try:
                wait_until_ready(base_url, process, timeout)
            except (RuntimeError, TimeoutError) as exc:
                raise type(exc)(f"{exc}\n\nThe application said:\n"
                                f"{server.output(40)}") from None
            yield server
    finally:
        if server is not None:
            stop(server)
        # The content is copied into the failure artifacts when it matters;
        # leaving a temp log per run behind just accumulates.
        log_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Start the app, run the browser suite against it, stop it, report."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="app.browser_tests",
        description="Run the browser suite against an app started here.")
    parser.add_argument("--smoke", action="store_true",
                        help="only the fast smoke suite (~13s)")
    parser.add_argument("--trace", action="store_true",
                        help="record a Playwright trace for failures")
    parser.add_argument("pytest_args", nargs="*",
                        help="anything else is passed straight to pytest")
    args = parser.parse_args(argv)

    target = ("tests/browser/test_smoke.py" if args.smoke else "tests/browser")
    os.environ["CLARE_BROWSER_TESTS"] = "1"
    if args.trace:
        os.environ["CLARE_BROWSER_TRACE"] = "1"

    import pytest

    # The app is started by the session fixture, which is also what makes a
    # plain `pytest tests/browser` work. Starting one here too would mean two
    # servers and two lifecycles.
    return int(pytest.main([target, "-q", *args.pytest_args]))


if __name__ == "__main__":
    raise SystemExit(main())
