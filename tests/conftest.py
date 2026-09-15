"""Shared fixtures.

Two things matter here. The breaker and limiter registries are process-global,
so a test that trips a breaker would otherwise poison every later test. And
the cache must never be the developer's real `~/.marketpulse/cache.sqlite3`.
Both are reset per test rather than left to ordering luck.
"""

from __future__ import annotations

import pytest

from marketpulse.platform import http as http_mod
from marketpulse.platform.cache import Cache


@pytest.fixture(autouse=True)
def _clean_registries():
    """Isolate the global breaker and rate-limiter registries per test."""
    http_mod.reset_registries()
    yield
    http_mod.reset_registries()


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    """Neutralise retry backoff so the suite does not spend real seconds.

    Only the retry sleep. The rate limiter still sleeps for real, because its
    tests are about timing and faking that would test nothing.
    """
    monkeypatch.setattr(http_mod, "_RETRY_SLEEP", lambda _seconds: None)


@pytest.fixture
def cache(tmp_path) -> Cache:
    """A Cache backed by a throwaway SQLite file."""
    return Cache(db_path=tmp_path / "cache.sqlite3")


@pytest.fixture
def no_sleep():
    """A sleep function that records durations instead of spending them."""
    slept: list[float] = []
    return slept.append, slept
