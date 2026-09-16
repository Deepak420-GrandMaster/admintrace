"""One command that says whether Claré is fit to deploy, and why not.

    uv run python -m app.verify
    uv run python -m app.verify --quick      # skip the browser layer
    uv run python -m app.verify --json

The layers run in the order that fails cheapest first: configuration before
tests, tests before the browser, the browser before the scorecard. Each
reports one of PASSED, FAILED, SKIPPED, NOT CONFIGURED or BLOCKED, and the
difference between the last three matters — a suite nobody ran, a capability
nobody configured, and a site that refuses us are three different facts and
this command never flattens them into one.

The exit code is a deployment gate, not a summary of the output. It asks a
narrower question than "is everything green": is anything *we* are responsible
for broken? An external site refusing us does not stop Claré from shipping;
the system is built to say so honestly at answer time and the tests prove it
does. A failing security check or a broken interface does stop it.

    CRITICAL      configuration, the offline suite (core backend, security,
                  answer validation, entity and jurisdiction resolution,
                  localization), the security checks, browser smoke
    NON-CRITICAL  an individual external source being unavailable or blocked,
                  SMTP not configured, history not yet deep enough, a
                  real-world change not yet observed, the network suite —
                  which depends on sites that are not ours
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum

from app.config import ConfigError, PROJECT_ROOT, get_settings

#: Offline test files whose subject is the security boundary. Run again on
#: their own so a security regression is never a line buried in a long run.
SECURITY_TESTS = (
    "tests/test_hardening.py",
    "tests/test_sources.py",
    "tests/test_gate.py",
    "tests/test_render_health.py",
    "tests/test_no_hardcoded_facts.py",
)


class Outcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_CONFIGURED = "NOT CONFIGURED"
    BLOCKED = "BLOCKED"


@dataclass
class Layer:
    name: str
    outcome: Outcome
    detail: str = ""
    critical: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def blocks_deploy(self) -> bool:
        return self.critical and self.outcome is Outcome.FAILED


_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")


def _run_pytest(targets: list[str], env_extra: dict | None = None
                ) -> tuple[bool, str]:
    """Run a pytest selection in its own process and summarise the tail.

    A subprocess rather than ``pytest.main`` so each layer starts clean: the
    browser layer in particular must not inherit an open Playwright session
    from this one.
    """
    # Marks the child so the test that exercises this command knows it is
    # already inside it. Without it, verify runs the suite, the suite runs
    # verify, and the machine finds out the hard way.
    environment = dict(os.environ, CLARE_VERIFY_CHILD="1", **(env_extra or {}))
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-q", "--tb=no",
         "-p", "no:cacheprovider"],
        cwd=str(PROJECT_ROOT), env=environment,
        capture_output=True, text=True)
    tail = [line for line in completed.stdout.strip().splitlines()
            if _COUNTS.search(line)]
    summary = tail[-1].strip() if tail else (
        completed.stdout.strip().splitlines() or ["no output"])[-1]
    return completed.returncode == 0, summary


def _failing_tests(targets: list[str], env_extra: dict | None = None) -> list[str]:
    environment = dict(os.environ, CLARE_VERIFY_CHILD="1", **(env_extra or {}))
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-q", "--tb=no",
         "-p", "no:cacheprovider", "-rf"],
        cwd=str(PROJECT_ROOT), env=environment,
        capture_output=True, text=True)
    return [line.split(" ", 1)[-1].strip()
            for line in completed.stdout.splitlines()
            if line.startswith("FAILED")][:10]


def check_configuration() -> Layer:
    try:
        settings = get_settings()
    except ConfigError as exc:
        return Layer("Backend", Outcome.FAILED, str(exc), critical=True)
    return Layer("Backend", Outcome.PASSED,
                 f"{settings.llm_provider} · {settings.embed_model}",
                 critical=True)


def check_offline() -> Layer:
    ok, summary = _run_pytest(["tests", "--ignore=tests/browser"],
                              {"CLARE_NETWORK_TESTS": "0"})
    return Layer("Offline tests", Outcome.PASSED if ok else Outcome.FAILED,
                 summary, critical=True,
                 notes=[] if ok else _failing_tests(
                     ["tests", "--ignore=tests/browser"],
                     {"CLARE_NETWORK_TESTS": "0"}))


def check_network() -> Layer:
    if os.environ.get("CLARE_NETWORK_TESTS") != "1":
        return Layer("Network tests", Outcome.SKIPPED,
                     "set CLARE_NETWORK_TESTS=1 to reach the live web")
    ok, summary = _run_pytest(["tests", "--ignore=tests/browser"],
                              {"CLARE_NETWORK_TESTS": "1"})
    # Not critical: these depend on sites nobody here controls, and an
    # outage at a préfecture is not a reason to hold a release.
    return Layer("Network tests", Outcome.PASSED if ok else Outcome.FAILED,
                 summary, critical=False)


def check_browser(quick: bool = False) -> Layer:
    if quick:
        return Layer("Browser", Outcome.SKIPPED, "--quick")
    try:
        from app.sources.render import available
    except ImportError:  # pragma: no cover - defensive
        available = lambda: False  # noqa: E731
    if not available():
        return Layer("Browser", Outcome.NOT_CONFIGURED,
                     "playwright is not installed; `uv sync --extra render`")
    ok, summary = _run_pytest(["tests/browser/test_smoke.py"],
                              {"CLARE_BROWSER_TESTS": "1"})
    return Layer("Browser smoke", Outcome.PASSED if ok else Outcome.FAILED,
                 summary, critical=True,
                 notes=[] if ok else _failing_tests(
                     ["tests/browser/test_smoke.py"],
                     {"CLARE_BROWSER_TESTS": "1"}))


def check_security() -> Layer:
    ok, summary = _run_pytest([*SECURITY_TESTS], {"CLARE_NETWORK_TESTS": "0"})
    return Layer("Security", Outcome.PASSED if ok else Outcome.FAILED,
                 summary, critical=True,
                 notes=[] if ok else _failing_tests(
                     [*SECURITY_TESTS], {"CLARE_NETWORK_TESTS": "0"}))


def check_sources() -> Layer:
    from app.sources.gating import QueryMode, mode_for
    from app.sources.registry import Health, load_registry

    settings = get_settings()
    registry = load_registry()
    blocked = [s.id for s in registry if s.health is Health.BLOCKED]
    unavailable = [s.id for s in registry
                   if mode_for(s, settings) is QueryMode.UNAVAILABLE
                   and s.health is not Health.BLOCKED]
    current = sum(1 for s in registry
                  if s.health in (Health.HEALTHY, Health.HEALTHY_RENDERED))

    notes = []
    if blocked:
        notes.append(f"blocked: {', '.join(blocked)}")
    if unavailable:
        notes.append(f"unavailable: {', '.join(unavailable)}")
    # An external site refusing us is a fact about them, reported and never
    # converted into a pass — but it does not fail this layer either.
    outcome = Outcome.BLOCKED if blocked and not current else Outcome.PASSED
    return Layer("Sources", outcome,
                 f"{current} current · {len(blocked)} blocked · "
                 f"{len(unavailable)} unavailable",
                 critical=False, notes=notes)


def check_smtp() -> Layer:
    settings = get_settings()
    if not settings.email_configured:
        return Layer("SMTP", Outcome.NOT_CONFIGURED,
                     "no SMTP host or recipient; nothing has been sent")
    return Layer("SMTP", Outcome.PASSED,
                 f"configured for {settings.bug_email_to}")


def check_performance() -> Layer:
    """What answers have actually cost, when any have been measured."""
    from app.telemetry import provider_health, summarise, traces

    rows = traces()
    if not rows:
        return Layer("Performance", Outcome.SKIPPED,
                     "no answers timed yet; ask something and re-run")
    health = provider_health(rows)
    overall = summarise(rows, by="provider")
    p95 = max((stats["p95"] for stats in overall.values()), default=0.0)
    notes = []
    if health["rate_limit_count"]:
        notes.append(f"rate limited on {health['rate_limit_count']} of "
                     f"{health['answers']} answers")
    if health["empty_answers"]:
        notes.append(f"{health['empty_answers']} empty answer(s) from the provider")
    # Not critical: latency is a product target, not a correctness claim, and
    # a metered provider's quota is not something a deploy should hang on.
    return Layer("Performance", Outcome.PASSED,
                 f"{health['answers']} answers measured · p95 {p95:.1f}s",
                 critical=False, notes=notes)


def check_scorecard() -> Layer:
    from app.status import Verification, assess, write_scorecard

    # Recomputed here, never read from a file somebody could have edited.
    write_scorecard()
    capabilities = assess()
    proven = sum(1 for c in capabilities.values() if c.is_production)
    notes = [f"{c.name}: {c.status.value}" for c in capabilities.values()
             if c.status in (Verification.BLOCKED, Verification.FAILED)]
    return Layer("Production scorecard", Outcome.PASSED,
                 f"{proven}/{len(capabilities)} production verified",
                 critical=False, notes=notes)


def run(quick: bool = False) -> list[Layer]:
    layers = [check_configuration()]
    if layers[0].outcome is Outcome.FAILED:
        # Nothing below can mean anything if the configuration is broken.
        return layers
    layers.append(check_offline())
    layers.append(check_network())
    layers.append(check_security())
    layers.append(check_sources())
    layers.append(check_browser(quick))
    layers.append(check_performance())
    layers.append(check_smtp())
    layers.append(check_scorecard())
    return layers


def render(layers: list[Layer]) -> str:
    width = max(len(layer.name) for layer in layers) + 2
    lines = ["CLARÉ VERIFICATION", ""]
    for layer in layers:
        mark = "✗" if layer.outcome is Outcome.FAILED else " "
        lines.append(f"{mark} {layer.name:<{width}} {layer.outcome.value}")
        if layer.detail:
            lines.append(f"  {'':<{width}} {layer.detail}")
        for note in layer.notes:
            lines.append(f"  {'':<{width}} · {note}")
    blocking = [layer for layer in layers if layer.blocks_deploy]
    other = [layer for layer in layers
             if layer.outcome is Outcome.FAILED and not layer.critical]
    lines.append("")
    if blocking:
        lines.append("DEPLOYMENT: BLOCKED")
        for layer in blocking:
            lines.append(f"  critical failure: {layer.name} — {layer.detail}")
    else:
        lines.append("DEPLOYMENT: CLEAR")
        if other:
            lines.append("  non-critical failures, reported and not blocking:")
            for layer in other:
                lines.append(f"    {layer.name} — {layer.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.verify",
        description="Run every validation layer and gate deployment on it.")
    parser.add_argument("--quick", action="store_true",
                        help="skip the browser layer")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    layers = run(quick=args.quick)
    if args.as_json:
        print(json.dumps({
            "layers": [{"name": l.name, "outcome": l.outcome.value,
                        "detail": l.detail, "critical": l.critical,
                        "notes": l.notes} for l in layers],
            "deployable": not any(l.blocks_deploy for l in layers),
        }, ensure_ascii=False, indent=2))
    else:
        print(render(layers))
    return 1 if any(layer.blocks_deploy for layer in layers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
