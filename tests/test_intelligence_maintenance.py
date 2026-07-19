"""maintenance_after_launch: three outcomes, and the third one is the point.

fired / not-fired / NOT APPLICABLE. The tests that matter here are the ones
pinning the third state, because collapsing it into "not fired" turns the rule
into a young-project penalty wearing a quality signal's clothes -- and in a
pre-seed corpus that is most of the population.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from intelligence import flags
from intelligence.flags import MAINTENANCE_MATURITY_DAYS, MAINTENANCE_MILESTONE_DAYS
from schema.events import Event, EventKind, Source

UTC = timezone.utc
RULE = "maintenance_after_launch"
LAUNCH = datetime(2023, 1, 1, tzinfo=UTC)


def _release(at: datetime, entity_id, title: str = "v1.0.0") -> Event:
    return Event(
        entity_id=entity_id,
        kind=EventKind.RELEASE,
        source=Source.GITHUB,
        observed_at=at,
        payload={"repo": "thing", "title": title},
    )


def _activity(at: datetime, entity_id) -> Event:
    return Event(
        entity_id=entity_id,
        kind=EventKind.REPO_ACTIVITY,
        source=Source.GITHUB,
        observed_at=at,
        payload={"repo": "thing"},
    )


def _rows(events, entity_id, as_of):
    return {f.payload["rule_id"]: f.payload["fired"] for f in flags.evaluate_events(events, entity_id, as_of)}


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------


def test_fires_when_launch_is_mature_and_maintenance_is_present() -> None:
    e = uuid4()
    events = [
        _release(LAUNCH, e),
        _release(LAUNCH + timedelta(days=120), e, "v1.0.1"),
        _release(LAUNCH + timedelta(days=300), e, "v1.0.2"),
    ]
    rows = _rows(events, e, LAUNCH + timedelta(days=400))
    assert rows[RULE] is True


def test_does_not_fire_when_launch_is_mature_and_nothing_followed() -> None:
    """The abandoned-demo case -- the valuable negative, and a real finding."""
    e = uuid4()
    events = [_release(LAUNCH, e)]
    rows = _rows(events, e, LAUNCH + timedelta(days=400))
    assert RULE in rows, "a mature launch must be JUDGED, not skipped"
    assert rows[RULE] is False


def test_is_not_evaluated_at_all_when_the_launch_is_too_recent() -> None:
    """The load-bearing test. Too recent to tell is NOT abandoned.

    The rule must be absent from the evaluated set entirely, so it never reaches
    the y_t denominator. Asserting `rows[RULE] is False` would pass for a rule
    that had silently become a young-project penalty; asserting absence does not.
    """
    e = uuid4()
    events = [_release(LAUNCH, e)]
    as_of = LAUNCH + timedelta(days=MAINTENANCE_MATURITY_DAYS - 1)
    assert RULE not in _rows(events, e, as_of)


def test_is_not_evaluated_when_nothing_has_launched() -> None:
    e = uuid4()
    events = [_activity(LAUNCH, e), _activity(LAUNCH + timedelta(days=400), e)]
    assert RULE not in _rows(events, e, LAUNCH + timedelta(days=500))


def test_maturity_boundary_is_exactly_the_stated_window() -> None:
    e = uuid4()
    events = [_release(LAUNCH, e)]
    just_before = LAUNCH + timedelta(days=MAINTENANCE_MATURITY_DAYS) - timedelta(seconds=1)
    exactly = LAUNCH + timedelta(days=MAINTENANCE_MATURITY_DAYS)
    assert RULE not in _rows(events, e, just_before)
    assert RULE in _rows(events, e, exactly)


# ---------------------------------------------------------------------------
# What counts as maintenance
# ---------------------------------------------------------------------------


def test_activity_just_after_launch_is_not_maintenance() -> None:
    """Work inside the launch spike is the launch, not maintenance."""
    e = uuid4()
    early = MAINTENANCE_MILESTONE_DAYS[0] - 10
    events = [_release(LAUNCH, e), _activity(LAUNCH + timedelta(days=early), e)]
    rows = _rows(events, e, LAUNCH + timedelta(days=400))
    assert rows[RULE] is False


def test_commits_without_a_new_release_still_count_as_maintenance() -> None:
    """A project kept alive with commits is maintained even without a new tag."""
    e = uuid4()
    events = [
        _release(LAUNCH, e),
        _activity(LAUNCH + timedelta(days=MAINTENANCE_MILESTONE_DAYS[0] + 30), e),
    ]
    rows = _rows(events, e, LAUNCH + timedelta(days=400))
    assert rows[RULE] is True


def test_not_applicable_does_not_change_the_observation() -> None:
    """A skipped rule must neither help nor hurt -- it stays out of the denominator."""
    e = uuid4()
    launched = [_release(LAUNCH, e)]
    recent = flags.observation(
        flags.evaluate_events(launched, e, LAUNCH + timedelta(days=MAINTENANCE_MATURITY_DAYS - 1))
    )
    # Same evidence, same rules fired, only the calendar differs.
    mature = flags.observation(
        flags.evaluate_events(launched, e, LAUNCH + timedelta(days=400))
    )
    # The mature reading is judged and fails; the recent one is not judged at all,
    # so its yes-rate must be the HIGHER of the two.
    assert recent[0] > mature[0]


def test_the_rule_is_registered_against_a_trait() -> None:
    """SOURCES.md specifies the signal; traits.json must actually route it."""
    import json
    from pathlib import Path

    blob = json.loads(
        (Path(__file__).resolve().parent.parent / "data" / "traits.json").read_text("utf-8")
    )
    owners = [t["id"] for t in blob["traits"] if RULE in (t.get("flag_rules") or [])]
    assert owners == ["iteration_velocity"], (
        "maintenance_after_launch evidences sustained iteration on one artifact. "
        "traits.json already lists it as a registry_signal of iteration_velocity; "
        "the flag rule must be routed to the same trait."
    )


def test_rule_exists_with_the_specified_weight() -> None:
    rule = next(r for r in flags.RULES if r.rule_id == RULE)
    assert rule.weight == 3.0
    assert rule.applicable_when is not None
