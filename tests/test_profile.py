"""VC profile: parsing, derivation, provenance, the empty state, and the gap.

The theme running through this file is the hard constraint the feature was built under:
everything in a profile must originate from something a real user submitted. Where the
data does not support an inference, the field is ABSENT and says why. Several tests below
exist purely to assert that we did NOT infer something.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from api.main import app
from memory import profiles
from schema.vc import SURVEY_QUESTIONS, Choice, DecisionKind, SurveyAnswer

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def client() -> TestClient:
    c = TestClient(app)
    c.post(
        "/auth/register",
        json={"email": f"vc-{uuid.uuid4().hex[:12]}@fund.example", "password": PASSWORD},
    )
    return c


@pytest.fixture(autouse=True)
def _clean_tables():
    c = profiles.conn()
    for table in (
        "vc_decisions",
        "vc_survey_answers",
        "vc_profiles",
        "sessions",
        "login_attempts",
        "users",
    ):
        c.execute(f"delete from {table}")
    yield


def _user(client: TestClient):
    return profiles.get_user_by_email(client.get("/auth/me").json()["user"]["email"])


# ---------------------------------------------------------------------------
# The survey catalog itself is a deliverable
# ---------------------------------------------------------------------------


def test_survey_is_twelve_forced_trade_offs():
    assert len(SURVEY_QUESTIONS) == 12
    ids = [q.id for q in SURVEY_QUESTIONS]
    assert len(set(ids)) == 12


def test_every_question_is_a_real_trade_off_with_signals_on_both_sides():
    """A question where only one option carries signal is not a trade-off — it is a
    leading question, and it would bias the derivation toward whoever answers it."""
    for q in SURVEY_QUESTIONS:
        assert q.option_a.signals, f"{q.id} option A carries no signal"
        assert q.option_b.signals, f"{q.id} option B carries no signal"
        assert q.option_a.text != q.option_b.text
        assert q.prompt.strip()


def test_no_question_references_pedigree():
    """SHARED.md Invariant #3 — no school, employer brand or investor name anywhere."""
    banned = ("stanford", "mit", "harvard", "google", "ex-", "alumni", "yc ", "sequoia", "a16z")
    for q in SURVEY_QUESTIONS:
        blob = f"{q.prompt} {q.option_a.text} {q.option_b.text}".lower()
        for word in banned:
            assert word not in blob, f"{q.id} references pedigree: {word!r}"


# ---------------------------------------------------------------------------
# Empty state is a real path, not an error
# ---------------------------------------------------------------------------


def test_new_profile_is_empty_and_personalisation_is_off_with_a_reason(client: TestClient):
    body = client.get("/profile").json()
    derived = body["derived"]

    assert derived["survey_answered"] == 0
    assert derived["decisions_count"] == 0
    assert derived["personalisation_enabled"] is False
    assert "OFF" in derived["personalisation_reason"]
    assert "core objective ranking is unaffected" in derived["personalisation_reason"]

    # Nothing invented to fill the gap.
    assert derived["axis_weights_stated"] is None
    assert derived["conviction_style_stated"] is None
    assert derived["sector_priors"] == []
    assert derived["stage_priors"] == []
    assert derived["red_lines"] == []
    # And every empty field says why it is empty.
    assert {n["field_name"] for n in derived["not_inferred"]} >= {
        "axis_weights_stated",
        "axis_weights_revealed",
        "conviction_style_stated",
        "conviction_style_revealed",
        "sector_priors",
        "stage_priors",
    }


def test_gap_on_an_empty_profile_reports_uncomputable_not_agreement(client: TestClient):
    """The failure this guards against: a gap report that omits what it could not compare
    reads as 'you are perfectly self-consistent', which is a fabrication about the user."""
    body = client.get("/profile/gap").json()
    assert body["findings"] == []
    assert body["agreements"] == []
    dims = {u["dimension"] for u in body["uncomputable"]}
    assert dims == {"conviction_style", "stage", "sector"}
    for entry in body["uncomputable"]:
        assert entry["missing"] in ("stated", "revealed")
        assert entry["reason"]


# ---------------------------------------------------------------------------
# Parsing — defensive, and it reports what it could not read
# ---------------------------------------------------------------------------


def test_csv_parses_and_reports_unreadable_rows_rather_than_dropping_them():
    csv_text = (
        "company,sector,stage,decision,date,rationale\n"
        "Northwind,ai-infra,seed,invested,2023-04-01,strong systems depth\n"
        "Bluefin,fintech,series a,passed,2023-06-11,crowded\n"
        "Ghostline,devtools,seed,teleported,2023-07-02,unclear\n"          # bad decision
        ",devtools,seed,passed,2023-08-01,no name\n"                        # no company
        "Cormorant,bio,pre-seed,watched,not-a-date,keeping an eye\n"        # bad date
    )
    decisions, result = profiles.parse_decisions(csv_text)

    assert result.total_rows == 5
    assert result.accepted == 3
    assert result.accepted + len(result.rejected) == result.total_rows

    reasons = {r.row_number: r.reason for r in result.rejected}
    assert "teleported" in reasons[3]
    assert "no company name" in reasons[4]

    # A bad date is a warning, not a rejection — the verdict and sector are still legible.
    assert [w.row_number for w in result.warnings] == [5]
    assert "unreadable date" in result.warnings[0].reason
    assert decisions[2].decided_on is None
    assert decisions[2].decision == DecisionKind.WATCHED


def test_json_upload_is_accepted_in_both_shapes():
    as_list = '[{"company":"Northwind","sector":"ai-infra","stage":"seed","decision":"invested"}]'
    wrapped = '{"decisions": [{"company":"Northwind","decision":"invested"}]}'
    for payload in (as_list, wrapped):
        decisions, result = profiles.parse_decisions(payload)
        assert result.accepted == 1
        assert decisions[0].company == "Northwind"


def test_header_aliases_and_decision_synonyms_are_tolerated():
    csv_text = (
        "Company Name,Industry,Round,Action,Decision Date\n"
        "Northwind,AI Infra,Seed,yes,2023-04-01\n"
        "Bluefin,Fintech,Series A,declined,2023-06-11\n"
    )
    decisions, result = profiles.parse_decisions(csv_text)
    assert result.accepted == 2
    assert decisions[0].decision == DecisionKind.INVESTED
    assert decisions[1].decision == DecisionKind.PASSED
    assert decisions[0].sector == "ai infra"


def test_ambiguous_date_is_refused_rather_than_guessed():
    """03/04/2024 is March 4th or April 3rd depending on locale. Picking one would
    invent a date the user never supplied."""
    decisions, result = profiles.parse_decisions(
        "company,decision,date\nNorthwind,invested,03/04/2024\n"
    )
    assert result.accepted == 1
    assert decisions[0].decided_on is None
    assert "ambiguous" in result.warnings[0].reason


def test_unambiguous_slash_date_is_resolved():
    decisions, _ = profiles.parse_decisions("company,decision,date\nNorthwind,invested,25/12/2023\n")
    assert decisions[0].decided_on.isoformat() == "2023-12-25"


def test_malformed_json_is_reported_not_swallowed():
    _, result = profiles.parse_decisions("{not json at all")
    assert result.accepted == 0
    assert "invalid JSON" in result.rejected[0].reason


def test_empty_upload_is_rejected_by_the_route(client: TestClient):
    assert client.post("/profile/decisions", content="   ").status_code == 400


# ---------------------------------------------------------------------------
# Derivation — stated side
# ---------------------------------------------------------------------------


def _answer_all(choice: Choice) -> list[dict]:
    return [{"question_id": q.id, "choice": str(choice)} for q in SURVEY_QUESTIONS]


def _answers_extreme(signal: str, *, maximise: bool) -> list[dict]:
    """Answer every question the way that pushes one signal as far as it will go.

    Deliberately NOT "all A". Because each question is a genuine multi-dimensional
    trade-off, option A is conviction-heavy on some questions and evidence-heavy on
    others — a respondent who picks A twelve times is a mixed respondent, not an extreme
    one, and testing the derivation against "all A" would be testing an accident of the
    catalog's ordering rather than the derivation.
    """
    out = []
    for q in SURVEY_QUESTIONS:
        a = q.option_a.signals.get(signal, 0.0)
        b = q.option_b.signals.get(signal, 0.0)
        pick = (a >= b) if maximise else (a <= b)
        out.append({"question_id": q.id, "choice": "a" if pick else "b"})
    return out


def test_survey_derives_axis_weights_with_provenance(client: TestClient):
    r = client.post("/profile/survey", json={"answers": _answer_all(Choice.A)})
    assert r.status_code == 200
    derived = r.json()["derived"]

    weights = derived["axis_weights_stated"]
    assert weights is not None
    total = weights["founder"] + weights["market"] + weights["idea_vs_market"]
    assert total == pytest.approx(1.0, abs=1e-3)

    # Every derived value names the questions that produced it.
    assert weights["provenance"]["basis"] == "survey"
    assert weights["provenance"]["n"] == len(weights["provenance"]["question_ids"])
    assert set(weights["provenance"]["question_ids"]) <= {q.id for q in SURVEY_QUESTIONS}
    assert weights["confidence"] == 1.0


def test_partial_survey_lowers_confidence_and_is_not_padded(client: TestClient):
    """A profile inferred from thin input must SAY it was inferred from thin input."""
    four = [{"question_id": q.id, "choice": "a"} for q in SURVEY_QUESTIONS[:4]]
    derived = client.post("/profile/survey", json={"answers": four}).json()["derived"]

    assert derived["survey_answered"] == 4
    assert derived["survey_total"] == 12
    assert derived["axis_weights_stated"]["confidence"] == pytest.approx(4 / 12, abs=1e-3)
    # The 8 unanswered questions contributed nothing — they were not imputed as neutral.
    assert derived["axis_weights_stated"]["provenance"]["n"] <= 4


def test_unknown_question_ids_are_ignored_and_named(client: TestClient):
    r = client.post(
        "/profile/survey",
        json={"answers": [{"question_id": "q99_invented", "choice": "a"}]},
    )
    body = r.json()
    assert body["stored"] == 0
    assert body["ignored_unknown_question_ids"] == ["q99_invented"]
    assert body["derived"]["axis_weights_stated"] is None


def test_opposite_answers_produce_opposite_conviction(client: TestClient):
    high = client.post(
        "/profile/survey", json={"answers": _answers_extreme("conviction", maximise=True)}
    ).json()["derived"]["conviction_style_stated"]
    low = client.post(
        "/profile/survey", json={"answers": _answers_extreme("conviction", maximise=False)}
    ).json()["derived"]["conviction_style_stated"]

    assert high["score"] > 0 > low["score"]
    assert high["label"] == "conviction-heavy"
    assert low["label"] == "evidence-heavy"


def test_a_uniform_respondent_is_not_forced_to_an_extreme(client: TestClient):
    """Picking option A twelve times must NOT read as maximum conviction. If it did, the
    questions would not be trade-offs — they would all be the same question."""
    uniform = client.post("/profile/survey", json={"answers": _answer_all(Choice.A)}).json()
    assert uniform["derived"]["conviction_style_stated"]["label"] == "balanced"


def test_survey_answers_are_upserted_not_duplicated(client: TestClient):
    client.post("/profile/survey", json={"answers": [{"question_id": "q01_founder_vs_market", "choice": "a"}]})
    body = client.post(
        "/profile/survey", json={"answers": [{"question_id": "q01_founder_vs_market", "choice": "b"}]}
    ).json()
    assert body["answered"] == 1
    user = _user(client)
    assert profiles.get_survey(user.user_id) == [
        SurveyAnswer(question_id="q01_founder_vs_market", choice=Choice.B)
    ]


# ---------------------------------------------------------------------------
# Derivation — revealed side
# ---------------------------------------------------------------------------

DECISIONS_CSV = (
    "company,sector,stage,decision,date,rationale,outcome\n"
    "Northwind,ai-infra,seed,invested,2022-03-01,systems depth,active\n"
    "Halyard,ai-infra,seed,invested,2022-07-14,compiler team,active\n"
    "Bluefin,ai-infra,series a,invested,2023-01-09,traction was real,active\n"
    "Torsion,devtools,seed,invested,2023-05-02,loved the primitive,active\n"
    "Cormorant,fintech,seed,passed,2022-04-11,regulatory drag,\n"
    "Marlin,fintech,series a,passed,2022-09-19,margins,\n"
    "Petrel,fintech,seed,passed,2023-02-27,crowded,\n"
    "Skua,fintech,series b,passed,2023-08-03,too late for us,\n"
    "Gannet,bio,seed,watched,2023-09-15,outside our depth,\n"
)


def _upload(client: TestClient, csv_text: str = DECISIONS_CSV):
    return client.post("/profile/decisions", content=csv_text)


def test_priors_come_only_from_invested_rows(client: TestClient):
    """A pass is not a preference for the thing passed on. fintech has four decisions
    here and must not appear in the sector priors, because all four were passes."""
    derived = _upload(client).json()["derived"]

    sectors = {p["key"]: p for p in derived["sector_priors"]}
    assert set(sectors) == {"ai-infra", "devtools"}
    assert sectors["ai-infra"]["count"] == 3
    assert sectors["ai-infra"]["share"] == pytest.approx(0.75)
    assert sum(p["share"] for p in derived["sector_priors"]) == pytest.approx(1.0)

    # Provenance points back at the actual uploaded rows.
    assert sectors["ai-infra"]["provenance"]["decision_rows"] == [1, 2, 3]


def test_unanimous_pass_pattern_is_a_candidate_red_line_never_an_asserted_rule(client: TestClient):
    derived = _upload(client).json()["derived"]
    candidates = [r for r in derived["red_lines"] if r["source"] == "revealed_candidate"]
    fintech = [r for r in candidates if "fintech" in r["statement"]]

    assert fintech, "four straight passes in one sector should surface as a candidate"
    assert fintech[0]["confidence"] < 1.0, "a pass streak is a pattern, not a proven rule"
    assert "possible red line" in fintech[0]["statement"]
    assert fintech[0]["provenance"]["n"] == 4


def test_stated_red_lines_are_taken_at_face_value(client: TestClient):
    client.put("/profile", json={"stated_red_lines": ["no defence contracts"]})
    derived = client.get("/profile").json()["derived"]
    stated = [r for r in derived["red_lines"] if r["source"] == "stated"]
    assert stated[0]["statement"] == "no defence contracts"
    assert stated[0]["confidence"] == 1.0
    assert stated[0]["provenance"]["basis"] == "profile_field"


def test_thin_history_yields_no_priors_and_says_why(client: TestClient):
    _upload(client, "company,sector,stage,decision\nNorthwind,ai-infra,seed,invested\n")
    derived = client.get("/profile").json()["derived"]

    assert derived["decisions_count"] == 1
    assert derived["sector_priors"] == []
    reasons = {n["field_name"]: n["reason"] for n in derived["not_inferred"]}
    assert "at least 3" in reasons["sector_priors"]
    assert "found 1 investment" in reasons["sector_priors"]


def test_revealed_axis_weights_are_never_inferred(client: TestClient):
    """THE DELIBERATE REFUSAL. Producing revealed axis weights would require per-axis
    scores for companies we hold no evidence about. The field stays empty and explains
    itself rather than being filled with a plausible number."""
    derived = _upload(client).json()["derived"]
    assert derived["axis_weights_revealed"] is None
    reasons = {n["field_name"]: n["reason"] for n in derived["not_inferred"]}
    assert "no per-axis scores" in reasons["axis_weights_revealed"]


def test_reupload_replaces_rather_than_appends(client: TestClient):
    _upload(client)
    assert client.get("/profile").json()["derived"]["decisions_count"] == 9
    _upload(client)
    assert client.get("/profile").json()["derived"]["decisions_count"] == 9


# ---------------------------------------------------------------------------
# §2.3 — the stated-vs-revealed gap
# ---------------------------------------------------------------------------


def test_gap_finds_divergence_between_stated_and_revealed(client: TestClient):
    """Answer every question the conviction-heavy way, then upload a history whose
    investments cluster at later stages. The two must disagree, and the report must say
    so with provenance on both sides."""
    client.post(
        "/profile/survey", json={"answers": _answers_extreme("conviction", maximise=False)}
    )
    _upload(
        client,
        "company,sector,stage,decision\n"
        "A,ai-infra,pre-seed,invested\n"
        "B,ai-infra,pre-seed,invested\n"
        "C,ai-infra,pre-seed,invested\n"
        "D,ai-infra,pre-seed,invested\n",
    )
    report = client.get("/profile/gap").json()

    conviction = [f for f in report["findings"] if f["dimension"] == "conviction_style"]
    assert conviction, "stated evidence-heavy vs four pre-seed cheques must be a finding"
    finding = conviction[0]
    assert finding["magnitude"] > 0
    assert finding["provenance"]["question_ids"], "the stated side must cite its questions"
    assert finding["provenance"]["decision_rows"], "the revealed side must cite its rows"
    assert finding["provenance"]["basis"] == "survey+decisions"


def test_gap_confidence_is_discounted_by_the_thinner_side(client: TestClient):
    """A finding drawn from four investments must not claim the confidence of forty.
    Every finding built on the same rows should report the same revealed-side discount."""
    client.post(
        "/profile/survey", json={"answers": _answers_extreme("conviction", maximise=False)}
    )
    _upload(
        client,
        "company,sector,stage,decision\n"
        "A,ai-infra,pre-seed,invested\n"
        "B,ai-infra,pre-seed,invested\n"
        "C,ai-infra,pre-seed,invested\n"
        "D,ai-infra,pre-seed,invested\n",
    )
    report = client.get("/profile/gap").json()
    by_dim = {f["dimension"]: f for f in report["findings"]}
    expected = 4 / profiles.DECISIONS_FOR_FULL_CONFIDENCE
    for dim in ("conviction_style", "stage"):
        assert by_dim[dim]["confidence"] <= expected + 1e-6, (
            f"{dim} overstates confidence on a 4-investment history"
        )


def test_gap_reports_agreement_when_the_two_sides_match(client: TestClient):
    client.post(
        "/profile/survey", json={"answers": _answers_extreme("conviction", maximise=False)}
    )
    _upload(
        client,
        "company,sector,stage,decision\n"
        "A,ai-infra,series b,invested\n"
        "B,ai-infra,series b,invested\n"
        "C,ai-infra,series c,invested\n",
    )
    report = client.get("/profile/gap").json()
    assert any("conviction style agrees" in a for a in report["agreements"])


def test_sector_gap_names_a_stated_focus_that_was_never_funded(client: TestClient):
    client.put("/profile", json={"focus_sectors": ["climate", "ai-infra"]})
    _upload(client)
    report = client.get("/profile/gap").json()

    sector = [f for f in report["findings"] if f["dimension"] == "sector"]
    assert sector, "a stated focus with zero investments is exactly the finding we want"
    assert "climate" in sector[0]["finding"]
    assert sector[0]["provenance"]["decision_rows"]


def test_gap_lists_the_side_that_was_missing(client: TestClient):
    """Survey only, no decisions: the stated side exists, the revealed side does not,
    and the report must name which was missing rather than staying silent."""
    client.post("/profile/survey", json={"answers": _answer_all(Choice.A)})
    report = client.get("/profile/gap").json()

    assert report["findings"] == []
    missing = {u["dimension"]: u["missing"] for u in report["uncomputable"]}
    assert missing["conviction_style"] == "revealed"
    assert missing["stage"] == "revealed"


# ---------------------------------------------------------------------------
# Personalisation threshold
# ---------------------------------------------------------------------------


def test_personalisation_switches_on_only_with_enough_of_both_sources(client: TestClient):
    client.post("/profile/survey", json={"answers": _answer_all(Choice.A)})
    only_survey = client.get("/profile").json()["derived"]
    # A full survey alone is half the evidence — 0.5 average, which clears the bar.
    assert only_survey["confidence"] == pytest.approx(0.5)
    assert only_survey["personalisation_enabled"] is True

    fresh = TestClient(app)
    fresh.post(
        "/auth/register",
        json={"email": f"vc-{uuid.uuid4().hex[:12]}@fund.example", "password": PASSWORD},
    )
    four = [{"question_id": q.id, "choice": "a"} for q in SURVEY_QUESTIONS[:2]]
    fresh.post("/profile/survey", json={"answers": four})
    thin = fresh.get("/profile").json()["derived"]
    assert thin["personalisation_enabled"] is False
    assert str(profiles.PERSONALISATION_MIN_CONFIDENCE) in thin["personalisation_reason"]


def test_gap_endpoint_states_whether_personalisation_is_on(client: TestClient):
    body = client.get("/profile/gap").json()
    assert body["personalisation_enabled"] is False
    assert body["personalisation_reason"]


# ---------------------------------------------------------------------------
# Storage separation (§0) — the two sources must never become one blob
# ---------------------------------------------------------------------------


def test_stated_and_revealed_live_in_separate_tables(client: TestClient):
    client.post("/profile/survey", json={"answers": _answer_all(Choice.A)})
    _upload(client)

    survey_rows = profiles.fetch("select * from vc_survey_answers")
    decision_rows = profiles.fetch("select * from vc_decisions")
    assert len(survey_rows) == 12
    assert len(decision_rows) == 9

    # And no merged blob anywhere in the profile row itself.
    profile_rows = profiles.fetch("select * from vc_profiles")
    assert set(profile_rows[0]) == {
        "profile_id",
        "user_id",
        "fund_name",
        "focus_sectors",
        "stated_red_lines",
        "created_at",
        "updated_at",
    }


def test_profile_data_is_not_written_to_the_event_log(client: TestClient):
    """Profiles are mutable state. An `observed_at` of 'when the user filled a form' is
    not a fact about the world and would corrupt every as_of query that reads the log."""
    from memory import store
    from schema.events import utcnow

    before = len(store.events(as_of=utcnow()))
    client.post("/profile/survey", json={"answers": _answer_all(Choice.A)})
    _upload(client)
    client.put("/profile", json={"fund_name": "Example Capital"})
    assert len(store.events(as_of=utcnow())) == before


def test_profile_update_is_partial_not_destructive(client: TestClient):
    client.put("/profile", json={"fund_name": "Example Capital", "focus_sectors": ["ai-infra"]})
    client.put("/profile", json={"stated_red_lines": ["no defence"]})
    body = client.get("/profile").json()
    assert body["fund_name"] == "Example Capital"
    assert body["focus_sectors"] == ["ai-infra"]
    assert body["stated_red_lines"] == ["no defence"]


def test_derivation_is_recomputed_from_the_raw_sources_not_cached(client: TestClient):
    _upload(client)
    first = client.get("/profile").json()["derived"]["sector_priors"]
    _upload(client, "company,sector,stage,decision\nZ,climate,seed,invested\nY,climate,seed,invested\nX,climate,seed,invested\n")
    second = client.get("/profile").json()["derived"]["sector_priors"]
    assert [p["key"] for p in first] != [p["key"] for p in second]
    assert [p["key"] for p in second] == ["climate"]
