"""Which seeded companies are constructed, derived from the fixtures themselves.

THE POINT OF THIS MODULE IS THAT IT CONTAINS NO COMPANY NAMES. A hardcoded list of
"the fake ones" is the pattern this codebase keeps regretting: it is correct on the
day it is written and wrong the first time somebody adds a fixture, and nothing fails
when it goes wrong — the ranking just quietly starts presenting an authored scenario
as sourced evidence. So provenance is read out of the same files that create the
companies, and a new fixture is classified whether or not anyone remembers this file.

The two bases, both already present in the data before this module existed:

  data/seed/archetype_*.json
      Authored demo scenarios. Every profile in every one of these files is
      CONSTRUCTED by definition — that is what an archetype fixture IS. This includes
      `prior_companies`, which are authored just as much as the profile that owns them.

  data/seed/backtest.json
      Per-member `evidence_provenance`, which the cohort file has always carried and
      which tests/test_seed.py already constrains to a closed set:
        "reconstructed-from-public-record" -> SOURCED   (the real winners, and the
                                                         real non-breakout controls)
        "synthetic"                        -> CONSTRUCTED (the matched synthetic
                                                         controls and the deliberately
                                                         deprioritized failure)

Anything the seed loader does not find here is left at the model default (SOURCED),
which is right: the only companies that reach the store without passing through these
fixtures are the ones the scanners and inbound intake found in the real world.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from schema.events import CompanyProvenance

SEED_DIR = Path(__file__).resolve().parent

# The values `evidence_provenance` is allowed to take in backtest.json. Kept as an
# explicit mapping rather than a truthiness check so that a NEW value added to the
# cohort file raises here instead of silently defaulting a synthetic member to sourced.
_COHORT_PROVENANCE = {
    "reconstructed-from-public-record": CompanyProvenance.SOURCED,
    "synthetic": CompanyProvenance.CONSTRUCTED,
}


def _archetype_company_names() -> set[str]:
    names: set[str] = set()
    for path in sorted(SEED_DIR.glob("archetype_*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for profile in fixture.get("profiles", []):
            names.add(profile["company_name"])
            # A serial founder's prior company is authored too, and it is a separate
            # company row. Missing these was the gap that let Type 3's prior companies
            # look sourced while the companies they belong to were marked constructed.
            for prior in profile.get("prior_companies", []):
                names.add(prior["name"])
    return names


def _cohort_provenance() -> dict[str, CompanyProvenance]:
    path = SEED_DIR / "backtest.json"
    if not path.exists():
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))

    members: list[dict] = list(blob.get("cohort") or [])
    for key in ("winners", "controls", "failures"):
        members.extend(blob.get(key) or [])
    failure = blob.get("correctly_deprioritized_failure")
    if isinstance(failure, dict):
        members.append(failure)

    out: dict[str, CompanyProvenance] = {}
    for member in members:
        name = member.get("company_name") or member.get("name")
        raw = member.get("evidence_provenance")
        if not name or raw is None:
            continue
        if raw not in _COHORT_PROVENANCE:
            raise ValueError(
                f"unknown evidence_provenance {raw!r} for cohort member {name!r}. "
                f"Add it to _COHORT_PROVENANCE and decide, deliberately, whether a "
                f"member with that provenance may be presented as sourced evidence."
            )
        out[name] = _COHORT_PROVENANCE[raw]
    return out


@lru_cache(maxsize=1)
def provenance_by_company_name() -> dict[str, CompanyProvenance]:
    """company_name -> provenance, for every company the seed fixtures can create."""
    mapping = {name: CompanyProvenance.CONSTRUCTED for name in _archetype_company_names()}
    # The cohort is applied second and wins on conflict: a company appearing in both
    # would be a real company reused as an archetype, and the cohort file is the one
    # that carries an explicit, per-member provenance claim.
    mapping.update(_cohort_provenance())
    return mapping


def provenance_for(company_name: str) -> CompanyProvenance:
    """Provenance for a seeded company, defaulting to SOURCED for anything not seeded."""
    return provenance_by_company_name().get(company_name, CompanyProvenance.SOURCED)
