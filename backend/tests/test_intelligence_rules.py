"""Pure rule tests: exact boundaries, time zones, closed deals, determinism (no DB, no clock)."""

import random
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.intelligence.rules import (
    CommitmentSnap,
    DealSnap,
    IntelConfig,
    InteractionSnap,
    evaluate_customer_level,
    evaluate_deal,
    priority_key,
)

AS_OF = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
CFG = IntelConfig()  # proposal = 10 days, discovery = 21, default 14, due soon 7, UTC


def deal(**kw) -> DealSnap:
    base = dict(id="deal_1", customer_id="cus_1", title="Pilot", stage="proposal", status="open",
                value_minor=4_200_000, currency="USD", expected_close_date=date(2026, 12, 1),
                owner_name="Asha", created_at=AS_OF - timedelta(days=60))
    return DealSnap(**{**base, **kw})


def act(i: int, ago: timedelta, channel: str = "call", deal_id: str = "deal_1") -> InteractionSnap:
    return InteractionSnap(id=f"int_{i}", deal_id=deal_id, channel=channel, occurred_at=AS_OF - ago)


def com(i: int, due: date | None, status: str = "open", owner: str = "us", **kw) -> CommitmentSnap:
    return CommitmentSnap(id=f"com_{i}", deal_id="deal_1", description=f"task {i}", owner_party=owner,
                          owner_name=kw.get("owner_name"), due_date=due, status=status,
                          source_interaction_id=kw.get("source"))


def types(evaluation) -> list[str]:
    return [s.type for s in evaluation.signals]


def by_type(evaluation, t):
    return [s for s in evaluation.signals if s.type == t]


# -- stalled deals --------------------------------------------------------------------------


@pytest.mark.parametrize("ago,expected", [
    (timedelta(days=10) - timedelta(seconds=1), None),     # just below the proposal threshold
    (timedelta(days=10), "medium"),                        # exactly the threshold -> stalled
    (timedelta(days=20) - timedelta(seconds=1), "medium"),
    (timedelta(days=20), "high"),                          # exactly 2x -> high
])
def test_stall_threshold_boundaries(ago, expected):
    e = evaluate_deal(deal(), [act(1, ago)], [], AS_OF, CFG)
    stalled = by_type(e, "stalled_deal")
    assert (stalled[0].severity if stalled else None) == expected
    if stalled:
        s = stalled[0]
        assert s.rule.id == "stalled_deal" and s.rule.thresholds["stall_days"] == 10
        assert s.rule.thresholds["stage"] == "proposal"
        assert s.facts["last_meaningful_activity_at"] == (AS_OF - ago).isoformat()
        assert [(r.type, r.id) for r in s.sources] == [("deal", "deal_1"), ("interaction", "int_1")]


@pytest.mark.parametrize("stage,threshold", [("discovery", 21), ("closing", 5)])
def test_stall_threshold_depends_on_stage(stage, threshold):
    assert "stalled_deal" not in types(evaluate_deal(deal(stage=stage), [act(1, timedelta(days=threshold - 1))],
                                                     [], AS_OF, CFG))
    assert "stalled_deal" in types(evaluate_deal(deal(stage=stage), [act(1, timedelta(days=threshold))],
                                                 [], AS_OF, CFG))


def test_unknown_stage_uses_default_threshold():
    cfg = IntelConfig(stall_days_default=3, stall_days_by_stage={})
    e = evaluate_deal(deal(stage="proposal"), [act(1, timedelta(days=3))], [], AS_OF, cfg)
    assert by_type(e, "stalled_deal")[0].rule.thresholds["stall_days"] == 3


def test_latest_meaningful_interaction_counts_and_notes_do_not():
    interactions = [act(1, timedelta(days=30)), act(2, timedelta(days=2), channel="note"),
                    act(3, timedelta(days=12), channel="email")]
    e = evaluate_deal(deal(), interactions, [], AS_OF, CFG)
    [s] = by_type(e, "stalled_deal")
    assert s.facts["days_inactive"] == 12 and s.sources[1].id == "int_3"  # the note does not reset activity
    assert e.activity.meaningful_interaction_count == 2 and e.activity.non_meaningful_interaction_count == 1


def test_only_non_meaningful_activity_is_missing_information_not_a_stall():
    e = evaluate_deal(deal(), [act(1, timedelta(days=1), channel="note")], [], AS_OF, CFG)
    assert "stalled_deal" not in types(e)
    [s] = by_type(e, "no_recorded_activity")
    assert s.nature == "missing_information" and s.facts["non_meaningful_interactions"] == 1


@pytest.mark.parametrize("age,severity", [(timedelta(days=10) - timedelta(seconds=1), "low"),
                                          (timedelta(days=10), "medium")])
def test_no_recorded_activity_severity_boundary(age, severity):
    e = evaluate_deal(deal(created_at=AS_OF - age), [], [], AS_OF, CFG)
    [s] = by_type(e, "no_recorded_activity")
    assert s.severity == severity and s.category == "activity"


def test_deal_created_after_as_of_has_zero_age():
    e = evaluate_deal(deal(created_at=AS_OF + timedelta(days=3)), [], [], AS_OF, CFG)
    assert by_type(e, "no_recorded_activity")[0].facts["deal_age_days"] == 0


def test_future_dated_interactions_are_excluded_and_reported():
    e = evaluate_deal(deal(), [act(1, timedelta(days=15)), act(2, -timedelta(hours=1)),
                               act(3, -timedelta(days=2))], [], AS_OF, CFG)
    [stall] = by_type(e, "stalled_deal")
    assert stall.facts["days_inactive"] == 15  # the future interactions did not count as activity
    [future] = by_type(e, "future_dated_activity")
    assert future.nature == "data_quality" and future.facts["count"] == 2
    assert {r.id for r in future.sources if r.type == "interaction"} == {"int_2", "int_3"}
    assert e.activity.future_dated_interaction_count == 2


def test_interaction_exactly_at_as_of_is_not_future():
    e = evaluate_deal(deal(), [act(1, timedelta(0))], [], AS_OF, CFG)
    assert types(e) == [] and e.activity.days_since_meaningful_activity == 0


# -- closed deals ------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["won", "lost", "no_decision"])
def test_closed_deals_get_no_activity_or_deal_field_signals(status):
    d = deal(status=status, expected_close_date=date(2026, 1, 1), value_minor=None, currency=None, owner_name=None)
    e = evaluate_deal(d, [act(1, timedelta(days=400))], [com(1, date(2026, 9, 1)), com(2, date(2026, 9, 1), "done")],
                      AS_OF, CFG)
    assert types(e) == ["open_commitment_on_closed_deal"]
    s = e.signals[0]
    assert s.severity == "low" and s.facts["past_due"] is True and s.sources[0].id == "com_1"
    assert e.activity.stall_threshold_days is None


# -- commitments ---------------------------------------------------------------------------------


@pytest.mark.parametrize("offset,expected", [
    (-8, ("commitment_overdue", "high")),
    (-1, ("commitment_overdue", "high")),
    (0, ("commitment_due_soon", "medium")),   # due today is not overdue
    (1, ("commitment_due_soon", "medium")),
    (2, ("commitment_due_soon", "low")),
    (7, ("commitment_due_soon", "low")),      # window is inclusive
    (8, None),
])
def test_commitment_deadline_boundaries(offset, expected):
    today = AS_OF.date()
    e = evaluate_deal(deal(), [act(1, timedelta(days=1))], [com(1, today + timedelta(days=offset))], AS_OF, CFG)
    got = [(s.type, s.severity) for s in e.signals if s.type.startswith("commitment")]
    assert got == ([expected] if expected else [])


def test_overdue_severity_depends_on_owner_and_includes_references():
    today = AS_OF.date()
    e = evaluate_deal(deal(), [act(1, timedelta(days=1))],
                      [com(1, today - timedelta(days=3), owner="customer", owner_name="Priya", source="int_9")],
                      AS_OF, CFG)
    [s] = by_type(e, "commitment_overdue")
    assert s.severity == "medium" and s.facts["days_overdue"] == 3 and s.facts["owner_name"] == "Priya"
    assert s.facts["due_date"] == (today - timedelta(days=3)).isoformat()
    assert [(r.type, r.id) for r in s.sources] == [("commitment", "com_1"), ("deal", "deal_1"), ("interaction", "int_9")]


def test_completed_and_cancelled_commitments_are_never_overdue():
    past = AS_OF.date() - timedelta(days=30)
    e = evaluate_deal(deal(), [act(1, timedelta(days=1))], [com(1, past, "done"), com(2, past, "cancelled")],
                      AS_OF, CFG)
    assert types(e) == []
    assert (e.commitments.done, e.commitments.cancelled, e.commitments.open, e.commitments.overdue) == (1, 1, 0, 0)


def test_undated_commitment_is_missing_information():
    e = evaluate_deal(deal(), [act(1, timedelta(days=1))], [com(1, None)], AS_OF, CFG)
    [s] = by_type(e, "commitment_undated")
    assert s.nature == "missing_information" and e.commitments.undated == 1 and e.commitments.open == 1


def test_commitment_summary_counts():
    t = AS_OF.date()
    cs = [com(1, t - timedelta(days=1)), com(2, t), com(3, t + timedelta(days=30)), com(4, None),
          com(5, t, "done"), com(6, t, "cancelled")]
    s = evaluate_deal(deal(), [act(1, timedelta(days=1))], cs, AS_OF, CFG).commitments
    assert s.model_dump() == {"open": 4, "overdue": 1, "due_soon": 1, "undated": 1, "done": 1, "cancelled": 1}


# -- time zones ----------------------------------------------------------------------------------


@pytest.mark.parametrize("tz,due,expected", [
    ("UTC", date(2026, 9, 28), "commitment_due_soon"),           # 20:00Z is still the 28th
    ("Asia/Kolkata", date(2026, 9, 28), "commitment_overdue"),   # 01:30 on the 29th in IST
    ("America/Los_Angeles", date(2026, 9, 28), "commitment_due_soon"),
])
def test_business_date_follows_configured_timezone(tz, due, expected):
    as_of = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    cfg = IntelConfig(timezone=tz)
    e = evaluate_deal(deal(), [InteractionSnap("int_1", "deal_1", "call", as_of - timedelta(days=1))],
                      [com(1, due)], as_of, cfg)
    assert [s.type for s in e.signals if s.type.startswith("commitment")] == [expected]


def test_negative_offset_timezone_moves_business_date_back():
    as_of = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)  # 20:00 on the 27th in Los Angeles
    cfg = IntelConfig(timezone="America/Los_Angeles")
    assert cfg.business_date(as_of) == date(2026, 9, 27)
    e = evaluate_deal(deal(), [InteractionSnap("int_1", "deal_1", "call", as_of)], [com(1, date(2026, 9, 27))],
                      as_of, cfg)
    assert by_type(e, "commitment_due_soon")[0].facts["days_until_due"] == 0


def test_as_of_offset_does_not_change_results():
    same_instant = AS_OF.astimezone(ZoneInfo("Asia/Tokyo"))
    a = evaluate_deal(deal(), [act(1, timedelta(days=12))], [], AS_OF, CFG)
    b = evaluate_deal(deal(), [act(1, timedelta(days=12))], [], same_instant, CFG)
    assert [s.model_dump(exclude={"facts"}) for s in a.signals] == [s.model_dump(exclude={"facts"}) for s in b.signals]


def test_naive_as_of_is_rejected():
    with pytest.raises(ValueError):
        evaluate_deal(deal(), [], [], datetime(2026, 9, 28, 12, 0), CFG)


# -- deal fields and customer level -------------------------------------------------------------------


def test_close_date_boundary_and_missing_fields():
    today = AS_OF.date()
    assert "close_date_passed" not in types(evaluate_deal(deal(expected_close_date=today),
                                                          [act(1, timedelta(days=1))], [], AS_OF, CFG))
    e = evaluate_deal(deal(expected_close_date=today - timedelta(days=1)), [act(1, timedelta(days=1))], [], AS_OF, CFG)
    [s] = by_type(e, "close_date_passed")
    assert s.severity == "medium" and s.facts["days_past"] == 1
    e = evaluate_deal(deal(expected_close_date=None, value_minor=None, currency=None, owner_name=None),
                      [act(1, timedelta(days=1))], [], AS_OF, CFG)
    assert sorted(types(e)) == ["missing_deal_owner", "missing_deal_value", "missing_expected_close_date"]
    assert all(s.nature == "missing_information" and s.severity == "low" for s in e.signals)


def test_no_stakeholders_signal_only_with_open_deals():
    [s] = evaluate_customer_level("cus_1", 0, has_open_deal=True)
    assert s.deal_id is None and s.sources[0].type == "customer" and s.nature == "missing_information"
    assert evaluate_customer_level("cus_1", 0, has_open_deal=False) == []
    assert evaluate_customer_level("cus_1", 2, has_open_deal=True) == []


# -- ordering and determinism --------------------------------------------------------------------------


def test_priority_order_is_severity_then_nature_then_urgency():
    today = AS_OF.date()
    e = evaluate_deal(
        deal(expected_close_date=None, owner_name=None),
        [act(1, timedelta(days=25)), act(2, -timedelta(days=1))],   # stalled high (25 >= 20), future-dated
        [com(1, today - timedelta(days=2)), com(2, today - timedelta(days=9)), com(3, today + timedelta(days=1),
                                                                                     owner="customer"),
         com(4, None)],
        AS_OF, CFG)
    order = [(s.severity, s.nature, s.type, s.urgency) for s in e.signals]
    assert order == [
        ("high", "finding", "stalled_deal", 15),
        ("high", "finding", "commitment_overdue", 9),
        ("high", "finding", "commitment_overdue", 2),
        ("medium", "finding", "commitment_due_soon", 6),
        ("low", "missing_information", "commitment_undated", 0),
        ("low", "missing_information", "missing_deal_owner", 0),
        ("low", "missing_information", "missing_expected_close_date", 0),
        ("low", "data_quality", "future_dated_activity", 0),
    ]
    assert e.signals == sorted(e.signals, key=priority_key)


def test_same_input_same_output_regardless_of_record_order():
    today = AS_OF.date()
    interactions = [act(i, timedelta(days=i * 3), channel=["call", "note", "email"][i % 3]) for i in range(1, 9)]
    commitments = [com(i, today + timedelta(days=i - 4) if i % 4 else None, owner=["us", "customer"][i % 2])
                   for i in range(1, 11)]
    baseline = evaluate_deal(deal(), interactions, commitments, AS_OF, CFG)
    rng = random.Random(7)
    for _ in range(5):
        rng.shuffle(interactions)
        rng.shuffle(commitments)
        again = evaluate_deal(deal(), interactions, commitments, AS_OF, CFG)
        assert [s.model_dump() for s in again.signals] == [s.model_dump() for s in baseline.signals]
        assert again.activity == baseline.activity and again.commitments == baseline.commitments


def test_signal_ids_are_deterministic_and_unique():
    today = AS_OF.date()
    e = evaluate_deal(deal(expected_close_date=None), [], [com(1, today), com(2, None)], AS_OF, CFG)
    ids = [s.id for s in e.signals]
    assert len(ids) == len(set(ids))
    assert set(ids) == {"no_recorded_activity:deal_1", "missing_expected_close_date:deal_1",
                        "commitment_due_soon:com_1", "commitment_undated:com_2"}
