"""Deal Time Machine TM0 contracts: validation, evidence-kind separation, backward compatibility. No I/O."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.ai.evidence import EvidenceSource, MemoryEvidenceStatus, SourceRecord
from app.ai.schemas import (
    STRATEGY_DISCLAIMER,
    CitedText,
    DealBriefing,
    GenerationInfo,
    StrategyAssessment,
    StrategyComparison,
    StrategyOption,
)
from app.timemachine.schemas import (
    TIME_MACHINE_NOTE,
    TIMELINE_LIMITATIONS,
    BranchView,
    DealTimeline,
    HypotheticalCommitment,
    StrategyEvidenceMap,
    StrategyTemplate,
    TimeMachineAssessment,
    TimeMachineAssessRequest,
    TimelineEvent,
)

CALL = datetime(2026, 9, 20, 10, tzinfo=UTC)          # the synthetic Aurora call
ENTERED = datetime(2026, 9, 28, 16, 17, tzinfo=UTC)    # deal record entered (after the call)
STALL = datetime(2026, 9, 30, 10, tzinfo=UTC)          # proposal stall threshold (10 days)
NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
GEN = GenerationInfo(provider="fake", model="fake-1", generated_at=NOW)
MEMORY = MemoryEvidenceStatus(status="disabled")


def interaction_event(at=CALL, id_="interaction:int_1"):
    return TimelineEvent(id=id_, kind="interaction", evidence_kind="rep_note", at=at, date_basis="occurred",
                         title="Call: Budget call (rep note)", source=SourceRecord(type="interaction", id="int_1"))


def deal_entered_event():
    return TimelineEvent(id="deal_entered:deal_a", kind="deal_entered", evidence_kind="recorded", at=ENTERED,
                         date_basis="entered_in_system", title="Deal entered in system",
                         source=SourceRecord(type="deal", id="deal_a"))


def checkpoint_event(at=STALL):
    return TimelineEvent(id="rule:stalled_deal:deal_a", kind="rule_checkpoint", evidence_kind="signal", at=at,
                         date_basis="rule_computed", title="Stall threshold reached", rule_id="stalled_deal")


def source(ref, kind, at=None, source_type="interaction", source_id="int_1"):
    return EvidenceSource(ref=ref, kind=kind, source_type=source_type, source_id=source_id, occurred_at=at,
                          excerpt=f"{ref} text")


SOURCES = [source("R1", "recorded", None, "deal", "deal_a"), source("D1", "signal", None, "signal", "sig"),
           source("N1", "rep_note", CALL), source("M1", "memory", CALL, "memory")]


# -- actual history ------------------------------------------------------------------------------


def test_valid_timeline_events_for_every_kind():
    events = [
        interaction_event(),
        TimelineEvent(id="commitment_created:c1", kind="commitment_created", evidence_kind="recorded", at=CALL,
                      date_basis="entered_in_system", title="Commitment entered",
                      source=SourceRecord(type="commitment", id="c1")),
        TimelineEvent(id="commitment_due:c1", kind="commitment_due", evidence_kind="recorded", at=STALL,
                      date_basis="due", title="Commitment due", source=SourceRecord(type="commitment", id="c1")),
        TimelineEvent(id="commitment_completed:c1", kind="commitment_completed", evidence_kind="recorded", at=NOW,
                      date_basis="completed", title="Commitment done", source=SourceRecord(type="commitment", id="c1")),
        TimelineEvent(id="stakeholder_recorded:s1", kind="stakeholder_recorded", evidence_kind="recorded", at=NOW,
                      date_basis="entered_in_system", title="Stakeholder entered",
                      source=SourceRecord(type="stakeholder", id="s1")),
        deal_entered_event(),
        checkpoint_event(),
    ]
    assert {e.kind for e in events} == {"interaction", "commitment_created", "commitment_due",
                                        "commitment_completed", "stakeholder_recorded", "deal_entered",
                                        "rule_checkpoint"}


@pytest.mark.parametrize("evidence_kind", ["inference", "hypothetical", "statement", "memory"])
def test_timeline_cannot_carry_inference_or_hypothetical_content(evidence_kind):
    with pytest.raises(ValidationError):
        TimelineEvent(id="x", kind="interaction", evidence_kind=evidence_kind, at=CALL, date_basis="occurred",
                      title="t", source=SourceRecord(type="interaction", id="int_1"))


@pytest.mark.parametrize("changes", [
    {"evidence_kind": "recorded"},                          # rep notes are not recorded facts
    {"date_basis": "entered_in_system"},                    # an interaction date is when it occurred
    {"source": SourceRecord(type="deal", id="deal_a")},     # wrong source type
    {"source": None},                                       # recorded events need a source
    {"rule_id": "stalled_deal"},                            # only checkpoints carry a rule
])
def test_event_kind_evidence_kind_and_date_basis_must_match(changes):
    data = interaction_event().model_dump() | changes
    with pytest.raises(ValidationError):
        TimelineEvent(**data)


def test_rule_checkpoint_needs_rule_and_no_source():
    with pytest.raises(ValidationError):
        TimelineEvent(**(checkpoint_event().model_dump() | {"rule_id": None}))
    with pytest.raises(ValidationError):
        TimelineEvent(**(checkpoint_event().model_dump() | {"source": {"type": "deal", "id": "deal_a"}}))


def test_naive_datetimes_and_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        interaction_event(at=datetime(2026, 9, 20, 10))
    with pytest.raises(ValidationError):
        TimelineEvent(**(interaction_event().model_dump() | {"prediction": "will close"}))


def test_timeline_must_be_sorted_and_unique_and_carries_limitations():
    timeline = DealTimeline(customer_id="cus_a", deal_id="deal_a", as_of=NOW,
                            events=[interaction_event(), deal_entered_event(), checkpoint_event()],
                            data_notes=["Deal record entered 2026-09-28, after the first interaction (2026-09-20)."])
    assert timeline.limitations == list(TIMELINE_LIMITATIONS)
    assert timeline.events[-1].at > timeline.as_of  # scheduled/rule dates after NOW are allowed on the timeline
    with pytest.raises(ValidationError, match="sorted"):
        DealTimeline(customer_id="cus_a", deal_id="deal_a", as_of=NOW,
                     events=[deal_entered_event(), interaction_event()])
    with pytest.raises(ValidationError, match="unique"):
        DealTimeline(customer_id="cus_a", deal_id="deal_a", as_of=NOW,
                     events=[interaction_event(), interaction_event()])


# -- known then vs actually followed -----------------------------------------------------------


def branch(**changes):
    data = dict(customer_id="cus_a", deal_id="deal_a", branch_at=CALL + timedelta(hours=1), as_of=NOW,
                known_then=SOURCES, followed=[deal_entered_event()], memory=MEMORY)
    return BranchView(**(data | changes))


def test_valid_branch_view_separates_known_then_from_followed():
    view = branch()
    assert [s.ref for s in view.known_then] == ["R1", "D1", "N1", "M1"]
    assert [e.id for e in view.followed] == ["deal_entered:deal_a"]


def test_evidence_after_the_branch_point_cannot_be_known_then():
    with pytest.raises(ValidationError, match="after branch_at"):
        branch(branch_at=CALL - timedelta(days=1), followed=[])


@pytest.mark.parametrize("event", [interaction_event(), checkpoint_event()])
def test_followed_events_must_lie_between_branch_and_now(event):
    # the call is at/before the branch; the stall checkpoint (09-30) is after NOW (09-29)
    with pytest.raises(ValidationError, match="outside"):
        branch(followed=[event])


def test_branch_after_now_and_duplicate_refs_are_rejected():
    with pytest.raises(ValidationError, match="must not be after"):
        branch(branch_at=NOW + timedelta(days=1), followed=[])
    with pytest.raises(ValidationError, match="unique"):
        branch(known_then=[SOURCES[0], SOURCES[0]])


# -- strategies and rule-derived maps ----------------------------------------------------------


def template(**changes):
    data = dict(id="identify_decision_maker", label="Identify the decision-maker earlier",
                description="Record the decision-maker and their priorities early.", offered=True,
                offered_because=["D1"])
    return StrategyTemplate(**(data | changes))


def test_offered_and_not_offered_templates_must_explain_themselves():
    assert template().offered
    assert not template(offered=False, offered_because=[], not_offered_reason="Stakeholders are recorded.").offered
    with pytest.raises(ValidationError):
        template(offered_because=[])
    with pytest.raises(ValidationError):
        template(offered=False)


def test_unknown_template_ids_are_rejected_and_templates_map_to_the_existing_option_contract():
    with pytest.raises(ValidationError):
        template(id="offer_discount")
    option = template(id="address_requirement_early", needs_anchor=True).as_option()
    assert isinstance(option, StrategyOption) and option.id == "address_requirement_early"


def test_evidence_map_is_rule_derived_and_its_commitments_are_hypothetical():
    m = StrategyEvidenceMap(strategy_id="address_requirement_early", anchor_ref="N1", related=["N1", "M1"],
                            gaps=["No stakeholders recorded."],
                            implied_commitments=[HypotheticalCommitment(owner_party="us",
                                                                        description="Provide the SOC 2 report")])
    assert m.basis == "rule_derived" and m.implied_commitments[0].kind == "hypothetical"
    with pytest.raises(ValidationError):
        HypotheticalCommitment(kind="recorded", owner_party="us", description="x")
    with pytest.raises(ValidationError):
        StrategyEvidenceMap(strategy_id="earlier_follow_up", basis="ai")
    with pytest.raises(ValidationError, match="anchor_ref"):
        StrategyEvidenceMap(strategy_id="address_requirement_early", anchor_ref="N1", related=["M1"])
    with pytest.raises(ValidationError, match="invalid evidence refs"):
        StrategyEvidenceMap(strategy_id="earlier_follow_up", related=["not a ref"])


def test_assess_request_accepts_no_free_text():
    req = TimeMachineAssessRequest(strategy_id="earlier_follow_up", branch_at=CALL)
    assert req.anchor_ref is None
    with pytest.raises(ValidationError):
        TimeMachineAssessRequest(strategy_id="earlier_follow_up", branch_at=CALL, description="offer 50% off")
    with pytest.raises(ValidationError):
        TimeMachineAssessRequest(strategy_id="earlier_follow_up", branch_at=datetime(2026, 9, 20))


# -- optional AI assessment (hypothetical) ------------------------------------------------------


def comparison(strategy_id="earlier_follow_up", citations=("N1",), extra=()):
    assessments = [StrategyAssessment(strategy_id=strategy_id, verdict="mixed",
                                      supporting=[CitedText(text="A call is recorded.", citations=list(citations))]),
                   *extra]
    return StrategyComparison(assessments=assessments, generated=GEN)


def assessment(**changes):
    data = dict(status="generated", customer_id="cus_a", deal_id="deal_a", branch_at=CALL + timedelta(hours=1),
                as_of=NOW, strategy=StrategyOption(id="earlier_follow_up", description="Follow up sooner."),
                evidence_map=StrategyEvidenceMap(strategy_id="earlier_follow_up", related=["N1"]),
                comparison=comparison(), sources=SOURCES, memory=MEMORY)
    return TimeMachineAssessment(**(data | changes))


def test_generated_assessment_keeps_ai_content_hypothetical():
    a = assessment()
    assert a.comparison.kind == "hypothetical" and a.comparison.disclaimer == STRATEGY_DISCLAIMER
    assert a.note == TIME_MACHINE_NOTE and a.evidence_map.basis == "rule_derived"
    with pytest.raises(ValidationError):
        StrategyComparison(kind="recorded", assessments=[], generated=GEN)


def test_status_and_comparison_must_agree():
    ok = assessment(status="insufficient_evidence", comparison=None, insufficient_reason="No notes known then.")
    assert ok.comparison is None
    with pytest.raises(ValidationError):
        assessment(comparison=None)
    with pytest.raises(ValidationError):
        assessment(status="insufficient_evidence", insufficient_reason="x")
    with pytest.raises(ValidationError):
        assessment(status="insufficient_evidence", comparison=None)


@pytest.mark.parametrize("changes, message", [
    ({"comparison": comparison(citations=("N9",))}, "not in sources"),
    ({"evidence_map": StrategyEvidenceMap(strategy_id="earlier_follow_up", related=["Z1"])}, "not in sources"),
    ({"sources": [*SOURCES, source("N2", "rep_note", NOW)]}, "after branch_at"),
    ({"comparison": comparison(strategy_id="identify_decision_maker")}, "exactly one"),
    ({"comparison": comparison(extra=[StrategyAssessment(strategy_id="x", verdict="mixed")])}, "exactly one"),
    ({"evidence_map": StrategyEvidenceMap(strategy_id="identify_decision_maker")}, "different strategy"),
    ({"branch_at": NOW + timedelta(days=1)}, "must not be after"),
])
def test_assessment_rejects_inconsistent_or_later_evidence(changes, message):
    with pytest.raises(ValidationError, match=message):
        assessment(**changes)


# -- backward compatibility of the existing AI contracts --------------------------------------------


def test_existing_cited_text_and_assessment_payloads_still_parse_with_defaults():
    old = {"strategy_id": "A", "supporting": [{"text": "x", "citations": ["R1"]}], "verdict": "supported"}
    parsed = StrategyAssessment.model_validate(old)
    assert parsed.rejected_items == 0 and parsed.supporting[0].issues == []
    with pytest.raises(ValidationError):
        StrategyAssessment(strategy_id="A", verdict="mixed", rejected_items=-1)


def test_strategy_option_id_limit_was_only_widened():
    assert StrategyOption(id="A" * 16, description="x").id  # everything valid before is still valid
    assert StrategyOption(id="address_requirement_early", description="x").id
    with pytest.raises(ValidationError):
        StrategyOption(id="A" * 33, description="x")
    with pytest.raises(ValidationError):
        StrategyOption(id="bad id", description="x")


def test_briefing_contract_is_unchanged():
    assert set(DealBriefing.model_fields) == {"claims", "missing_evidence", "rejected_citations",
                                              "relabelled_claims", "unsupported_claims", "generated"}
