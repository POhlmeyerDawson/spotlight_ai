"""Dissent Engine. Owner: C. The LLM is always mocked — no live calls in CI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from core import llm
from intelligence import dissent
from memory import db, store
from schema.events import AntiMemo, Event, EventKind, Source

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=365)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    yield
    db.reset_connections()


def _seed_company() -> tuple:
    company_id, entity_id = uuid4(), uuid4()
    event_id = store.append(
        Event(
            company_id=company_id,
            entity_id=entity_id,
            kind=EventKind.DECK_CLAIM,
            source=Source.DECK,
            observed_at=T0,
            payload={"claim": "40 design partners are live in production."},
            evidence_span="slide 9",
        )
    )
    return company_id, event_id


def _mock_llm(monkeypatch, response: dict) -> dict:
    seen: dict = {}

    def fake(prompt, *, system=None, tier="fast", untrusted=None, json_mode=False, **kw):
        seen.update(prompt=prompt, system=system, untrusted=untrusted, tier=tier)
        return dict(response)

    monkeypatch.setattr(llm, "complete", fake)
    return seen


def _response(**over) -> dict:
    base = {
        "bear_case": "The production claim rests on one self-reported slide and nothing else.",
        "weakest_evidence": ["the design-partner count has no independent corroboration"],
        "load_bearing_claim": "40 design partners are live in production.",
        "axes": {
            "founder": {"bull": 0.7, "bear": 0.4},
            "market": {"bull": 0.6, "bear": 0.5},
            "idea_vs_market": {"bull": 0.8, "bear": 0.2},
        },
    }
    base.update(over)
    return base


def _anti_memo(**spreads) -> AntiMemo:
    return AntiMemo(
        company_id=uuid4(),
        bear_case="x",
        weakest_evidence=["x"],
        load_bearing_claim="x",
        axis_spreads=spreads,
    )


# ---------------------------------------------------------------------------
# the anti-memo
# ---------------------------------------------------------------------------


def test_generate_names_a_load_bearing_claim(monkeypatch) -> None:
    company_id, _ = _seed_company()
    _mock_llm(monkeypatch, _response())

    memo = dissent.generate(company_id, as_of=LATER)
    assert memo.load_bearing_claim.strip()
    assert memo.bear_case.strip()
    assert memo.weakest_evidence
    assert set(memo.axis_spreads) == set(dissent.AXES)


@pytest.mark.parametrize(
    "response",
    [_response(load_bearing_claim=""), _response(load_bearing_claim=None), {}],
)
def test_load_bearing_claim_is_never_empty_even_when_the_model_refuses(
    monkeypatch, response
) -> None:
    """The required output. A blank here would let the UI render a dissent with no teeth."""
    company_id, _ = _seed_company()
    _mock_llm(monkeypatch, response)

    memo = dissent.generate(company_id, as_of=LATER)
    assert memo.load_bearing_claim.strip()
    assert memo.bear_case.strip()
    assert memo.weakest_evidence


def test_prompt_is_adversarial_and_evidence_travels_untrusted(monkeypatch) -> None:
    company_id, _ = _seed_company()
    seen = _mock_llm(monkeypatch, _response())
    dissent.generate(company_id, as_of=LATER)

    assert "kill this deal" in seen["system"].lower()
    assert seen["tier"] == "deep"
    # Founder-supplied deck text is data, never prompt (Invariant #4).
    assert "40 design partners" in (seen["untrusted"] or "")
    assert "40 design partners" not in seen["prompt"]


def test_fabricated_event_ids_are_replaced_with_unknown(monkeypatch) -> None:
    company_id, real_id = _seed_company()
    ghost = uuid4()
    _mock_llm(
        monkeypatch,
        _response(
            bear_case=f"Event {ghost} contradicts event {real_id} outright.",
            weakest_evidence=[f"nothing corroborates {ghost}"],
        ),
    )

    memo = dissent.generate(company_id, as_of=LATER)
    assert str(ghost) not in memo.bear_case
    assert str(ghost) not in memo.weakest_evidence[0]
    assert dissent.UNKNOWN in memo.bear_case
    assert str(real_id) in memo.bear_case  # the real citation survives


def test_generate_is_as_of_scoped(monkeypatch) -> None:
    company_id, _ = _seed_company()
    store.append(
        Event(
            company_id=company_id,
            kind=EventKind.DECK_CLAIM,
            source=Source.DECK,
            observed_at=LATER,
            payload={"claim": "a future claim that must not leak backwards"},
        )
    )
    seen = _mock_llm(monkeypatch, _response())
    dissent.generate(company_id, as_of=T0)
    assert "must not leak backwards" not in (seen["untrusted"] or "")


# ---------------------------------------------------------------------------
# spread -> uncertainty
# ---------------------------------------------------------------------------


def test_uncertainty_rises_as_spread_widens() -> None:
    values = [
        dissent.uncertainty_from_spread(_anti_memo(founder=s, market=s, idea_vs_market=s))
        for s in (0.0, 0.1, 0.3, 0.6, 0.9)
    ]
    assert values == sorted(values)
    assert values[0] < values[-1]
    assert all(0.0 <= v <= 1.0 for v in values)


def test_one_wide_axis_dominates_the_uncertainty() -> None:
    """A great founder on an undecidable market is not an average — it is a no-call."""
    narrow = _anti_memo(founder=0.1, market=0.1, idea_vs_market=0.1)
    one_wide = _anti_memo(founder=0.1, market=0.1, idea_vs_market=0.9)
    assert dissent.uncertainty_from_spread(one_wide) > dissent.uncertainty_from_spread(narrow)


def test_no_spreads_means_unknown_not_certain() -> None:
    assert dissent.uncertainty_from_spread(_anti_memo()) == dissent.UNKNOWN_UNCERTAINTY


def test_malformed_axis_scores_are_dropped_not_guessed(monkeypatch) -> None:
    company_id, _ = _seed_company()
    _mock_llm(
        monkeypatch,
        _response(
            axes={
                "founder": {"bull": "high", "bear": 0.2},
                "market": {"bull": 0.9, "bear": 0.3},
                "idea_vs_market": "wide",
            }
        ),
    )
    memo = dissent.generate(company_id, as_of=LATER)
    assert memo.axis_spreads == {"market": pytest.approx(0.6)}
