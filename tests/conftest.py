"""Shared test fixtures. The store is a module-global singleton, so it must be
reset between tests or state leaks across them. Scoring and backend env vars are
cleared too, so a test that flips SCORE_MODEL or MEMORY_BACKEND can't poison the
next one — every test starts on the deterministic in-memory backend."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from memory import store

_MANAGED_ENV = ("SCORE_MODEL", "SCORE_Q", "SCORE_R0", "SCORE_LAMBDA", "MEMORY_BACKEND")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    for var in _MANAGED_ENV:
        monkeypatch.delenv(var, raising=False)
    store._pg = None  # drop any cached Postgres backend from a prior test
    store.reset()
    yield
    store._pg = None
    store.reset()
