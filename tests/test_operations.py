"""The operational surface: starting the app, gating a deploy, staying honest.

These cover the machinery that makes the rest runnable unattended — the
browser harness's lifecycle, the deployment gate's critical/non-critical
split, the scorecard's transition ledger — plus the promises that machinery
must not quietly break: a blocked source stays blocked, unconfigured SMTP
stays unconfigured, and stale evidence is never served as current.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.browser_tests import ARTIFACTS, chosen_port, free_port, wait_until_ready
from app.config import PROJECT_ROOT
from app.status import Capability, Verification, transitions
from app.verify import Layer, Outcome, render


# ------------------------------------------------------- starting the app ---

def test_a_free_port_is_actually_free():
    port = free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))  # would raise if it were taken


def test_a_configured_test_port_is_honoured(monkeypatch):
    monkeypatch.setenv("CLARE_TEST_PORT", "8123")
    assert chosen_port() == 8123


def test_an_unset_test_port_is_allocated(monkeypatch):
    monkeypatch.delenv("CLARE_TEST_PORT", raising=False)
    assert chosen_port() > 0


def test_readiness_gives_up_immediately_when_the_app_is_already_dead():
    """A crashed app is reported now, not after the full timeout.

    Waiting sixty seconds to say "it exited two seconds ago" is the kind of
    slow, uninformative failure that makes people stop running a suite.
    """
    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    dead.wait()
    with pytest.raises(RuntimeError, match="exited with code 3"):
        wait_until_ready("http://127.0.0.1:1", dead, timeout=30)


def test_the_browser_suite_does_not_need_a_server_to_be_running():
    """The documented acceptance criterion, asserted rather than remembered."""
    conftest = (PROJECT_ROOT / "tests" / "browser" / "conftest.py").read_text(
        encoding="utf-8")
    assert "serve(" in conftest, "the suite must start the app itself"
    assert "clare_app" in conftest


def test_failure_artifacts_are_not_source_control():
    ignore = (ARTIFACTS.parent / ".gitignore")
    assert ignore.exists(), "artifacts/ must carry its own .gitignore"
    assert "*" in ignore.read_text(encoding="utf-8")


def test_the_app_entrypoint_reads_its_port_from_the_environment():
    """One server implementation. The harness starts what users run."""
    source = (PROJECT_ROOT / "app" / "ui" / "app.py").read_text(encoding="utf-8")
    assert "CLARE_APP_PORT" in source and "CLARE_APP_HOST" in source


# ------------------------------------------------------- deployment gate ----

def test_a_critical_failure_blocks_deployment():
    layers = [Layer("Security", Outcome.FAILED, "1 failed", critical=True)]
    assert layers[0].blocks_deploy
    assert "DEPLOYMENT: BLOCKED" in render(layers)


def test_an_external_source_outage_does_not_block_deployment():
    """CAF refusing us is a fact about CAF, not a reason to hold a release."""
    layers = [
        Layer("Offline tests", Outcome.PASSED, "350 passed", critical=True),
        Layer("Sources", Outcome.PASSED, "14 current · 1 blocked",
              critical=False, notes=["blocked: caf"]),
        Layer("Network tests", Outcome.FAILED, "1 failed", critical=False),
        Layer("SMTP", Outcome.NOT_CONFIGURED, "nothing has been sent"),
    ]
    assert not any(layer.blocks_deploy for layer in layers)
    text = render(layers)
    assert "DEPLOYMENT: CLEAR" in text
    # Cleared, but never silently: a non-critical failure is still printed.
    assert "non-critical failures" in text and "Network tests" in text


def test_the_gate_keeps_its_three_kinds_of_not_passing_apart():
    """SKIPPED, NOT CONFIGURED and BLOCKED are different facts."""
    layers = [
        Layer("Network tests", Outcome.SKIPPED, "not enabled"),
        Layer("SMTP", Outcome.NOT_CONFIGURED, "no host"),
        Layer("Sources", Outcome.BLOCKED, "everything refused us"),
    ]
    text = render(layers)
    assert "SKIPPED" in text and "NOT CONFIGURED" in text and "BLOCKED" in text


def test_every_critical_layer_is_one_we_are_responsible_for():
    from app import verify

    layers = [verify.check_configuration()]
    names = {layer.name for layer in layers if layer.critical}
    assert "Backend" in names


# --------------------------------------------------- scorecard transitions --

def test_a_recovery_is_recorded_not_only_a_regression():
    before = {"capabilities": {"x": {"status": "blocked"}}}
    after = {"x": Capability("X", Verification.PRODUCTION_VERIFIED, "live now")}
    moved = transitions(before, after)
    assert moved[0]["previous_status"] == "blocked"
    assert moved[0]["new_status"] == "production_verified"
    assert moved[0]["direction"] == "recovered"


def test_a_degradation_is_recorded_with_its_evidence():
    before = {"capabilities": {"x": {"status": "production_verified"}}}
    after = {"x": Capability("X", Verification.FAILED, "the site went dark")}
    moved = transitions(before, after)
    assert moved[0]["direction"] == "degraded"
    assert moved[0]["evidence"] == "the site went dark"


def test_an_unchanged_capability_is_not_a_transition():
    before = {"capabilities": {"x": {"status": "blocked"}}}
    after = {"x": Capability("X", Verification.BLOCKED, "still blocked")}
    assert transitions(before, after) == []


# ------------------------------------------------------------- honesty ------

def test_no_production_code_serves_stale_evidence_as_current():
    """``allow_stale`` exists for tests. Production must never pass it.

    A source going dark must not silently turn last month's rule into this
    month's answer — for an administrative deadline that is the difference
    between a valid application and a missed one.
    """
    offenders = []
    for path in (PROJECT_ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "allow_stale=True" in text or "allow_stale = True" in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert not offenders, f"stale evidence allowed in production: {offenders}"


def test_one_blocked_source_does_not_take_the_others_down():
    """An outage is isolated to the source that has it."""
    from app.sources.gating import may_cite
    from app.sources.registry import Health, load_registry

    registry = load_registry()
    blocked = [s for s in registry if s.health is Health.BLOCKED]
    assert blocked, "this test is meaningless if nothing is blocked"
    assert not any(may_cite(s) for s in blocked)

    healthy = [s for s in registry
               if s.health in (Health.HEALTHY, Health.HEALTHY_RENDERED)]
    assert sum(1 for s in healthy if may_cite(s)) >= 10, \
        "a blocked source has taken healthy ones with it"


def test_the_scorecard_is_never_hand_written():
    """No code path writes production_status.json except the derivation."""
    writers = []
    for path in (PROJECT_ROOT / "app").rglob("*.py"):
        if path.name == "status.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "production_status.json" in text and "write_text" in text:
            writers.append(str(path.relative_to(PROJECT_ROOT)))
    assert not writers, f"the scorecard is written outside status.py: {writers}"


# -------------------------------------------------------------- the CLIs ----

def _cli(module: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *args],
                          cwd=str(PROJECT_ROOT), capture_output=True, text=True)


def test_the_review_queue_is_not_an_error_when_it_is_empty():
    result = _cli("app.review_queue")
    assert result.returncode == 0, result.stderr
    assert "awaiting review" in result.stdout


def test_the_healthcheck_runs_with_sources_blocked_and_unavailable():
    """It reports the state of the world; it does not require a perfect one."""
    result = _cli("app.healthcheck")
    assert result.returncode in (0, 1), result.stderr
    assert "Sources" in result.stdout


def test_sending_mail_fails_when_nothing_is_configured():
    """--send must never report a success it did not earn."""
    result = _cli("app.test_email", "--send")
    if "Sent." in result.stdout:
        pytest.skip("SMTP is configured on this machine")
    assert result.returncode == 1
    assert "missing: SMTP_HOST" in result.stderr


def test_a_dry_run_shows_the_message_even_with_nothing_configured():
    result = _cli("app.test_email", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout


@pytest.mark.skipif(
    os.environ.get("CLARE_VERIFY_CHILD") == "1",
    reason="already running inside app.verify; spawning it again would recurse")
def test_verify_reports_every_layer_and_gates_on_the_critical_ones():
    result = _cli("app.verify", "--quick", "--json")
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    names = {layer["name"] for layer in payload["layers"]}
    assert {"Backend", "Offline tests", "Security", "Sources", "SMTP"} <= names
    assert isinstance(payload["deployable"], bool)


def test_a_status_change_is_appended_to_the_ledger(tmp_path, monkeypatch):
    """The ledger is what survives a snapshot being overwritten.

    Driven with a stubbed assessment rather than real evidence: the point
    under test is that a change gets written down, not that any particular
    capability changed.
    """
    import dataclasses

    from app import status as status_module
    from app.config import get_settings

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path)

    first = {"live": Capability("Live retrieval", Verification.BLOCKED, "dark")}
    monkeypatch.setattr(status_module, "assess", lambda s=None: first)
    status_module.write_scorecard(settings)

    then = {"live": Capability("Live retrieval",
                               Verification.PRODUCTION_VERIFIED, "answering")}
    monkeypatch.setattr(status_module, "assess", lambda s=None: then)
    status_module.write_scorecard(settings)

    ledger = tmp_path / "status_transitions.jsonl"
    assert ledger.exists(), "a status change left no record"
    entries = [json.loads(line) for line in
               ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert entries[-1]["previous_status"] == "blocked"
    assert entries[-1]["new_status"] == "production_verified"
    assert entries[-1]["direction"] == "recovered"
    assert entries[-1]["timestamp"]


def test_snapshots_are_kept_so_last_week_has_an_answer(tmp_path, monkeypatch):
    import dataclasses

    from app import status as status_module
    from app.config import get_settings

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path)
    monkeypatch.setattr(status_module, "assess", lambda s=None: {
        "live": Capability("Live retrieval", Verification.MECHANISM_VERIFIED, "x")})
    status_module.write_scorecard(settings)

    archive = tmp_path / "status_history"
    assert archive.exists() and list(archive.glob("*.json"))
