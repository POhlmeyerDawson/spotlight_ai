"""Two cross-module failures that no single owner could see.

Both are silent: nothing errors, the numbers just quietly become untrustworthy.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from api import attest, memo
from core import pipeline
from schema.events import Event, EventKind, Source

T0 = datetime(2024, 6, 1, tzinfo=timezone.utc)


# --- 1. as_of must reach the validator ---------------------------------------


def test_memo_threads_as_of_into_the_validator() -> None:
    """A memo generated at a historical cutoff must not validate against today.

    check_claims defaults as_of to now(), so omitting the argument is a lookahead
    leak that raises nothing and shows up only as a backtest that looks too good.
    """
    src = inspect.getsource(memo._verdicts)
    assert "as_of" in inspect.signature(memo._verdicts).parameters
    assert "check_claims(company_id, as_of)" in src

    caller = inspect.getsource(memo.generate_memo)
    assert "_verdicts(cid, as_of)" in caller, "generate_memo must pass its cutoff through"


def test_pipeline_threads_as_of_into_the_validator() -> None:
    assert "as_of" in inspect.signature(pipeline._check_claims).parameters
    assert "check_claims(company_id, as_of)" in inspect.getsource(pipeline._check_claims)


def test_pipeline_does_not_duplicate_validator_events() -> None:
    """The validator persists its own VALIDATION_RESULT, stamped when the evidence
    existed. Writing a second copy at the run cutoff both duplicated the verdict and
    dated it wrongly."""
    src = inspect.getsource(pipeline.derive)
    assert "EventKind.VALIDATION_RESULT" not in src


# --- 2. the proof trace must be attested --------------------------------------


def _events(confidence: float = 0.8) -> list[Event]:
    return [
        Event(
            kind=EventKind.PROOF_BEHAVIOR,
            source=Source.PROOF_PROTOCOL,
            observed_at=T0,
            confidence=confidence,
            payload={"value": 0.9, "y": 0.9},
        )
    ]


def test_self_reported_pushback_is_not_counted_as_observed() -> None:
    """The whole point: asserting you pushed back must not buy the same weight as
    having been observed doing it."""
    attest.reset()
    trace = {"pushed_back_on_constraint": True, "questions_asked": ["is 32 fixed?"]}
    _, att = attest.attest("unknown-challenge", trace)

    assert att["challenge_anchored"] is False
    assert "pushed_back_on_constraint" in att["self_reported_fields"]
    assert "pushed_back_on_constraint" not in att["attested_fields"]
    assert att["trust"] < 0.6


def test_server_anchored_submission_scores_higher_trust() -> None:
    attest.reset()
    attest.record_issue("ch-1", T0)
    _, anchored = attest.attest("ch-1", {"pushed_back_on_constraint": True})
    _, floating = attest.attest("ch-2", {"pushed_back_on_constraint": True})

    assert anchored["challenge_anchored"] is True
    assert "started_at" in anchored["attested_fields"]
    assert anchored["trust"] > floating["trust"]


def test_client_supplied_timestamps_are_overwritten_by_server_observation() -> None:
    """A submitter claiming they finished in 90 seconds does not get to say so."""
    attest.reset()
    attest.record_issue("ch-1", T0)
    merged, _ = attest.attest(
        "ch-1",
        {"started_at": "2099-01-01T00:00:00+00:00", "submitted_at": "2099-01-01T00:01:00+00:00"},
    )
    assert merged["started_at"] == T0.isoformat()
    assert not merged["submitted_at"].startswith("2099")
    assert merged["elapsed_seconds"] > 0


def test_attestation_scales_event_confidence_down() -> None:
    """Trust multiplies confidence rather than sitting beside it — the filter treats
    PROOF_* events as low-noise, and an unattested trace must not buy that weight."""
    attest.reset()
    _, att = attest.attest("nope", {"pushed_back_on_constraint": True})
    out = attest.apply(_events(0.8), att)
    assert out[0].confidence < 0.8
    assert "unattested_trace" in out[0].integrity_flags
    assert out[0].payload["attestation"]["trust"] == att["trust"]


def test_demo_path_is_labelled_not_scored_as_real() -> None:
    attest.reset()
    _, att = attest.attest("ch", {}, demo=True)
    assert att["demo_seeded"] is True
    assert "pre-recorded" in att["note"]


# --- 3. the attestation must reach the grader, not only the events ------------


def test_attestation_travels_inside_the_trace() -> None:
    """C weights self-reported behaviour down at scoring time, so the grader needs
    to know what was observed BEFORE it produces a scalar. Post-grade confidence
    scaling stays as a second line of defence, not the only one."""
    attest.reset()
    attest.record_issue("ch-1", T0, "company-a")
    merged, att = attest.attest("ch-1", {"pushed_back_on_constraint": True})
    assert merged["attestation"] == att
    assert merged["attestation"]["self_reported_fields"] == ["pushed_back_on_constraint"]


# --- 4. a challenge is bound to the company it was written for ----------------


def test_challenge_is_bound_to_its_company() -> None:
    """A challenge is generated from ONE company's central technical claim. Grading
    it onto another company would let an easy challenge inflate an unrelated founder."""
    from uuid import uuid4

    attest.reset()
    a, b = uuid4(), uuid4()
    attest.record_issue("ch-1", T0, str(a))

    assert attest.challenge_belongs_to("ch-1", a) is True
    assert attest.challenge_belongs_to("ch-1", b) is False


def test_unknown_provenance_is_not_treated_as_a_mismatch() -> None:
    """None means 'cannot tell' and must not be conflated with False — a server
    restart loses the issue record, and that is our problem, not the founder's."""
    from uuid import uuid4

    attest.reset()
    assert attest.challenge_belongs_to("never-issued", uuid4()) is None


# --- 5. a transliterated name must not void a founder's evidence --------------


def test_provenance_flags_do_not_disqualify_evidence() -> None:
    """The Type 6 failure, caught in code rather than on stage.

    Every event belonging to a non-Latin-script founder carries `transliterated_name`.
    Excluding any event with ANY integrity flag therefore voided their entire evidence
    base: all three scored at the untouched prior, which reads as "average founder"
    rather than "we could not read this". The system built to find founders others
    cannot see was blind to precisely them.
    """
    from intelligence.flags import IMPEACHING_FLAGS

    # Provenance notes — they belong in the memo and the trace, never in a decision
    # to ignore the evidence.
    for annotation in (
        "transliterated_name",
        "non_english_source",
        "date_inferred",
        "ocr_low_conf",
        "low_visibility",
    ):
        assert annotation not in IMPEACHING_FLAGS, (
            f"{annotation!r} describes where evidence came from, not whether it can be "
            "trusted — treating it as disqualifying silently penalises exactly the "
            "founders this thesis exists to find"
        )

    # Tampered content genuinely cannot be trusted.
    assert "injection_stripped" in IMPEACHING_FLAGS
