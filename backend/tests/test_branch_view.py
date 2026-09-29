"""TM1 branch view: known then vs actually followed, point-in-time evidence and signals, validation, isolation."""

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.ai.evidence import MEMORY_NOT_REQUESTED, assemble_briefing_evidence
from app.timemachine.timeline import BranchAfterAsOfError, build_branch_view
from tests import tm_scenario as tm

URL = "/api/customers/{cid}/deals/{did}/timeline/branch"


@pytest.fixture
def sc(client):
    return tm.build(client.app.state.engine)


def branch(client, sc, branch_at=tm.BRANCH_AT, as_of=tm.AS_OF, did=None, cid=None, **extra):
    params = {"branch_at": branch_at.isoformat() if hasattr(branch_at, "isoformat") else branch_at,
              "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of, **extra}
    return client.get(URL.format(cid=cid or sc.customer, did=did or sc.deal), params=params)


def excerpts(body):
    return " ".join(s["excerpt"] for s in body["known_then"])


def test_known_then_contains_only_what_was_recorded_by_the_branch(client, sc):
    r = branch(client, sc)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {(s["source_type"], s["source_id"]) for s in body["known_then"]}
    assert ("interaction", sc.call) in ids                       # occurred 09-20
    assert ("interaction", sc.note) not in ids                   # occurred 09-25, after the branch
    assert ("commitment", sc.c1) in ids and ("commitment", sc.c3) in ids   # entered 09-21 / 09-22
    assert ("commitment", sc.late_commitment) not in ids
    assert ("stakeholder", sc.stakeholder) not in ids            # entered 09-24
    text = excerpts(body)
    assert "LATER-NOTE" not in text and "FUTURE-CALL" not in text and "Priya" not in text
    for s in body["known_then"]:
        assert s["occurred_at"] is None or s["occurred_at"] <= "2026-09-23T12:00:00Z"
    kinds = {s["source_type"]: s["kind"] for s in body["known_then"]}
    assert kinds["interaction"] == "rep_note" and kinds["deal"] == "recorded" and kinds["signal"] == "signal"


def test_status_known_then_is_never_a_later_status(client, sc):
    text = excerpts(branch(client, sc).json())
    assert "Send SOC 2 report; due 2026-09-26; status open." in text   # completed 09-27, after the branch
    assert "Share pricing sheet; due no due date; status not recorded for this date" in text  # cancel date unknown
    later = excerpts(branch(client, sc, branch_at=tm.C1_DONE).json())
    assert "Send SOC 2 report; due 2026-09-26; status done." in later


def test_deal_item_says_it_shows_current_values(client, sc):
    deal_item = next(s for s in branch(client, sc).json()["known_then"] if s["source_type"] == "deal")
    assert deal_item["excerpt"].startswith("Current values (no change history): ")


def test_followed_is_the_recorded_events_after_the_branch_up_to_as_of(client, sc):
    body = branch(client, sc).json()
    assert [e["id"] for e in body["followed"]] == [
        f"stakeholder_recorded:{sc.stakeholder}", f"interaction:{sc.note}", f"commitment_due:{sc.c1}",
        f"commitment_completed:{sc.c1}", f"deal_entered:{sc.deal}"]
    assert all("2026-09-23T12:00:00Z" < e["at"] <= "2026-09-29T12:00:00Z" for e in body["followed"])
    assert all(e["evidence_kind"] in ("recorded", "rep_note", "signal") for e in body["followed"])


def test_an_event_at_the_branch_instant_is_known_then_not_followed(client, sc):
    body = branch(client, sc, branch_at=tm.NOTE_AT).json()
    assert f"interaction:{sc.note}" not in [e["id"] for e in body["followed"]]
    assert ("interaction", sc.note) in {(s["source_type"], s["source_id"]) for s in body["known_then"]}


def test_signals_then_are_evaluated_on_what_was_recorded_then(client, sc):
    then = {s["type"] for s in branch(client, sc).json()["signals_then"]}
    assert "no_stakeholders_recorded" in then        # the stakeholder was entered after the branch
    assert "commitment_due_soon" in then             # C1 was still open then, due 09-26
    assert "future_dated_activity" not in then       # later interactions are not "known" at all
    now = {s["type"] for s in branch(client, sc, branch_at=tm.AS_OF).json()["signals_then"]}
    assert "no_stakeholders_recorded" not in now and "commitment_due_soon" not in now


def test_memory_is_not_recalled_and_says_so(client, sc):
    def boom(*a, **k):
        raise AssertionError("memory must not be contacted by the TM1 branch view")
    client.app.state.memory.recall_customer_evidence = boom
    body = branch(client, sc).json()
    assert body["memory"]["status"] == "not_requested" and body["memory"]["reason"] == MEMORY_NOT_REQUESTED
    assert not any(s["kind"] == "memory" for s in body["known_then"])


def test_branch_after_as_of_and_bad_timestamps_are_rejected(client, sc):
    r = branch(client, sc, branch_at=tm.AS_OF + timedelta(seconds=1))
    assert r.status_code == 422 and r.json()["error"] == {
        "code": "validation_error", "message": "branch_at must not be after as_of", "details": None}
    assert branch(client, sc, branch_at="2026-09-23T12:00:00").status_code == 422      # naive
    assert branch(client, sc, as_of="2026-09-29T12:00:00").status_code == 422           # naive
    assert client.get(URL.format(cid=sc.customer, did=sc.deal)).status_code == 422      # branch_at required
    assert branch(client, sc, branch_at=tm.AS_OF).status_code == 200                    # equal is allowed


def test_ownership_and_isolation(client, sc):
    assert branch(client, sc, did=sc.other_deal).status_code == 404
    assert branch(client, sc, cid="cus_missing").status_code == 404
    body = branch(client, sc, branch_at=tm.AS_OF).json()
    assert "BOREALIS" not in str(body) and sc.other_call not in str(body)


def test_branch_before_the_deal_record_existed_and_sparse_deals(client, sc):
    body = branch(client, sc, did=sc.empty_deal, branch_at=tm.BRANCH_AT).json()
    assert [s["source_type"] for s in body["known_then"] if s["source_type"] != "signal"] == ["deal"]
    early = branch(client, sc, branch_at=tm.CALL_AT - timedelta(days=1)).json()
    assert f"deal_entered:{sc.deal}" in [e["id"] for e in early["followed"]]
    assert not any(s["source_type"] == "interaction" for s in early["known_then"])


def test_repeat_requests_are_identical(client, sc):
    assert branch(client, sc).json() == branch(client, sc).json()


def test_service_rejects_branch_after_as_of_and_naive_times(client, sc):
    state = client.app.state

    async def run(branch_at, as_of):
        with Session(state.engine) as s:
            return await build_branch_view(s, sc.customer, sc.deal, branch_at, as_of, intelligence=state.intelligence)

    with pytest.raises(BranchAfterAsOfError):
        client.portal.call(run, tm.AS_OF + timedelta(days=1), tm.AS_OF)
    with pytest.raises(ValueError):
        client.portal.call(run, tm.BRANCH_AT.replace(tzinfo=None), tm.AS_OF)


def test_briefing_assembly_is_unchanged_by_default(client, sc):
    state = client.app.state

    async def run():
        with Session(state.engine) as s:
            return await assemble_briefing_evidence(s, sc.customer, sc.deal, tm.BRANCH_AT, memory=state.memory,
                                                    intelligence=state.intelligence)

    pack = client.portal.call(run)
    text = " ".join(s.excerpt for s in pack.sources)
    assert not text.startswith("Current values")
    assert "status done" in text and "Priya" in text        # default: records as currently stored
    assert pack.memory.status == "disabled" and pack.memory.reason != MEMORY_NOT_REQUESTED


def test_intelligence_point_in_time_is_opt_in(client, sc):
    state = client.app.state
    with Session(state.engine) as s:
        default = {x.type for x in state.intelligence.evaluate_customer(s, sc.customer, tm.BRANCH_AT,
                                                                         deal_id=sc.deal).all_signals()}
        then = {x.type for x in state.intelligence.evaluate_customer(s, sc.customer, tm.BRANCH_AT, deal_id=sc.deal,
                                                                      point_in_time=True).all_signals()}
    assert "future_dated_activity" in default and "future_dated_activity" not in then
    assert "no_stakeholders_recorded" not in default and "no_stakeholders_recorded" in then
