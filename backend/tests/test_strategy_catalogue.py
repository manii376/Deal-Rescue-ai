"""TM2 strategy catalogue: triggers on/off, reasons, anchors, evidence maps, cutoffs, ordering, scope. No model."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from app.ai.schemas import STRATEGY_DISCLAIMER
from app.domain.models import Interaction, Stakeholder
from tests import tm_scenario as tm

URL = "/api/customers/{cid}/deals/{did}/time-machine/strategies"
IDS = ["earlier_follow_up", "address_requirement_early", "identify_decision_maker"]


@pytest.fixture
def sc(client):
    return tm.build(client.app.state.engine)


def catalogue(client, sc, *, branch_at=tm.BRANCH_AT, as_of=tm.AS_OF, did=None, cid=None, **params):
    return client.get(URL.format(cid=cid or sc.customer, did=did or sc.deal),
                      params={"branch_at": branch_at.isoformat(), "as_of": as_of.isoformat(), **params})


def ok(client, sc, **kw):
    r = catalogue(client, sc, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def choice(body, sid):
    return next(c for c in body["strategies"] if c["template"]["id"] == sid)


def ref(body, source_type, source_id):
    return next(s["ref"] for s in body["sources"] if s["source_type"] == source_type and s["source_id"] == source_id)


def signal_ref(body, rule):
    return next(s["ref"] for s in body["sources"] if s["source_type"] == "signal" and s["source_id"].startswith(rule))


def add(client, *records):
    with Session(client.app.state.engine) as s:
        for r in records:
            s.add(r)
        s.commit()


# -- catalogue shape ------------------------------------------------------------------------------------


def test_catalogue_is_fixed_ordered_hypothetical_and_explained(client, sc):
    body = ok(client, sc)
    assert [c["template"]["id"] for c in body["strategies"]] == IDS
    assert body["kind"] == "hypothetical" and body["disclaimer"] == STRATEGY_DISCLAIMER
    assert "not predictions" in body["disclaimer"]
    for c in body["strategies"]:
        assert c["reasons"], c
        assert c["template"]["description"].startswith("Scenario:")
        if c["evidence_map"]:
            assert c["evidence_map"]["basis"] == "rule_derived"
            assert all(h["kind"] == "hypothetical" for h in c["evidence_map"]["implied_commitments"])
    text = str(body).lower()
    for banned in ("will win", "would have won", "probability", "likely to close", "%"):
        assert banned not in text


def test_every_cited_ref_is_known_at_the_branch(client, sc):
    body = ok(client, sc, anchor_ref="N1")
    known = {s["ref"] for s in body["sources"]}
    assert all(s["occurred_at"] is None or s["occurred_at"] <= "2026-09-23T12:00:00Z" for s in body["sources"])
    for c in body["strategies"]:
        m = c["evidence_map"]
        assert set(c["anchor_candidates"]) <= known
        if m:
            assert set(m["related"]) <= known
            assert m["related"] == sorted(m["related"], key=[s["ref"] for s in body["sources"]].index)


# -- earlier_follow_up ----------------------------------------------------------------------------------


def test_follow_up_offered_when_nothing_meaningful_followed(client, sc):
    body = ok(client, sc)
    c = choice(body, "earlier_follow_up")
    call = ref(body, "interaction", sc.call)
    assert c["template"]["offered"] is True and c["template"]["offered_because"] == [call]
    assert any("was a call on 2026-09-20" in r for r in c["reasons"])
    assert any("No meaningful interaction is recorded between 2026-09-23 and 2026-09-29" in r for r in c["reasons"])
    m = c["evidence_map"]
    assert call in m["related"]
    stall = client.app.state.intelligence.config.stall_days("proposal")
    [commitment] = m["implied_commitments"]
    assert commitment["owner_party"] == "us" and f"within {stall // 2} day(s) of 2026-09-20" in commitment["description"]
    assert "not advice" in commitment["description"]
    assert any(g.startswith("No expected close date") for g in m["gaps"])  # from missing_information signals


def test_follow_up_names_the_stall_rule_once_it_applies(client, sc):
    later = tm.CALL_AT + timedelta(days=client.app.state.intelligence.config.stall_days("proposal"), hours=1)
    c = choice(ok(client, sc, as_of=later), "earlier_follow_up")
    assert "stalled_deal" in c["template"]["offered_because"]
    assert any("stall rule (stalled_deal) applies" in r for r in c["reasons"])
    # the "as of now" part explains the offer but is never cited as known-then evidence
    assert all(not r.startswith("stalled") for r in c["evidence_map"]["related"])


def test_follow_up_not_offered_without_a_prior_meaningful_interaction(client, sc):
    c = choice(ok(client, sc, branch_at=tm.CALL_AT - timedelta(days=1)), "earlier_follow_up")
    assert c["template"]["offered"] is False and c["evidence_map"] is None
    assert c["template"]["offered_because"] == []
    assert "No meaningful interaction" in c["template"]["not_offered_reason"]


def test_follow_up_not_offered_when_a_meaningful_contact_followed_and_no_stall(client, sc):
    add(client, Interaction(customer_id=sc.customer, deal_id=sc.deal, occurred_at=datetime(2026, 9, 26, tzinfo=UTC),
                            channel="meeting", notes="Follow-up meeting.", created_at=datetime(2026, 9, 26, tzinfo=UTC)))
    c = choice(ok(client, sc), "earlier_follow_up")
    assert c["template"]["offered"] is False
    assert "A meaningful interaction was recorded after 2026-09-23" in c["template"]["not_offered_reason"]


def test_follow_up_ignores_non_meaningful_notes(client, sc):
    # The 09-25 item is a "note" (not meaningful): it neither counts as a follow-up nor as the last contact.
    body = ok(client, sc, branch_at=tm.NOTE_AT + timedelta(hours=1))
    c = choice(body, "earlier_follow_up")
    assert c["template"]["offered_because"] == [ref(body, "interaction", sc.call)]


# -- address_requirement_early -------------------------------------------------------------------------


def test_requirement_lists_candidates_and_needs_a_chosen_anchor(client, sc):
    body = ok(client, sc)
    c = choice(body, "address_requirement_early")
    t = c["template"]
    assert (t["offered"], t["needs_anchor"], c["evidence_map"]) == (True, True, None)
    expected = [ref(body, "commitment", sc.c1), ref(body, "commitment", sc.c3), ref(body, "interaction", sc.call)]
    assert sorted(c["anchor_candidates"]) == sorted(expected) and t["offered_because"] == c["anchor_candidates"]
    assert any("never detected automatically" in r for r in c["reasons"])
    assert any("Choose an anchor" in r for r in c["reasons"])


def test_requirement_candidates_respect_the_cutoff(client, sc):
    body = ok(client, sc)
    c = choice(body, "address_requirement_early")
    sources = {s["ref"]: s for s in body["sources"]}
    ids = {sources[r]["source_id"] for r in c["anchor_candidates"]}
    assert sc.note not in ids and sc.late_commitment not in ids and sc.future_call not in ids


def test_requirement_map_for_a_commitment_anchor(client, sc):
    body = ok(client, sc)
    anchor = ref(body, "commitment", sc.c1)
    mapped = ok(client, sc, anchor_ref=anchor)
    m = choice(mapped, "address_requirement_early")["evidence_map"]
    assert m["anchor_ref"] == anchor and m["related"][0] == anchor
    assert signal_ref(mapped, "commitment_due_soon") in m["related"]  # the rule about this commitment, known then
    [h] = m["implied_commitments"]
    assert h["kind"] == "hypothetical" and anchor in h["description"]
    assert "no milestone date is recorded" in h["description"]


def test_requirement_map_for_a_rep_note_anchor(client, sc):
    m = choice(ok(client, sc, anchor_ref="N1"), "address_requirement_early")["evidence_map"]
    assert m["anchor_ref"] == "N1" and m["related"] == ["N1"]


@pytest.mark.parametrize("anchor", ["R1", "D1", "Z9"])  # the deal record, a signal, an unknown ref
def test_invalid_anchor_is_rejected(client, sc, anchor):
    r = catalogue(client, sc, anchor_ref=anchor)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_anchor"
    assert anchor not in r.json()["error"]["message"]


def test_malformed_anchor_is_a_validation_error(client, sc):
    r = catalogue(client, sc, anchor_ref="not a ref!")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_requirement_not_offered_without_notes_or_commitments(client, sc):
    c = choice(ok(client, sc, did=sc.empty_deal), "address_requirement_early")
    assert c["template"]["offered"] is False and c["anchor_candidates"] == []
    assert "No rep note, linked memory or commitment" in c["template"]["not_offered_reason"]


# -- identify_decision_maker ----------------------------------------------------------------------------


def test_decision_maker_offered_when_no_stakeholders_were_recorded_then(client, sc):
    body = ok(client, sc)  # Priya was entered on 09-24, after the branch
    c = choice(body, "identify_decision_maker")
    no_stk = signal_ref(body, "no_stakeholders_recorded")
    assert c["template"]["offered_because"] == [no_stk] and no_stk in c["evidence_map"]["related"]
    assert c["evidence_map"]["gaps"] == [f"No stakeholders recorded ({no_stk})"]
    assert "Priya" not in str(body)


def test_decision_maker_offered_when_no_high_influence_stakeholder(client, sc):
    body = ok(client, sc, branch_at=tm.AS_OF)
    c = choice(body, "identify_decision_maker")
    stk = ref(body, "stakeholder", sc.stakeholder)
    assert c["template"]["offered_because"] == [stk] and c["evidence_map"]["related"] == [stk]
    assert "none with influence 'high'" in c["reasons"][0]


def test_decision_maker_not_offered_once_a_high_influence_stakeholder_is_recorded(client, sc):
    add(client, Stakeholder(customer_id=sc.customer, name="Arjun Mehta", role="COO", influence="high",
                            created_at=datetime(2026, 9, 25, tzinfo=UTC)))
    at_now = choice(ok(client, sc, branch_at=tm.AS_OF), "identify_decision_maker")
    assert at_now["template"]["offered"] is False and "influence 'high'" in at_now["template"]["not_offered_reason"]
    before = choice(ok(client, sc), "identify_decision_maker")  # 09-23: Arjun not yet recorded
    assert before["template"]["offered"] is True


# -- validation, scope, determinism, memory -----------------------------------------------------------


def test_branch_after_as_of_and_naive_times_are_rejected(client, sc):
    assert catalogue(client, sc, branch_at=tm.AS_OF + timedelta(seconds=1)).status_code == 422
    r = client.get(URL.format(cid=sc.customer, did=sc.deal), params={"branch_at": "2026-09-23T12:00:00"})
    assert r.status_code == 422


def test_scope_is_enforced(client, sc):
    assert catalogue(client, sc, did=sc.other_deal).status_code == 404
    assert catalogue(client, sc, cid="cus_missing").status_code == 404
    body = ok(client, sc, branch_at=tm.AS_OF)
    assert "BOREALIS" not in str(body) and sc.other_call not in str(body)


def test_repeat_requests_are_identical(client, sc):
    assert ok(client, sc, anchor_ref="N1") == ok(client, sc, anchor_ref="N1")


def test_memory_is_not_requested_by_default(client, sc):
    assert ok(client, sc)["memory"]["status"] == "not_requested"
    assert ok(client, sc, include_memory="true")["memory"]["status"] == "disabled"  # backend off in tests
