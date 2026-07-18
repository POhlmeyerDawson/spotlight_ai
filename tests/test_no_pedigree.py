"""Invariant #3, enforced by construction rather than intention. Owner: C.

Greps every prompt string and identifier in the codebase against the banned list.
This is the Type 6 guarantee and it is a hard CI fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from intelligence.banned import BANNED_TERMS

ROOT = Path(__file__).resolve().parent.parent
SCANNED_DIRS = ["core", "memory", "sourcing", "intelligence", "api", "backtest"]

# banned.py is the list itself; this test file quotes it too.
EXEMPT = {"intelligence/banned.py", "tests/test_no_pedigree.py"}


def _python_files() -> list[Path]:
    return [
        p
        for d in SCANNED_DIRS
        for p in (ROOT / d).rglob("*.py")
        if p.relative_to(ROOT).as_posix() not in EXEMPT  # as_posix so '/' matches on Windows
    ]


@pytest.mark.parametrize("term", BANNED_TERMS)
def test_no_pedigree_term_in_source(term: str) -> None:
    """No feature, prompt, or rule may reference school, employer brand, or investor."""
    # Word-boundary anchored: "mit" must not match "commit"/"limit"/"submit".
    pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    offenders = [
        f"{p.relative_to(ROOT).as_posix()}:{i}"
        for p in _python_files()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        f"Pedigree term {term!r} found at {offenders}. Scoring must be substance-only "
        f"by construction — see SHARED.md Invariant #3."
    )
