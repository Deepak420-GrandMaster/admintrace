"""Shared fixtures for the offline suite."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def institution_register(monkeypatch):
    """A small, versioned institution register in place of the downloaded one.

    The real register is fetched from ONISEP into the gitignored data cache.
    Tests that resolve an institution by name used to pass only where that
    download happened to exist, and failed on a fresh clone.
    """
    from app.directory import universities

    monkeypatch.setattr(universities, "_cache_path",
                        lambda settings: FIXTURES / "institutions.json")
    universities.load.cache_clear()
    yield
    universities.load.cache_clear()
