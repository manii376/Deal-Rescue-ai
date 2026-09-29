"""TM1 recorded timeline: event conversion, kinds, ids, ordering, dates, limitations, isolation. No model, no memory."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, func, select

from app.domain.models import Commitment, Deal, Interaction, Stakeholder
from app.timemachine.schemas import TIMELINE_LIMITATIONS
from app.timemachine.timeline import build_timeline
from tests import tm_scenario as tm

URL = "/api/customers/{cid}/deals/{did}/timeline"


@pytest.fixture
def sc(client):
    return tm.build(client.app.state.engine)


def timeline(client, sc, did=None, cid=None, **params):
    return client.get(URL.format(cid=cid or sc.customer, did=did or sc.deal),
                      params={"as_of": tm.AS_OF.isoformat(), **params})


def events(body):
    return [(e["kind"], e["id"]) for e in body["events"]]


def test_records_become_the_expected_events_in_order(client, sc):
    r = timeline(client, sc)
    assert r.status_code == 200, r.text
    body = r.json()
    assert events(body) == [
        ("interaction", f"interaction:{sc.call}"),                    # 09-20 10:00, occurred
        ("commitment_created", f"commitment_created:{sc.c1}"),        # 09-21, entered
        ("commitment_created", f"commitment_created:{sc.c3}"),        # 09-22, entered
        ("stakeholder_recorded", f"stakeholder_recorded:{sc.stakeholder}"),  # 09-24, entered
        ("interaction", f"interaction:{sc.note}"),                    # 09-25, occurred
        ("commitment_due", f"commitment_due:{sc.c1}"),                # 09-26 00:00 UTC
        ("commitment_completed", f"commitment_completed:{sc.c1}"),    # 09-27, completed_at
        ("deal_entered", f"deal_entered:{sc.deal}"),                  # 09-28, entered
        ("rule_checkpoint", f"rule:stalled_deal:{sc.deal}"),          # 09-20 + 10 days, computed
    ]
    assert body["as_of"].startswith("2026-09-29T12:00")


def test_event_kinds_evidence_kinds_date_bases_and_sources(client, sc):
    body = timeline(client, sc).json()
    by_id = {e["id"]: e for e in body["events"]}
    call = by_id[f"interaction:{sc.call}"]
    assert (call["evidence_kind"], call["date_basis"], call["source"]) == (
        "rep_note", "occurred", {"type": "interaction", "id": sc.call})
    assert "rep note" in call["title"].lower() and "Budget call" in call["title"]
    assert "CFO said" not in call["title"]  # the note text itself is not copied into the timeline
    assert by_id[f"deal_entered:{sc.deal}"]["date_basis"] == "entered_in_system"
    due = by_id[f"commitment_due:{sc.c1}"]
    assert (due["evidence_kind"], due["date_basis"], due["at"]) == ("recorded", "due", "2026-09-26T00:00:00Z")
    done = by_id[f"commitment_completed:{sc.c1}"]
    assert (done["date_basis"], done["at"]) == ("completed", "2026-09-27T12:00:00Z")
    checkpoint = by_id[f"rule:stalled_deal:{sc.deal}"]
    assert (checkpoint["evidence_kind"], checkpoint["date_basis"], checkpoint["source"], checkpoint["rule_id"]) == (
        "signal", "rule_computed", None, "stalled_deal")
    stall_days = client.app.state.intelligence.config.stall_days("proposal")
    assert checkpoint["at"] == (tm.CALL_AT + timedelta(days=stall_days)).isoformat().replace("+00:00", "Z")
    assert all(e["evidence_kind"] in ("recorded", "rep_note", "signal") for e in body["events"])
    assert len({e["id"] for e in body["events"]}) == len(body["events"])


def test_missing_dates_are_not_invented_and_are_stated(client, sc):
    body = timeline(client, sc).json()
    ids = {e["id"] for e in body["events"]}
    assert f"commitment_due:{sc.c3}" not in ids              # no due date recorded
    assert not any(i.endswith(sc.c3) and not i.startswith("commitment_created") for i in ids)  # no cancel event
    notes = " ".join(body["data_notes"])
    assert "no due date" in notes and "cancellation date is not recorded" in notes
    assert "after the first recorded interaction (2026-09-20)" in notes  # deal entered 09-28 is not its start
    assert "current stage ('proposal'" in notes
    assert body["limitations"] == list(TIMELINE_LIMITATIONS)


def test_current_deal_fields_are_not_presented_as_history(client, sc):
    body = timeline(client, sc).json()
    text = " ".join(e["title"] for e in body["events"]).lower()
    assert "value" not in text and "owner" not in text  # no past value/owner/stage snapshots are shown
    assert any("change history is not recorded" in lim for lim in body["limitations"])


def test_records_after_as_of_are_excluded_but_scheduled_dates_may_follow(client, sc):
    body = timeline(client, sc).json()
    ids = {e["id"] for e in body["events"]}
    assert f"interaction:{sc.future_call}" not in ids
    assert not any(sc.late_commitment in i for i in ids)
    assert "1 interaction(s) are dated after 2026-09-29" in " ".join(body["data_notes"])
    after_now = [e for e in body["events"] if e["at"] > "2026-09-29T12:00:00Z"]
    assert [e["kind"] for e in after_now] == ["rule_checkpoint"]


def test_earlier_as_of_hides_later_records_and_uses_recorded_completion(client, sc):
    body = timeline(client, sc, as_of="2026-09-26T12:00:00Z").json()
    ids = [i for _, i in events(body)]
    assert f"commitment_completed:{sc.c1}" not in ids  # completed on 09-27, after this as_of
    assert f"deal_entered:{sc.deal}" not in ids
    assert "deal record was entered after 2026-09-26" in " ".join(body["data_notes"])
    assert f"interaction:{sc.note}" in ids and f"commitment_due:{sc.c1}" in ids


def test_timezone_offsets_are_compared_as_instants(client, sc):
    utc = timeline(client, sc).json()
    ist = timeline(client, sc, as_of=tm.AS_OF.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()).json()
    assert events(ist) == events(utc)
    # an interaction exactly at as_of is included (occurred_at <= as_of)
    at_note = timeline(client, sc, as_of=tm.NOTE_AT.isoformat()).json()
    assert f"interaction:{sc.note}" in [i for _, i in events(at_note)]


def test_repeat_requests_are_identical(client, sc):
    assert timeline(client, sc).json() == timeline(client, sc).json()


def test_sparse_deal_has_only_its_entry_event(client, sc):
    body = timeline(client, sc, did=sc.empty_deal).json()
    kinds = [k for k, _ in events(body)]
    # The customer's stakeholder is customer-level; there are no interactions, so no stall checkpoint.
    assert "interaction" not in kinds and "rule_checkpoint" not in kinds and "commitment_created" not in kinds
    assert kinds.count("deal_entered") == 1


def test_customer_and_deal_ownership_are_enforced(client, sc):
    assert timeline(client, sc, did=sc.other_deal).status_code == 404            # other customer's deal
    assert timeline(client, sc, cid="cus_missing").status_code == 404
    assert timeline(client, sc, did="deal_missing").status_code == 404
    other = timeline(client, sc, cid=sc.other_customer, did=sc.other_deal).json()
    assert all(sc.customer not in (e["source"] or {}).get("id", "") for e in other["events"])
    mine = timeline(client, sc).json()
    assert sc.other_call not in str(mine) and "BOREALIS" not in str(mine)
    assert timeline(client, sc, did=sc.other_deal).json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("as_of", ["2026-09-29T12:00:00", "not-a-date"])
def test_naive_or_invalid_as_of_is_rejected(client, sc, as_of):
    r = timeline(client, sc, as_of=as_of)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_nothing_is_written(client, sc):
    def counts():
        with Session(client.app.state.engine) as s:
            return [s.exec(select(func.count()).select_from(m)).one()
                    for m in (Deal, Interaction, Commitment, Stakeholder)]
    before = counts()
    timeline(client, sc)
    assert counts() == before


def test_service_is_usable_without_fastapi(client, sc):
    with Session(client.app.state.engine) as s:
        t = build_timeline(s, sc.customer, sc.deal, tm.AS_OF, client.app.state.intelligence.config)
        with pytest.raises(ValueError):
            build_timeline(s, sc.customer, sc.other_deal, tm.AS_OF, client.app.state.intelligence.config)
        with pytest.raises(ValueError):
            build_timeline(s, sc.customer, sc.deal, datetime(2026, 9, 29), client.app.state.intelligence.config)
    assert t.events[0].at == tm.CALL_AT and t.events[0].at.tzinfo is not None
    assert all(e.at.utcoffset() is not None for e in t.events)
    assert t.as_of == tm.AS_OF.astimezone(UTC)
