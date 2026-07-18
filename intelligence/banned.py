"""Invariant #3: no pedigree, anywhere, by construction. Owner: C.

tests/test_no_pedigree.py greps every prompt and feature name against this list.
It runs in CI and it is a hard fail.
"""

from __future__ import annotations

BANNED_TERMS: list[str] = [
    "stanford",
    "mit",
    "harvard",
    "berkeley",
    "cmu",
    "oxford",
    "cambridge",
    "ivy league",
    "top-tier",
    "tier-1",
    "prestigious",
    "elite university",
    "ex-google",
    "ex-meta",
    "ex-openai",
    "faang",
    "y combinator",
    "ycombinator",
    "a16z",
    "sequoia",
    "backed by",
    "phd from",
    "mba",
    "alma mater",
    "pedigree",
    # --- schools -----------------------------------------------------------
    "princeton",
    "yale",
    "caltech",
    "eth zurich",
    "tsinghua",
    "iit",
    "top school",
    "elite school",
    "valedictorian",
    "gpa",
    "alumnus",
    "alumni",
    # --- employer brand ----------------------------------------------------
    "ex-amazon",
    "ex-apple",
    "ex-stripe",
    "ex-tesla",
    "big tech",
    "name-brand",
    "brand-name",
    "blue-chip",
    "fortune 500",
    # --- investors and their halo -----------------------------------------
    "andreessen",
    "greylock",
    "khosla",
    "founders fund",
    "benchmark capital",
    "well-connected",
    "warm intro",
    "serial entrepreneur",
]
