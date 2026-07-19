"""A constructed company's citation must never be able to pass as a sourced one.

WHY THIS EXISTS. A user opened a company page and was shown a fabricated founder
whose evidence cited `https://vcbrain.local/proof/pc-an-1#turn-2`. The problem was
not that the company was invented — the whole archetype corpus is invented, openly
and on purpose. The problem was that its receipts were shaped like real receipts, so
a reader checking the trace could not tell authored scenario from scanned evidence
without leaving the page. Fabrication is fine. Fabrication wearing the costume of
sourced evidence is the failure.

So the rule this file enforces is narrow and mechanical: if `data/seed/provenance.py`
says a company is CONSTRUCTED, every citation it carries must be visibly non-real ON
INSPECTION — no lookup, no network call, no judgement. Two forms qualify:

  * the reserved TLD `example.invalid`. RFC 2606 guarantees `.invalid` is never
    delegated, so these can never resolve to anything, ever.
  * a scheme that is not a web address at all — `deck://`, `proof://`. There is
    nothing for a browser to fetch and nothing for a reader to mistake.

GAP NOW CLOSED. Archetypes 1-6 used to cite `github.com/...`,
`news.ycombinator.com/item?id=...` and `arxiv.org/abs/...` paths that were constructed
but syntactically indistinguishable from real ones — and worse, several of those ids DO
resolve, to real and completely unrelated content (an arXiv id cited for speculative
decoding is a neutrino physics paper; a stored HN "revenue claim" is someone discussing
einsum). A reviewer clicking through landed on a real page and had no way to know the
citation was authored. Those 131 citations have been rewritten to the `example.invalid`
convention and `_CONSTRUCTED_FIXTURES` now covers every archetype.

`data/seed/backtest.json` IS IN SCOPE, PER MEMBER. It used to be excluded wholesale, on
the rationale that it carries the 7 SOURCED companies (Docker, Hugging Face, Supabase,
Vercel, Deis, Flynn, Space Cloud) whose citations are real, verified and load-bearing.
That rationale was right about those 7 and wrong about the other 5: the file is MIXED.
Northgate Runtime, Parallax Models, Riverbend Data, Hallmark Edge and Veridian Stack are
synthetic composites, and they were carrying 25 real-domain citations —
`news.ycombinator.com/item?id=northgate-1` and `github.com/northgate/runtime/commits/main`
among them — behind a file-level exemption that was never meant to cover them.

A file-level exclusion cannot express "some members of this file are invented". So the
exclusion is now MEMBER-level, keyed on each member's own `evidence_provenance`:

  * `synthetic`                        -> must satisfy the same rule as the archetypes.
  * `reconstructed-from-public-record` -> real citations, deliberately NOT checked.

Keying on the data rather than the filename means a synthetic member added to this file
tomorrow is covered on arrival, and a real one is never rewritten by mistake.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"

# Every archetype fixture. `data/seed/provenance.py` classifies every profile in every
# `archetype_*.json` as CONSTRUCTED by definition, so the whole glob is in scope — and
# `test_every_constructed_fixture_is_classified_constructed` below re-checks that claim
# against `provenance.py` rather than trusting the filename. The 7 sourced companies
# (Docker, Hugging Face, Supabase, Vercel, Deis, Flynn, Space Cloud) live in
# `backtest.json`, which is deliberately NOT globbed here: their citations are real,
# verified, and load-bearing for the backtest.
_CONSTRUCTED_FIXTURES = tuple(sorted(p.name for p in SEED.glob("archetype_*.json")))

# The derived per-company views carry copies of the same citations. They are shaped
# differently from the archetype fixtures (no `profiles` list), so they are scanned
# generically for URLs rather than parsed — see `test_no_constructed_seed_file_cites_a_real_host`.
_DERIVED_GLOBS = ("company_*.json", "memo_*.json", "dissent_*.json")

_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'\\]+")

# Schemes that are not web addresses, so nothing about them invites a lookup.
_NON_WEB_SCHEMES = {"deck", "proof"}

# RFC 2606 reserves this TLD precisely so it can never be delegated or resolve.
_RESERVED_TLD = ".invalid"


def _fixture_events() -> list[tuple[str, str, str]]:
    """(fixture, company_id, source_url) for every event in the constructed fixtures."""
    out: list[tuple[str, str, str]] = []
    for name in _CONSTRUCTED_FIXTURES:
        path = SEED / name
        if not path.exists():
            continue
        blob = json.loads(path.read_text(encoding="utf-8"))
        for profile in blob.get("profiles", []):
            for event in profile.get("events", []):
                url = event.get("source_url")
                if url:
                    out.append((name, profile["company_id"], url))
    return out


def _is_visibly_unreal(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme in _NON_WEB_SCHEMES:
        return True
    host = (parsed.netloc or "").split(":")[0].lower()
    return host.endswith(_RESERVED_TLD)


def test_the_constructed_fixtures_actually_carry_citations() -> None:
    """Guard the guard: an empty corpus would make every assertion below vacuous."""
    events = _fixture_events()
    assert len(events) >= 20, f"expected a populated constructed corpus, got {len(events)}"


@pytest.mark.parametrize("fixture,company_id,url", _fixture_events())
def test_constructed_citations_cannot_pass_as_real(fixture: str, company_id: str, url: str) -> None:
    assert _is_visibly_unreal(url), (
        f"{fixture}:{company_id} cites {url!r}, which is shaped like a real source. "
        f"A constructed company's citation must be visibly non-real on inspection: "
        f"use the reserved {_RESERVED_TLD} TLD or a non-web scheme "
        f"({', '.join(sorted(_NON_WEB_SCHEMES))})."
    )


@pytest.mark.parametrize("fixture,company_id,url", _fixture_events())
def test_constructed_citations_never_borrow_a_real_hosts_name(
    fixture: str, company_id: str, url: str
) -> None:
    """`github.example.invalid` is honest; `github.com.example.invalid` is not.

    The first reads as "a stand-in for GitHub". The second is a real registrable
    domain pasted in front of the reserved TLD, which is exactly the shape that
    survives being copied out of the page and pasted into a browser bar.
    """
    host = (urlparse(url).netloc or "").split(":")[0].lower()
    if not host:
        return
    labels = host.removesuffix(_RESERVED_TLD).strip(".").split(".")
    assert not any(
        label in {"com", "org", "net", "io", "dev", "ai"} for label in labels
    ), f"{fixture}:{company_id} cites {url!r}, which embeds a real-looking domain suffix"


def _constructed_seed_urls() -> list[tuple[str, str]]:
    """(file, url) for every URL in every constructed seed file, archetype or derived.

    Deliberately a text scan rather than a schema-aware walk: the derived files nest
    citations at several depths and under several key names, and a walk that missed one
    of those keys would pass while the fabricated citation it skipped stayed in the
    product. A URL anywhere in a constructed file is in scope, whatever holds it.
    """
    out: list[tuple[str, str]] = []
    globs = (*(f"{n}" for n in _CONSTRUCTED_FIXTURES), *_DERIVED_GLOBS)
    for glob in globs:
        for path in sorted(SEED.glob(glob)):
            for url in _URL_RE.findall(path.read_text(encoding="utf-8")):
                out.append((path.name, url.rstrip(".,;)")))
    return out


def test_the_derived_seed_files_actually_carry_citations() -> None:
    """Guard the guard, again: a bad glob would make the scan below vacuous."""
    names = {f for f, _ in _constructed_seed_urls()}
    assert any(f.startswith("company_") for f in names), "no company_*.json was scanned"
    assert any(f.startswith("memo_") for f in names), "no memo_*.json was scanned"


@pytest.mark.parametrize("fixture,url", _constructed_seed_urls())
def test_no_constructed_seed_file_cites_a_real_host(fixture: str, url: str) -> None:
    """Catches the derived copies the archetype-shaped parse above cannot reach."""
    assert _is_visibly_unreal(url), (
        f"{fixture} cites {url!r}, which is shaped like a real source and may well "
        f"resolve to real, unrelated content. Constructed citations must use the "
        f"reserved {_RESERVED_TLD} TLD or a non-web scheme "
        f"({', '.join(sorted(_NON_WEB_SCHEMES))})."
    )


@pytest.mark.parametrize("fixture,url", _constructed_seed_urls())
def test_no_constructed_seed_file_embeds_a_real_suffix(fixture: str, url: str) -> None:
    """`github.example.invalid` is honest; `github.com.example.invalid` is not."""
    host = (urlparse(url).netloc or "").split(":")[0].lower()
    if not host:
        return
    labels = host.removesuffix(_RESERVED_TLD).strip(".").split(".")
    assert not any(
        label in {"com", "org", "net", "io", "dev", "ai", "co", "ru", "cn"} for label in labels
    ), f"{fixture} cites {url!r}, which embeds a real-looking domain suffix"


# ---------------------------------------------------------------------------
# backtest.json, member by member.
# ---------------------------------------------------------------------------

# The literal `evidence_provenance` value that means "this member is invented". The only
# other permitted value is `reconstructed-from-public-record`; `data/seed/provenance.py`
# raises on any third, so an unrecognised value cannot silently fall out of scope here.
_SYNTHETIC = "synthetic"
_REAL = "reconstructed-from-public-record"

_BACKTEST = SEED / "backtest.json"


def _backtest_members() -> list[dict]:
    """Every cohort member, from all three shapes the file uses.

    `correctly_deprioritized_failure` is a single OBJECT, not a list — it is the one
    member that a naive `for m in blob[key]` would iterate character by character and
    silently check nothing.
    """
    if not _BACKTEST.exists():
        return []
    blob = json.loads(_BACKTEST.read_text(encoding="utf-8"))
    members = [*blob.get("winners", []), *blob.get("controls", [])]
    failure = blob.get("correctly_deprioritized_failure")
    if isinstance(failure, dict):
        members.append(failure)
    return members


def _synthetic_backtest_urls() -> list[tuple[str, str]]:
    """(company_name, source_url) for the SYNTHETIC members only."""
    out: list[tuple[str, str]] = []
    for member in _backtest_members():
        if member.get("evidence_provenance") != _SYNTHETIC:
            continue
        name = member.get("company_name") or member.get("name") or "?"
        for event in member.get("events", []):
            url = event.get("source_url")
            if url:
                out.append((name, url))
    return out


def test_backtest_members_all_declare_a_known_provenance() -> None:
    """Guard the guard: an unrecognised value must not quietly skip the check below."""
    members = _backtest_members()
    assert len(members) >= 12, f"expected the full cohort, got {len(members)}"
    unknown = {
        m.get("company_name"): m.get("evidence_provenance")
        for m in members
        if m.get("evidence_provenance") not in {_SYNTHETIC, _REAL}
    }
    assert not unknown, f"members with an unrecognised evidence_provenance: {unknown}"


def test_the_synthetic_backtest_members_actually_carry_citations() -> None:
    """Guard the guard: a member-level key that matched nothing would prove nothing."""
    urls = _synthetic_backtest_urls()
    names = {n for n, _ in urls}
    assert len(names) >= 5, f"expected 5 synthetic members, saw {sorted(names)}"
    assert len(urls) >= 20, f"expected a populated synthetic cohort, got {len(urls)}"


@pytest.mark.parametrize("company,url", _synthetic_backtest_urls())
def test_synthetic_backtest_citations_cannot_pass_as_real(company: str, url: str) -> None:
    """The 25 real-domain citations the old file-level exemption used to hide."""
    assert _is_visibly_unreal(url), (
        f"backtest.json:{company} is evidence_provenance={_SYNTHETIC!r} but cites {url!r}, "
        f"which is shaped like a real source. A synthetic cohort member's citation must be "
        f"visibly non-real on inspection: use the reserved {_RESERVED_TLD} TLD or a non-web "
        f"scheme ({', '.join(sorted(_NON_WEB_SCHEMES))})."
    )


@pytest.mark.parametrize("company,url", _synthetic_backtest_urls())
def test_synthetic_backtest_citations_never_borrow_a_real_hosts_name(
    company: str, url: str
) -> None:
    host = (urlparse(url).netloc or "").split(":")[0].lower()
    if not host:
        return
    labels = host.removesuffix(_RESERVED_TLD).strip(".").split(".")
    assert not any(
        label in {"com", "org", "net", "io", "dev", "ai", "co", "ru", "cn"}
        for label in labels
    ), f"backtest.json:{company} cites {url!r}, which embeds a real-looking domain suffix"


def test_the_real_backtest_members_are_left_alone() -> None:
    """The other half of the contract, asserted rather than assumed.

    The 7 reconstructed members are supposed to cite real hosts — that is what makes the
    H12 backtest mean anything. If a well-meaning rewrite ever points them at
    `example.invalid`, the citations stop being verifiable and this test says so.
    """
    real = [m for m in _backtest_members() if m.get("evidence_provenance") == _REAL]
    assert len(real) >= 7, f"expected >=7 reconstructed members, got {len(real)}"
    for member in real:
        name = member.get("company_name") or member.get("name")
        urls = [e["source_url"] for e in member.get("events", []) if e.get("source_url")]
        assert urls, f"{name} is {_REAL} but carries no citations at all"
        assert not any(_RESERVED_TLD in u for u in urls), (
            f"{name} is a REAL cohort member but cites a reserved-TLD placeholder. "
            f"Its citations are load-bearing for the backtest and must stay verifiable."
        )


# ---------------------------------------------------------------------------
# The frontend fallback fixtures.
# ---------------------------------------------------------------------------

_APP_FIXTURES = ROOT / "app" / "lib" / "fixtures.ts"


def _app_fixture_urls() -> list[tuple[str, str]]:
    """(file, url) for every URL in the frontend's fallback fixtures."""
    if not _APP_FIXTURES.exists():
        return []
    return [
        (_APP_FIXTURES.name, url.rstrip(".,;)"))
        for url in _URL_RE.findall(_APP_FIXTURES.read_text(encoding="utf-8"))
    ]


def test_the_app_fixtures_are_in_scope_at_all() -> None:
    """Guard the guard, and the reason this whole section exists.

    THE GAP THIS CLOSES. The 131-citation cleanup this file documents globbed
    `data/seed/` and nothing else, so `app/lib/fixtures.ts` — the fallback records the
    UI renders whenever the backend is unreachable — was never in scope. It went on
    carrying `https://vcbrain.local/proof/pc-an-1#turn-2`, the EXACT url named in this
    module's own docstring as the original bug report, plus `arxiv.org/abs/2401.09417`,
    `arxiv.org/abs/2405.11238` and `news.ycombinator.com/item?id=38911204` — ids that
    resolve to real, unrelated papers and threads. `app/lib/api.ts` serves these on any
    failed call and `TraceDrawer.tsx` renders them as clickable hrefs.

    The page-level "FIXTURE DATA" banner was already correct and was not enough: it tells
    a reviewer the RECORD is authored, then hands them a citation that opens a real arXiv
    paper. A fallback may degrade; it may not fabricate. If this file is ever deleted in
    favour of honest empty states — the better end state — this test simply goes vacuous
    rather than failing.
    """
    if not _APP_FIXTURES.exists():
        pytest.skip("app/lib/fixtures.ts has been removed in favour of empty states")
    assert len(_app_fixture_urls()) >= 20, "expected a populated fallback fixture corpus"


@pytest.mark.parametrize("fixture,url", _app_fixture_urls())
def test_app_fixture_citations_cannot_pass_as_real(fixture: str, url: str) -> None:
    assert _is_visibly_unreal(url), (
        f"{fixture} cites {url!r}. These records are served by `app/lib/api.ts` whenever "
        f"a live call fails and are rendered as clickable links by `TraceDrawer.tsx`, so "
        f"a citation shaped like a real source sends a reviewer to a real, unrelated page. "
        f"Use the reserved {_RESERVED_TLD} TLD or a non-web scheme "
        f"({', '.join(sorted(_NON_WEB_SCHEMES))})."
    )


@pytest.mark.parametrize("fixture,url", _app_fixture_urls())
def test_app_fixture_citations_never_borrow_a_real_hosts_name(fixture: str, url: str) -> None:
    host = (urlparse(url).netloc or "").split(":")[0].lower()
    if not host:
        return
    labels = host.removesuffix(_RESERVED_TLD).strip(".").split(".")
    assert not any(
        label in {"com", "org", "net", "io", "dev", "ai", "co", "ru", "cn"}
        for label in labels
    ), f"{fixture} cites {url!r}, which embeds a real-looking domain suffix"


def test_no_fixture_cites_the_reserved_demo_host() -> None:
    """`vcbrain.local` is not a real domain, but it is not visibly unreal either.

    `.local` is mDNS, not a reserved documentation TLD: a reader cannot tell from the
    string whether it resolves, and on some networks it will try to. It is also the exact
    shape of the original bug report. Banned by name so it cannot drift back in.
    """
    if not _APP_FIXTURES.exists():
        pytest.skip("app/lib/fixtures.ts has been removed in favour of empty states")
    text = _APP_FIXTURES.read_text(encoding="utf-8")
    assert "vcbrain.local" not in text, (
        "vcbrain.local is back in the fallback fixtures — use deck://, proof:// or the "
        "reserved example.invalid TLD"
    )


def test_every_constructed_fixture_is_classified_constructed() -> None:
    """The fixture and `provenance.py` must agree, or the label is decorative."""
    from data.seed.provenance import provenance_for
    from schema.events import CompanyProvenance

    checked = 0
    for name in _CONSTRUCTED_FIXTURES:
        path = SEED / name
        if not path.exists():
            continue
        blob = json.loads(path.read_text(encoding="utf-8"))
        for profile in blob.get("profiles", []):
            assert (
                provenance_for(profile["company_name"]) is CompanyProvenance.CONSTRUCTED
            ), f"{profile['company_name']} is not classified CONSTRUCTED"
            checked += 1
    assert checked, "no constructed profiles were checked"
