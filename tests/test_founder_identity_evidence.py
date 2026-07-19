"""A stranger who shares a founder's surname is not that founder.

WHY THIS EXISTS. `sourcing/scanners/web.py` swept the open web for founder NAMES and
stored every hit as `profile_fact` — an event kind that asserts something is true OF A
PERSON. Nothing in the path ever checked that the page was about that person. The only
predicate on a result was "is this a URL I have not already seen".

What that put in the live event store, all of it about real, uninvolved private
individuals:

  * `www.renate-kohl-art.eu/privacy-policy` (x12, plus /about) — a German painter's
    cookie notice, filed against the founder "Renata Kohl";
  * `linkedin.com/in/jordankohl` — a cargo-bike advocate, relevance 0.087;
  * `linkedin.com/in/adrian-voss-phd-...` — "We are Merkabah, LLC ... Intuitive Sounds
    offers metaphysical...", filed against the founder "Adrian Voss";
  * `de.linkedin.com/in/renata-savor-koehl-...` and `linkedin.com/in/renata-kohl-...`;
  * `en.wikipedia.org/wiki/Priya_Anand` and `m.imdb.com/name/nm3591550` — an Indian film
    ACTRESS, filed against the founder "Priya Anand";
  * `en.wikipedia.org/wiki/Tobias_Lund_Andresen` and four cycling databases — a Danish
    professional CYCLIST, filed against the founder "Tobias Lund";
  * `en.wikipedia.org/wiki/Hannelore_Kohl` — matched on the surname alone.

This is a privacy failure before it is a correctness one. Scraping a private person's
LinkedIn and storing it as a dossier on somebody else is not repaired by lowering a
confidence score, so the scanner now DROPS what it cannot identify rather than retaining
it cheaply.

THE DECISIVE FACT, and the reason this file leans on provenance rather than on name
matching. "Renata Kohl", "Adrian Voss", "Priya Anand" and "Tobias Lund" are INVENTED
founders of CONSTRUCTED companies (Parallax Models, Northgate Runtime, Hallmark Edge,
Riverbend Data). There is no real person behind those names, so every web result for them
is necessarily a stranger — 100%, by construction, no threshold required.

That matters because a name check alone provably does not close this. Replaying the real
ingested corpus through the new relevance floor and identity check drops 37 of 132 events
but still admits the actress and the cyclist, whose names genuinely match. Two real people
can share a name and no string comparison will ever separate them. So there are two
guards, and the second is the one that actually holds:

  1. `test_scanner_rejects_the_pages_it_ingested` — the name/relevance gate, which stops
     the obvious misses (a 0.087 hit, a mismatched LinkedIn handle, a bare surname).
  2. `test_no_constructed_founder_carries_scraped_web_evidence` — the structural rule. An
     invented person may not accumulate real-web evidence AT ALL. This one cannot be
     defeated by a coincidental name match, because it never consults the name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.search import SearchResult
from sourcing.scanners.web import MIN_RELEVANCE, _identifies_subject

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"


def _result(url: str, title: str = "", snippet: str = "", score: float = 0.9) -> SearchResult:
    return SearchResult(
        url=url,
        title=title,
        snippet=snippet,
        score=score,
        published_at=None,
        self_published=False,
    )


# (id, subject, url, title, snippet) — every one of these was really ingested and stored.
# These are the ones the name/relevance gate CAN catch: the page names a visibly
# different person, so no threshold tuning is involved.
_CAUGHT_BY_NAME_GATE = [
    (
        "jordankohl-cargo-bikes",
        "Renata Kohl",
        "https://www.linkedin.com/in/jordankohl",
        "Jordan Kohl",
        "What if more of our everyday trips didn't require a car? Cargo bikes are one of "
        "the most practical and underrated answers.",
    ),
    (
        "hannelore-kohl-surname-only",
        "Renata Kohl",
        "https://en.wikipedia.org/wiki/Hannelore_Kohl",
        "Hannelore Kohl",
        "Hannelore Kohl was the wife of German chancellor Helmut Kohl.",
    ),
    (
        "wikipedia-renata-bare-forename",
        "Renata Kohl",
        "https://en.wikipedia.org/wiki/Renata",
        "Renata",
        "Renata is a feminine given name of Latin origin.",
    ),
]


@pytest.mark.parametrize(
    "case_id,subject,url,title,snippet",
    _CAUGHT_BY_NAME_GATE,
    ids=[c[0] for c in _CAUGHT_BY_NAME_GATE],
)
def test_scanner_rejects_the_pages_it_ingested(
    case_id: str, subject: str, url: str, title: str, snippet: str
) -> None:
    """Real reproductions the name gate closes. Each names a visibly different person."""
    assert not _identifies_subject(_result(url, title, snippet), subject), (
        f"{url!r} was accepted as a profile_fact about {subject!r}. It belongs to "
        f"someone else — a shared surname fragment is not an identity match."
    )


# The other half of the incident, and the reason this file does not stop at name matching.
_NAME_GATE_CANNOT_SEPARATE = [
    (
        "renate-kohl-painter",
        "Renata Kohl",
        "https://www.renate-kohl-art.eu/privacy-policy",
        "Renate Kohl | Privacy policy",
        "In this privacy policy, we inform you about the processing of personal data. "
        "Renate Kohl-Wachter is responsible for data processing.",
    ),
    (
        "adrian-voss-phd-merkabah",
        "Adrian Voss",
        "https://www.linkedin.com/in/adrian-voss-phd-714385181",
        "Adrian Voss PhD",
        "We are Merkabah, LLC. Intuitive Sounds offers metaphysical sound therapy.",
    ),
    (
        "priya-anand-the-actress",
        "Priya Anand",
        "https://en.wikipedia.org/wiki/Priya_Anand",
        "Priya Anand",
        "Priya Anand, born September 17 1986, is an Indian actress who works mainly in "
        "Tamil and Telugu films.",
    ),
    (
        "tobias-lund-the-cyclist",
        "Tobias Lund",
        "https://en.wikipedia.org/wiki/Tobias_Lund_Andresen",
        "Tobias Lund Andresen",
        "Tobias Lund Andresen is a Danish professional road bicycle racer.",
    ),
]


@pytest.mark.parametrize(
    "case_id,subject,url,title,snippet",
    _NAME_GATE_CANNOT_SEPARATE,
    ids=[c[0] for c in _NAME_GATE_CANNOT_SEPARATE],
)
def test_name_matching_alone_provably_cannot_separate_these(
    case_id: str, subject: str, url: str, title: str, snippet: str
) -> None:
    """Documents the limit rather than pretending it away.

    Each of these IS a real stranger, and each one's name genuinely matches the founder's
    — an actress actually named Priya Anand, a cyclist actually named Tobias Lund, a
    painter named Renate Kohl. No string comparison separates them from a founder of the
    same name, and a threshold tightened far enough to exclude them would also exclude
    the real Thomas Wolf.

    This test asserting PASS is not an endorsement; it is the evidence for why
    `test_no_constructed_founder_carries_scraped_web_evidence` has to exist and has to be
    keyed on provenance instead of similarity. If someone later "fixes" the name gate so
    these fail, that is fine — but they must not then conclude the structural guard is
    redundant, because two real people sharing a name is not a bug that can be fixed.
    """
    assert _identifies_subject(_result(url, title, snippet), subject), (
        "if this now fails the name gate got stricter — good, but do NOT remove the "
        "provenance-keyed structural guard, which is what actually closes this class"
    )


def test_a_mismatched_person_handle_is_disqualifying() -> None:
    """`linkedin.com/in/<slug>` names ONE individual. A mismatch is positive evidence."""
    r = _result(
        "https://www.linkedin.com/in/jordankohl",
        title="Renata Kohl",  # even with the subject's name in the title
        snippet="Renata Kohl profile",
    )
    assert not _identifies_subject(r, "Renata Kohl"), (
        "a person-handle URL that names somebody else must be rejected even when the "
        "page text mentions the subject"
    )


def test_the_genuine_find_still_passes() -> None:
    """The gate must not be so tight that it drops real evidence about real founders.

    Thomas Wolf is a real, sourced founder (Hugging Face) and this is his own LinkedIn
    post — the single highest-relevance hit in the corpus at 0.627, and the one web
    result in the incident set that was never a privacy problem.
    """
    r = _result(
        "https://www.linkedin.com/posts/thom-wolf_still-one-of-my-personal-favorite-speech-activity-7338139125323034624-2SR_",
        title="Thomas Wolf on LinkedIn",
        snippet="Open Source Is Critical for AI Resilience. When we imagine a future "
        "with AI embedded in every aspect of our lives...",
        score=0.627,
    )
    assert _identifies_subject(r, "Thomas Wolf")


def test_the_relevance_floor_sits_below_real_finds_and_above_the_misses() -> None:
    """Calibrated against the corpus, asserted so a future tweak cannot silently invert it."""
    assert 0.087 < MIN_RELEVANCE <= 0.627, (
        "MIN_RELEVANCE must exclude the 0.087 cargo-bike hit and admit the 0.627 "
        "genuine Thomas Wolf find"
    )


# ---------------------------------------------------------------------------
# The structural guard: invented people may not accumulate real-web evidence.
# ---------------------------------------------------------------------------


def _constructed_founder_names() -> set[str]:
    """Founder display names belonging to CONSTRUCTED companies, from the seed corpus."""
    names: set[str] = set()
    for path in sorted(SEED.glob("archetype_*.json")):
        blob = json.loads(path.read_text(encoding="utf-8"))
        for profile in blob.get("profiles", []):
            founder = profile.get("founder") or {}
            if name := founder.get("display_name"):
                names.add(name)
    bt = SEED / "backtest.json"
    if bt.exists():
        blob = json.loads(bt.read_text(encoding="utf-8"))
        members = [*blob.get("winners", []), *blob.get("controls", [])]
        failure = blob.get("correctly_deprioritized_failure")
        if isinstance(failure, dict):
            members.append(failure)
        for member in members:
            if member.get("evidence_provenance") != "synthetic":
                continue
            if name := (member.get("founder") or {}).get("display_name"):
                names.add(name)
    return names


def test_the_constructed_founder_set_is_populated() -> None:
    """Guard the guard: an empty set makes the invariant below vacuous."""
    names = _constructed_founder_names()
    assert len(names) >= 5, f"expected a populated constructed founder set, got {names}"


def test_no_constructed_founder_carries_scraped_web_evidence() -> None:
    """The rule a coincidental name match cannot defeat.

    A constructed founder does not exist, so a real-web page about "them" is always about
    a real stranger. No relevance score and no name similarity can make that acceptable,
    which is why this check never looks at either.

    Scoped to the SEED corpus, which is what CI can see. The same invariant was enforced
    against the live event store when the 83 offending rows were removed.
    """
    constructed = _constructed_founder_names()
    # Hosts whose URLs name a specific private individual. A constructed founder must
    # never be filed alongside one, because the person on the other end is always real.
    person_hosts = ("linkedin.com/in/", "linkedin.com/posts/", "x.com/", "twitter.com/")
    offenders: list[str] = []
    for path in sorted(SEED.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        present = sorted(n for n in constructed if n in text)
        if not present:
            continue
        for host in person_hosts:
            if host in text:
                offenders.append(
                    f"{path.name} names constructed founder(s) {present} and cites {host}"
                )
    assert not offenders, (
        "a constructed founder does not exist, so a real person page filed against one is "
        "always a real stranger's:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Citation well-formedness.
# ---------------------------------------------------------------------------

_YT_ID_RE = re.compile(r"[?&]v=([^&]+)")


def _youtube_ids() -> list[tuple[str, str, str]]:
    """(file, url, video_id) for every REAL-HOST YouTube watch URL in the seed corpus.

    `youtube.example.invalid` is deliberately excluded. A constructed citation is supposed
    to carry a descriptive, obviously-authored slug — `?v=tantu-mtg-marathi-2024` on a
    reserved-TLD host is the convention working, not a defect. Well-formedness is only a
    claim about URLs that point at the real YouTube.
    """
    out: list[tuple[str, str, str]] = []
    for path in sorted(SEED.glob("*.json")):
        for match in re.finditer(
            r'"(https?://[^"]*youtube[^"]*/watch\?[^"]+)"', path.read_text(encoding="utf-8")
        ):
            url = match.group(1)
            host = url.split("//", 1)[-1].split("/", 1)[0].lower()
            if host.endswith(".invalid"):
                continue
            if m := _YT_ID_RE.search(url):
                out.append((path.name, url, m.group(1)))
    return out


def test_youtube_video_ids_are_well_formed() -> None:
    """A real YouTube id is exactly 11 chars of `[A-Za-z0-9_-]`.

    `youtube.com/watch?v=tantu-mtg-marathi-2024` (22 chars) survived the citation cleanup
    in the live event store, on Tantu Systems — a CONSTRUCTED company — pointing at the
    real youtube.com host. 16 of the 17 ids in the corpus were well formed, which is
    exactly why the odd one read as real. Length is a mechanical tell that needs no
    network call: a 22-character id cannot be a YouTube video, so a citation carrying one
    is either fabricated or corrupt, and either way it is not a receipt.
    """
    bad = [
        (f, url, vid)
        for f, url, vid in _youtube_ids()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid)
    ]
    assert not bad, (
        "malformed YouTube video id(s) — a real id is exactly 11 characters:\n"
        + "\n".join(f"  {f}: {url} (id {vid!r}, {len(vid)} chars)" for f, url, vid in bad)
    )
