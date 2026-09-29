"""Regression tests for the three defects seen in the TM6 Phase A live run (fake providers only; no model).

1. A supported claim was rejected because its date lived in the source's recorded occurred_at, not its excerpt.
2. A proposed next step was removed because its number/date came from the server's own rule-derived next step.
3. An accepted item confused our deal owner (seller side) with the customer's decision-maker.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.ai.claim_checks import conflates_owner_and_decision_maker, owner_absence_conclusion
from app.ai.grounding import grounding_issues, recorded_dates
from app.ai.providers.ollama import (
    BRIEFING_SYSTEM_PROMPT,
    STRATEGY_SYSTEM_PROMPT,
    render_briefing_prompt,
    render_strategy_prompt,
)
from app.ai.schemas import (
    AIStatus,
    BriefingRequest,
    CitedText,
    Claim,
    DealBriefing,
    DealContext,
    EvidenceItem,
    GenerationInfo,
    StrategyAssessment,
    StrategyComparison,
    StrategyComparisonRequest,
    StrategyOption,
)
from app.ai.service import AIService

CALL = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
GEN = GenerationInfo(provider="fake", model="fake-1", generated_at=CALL)
DEAL = DealContext(customer_id="cus_a", deal_id="deal_a", customer_name="Aurora Logistics", deal_title="Pune pilot",
                   stage="proposal", status="open")
NOTE = ("call (rep note) 'Budget call': CFO said the annual budget is capped at USD 42,000 and a SOC 2 Type II report "
        "is mandatory before signing.")
EVIDENCE = [
    EvidenceItem(ref="R1", kind="recorded", text="Current values (no change history): Deal 'Pune pilot': stage proposal, "
                 "owner not recorded.", source_type="deal", source_id="deal_a"),
    EvidenceItem(ref="D1", kind="signal", text="No stakeholders recorded. The customer has open deals but no "
                 "stakeholders; decision-makers cannot be assessed.", source_type="signal", source_id="s1"),
    EvidenceItem(ref="D2", kind="signal", text="No deal owner. No owner is recorded for this deal.",
                 source_type="signal", source_id="s2"),
    EvidenceItem(ref="N1", kind="rep_note", text=NOTE, source_type="interaction", source_id="int_1", occurred_at=CALL),
]
# The exact supporting statement the live model produced (TM6 Phase A).
LIVE_CLAIM = ("The last meaningful interaction was a budget call on 2026-09-20 where the CFO stated the annual budget "
              "is capped at USD 42,000 and a SOC 2 Type II report is mandatory before signing.")
RULE_STEP = ("Schedule the next customer contact within 5 day(s) of 2026-09-20 (half the 10-day stall threshold for "
             "stage 'proposal'; a rule parameter, not advice).")


class Fake:
    name, model = "fake", "fake-1"

    def __init__(self, assessment=None, claims=None):
        self.assessment, self.claims = assessment, claims

    async def status(self):
        return AIStatus(provider="fake", implemented=True, available=True)

    async def compare_strategies(self, request):
        return StrategyComparison(assessments=[self.assessment], generated=GEN)

    async def generate_briefing(self, request):
        return DealBriefing(claims=self.claims, generated=GEN)


def strategy(evidence=EVIDENCE, rule_next_steps=(RULE_STEP,), **fields):
    a = StrategyAssessment(strategy_id="earlier_follow_up", verdict=fields.pop("verdict", "mixed"), **fields)
    req = StrategyComparisonRequest(deal=DEAL, evidence=evidence, as_of=CALL, rule_next_steps=list(rule_next_steps),
                                    strategies=[StrategyOption(id="earlier_follow_up", description="Scenario.")])
    return asyncio.run(AIService(Fake(assessment=a), timeout_seconds=5, max_retries=0).compare_strategies(req)).assessments[0]


def briefing(claims, evidence=EVIDENCE):
    req = BriefingRequest(deal=DEAL, evidence=evidence, as_of=CALL)
    return asyncio.run(AIService(Fake(claims=claims), timeout_seconds=5, max_retries=0).generate_briefing(req)).claims


# -- Defect 1: recorded source dates -----------------------------------------------------------------------------


def test_the_live_claim_is_now_grounded_by_its_sources_recorded_date():
    assert grounding_issues(LIVE_CLAIM, [NOTE], recorded_dates=[date(2026, 9, 20)]) == []
    # Without the recorded date the claim's date is not grounded (the live failure).
    assert grounding_issues(LIVE_CLAIM, [NOTE]) == ["date '2026-09-20' does not appear in the cited evidence"]


@pytest.mark.parametrize("claim", [
    "The call on 2026-09-20 capped the budget.", "The call on 20 Sep 2026 capped the budget.",
    "The call on 20th September 2026 capped the budget.", "The call on September 20, 2026 capped the budget.",
    "The call on Sept 20 2026 capped the budget.",
])
def test_recorded_date_is_accepted_in_common_formats(claim):
    assert grounding_issues(claim, [NOTE], recorded_dates={date(2026, 9, 20)}) == []


@pytest.mark.parametrize("claim, issue", [
    ("The call on 2026-09-21 capped the budget.", "date '2026-09-21'"),   # a different date
    ("The call on 20 Oct 2026 capped the budget.", "date '2026-10-20'"),  # a different month
    ("The call on 2026-02-30 capped the budget.", "date '2026-02-30'"),   # not a real date: never matched
    ("The customer listed 20 requirements.", "number '20'"),             # date parts are not whitelisted
    ("There were 9 stakeholders.", "number '9'"),
])
def test_other_dates_and_bare_date_parts_are_still_rejected(claim, issue):
    issues = grounding_issues(claim, [NOTE], recorded_dates={date(2026, 9, 20)})
    assert f"{issue} does not appear in the cited evidence" in issues


def test_a_source_without_a_recorded_date_adds_nothing():
    assert recorded_dates([None, None]) == set()
    assert grounding_issues("The deal was reviewed on 2026-09-20.", [EVIDENCE[0].text],
                            recorded_dates=recorded_dates([EVIDENCE[0].occurred_at]))


def test_recorded_dates_use_the_utc_date_the_prompt_shows():
    ist = datetime(2026, 9, 20, 2, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))  # 2026-09-19 20:30 UTC
    assert recorded_dates([ist]) == {date(2026, 9, 19)}
    note = EVIDENCE[3].model_copy(update={"occurred_at": ist})
    assert "[N1] (rep_note 2026-09-19)" in render_briefing_prompt(BriefingRequest(deal=DEAL, evidence=[note], as_of=CALL))
    assert grounding_issues("The call on 2026-09-19 capped the budget.", [NOTE], recorded_dates=recorded_dates([ist])) == []
    assert grounding_issues("The call on 2026-09-20 capped the budget.", [NOTE], recorded_dates=recorded_dates([ist]))


def test_strategy_item_uses_only_the_cited_sources_dates():
    a = strategy(supporting=[CitedText(text=LIVE_CLAIM, citations=["N1"])])
    assert a.supporting[0].citations == ["N1"] and a.supporting[0].issues == []
    # Citing a source without that date (R1) does not borrow N1's date.
    b = strategy(supporting=[CitedText(text="The deal was reviewed on 2026-09-20.", citations=["R1"])])
    assert b.supporting[0].citations == [] and any("date '2026-09-20'" in i for i in b.supporting[0].issues)


def test_briefing_claims_use_recorded_dates_and_keep_their_kind():
    ok, wrong_date = briefing([
        Claim(text="The budget call on 2026-09-20 capped the budget at USD 42,000.", kind="rep_note", citations=["N1"]),
        Claim(text="The budget call on 2026-09-21 capped the budget at USD 42,000.", kind="rep_note", citations=["N1"]),
    ])
    assert (ok.kind, ok.citations, ok.grounding_issues) == ("rep_note", ["N1"], [])
    assert wrong_date.kind == "unsupported" and any("date '2026-09-21'" in i for i in wrong_date.grounding_issues)
    assert ok.text == "The budget call on 2026-09-20 capped the budget at USD 42,000."  # text never changed


def test_uncited_briefing_claims_accept_only_dates_recorded_in_the_evidence():
    known, unknown = briefing([Claim(text="Activity stopped after 2026-09-20.", kind="inference"),
                               Claim(text="Activity stopped after 2026-09-22.", kind="inference")])
    assert known.kind == "inference" and unknown.kind == "unsupported"


# -- Defect 2: next steps grounded against the server's rule-derived next steps ----------------------------------------


def test_rule_derived_number_and_date_are_accepted_in_a_next_step():
    a = strategy(commitments_created=["Could schedule the next customer contact within 5 days of 2026-09-20."])
    assert a.commitments_created == ["Could schedule the next customer contact within 5 days of 2026-09-20."]
    assert a.check_notes == [] and a.rejected_items == 0


@pytest.mark.parametrize("step, issue", [
    ("Could schedule the next contact within 3 days of 2026-09-20.", "number '3'"),
    # 2026, 10 and 5 all appear in the rule text ("10-day", "5 day(s)"), but not as this date: rejected whole.
    ("Could schedule the next contact by 2026-10-05.", "date '2026-10-05'"),
    ("Could offer a 15 day extension.", "number '15'"),
])
def test_unrelated_numbers_and_dates_in_next_steps_are_still_removed(step, issue):
    a = strategy(commitments_created=[step])
    assert a.commitments_created == []
    assert any("A next step was removed" in n and issue in n for n in a.check_notes)


def test_without_the_rule_text_the_rule_number_is_not_accepted():
    a = strategy(rule_next_steps=(), commitments_created=["Could schedule the next contact within 5 days."])
    assert a.commitments_created == []


def test_rule_text_grounds_next_steps_only_never_evidence_items():
    a = strategy(supporting=[CitedText(text="Contact is due within 5 days.", citations=["N1"])])
    assert a.supporting[0].citations == [] and any("'5'" in i for i in a.supporting[0].issues)


def test_next_steps_stay_hypothetical_and_are_not_recorded_commitments():
    request = StrategyComparisonRequest(deal=DEAL, evidence=EVIDENCE, as_of=CALL, rule_next_steps=[RULE_STEP],
                                        strategies=[StrategyOption(id="earlier_follow_up", description="Scenario.")])
    assert RULE_STEP not in render_strategy_prompt(request)  # prompt context comes only from strategy_context
    a = strategy(commitments_created=["Could schedule the next customer contact within 5 days of 2026-09-20."])
    assert a.supporting == [] and a.contradicting == []  # never moved into the evidence-backed lists


# -- Defect 3: our deal owner vs the customer's decision-maker ---------------------------------------------------------


@pytest.mark.parametrize("text", [
    "No deal owner is recorded, so the next contact cannot be scheduled without identifying who the decision-maker is.",
    "The deal has no owner, which means nobody knows who the decision-maker is.",
    "The owner is not recorded, therefore the stakeholders cannot be engaged.",
])
def test_owner_decision_maker_conflation_is_detected(text):
    assert conflates_owner_and_decision_maker(text)


@pytest.mark.parametrize("text", [
    "No deal owner is recorded.",
    "No deal owner is recorded and no stakeholders are recorded.",
    "No stakeholders are recorded, so the decision-maker is unknown.",
    "The deal owner could identify the decision-maker earlier.",
    "No deal owner is recorded. No stakeholders are recorded, so the decision-maker is unknown.",
])
def test_separate_statements_about_owner_and_stakeholders_are_not_flagged(text):
    assert not conflates_owner_and_decision_maker(text)


def test_the_live_conflated_item_keeps_its_owner_fact_and_marks_the_conclusion():
    # Updated with the owner-gap safeguard: the supported owner fact is no longer discarded with the conclusion.
    a = strategy(contradicting=[
        CitedText(text="No deal owner is recorded, so the next contact cannot be scheduled without identifying who "
                       "the decision-maker is.", citations=["D2"]),
        CitedText(text="No stakeholders are recorded, so the decision-maker is unknown.", citations=["D1"])])
    fact, marked, kept = a.contradicting
    assert (fact.text, fact.citations, fact.issues) == ("No deal owner is recorded.", ["D2"], [])
    assert marked.citations == [] and "deal owner is seller-side" in marked.issues[0]
    assert kept.citations == ["D1"] and kept.issues == []


def test_both_prompts_state_the_owner_decision_maker_distinction():
    for prompt in (STRATEGY_SYSTEM_PROMPT, BRIEFING_SYSTEM_PROMPT):
        assert "deal owner is our own sales rep (seller side)" in prompt
        assert "A missing deal owner says nothing about the customer's decision-maker" in prompt
        assert "nothing about whether the customer can be contacted, whom to contact or when" in prompt
    assert "\\n" not in STRATEGY_SYSTEM_PROMPT and "\\n" not in BRIEFING_SYSTEM_PROMPT  # real newlines only


def test_dates_written_in_evidence_text_still_ground_whole_dates():
    commitment = "Commitment (owner: us): Send SOC 2 report; due 2026-09-26; status open."
    assert grounding_issues("The report was due on 26 September 2026.", [commitment]) == []
    assert grounding_issues("The report was due on 2026-09-25.", [commitment]) == [
        "date '2026-09-25' does not appear in the cited evidence"]


# -- Defect 3b (post-fix live run): customer-contact conclusions from a missing deal owner -----------------------------
# Live output: "No deal owner is recorded, so the sales rep cannot determine who to contact next." (accepted before).

LIVE_OWNER_CONTACT = "No deal owner is recorded, so the sales rep cannot determine who to contact next."


@pytest.mark.parametrize("text, fact, conclusion", [
    (LIVE_OWNER_CONTACT, "No deal owner is recorded.", "The sales rep cannot determine who to contact next."),
    ("The rep can't decide whom to reach out to because the internal deal owner is missing.",
     "The internal deal owner is missing.", "The rep can't decide whom to reach out to."),
    ("The rep doesn't know who to contact next because no deal owner is recorded.",
     "No deal owner is recorded.", "The rep doesn't know who to contact next."),
    ("A customer contact cannot be scheduled because no internal owner is assigned.",
     "No internal owner is assigned.", "A customer contact cannot be scheduled."),
    ("Because no deal owner is recorded, the rep does not know whom to contact.",
     "No deal owner is recorded.", "The rep does not know whom to contact."),
    ("The internal seller-side owner field is empty, so the rep cannot decide whom to call.",
     "The internal seller-side owner field is empty.", "The rep cannot decide whom to call."),
    ("Without a deal owner, the next contact cannot be scheduled.", None, "The next contact cannot be scheduled."),
    ("The next customer contact cannot be scheduled until an owner is assigned.", None,
     "The next customer contact cannot be scheduled."),
])
def test_contact_conclusions_from_a_missing_owner_are_detected_and_split(text, fact, conclusion):
    found = owner_absence_conclusion(text)
    assert found is not None and found.kind == "customer_contact"
    assert (found.fact, found.conclusion) == (fact, conclusion)
    assert "seller-side" in found.reason and conflates_owner_and_decision_maker(text)


@pytest.mark.parametrize("text", [
    "No deal owner is recorded.",
    "The internal seller-side owner field is empty.",
    "No customer stakeholders are recorded.",
    "The customer-side decision-maker has not been identified in the available records.",
    "No stakeholders are recorded, so the rep does not know whom to contact.",   # customer-side gap, not the owner
    "The CFO asked for the SOC 2 report, so the rep could follow up with the CFO.",
    "Could schedule the next customer contact within 5 days of 2026-09-20.",
    "No deal owner is recorded and the next contact is not scheduled.",          # no causal link: not this check
])
def test_valid_statements_and_customer_side_gaps_are_not_flagged(text):
    assert owner_absence_conclusion(text) is None


def test_the_live_contact_conclusion_is_marked_and_its_owner_fact_kept():
    a = strategy(contradicting=[CitedText(text=LIVE_OWNER_CONTACT, citations=["D2"])])
    fact, conclusion = a.contradicting
    assert (fact.text, fact.citations, fact.issues) == ("No deal owner is recorded.", ["D2"], [])
    assert conclusion.text == "The sales rep cannot determine who to contact next."
    assert conclusion.citations == [] and "says nothing about whom to contact or when" in conclusion.issues[0]
    assert a.rejected_items == 1 and a.verdict == "unsupported"  # no supporting item was given in this sample


def test_owner_gap_without_a_clean_fact_clause_is_marked_whole():
    a = strategy(contradicting=[CitedText(text="Without a deal owner, the next contact cannot be scheduled.",
                                          citations=["D2", "X9"])])
    [conclusion] = a.contradicting
    assert conclusion.citations == [] and conclusion.issues and a.rejected_items == 1


def test_unknown_citations_on_a_flagged_item_are_still_reported():
    req = StrategyComparisonRequest(deal=DEAL, evidence=EVIDENCE, as_of=CALL, strategies=[
        StrategyOption(id="earlier_follow_up", description="Scenario.")])
    item = StrategyAssessment(strategy_id="earlier_follow_up", verdict="mixed", contradicting=[
        CitedText(text=LIVE_OWNER_CONTACT, citations=["D2", "X9"])])
    result = asyncio.run(AIService(Fake(assessment=item), timeout_seconds=5, max_retries=0).compare_strategies(req))
    assert result.rejected_citations == ["X9"]
    assert result.assessments[0].contradicting[0].citations == ["D2"]


@pytest.mark.parametrize("text, cite", [
    ("No deal owner is recorded.", "D2"),
    ("The internal seller-side owner field is empty.", "R1"),
    ("No customer stakeholders are recorded.", "D1"),
    ("The customer-side decision-maker has not been identified in the available records.", "D1"),
])
def test_supported_missing_information_statements_stay_accepted(text, cite):
    a = strategy(contradicting=[CitedText(text=text, citations=[cite])])
    assert a.contradicting[0].citations == [cite] and a.contradicting[0].issues == []


@pytest.mark.parametrize("text", ["No customer stakeholders are recorded.",
                                  "The customer-side decision-maker has not been identified in the available records."])
def test_missing_information_statements_still_need_evidence(text):
    a = strategy(contradicting=[CitedText(text=text, citations=[])])
    assert a.contradicting[0].citations == [] and "no valid citation" in a.contradicting[0].issues[0]


def test_missing_stakeholders_remain_distinct_from_a_missing_owner():
    a = strategy(contradicting=[
        CitedText(text="No stakeholders are recorded, so the customer's decision-makers cannot be assessed.",
                  citations=["D1"]),
        CitedText(text=LIVE_OWNER_CONTACT, citations=["D2"])])
    stakeholders, owner_fact, owner_conclusion = a.contradicting
    assert stakeholders.citations == ["D1"] and stakeholders.issues == []
    assert owner_fact.citations == ["D2"] and owner_conclusion.citations == []


def test_grounded_contact_recommendations_still_work():
    a = strategy(supporting=[CitedText(text="The CFO asked for the SOC 2 report, so the rep could follow up with the "
                                            "CFO.", citations=["N1"])],
                 commitments_created=["Could schedule the next customer contact within 5 days of 2026-09-20."])
    assert a.supporting[0].citations == ["N1"] and a.supporting[0].issues == []
    assert a.commitments_created == ["Could schedule the next customer contact within 5 days of 2026-09-20."]
