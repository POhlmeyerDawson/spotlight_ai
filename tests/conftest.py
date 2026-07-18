"""Global test guard: the suite never talks to a network database.

memory.db picks its backend from DATABASE_URL, and .env points at Supabase. Tests that
set their own VCBRAIN_DB_PATH still win (monkeypatch.setenv overrides this); tests that
never touch the store just get a throwaway file instead of a Postgres session.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _offline_db(tmp_path_factory):
    import os

    os.environ["VCBRAIN_DB_PATH"] = str(tmp_path_factory.mktemp("db") / "session.db")
    yield
