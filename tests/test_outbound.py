"""Outbound cold-reach: the eligibility gate, the anti-hallucination gate, the queue.

OFFLINE. Every LLM call is monkeypatched; so are the gate, the validator and the memo's
cheque, because this file is testing outbound's own rules and not a second copy of theirs.

The two properties worth stating up front, because the rest of the file is in service of
them:

  1. A company is drafted for ONLY when every independent decision came out in its
     favour. The tests below turn each of those decisions negative in turn and assert
     that outbound refuses. If someone later relaxes eligibility to widen the funnel,
     one of these fails.

  2. A draft whose content cannot be resolved to a stored event is REJECTED, not flagged.
     "Rejected" is tested as: it never appears in the review queue, in any status filter
     a reviewer would plausibly use. A warning field on a queued draft would pass a
     weaker version of this test and would be worthless.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from schema.events import CompanyProvenance, ClaimStatus, ClaimVerdict, Event, EventKind, GateDecision, GateOutcome
from sourcing import outreach

AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_outbound():
    """The outbound tables live outside the event store, so store.reset() misses them."""
    c = outreach.conn()
    for table in ("outbound_drafts", "outbound_suppression"):
        c.execute(f"delete from {table}")
    c.commit()
    yield
    for table in ("outbound_drafts", "outbound_suppression"):
        c.execute(f"delete from {table}")
    c.commit()


def _event(company_id: UUID, entity_id: UUID, **kw) -> Event:
    base = dict(
        company_id=company_id,
        entity_id=entity_id,
        kind=EventKind.REPO_ACTIVITY,
        source="github",
        source_url="https://github.com/tensorpage/pagekv/commits/main",
        observed_at=AS_OF - timedelta(days=30),
        evidence_span='commit 4b91e0c "pagekv: block table with refcounted physical pages"',
        payload={"repo": "tensorpage/pagekv", "topic": "block-table allocator", "stars": 1800},
    )
    base.update(kw)
    return Event(**base)


@pytest.fixture
def company() -> tuple[UUID, UUID]:
    """A company with two citable build artifacts and a resolved founder."""
    from memory import store

    cid = store.upsert_company("Tensorpage", archetype=1, provenance=CompanyProvenance.SOURCED)
    eid = store.upsert_entity("Marisol Ferreira", "marisol ferreira")
    store.append(_event(cid, eid))
    store.append(
        _event(
            cid,
            eid,
            kind=EventKind.RELEASE,
            source_url="https://github.com/tensorpage/pagekv/releases/tag/v0.4.0",
            observed_at=AS_OF - timedelta(days=10),
            evidence_span='v0.4.0 — "per-tenant quotas, fail-closed. Two production users."',
            payload={"tag": "v0.4.0", "repo": "tensorpage/pagekv", "downloads_90d": 39800},
        )
    )
    return cid, eid


@pytest.fixture
def passing(monkeypatch, company):
    """Every upstream decision positive. Each test that needs a refusal flips one back."""
    cid, _ = company
    monkeypatch.setattr(
        "intelligence.gate.evaluate",
        lambda c, a, **k: GateDecision(
            company_id=c, outcome=GateOutcome.PROCEED, rationale="strong shipped trajectory"
        ),
    )
    monkeypatch.setattr("intelligence.validator.check_claims", lambda c, a=None: [])
    monkeypatch.setattr(
        "api.memo.recommendation",
        lambda c, a, v, g, s=None: {"decision": "invest", "amount_usd": 750_000.0, "reason": "x"},
    )
    return cid


GOOD_OUTPUT = {
    "subject": "pagekv per-tenant quotas",
    "observations": [
        {"text": "Your block table with refcounted physical pages in pagekv.", "ref": "e2"},
        {"text": "v0.4.0 made the per-tenant quotas fail-closed.", "ref": "e1"},
        {
            "text": "Why fail-closed on quota exhaustion rather than evicting the "
            "lowest-priority pages?",
            "ref": "e1",
        },
    ],
}

# Same content, one anchoring term short and over the free-token allowance.
VAGUE_QUESTION = {
    "subject": "pagekv",
    "observations": [
        {"text": "Your block table with refcounted physical pages in pagekv.", "ref": "e2"},
        {"text": "What are the biggest architectural challenges ahead for the roadmap?", "ref": "e2"},
    ],
}


def _llm(monkeypatch, output):
    """Stub the model. Fixtures list every line under `observations` for readability; the
    last one is moved into the `question` field the real contract requires, so the
    module's own parser is still what assembles them."""
    payload = dict(output)
    obs = list(payload.get("observations") or [])
    if obs:
        payload["observations"], payload["question"] = obs[:-1], obs[-1]
    monkeypatch.setattr("core.llm.complete", lambda *a, **k: payload)


# ---------------------------------------------------------------------------
# 1. Eligibility is a computed gate, not a threshold
# ---------------------------------------------------------------------------


def test_all_checks_pass_makes_a_company_eligible(passing):
    verdict = outreach.eligibility(passing, AS_OF)
    assert verdict["eligible"] is True
    assert verdict["blocked_by"] == []
    # Every check the docs promise is actually evaluated, not just the cheap ones.
    assert {c["name"] for c in verdict["checks"]} == {
        "not_suppressed",
        "gate_proceed",
        "no_contradicted_claims",
        "evidence_integrity",
        "red_lines",
        "recommendation_has_amount",
    }


@pytest.mark.parametrize("outcome", [GateOutcome.PROOF_PROTOCOL, GateOutcome.NO_CALL])
def test_only_proceed_is_eligible(monkeypatch, passing, outcome):
    monkeypatch.setattr(
        "intelligence.gate.evaluate",
        lambda c, a, **k: GateDecision(company_id=c, outcome=outcome, rationale="thin"),
    )
    verdict = outreach.eligibility(passing, AS_OF)
    assert verdict["eligible"] is False
    assert "gate_proceed" in verdict["blocked_by"]


def test_a_gate_that_cannot_be_evaluated_is_not_a_pass(monkeypatch, passing):
    def boom(c, a, **k):
        raise RuntimeError("gate offline")

    monkeypatch.setattr("intelligence.gate.evaluate", boom)
    assert outreach.eligibility(passing, AS_OF)["eligible"] is False


def test_a_single_contradicted_claim_blocks_outbound(monkeypatch, passing):
    monkeypatch.setattr(
        "intelligence.validator.check_claims",
        lambda c, a=None: [
            ClaimVerdict(
                company_id=c,
                claim_text="3x faster than vLLM",
                claim_source_span="slide 7",
                status=ClaimStatus.CONTRADICTED,
                trust=0.0,
            )
        ],
    )
    verdict = outreach.eligibility(passing, AS_OF)
    assert verdict["eligible"] is False
    assert "no_contradicted_claims" in verdict["blocked_by"]


def test_verified_claims_do_not_block(monkeypatch, passing):
    monkeypatch.setattr(
        "intelligence.validator.check_claims",
        lambda c, a=None: [
            ClaimVerdict(
                company_id=c,
                claim_text="two production users",
                claim_source_span="slide 3",
                status=ClaimStatus.VERIFIED,
                trust=0.9,
                corroborating_span="release notes",
            )
        ],
    )
    assert outreach.eligibility(passing, AS_OF)["eligible"] is True


def test_impeaching_integrity_flag_blocks_outbound(passing, company):
    from memory import store

    cid, eid = company
    store.append(_event(cid, eid, integrity_flags=["injection_stripped"]))
    verdict = outreach.eligibility(cid, AS_OF)
    assert verdict["eligible"] is False
    assert "evidence_integrity" in verdict["blocked_by"]


def test_non_impeaching_flags_do_not_block(passing, company):
    """A transliterated name is a note about provenance. Blanket-filtering integrity
    flags is what voided the whole Type 6 cohort elsewhere; it must not happen here."""
    from memory import store

    cid, eid = company
    store.append(_event(cid, eid, integrity_flags=["transliterated_name"]))
    assert outreach.eligibility(cid, AS_OF)["eligible"] is True


def test_a_refused_recommendation_blocks_outbound(monkeypatch, passing):
    monkeypatch.setattr(
        "api.memo.recommendation",
        lambda c, a, v, g, s=None: {
            "decision": "no_call",
            "amount_usd": None,
            "reason": "the founder band is too wide",
        },
    )
    verdict = outreach.eligibility(passing, AS_OF)
    assert verdict["eligible"] is False
    assert "recommendation_has_amount" in verdict["blocked_by"]
    assert "band" in verdict["why_not"]


class _RedLine:
    def __init__(self, statement, source="stated"):
        self.statement = statement
        self.source = source


def test_a_matching_stated_red_line_blocks_outbound(passing):
    lines = [_RedLine("no per-tenant multi-tenancy plays")]
    verdict = outreach.eligibility(passing, AS_OF, red_lines=lines)
    assert verdict["eligible"] is False
    assert "red_lines" in verdict["blocked_by"]
    assert "per-tenant" in verdict["why_not"]


def test_the_red_line_screen_is_lexical_and_says_so(passing):
    """Its limitation, tested rather than papered over. A red line phrased in words that
    appear nowhere in the evidence does NOT fire, so the check's own detail tells the
    reviewer that and the full list travels with the draft. Documenting a semantic
    guarantee this screen does not provide would be the worse failure."""
    lines = [_RedLine("nothing that touches defence procurement")]
    verdict = outreach.eligibility(passing, AS_OF, red_lines=lines)
    assert verdict["eligible"] is True
    detail = next(c["detail"] for c in verdict["checks"] if c["name"] == "red_lines")
    assert "lexical, not semantic" in detail


def test_a_revealed_candidate_red_line_is_not_a_rule(passing):
    """profiles.py's own contract: a revealed candidate is a pattern awaiting the user's
    confirmation. We do not get to invent a VC's red line and act on it by mailing."""
    lines = [_RedLine("no inference infrastructure", source="revealed_candidate")]
    assert outreach.eligibility(passing, AS_OF, red_lines=lines)["eligible"] is True


def test_suppression_blocks_before_anything_else_is_computed(passing):
    outreach.suppress(company_id=passing, reason="founder asked", source="opt_out")
    verdict = outreach.eligibility(passing, AS_OF)
    assert verdict["eligible"] is False
    assert verdict["blocked_by"] == ["not_suppressed"]
    # Nothing expensive ran — suppression is final, not one input among several.
    assert len(verdict["checks"]) == 1


# ---------------------------------------------------------------------------
# 2. Anti-hallucination: rejected, not flagged
# ---------------------------------------------------------------------------


def test_a_verified_draft_is_queued_with_resolved_citations(monkeypatch, passing):
    _llm(monkeypatch, GOOD_OUTPUT)
    row = outreach.draft(passing, AS_OF)

    assert row["status"] == outreach.QUEUED
    urls = {c["source_url"] for c in row["citations"]}
    assert urls == {
        "https://github.com/tensorpage/pagekv/releases/tag/v0.4.0",
        "https://github.com/tensorpage/pagekv/commits/main",
    }
    # Every citation resolves to an event that is actually in the store.
    from memory import store

    for c in row["citations"]:
        assert store.get_event(UUID(c["event_id"])) is not None
    # Every URL in the rendered mail is one of the stored ones — no others exist.
    import re

    for found in re.findall(r"https?://\S+", row["body"]):
        assert found in urls


def test_a_url_in_model_output_is_rejected_and_never_queued(monkeypatch, passing):
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                {
                    "text": "Saw pagekv at https://github.com/tensorpage/pagekv-core.",
                    "ref": "e1",
                }
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="URL-shaped"):
        outreach.draft(passing, AS_OF)

    # Recorded for the audit trail...
    recorded = outreach.history(passing)
    assert [r["status"] for r in recorded] == [outreach.REJECTED_UNVERIFIABLE]
    assert "github.com/tensorpage/pagekv-core" in recorded[0]["body"]
    # ...and absent from every queue a reviewer would read.
    for status in (outreach.QUEUED, outreach.APPROVED, outreach.REJECTED):
        assert outreach.queue(status) == []


@pytest.mark.parametrize(
    "text",
    [
        "Nice work on pagekv, see www.pagekv.dev for the docs.",
        "Ping me at partner@fund.com about pagekv.",
        "The pagekv writeup on tensorpage.io was clear.",
    ],
)
def test_every_shape_of_link_is_caught(monkeypatch, passing, text):
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                {"text": text, "ref": "e1"},
                {"text": "Why fail-closed on quota exhaustion?", "ref": "e1"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="URL-shaped"):
        outreach.draft(passing, AS_OF)


def test_an_invented_ref_is_rejected(monkeypatch, passing):
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                {"text": "Your work on pagekv.", "ref": "e99"},
                {"text": "Why fail-closed on quota exhaustion?", "ref": "e1"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="not one of"):
        outreach.draft(passing, AS_OF)
    assert outreach.queue() == []


def test_a_claim_not_in_the_cited_span_is_rejected(monkeypatch, passing):
    """The hard case: no URL, a valid ref, plausible prose — and a fabricated fact."""
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                {"text": "Your Series A round closing at 40 million.", "ref": "e1"},
                {"text": "Why fail-closed on quota exhaustion?", "ref": "e1"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="do not appear in the quoted span"):
        outreach.draft(passing, AS_OF)
    assert outreach.queue() == []


def test_a_fact_from_the_wrong_ref_is_rejected(monkeypatch, passing):
    """Grounding is per-line against the line's OWN ref, not the union. A claim backed by
    a different event than the one it cites would make the trace drill-down a lie."""
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                # "refcounted" belongs to the repo_activity event (e2), not the release.
                {"text": "In v0.4.0 you shipped refcounted physical pages.", "ref": "e1"},
                {"text": "Why fail-closed on quota exhaustion?", "ref": "e1"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="refcounted"):
        outreach.draft(passing, AS_OF)


def test_a_model_written_citation_marker_is_rejected(monkeypatch, passing):
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                {"text": "Your pagekv work [1].", "ref": "e1"},
                {"text": "Why fail-closed on quotas?", "ref": "e1"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="citation marker"):
        outreach.draft(passing, AS_OF)


def test_an_omitted_question_is_rejected(monkeypatch, passing):
    """The failure the positional contract kept producing against the live model: three
    good observations and no question. It must be a rejection, not an observation
    silently promoted into the question slot."""
    monkeypatch.setattr(
        "core.llm.complete",
        lambda *a, **k: {
            "subject": "pagekv quotas",
            "observations": [
                {"text": "Your block table with refcounted physical pages.", "ref": "e2"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="under the 2"):
        outreach.draft(passing, AS_OF)


def test_a_generic_question_is_rejected(monkeypatch, passing):
    """The tone requirement and the grounding requirement fail the same sentence. A
    question built out of "architectural challenges" and "roadmap" is not a question
    about them, and it is not something a technical founder replies to."""
    _llm(monkeypatch, VAGUE_QUESTION)
    with pytest.raises(outreach.Unverifiable):
        outreach.draft(passing, AS_OF)


def test_the_last_line_must_be_a_question(monkeypatch, passing):
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                {"text": "Your block table with refcounted physical pages.", "ref": "e2"},
                {"text": "Let me know if you want to chat about pagekv.", "ref": "e2"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable, match="not a question"):
        outreach.draft(passing, AS_OF)


def test_the_model_is_never_shown_a_url(monkeypatch, passing):
    """The mechanism, asserted directly: if no URL reaches the model, no URL can come
    back. Covers the prompt, the system message AND the untrusted block — a stored
    `artifact_link` in a payload is still a link."""
    from memory import store

    cid = passing
    store.append(
        _event(
            cid,
            store.upsert_entity("Marisol Ferreira", "marisol ferreira"),
            kind=EventKind.PAPER,
            source="arxiv",
            source_url="https://arxiv.org/abs/2405.09912",
            observed_at=AS_OF - timedelta(days=5),
            evidence_span='"2.3x increase in achievable batch size at 128k context."',
            payload={"artifact_link": "https://github.com/tensorpage/pagekv", "authors": 2},
        )
    )
    seen: dict = {}
    # Built against the refs as they stand AFTER the paper is added — newest first, so
    # the paper is e1. This is also the case that matters: the paper's payload carries a
    # stored `artifact_link`, which must not survive into the untrusted block.
    grounded = {
        "subject": "pagekv at 128k context",
        "observations": [
            {"text": "A 2.3x increase in achievable batch size at 128k context.", "ref": "e1"},
            {"text": "What made 128k the context length worth reporting against?", "ref": "e1"},
        ],
    }

    def capture(prompt, **kw):
        seen.update({"prompt": prompt, **kw})
        return grounded

    monkeypatch.setattr("core.llm.complete", capture)
    outreach.draft(cid, AS_OF)

    everything = " ".join(str(v) for v in seen.values())
    assert "http" not in everything
    assert "github.com" not in everything
    assert "arxiv.org" not in everything


def test_no_citable_evidence_means_no_email(monkeypatch, passing):
    """A company we know nothing quotable about does not get a generic mail instead."""
    from memory import store

    cid = store.upsert_company("Baseplate Systems", provenance=CompanyProvenance.SOURCED)
    eid = store.upsert_entity("Nobody", "nobody")
    # A green flag is our inference about them, not an observation of them.
    store.append(_event(cid, eid, kind=EventKind.GREEN_FLAG, source="manual", source_url=None))
    monkeypatch.setattr(
        "intelligence.gate.evaluate",
        lambda c, a, **k: GateDecision(
            company_id=c, outcome=GateOutcome.PROCEED, rationale="ok"
        ),
    )
    _llm(monkeypatch, GOOD_OUTPUT)
    with pytest.raises(outreach.Unverifiable, match="no citable evidence"):
        outreach.draft(cid, AS_OF)


def test_refs_exclude_uncitable_events(passing, company):
    """The model cannot cite something uncitable because uncitable things are not in the
    list it is handed. That filter IS the guarantee."""
    from memory import store

    cid, eid = company
    store.append(_event(cid, eid, source_url=None, evidence_span="no url on this one"))
    store.append(_event(cid, eid, evidence_span=None, source_url="https://example.com/x"))
    store.append(_event(cid, eid, integrity_flags=["injection_stripped"]))
    store.append(_event(cid, eid, kind=EventKind.GREEN_FLAG))

    got = outreach.refs(cid, AS_OF)
    assert all(r.source_url and r.evidence_span for r in got)
    assert all(r.kind in {str(k) for k in outreach.CITABLE_KINDS} for r in got)
    assert "injection_stripped" not in " ".join(r.evidence_span for r in got)


def test_refs_respect_as_of(passing, company):
    cid, eid = company
    from memory import store

    store.append(
        _event(
            cid,
            eid,
            observed_at=AS_OF + timedelta(days=400),
            evidence_span="a future commit that must not be quotable",
            source_url="https://github.com/tensorpage/pagekv/commits/future",
        )
    )
    assert all(r.observed_at <= AS_OF for r in outreach.refs(cid, AS_OF))


def test_drafting_an_ineligible_company_raises_before_any_model_call(monkeypatch, passing):
    monkeypatch.setattr(
        "intelligence.gate.evaluate",
        lambda c, a, **k: GateDecision(
            company_id=c, outcome=GateOutcome.NO_CALL, rationale="no"
        ),
    )

    def explode(*a, **k):
        raise AssertionError("the model must not be called for an ineligible company")

    monkeypatch.setattr("core.llm.complete", explode)
    with pytest.raises(outreach.Unverifiable, match="not eligible"):
        outreach.draft(passing, AS_OF)


# ---------------------------------------------------------------------------
# 3. The queue. Never auto-sent.
# ---------------------------------------------------------------------------


def test_nothing_in_this_feature_can_send_mail(monkeypatch, passing):
    """A structural check, not a behavioural one: an approve() that grew a send call
    would still pass every other test in this file."""
    import inspect

    from api.routers import outbound as router_mod

    source = inspect.getsource(outreach) + inspect.getsource(router_mod)
    for forbidden in ("smtplib", "sendgrid", "import ses", "postmark", "mailgun", "resend"):
        assert forbidden not in source


def test_approve_and_reject_record_a_person_and_a_time(monkeypatch, passing):
    _llm(monkeypatch, GOOD_OUTPUT)
    row = outreach.draft(passing, AS_OF)
    assert row["decided_at"] is None

    approved = outreach.approve(row["draft_id"], by="marisol@fund.example", note="send it")
    assert approved["status"] == outreach.APPROVED
    assert approved["decided_by"] == "marisol@fund.example"
    assert approved["decided_at"] is not None
    assert outreach.queue(outreach.QUEUED) == []
    assert len(outreach.queue(outreach.APPROVED)) == 1


def test_a_disposition_is_recorded_once(monkeypatch, passing):
    _llm(monkeypatch, GOOD_OUTPUT)
    row = outreach.draft(passing, AS_OF)
    outreach.reject(row["draft_id"], by="a-partner", note="tone")
    with pytest.raises(ValueError, match="not queued"):
        outreach.approve(row["draft_id"], by="another-partner")


def test_an_opt_out_between_drafting_and_review_blocks_approval(monkeypatch, passing):
    """The later fact wins. A queued draft is not a licence granted at draft time."""
    _llm(monkeypatch, GOOD_OUTPUT)
    row = outreach.draft(passing, AS_OF)
    outreach.suppress(company_id=passing, reason="unsubscribe link", source="opt_out")
    with pytest.raises(ValueError, match="suppressed"):
        outreach.approve(row["draft_id"], by="a-partner")


def test_a_suppressed_company_can_never_be_drafted_for(monkeypatch, passing):
    outreach.suppress(company_id=passing, reason="do not contact", source="manual")
    _llm(monkeypatch, GOOD_OUTPUT)
    with pytest.raises(outreach.Unverifiable):
        outreach.draft(passing, AS_OF)
    assert outreach.history(passing) == []


def test_history_includes_drafts_no_human_ever_saw(monkeypatch, passing):
    _llm(monkeypatch, GOOD_OUTPUT)
    outreach.draft(passing, AS_OF)
    _llm(
        monkeypatch,
        {
            "subject": "x",
            "observations": [
                {"text": "See https://x.dev now.", "ref": "e1"},
                {"text": "Why fail-closed on quota exhaustion?", "ref": "e1"},
            ],
        },
    )
    with pytest.raises(outreach.Unverifiable):
        outreach.draft(passing, AS_OF)

    statuses = sorted(r["status"] for r in outreach.history(passing))
    assert statuses == [outreach.QUEUED, outreach.REJECTED_UNVERIFIABLE]


def test_the_eligibility_verdict_is_snapshotted_onto_the_draft(monkeypatch, passing):
    """The reviewer must see what the system believed when it wrote the mail."""
    _llm(monkeypatch, GOOD_OUTPUT)
    row = outreach.draft(passing, AS_OF)
    assert row["eligibility"]["eligible"] is True
    assert any(
        c["name"] == "recommendation_has_amount" for c in row["eligibility"]["checks"]
    )


# ---------------------------------------------------------------------------
# 4. HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def test_eligible_endpoint_reports_both_halves(client, monkeypatch, passing):
    r = client.get("/outbound/eligible", params={"as_of": AS_OF.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert [c["name"] for c in body["eligible"]] == ["Tensorpage"]
    assert "PROCEED" in body["rule"]
    # Whoever is ineligible says which check refused, not just that it did.
    for row in body["ineligible"]:
        assert row["blocked_by"] and row["why_not"]


def test_draft_endpoint_queues_then_approves(client, monkeypatch, passing):
    _llm(monkeypatch, GOOD_OUTPUT)
    r = client.post(f"/outbound/draft/{passing}", params={"as_of": AS_OF.isoformat()})
    assert r.status_code == 200
    draft_id = r.json()["draft_id"]

    q = client.get("/outbound/queue").json()
    assert q["count"] == 1 and q["items"][0]["draft_id"] == draft_id

    assert client.post(f"/outbound/queue/{draft_id}/approve", json={}).status_code == 400
    ok = client.post(f"/outbound/queue/{draft_id}/approve", json={"by": "partner"})
    assert ok.status_code == 200 and ok.json()["status"] == outreach.APPROVED
    assert client.get("/outbound/queue").json()["count"] == 0


def test_draft_endpoint_returns_422_and_queues_nothing_on_a_bad_link(
    client, monkeypatch, passing
):
    _llm(
        monkeypatch,
        {
            "subject": "pagekv",
            "observations": [
                {"text": "Your pagekv fork at https://github.com/fake/repo.", "ref": "e1"},
                {"text": "Why fail-closed on quota exhaustion?", "ref": "e1"},
            ],
        },
    )
    r = client.post(f"/outbound/draft/{passing}", params={"as_of": AS_OF.isoformat()})
    assert r.status_code == 422
    assert "URL-shaped" in r.json()["detail"]
    assert client.get("/outbound/queue").json()["count"] == 0
    audit = client.get("/outbound/queue", params={"status": outreach.REJECTED_UNVERIFIABLE})
    assert audit.json()["count"] == 1


def test_suppression_endpoints(client, monkeypatch, passing):
    r = client.post(
        "/outbound/suppression",
        json={"company_id": str(passing), "reason": "replied no thanks", "source": "opt_out"},
    )
    assert r.status_code == 200
    listing = client.get("/outbound/suppression").json()
    assert listing["count"] == 1 and listing["items"][0]["source"] == "opt_out"

    _llm(monkeypatch, GOOD_OUTPUT)
    blocked = client.post(f"/outbound/draft/{passing}", params={"as_of": AS_OF.isoformat()})
    assert blocked.status_code == 422
    assert "suppress" in blocked.json()["detail"]


def test_suppression_needs_a_target(client):
    r = client.post("/outbound/suppression", json={"reason": "nothing to suppress"})
    assert r.status_code == 400


def test_unknown_company_is_404(client):
    assert client.post(f"/outbound/draft/{uuid4()}").status_code == 404
    assert client.get("/outbound/eligible", params={"company_id": "nope"}).status_code == 404
