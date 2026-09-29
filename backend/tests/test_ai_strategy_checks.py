"""AIService.compare_strategies checks for Deal Time Machine assessments (fake provider; no model)."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.claim_checks import absence_as_fact, outcome_language, quotes_without_statement
from app.ai.schemas import (
    AIStatus,
    CitedText,
    DealContext,
    EvidenceItem,
    GenerationInfo,
    StrategyAssessment,
    StrategyComparison,
    StrategyComparisonRequest,
    StrategyOption,
)
from app.ai.service import AIService

BRANCH = datetime(2026, 9, 23, 12, tzinfo=UTC)
GEN = GenerationInfo(provider="fake", model="fake-1", generated_at=BRANCH)
DEAL = DealContext(customer_id="cus_a", deal_id="deal_a", customer_name="Aurora Logistics", deal_title="Pune pilot",
                   stage="proposal", status="open")
EVIDENCE = [
    EvidenceItem(ref="R1", kind="recorded", text="Current values (no change history): Deal 'Pune pilot' for Aurora "
                 "Logistics: stage proposal.", source_type="deal", source_id="deal_a"),
    EvidenceItem(ref="R2", kind="recorded", text="Commitment (owner: us): Send SOC 2 report; due 2026-09-26; "
                 "status open.", source_type="commitment", source_id="com_1"),
    EvidenceItem(ref="D1", kind="signal", text="No stakeholders recorded. The customer has open deals but no "
                 "stakeholders; decision-makers cannot be assessed.", source_type="signal",
                 source_id="no_stakeholders_recorded:cus_a"),
    EvidenceItem(ref="N1", kind="rep_note", text="call (rep note) 'Budget call': CFO said the annual budget is capped "
                 "at USD 42,000 and a SOC 2 Type II report is mandatory before signing.", source_type="interaction",
                 source_id="int_1", occurred_at=datetime(2026, 9, 20, 10, tzinfo=UTC)),
]
STATEMENT = EvidenceItem(ref="S1", kind="statement", text='"We cannot sign without the SOC 2 report."',
                         source_type="interaction", source_id="int_2")
OUTCOME = EvidenceItem(ref="O1", kind="outcome", text="Borealis lost after a late SOC 2 report.",
                       source_type="outcome")


class Fake:
    name, model = "fake", "fake-1"

    def __init__(self, assessment: StrategyAssessment):
        self.assessment = assessment

    async def status(self):
        return AIStatus(provider="fake", implemented=True, available=True)

    async def compare_strategies(self, request):
        return StrategyComparison(assessments=[self.assessment], generated=GEN)


def check(verdict="supported", supporting=(), contradicting=(), gaps=(), steps=(), evidence=EVIDENCE):
    a = StrategyAssessment(strategy_id="address_requirement_early", verdict=verdict, supporting=list(supporting),
                           contradicting=list(contradicting), evidence_gaps=list(gaps), commitments_created=list(steps))
    request = StrategyComparisonRequest(deal=DEAL, evidence=evidence, as_of=BRANCH, strategies=[
        StrategyOption(id="address_requirement_early", description="Scenario: act earlier.")])
    result = asyncio.run(AIService(Fake(a), timeout_seconds=5, max_retries=0).compare_strategies(request))
    return result, result.assessments[0]


def cited(text, *refs):
    return CitedText(text=text, citations=list(refs))


# -- valid output ---------------------------------------------------------------------------------------


def test_valid_assessment_passes_unchanged():
    result, a = check(supporting=[cited("A SOC 2 Type II report is mandatory before signing.", "N1"),
                                  cited("Sending the SOC 2 report is an open commitment.", "R2")],
                      contradicting=[cited("No stakeholders are recorded, so the reviewer is unknown.", "D1")],
                      gaps=["No decision date is recorded."], steps=["Could send the SOC 2 report earlier."])
    assert a.verdict == "supported" and a.rejected_items == 0 and a.check_notes == []
    assert [x.citations for x in a.supporting] == [["N1"], ["R2"]] and all(not x.issues for x in a.supporting)
    assert a.commitments_created == ["Could send the SOC 2 report earlier."]
    assert result.kind == "hypothetical" and "not predictions" in result.disclaimer


def test_supported_is_capped_at_mixed_without_outcomes_and_two_factual_sources():
    _, a = check(supporting=[cited("A SOC 2 Type II report is mandatory before signing.", "N1")])
    assert a.verdict == "mixed" and any("capped" in n for n in a.check_notes)
    _, with_outcome = check(supporting=[cited("A SOC 2 Type II report is mandatory before signing.", "N1")],
                            evidence=EVIDENCE + [OUTCOME])
    assert with_outcome.verdict == "supported"


def test_verdict_is_only_weakened_never_strengthened():
    _, a = check(verdict="unsupported", supporting=[cited("A SOC 2 Type II report is mandatory.", "N1"),
                                                   cited("Sending the SOC 2 report is open.", "R2")])
    assert a.verdict == "unsupported"


# -- citations -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ["N9", "M1", "N2"])  # unknown, or a later item that is not in the point-in-time pack
def test_citations_outside_the_pack_are_rejected_and_the_item_marked(ref):
    result, a = check(supporting=[cited("A later call confirmed the report.", ref)])
    assert ref in result.rejected_citations
    [item] = a.supporting
    assert item.citations == [] and "no valid citation" in item.issues[0]
    assert a.verdict == "unsupported" and a.rejected_items == 1


def test_uncited_items_are_marked_not_accepted():
    _, a = check(verdict="mixed", contradicting=[cited("The timeline is unclear.")])
    assert a.contradicting[0].citations == [] and a.contradicting[0].issues


# -- grounding, statements, absence ----------------------------------------------------------------------


@pytest.mark.parametrize("text, fragment", [
    ("Northwind also requires the SOC 2 report.", "northwind"),
    ("The budget is capped at USD 50,000.", "50000"),
    ("Priya's approval depends on the SOC 2 report.", "priya"),
])
def test_invented_names_and_numbers_are_marked(text, fragment):
    _, a = check(supporting=[cited(text, "N1")])
    assert a.supporting[0].citations == [] and any(fragment in i for i in a.supporting[0].issues)


def test_a_rep_note_cannot_be_quoted_as_a_customer_statement():
    _, a = check(supporting=[cited('The customer said "the SOC 2 Type II report is mandatory before signing".', "N1")])
    assert a.supporting[0].citations == [] and "rep notes are the rep's account" in a.supporting[0].issues[0]
    _, ok = check(supporting=[cited('The customer said "We cannot sign without the SOC 2 report."', "S1")],
                  evidence=EVIDENCE + [STATEMENT])
    assert ok.supporting[0].citations == ["S1"]


def test_absence_of_records_is_not_stated_as_fact():
    _, a = check(contradicting=[cited("The customer has no decision-maker.", "D1")])
    assert "only show it was not recorded" in a.contradicting[0].issues[0]
    _, ok = check(contradicting=[cited("No decision-maker is recorded; the deal has no stakeholders recorded.", "D1")])
    assert ok.contradicting[0].citations == ["D1"]


def test_mixed_fact_and_inference_stays_hypothetical():
    # Strategy items are hypothetical by construction: a fact joined with a conclusion is never relabelled "recorded".
    result, a = check(supporting=[cited("No stakeholders are recorded, making the decision-maker unknown.", "D1"),
                                  cited("A SOC 2 Type II report is mandatory before signing.", "N1")])
    assert result.kind == "hypothetical" and a.supporting[0].citations == ["D1"]
    assert not hasattr(a.supporting[0], "kind")


# -- prohibited outcome language -------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Sending the report earlier would have closed the deal.",
    "This strategy will win the deal.",
    "There was a 70% chance of signing.",
    "Following up sooner would have led to a signature.",
    "If we had followed up sooner, it would have closed.",
    "The customer was likely to sign.",
    "This definitely helps.",
    "The win rate for this approach is high.",
    "Confidence score: high.",
])
def test_outcome_language_is_detected(text):
    assert outcome_language(text) is not None


@pytest.mark.parametrize("text", [
    "Following up sooner could have been considered.",
    "The evidence does not establish whether this would have changed the outcome.",
    "A plausible alternative was to send the report earlier.",
    "The deal is due to close on a date that is not recorded.",
    "A chance to discuss the SOC 2 report.",
])
def test_neutral_language_is_not_flagged(text):
    assert outcome_language(text) is None


def test_outcome_language_is_removed_everywhere_and_noted():
    _, a = check(supporting=[cited("Sending the report earlier would have closed the deal.", "N1"),
                             cited("A SOC 2 Type II report is mandatory before signing.", "N1")],
                 gaps=["There was a 70% chance of signing."],
                 steps=["Could send the SOC 2 report; this guarantees signature.", "Could share the report."])
    text = str(a.model_dump())
    assert "would have closed" not in text and "70%" not in text and "guarantees" not in text
    assert len(a.supporting) == 1 and a.evidence_gaps == [] and a.commitments_created == ["Could share the report."]
    assert a.rejected_items == 3 and len(a.check_notes) >= 3
    assert any("predicts a deal outcome" in n for n in a.check_notes)


def test_next_steps_with_invented_dates_or_names_are_removed():
    _, a = check(supporting=[cited("A SOC 2 Type II report is mandatory before signing.", "N1")],
                 steps=["Could send the report to Northwind by 2026-10-05.", "Could send the SOC 2 report."])
    assert a.commitments_created == ["Could send the SOC 2 report."]
    assert any("A next step was removed" in n for n in a.check_notes)


def test_helper_rules():
    assert absence_as_fact("There was no budget.") and not absence_as_fact("There was no budget recorded.")
    assert absence_as_fact("The customer lacked a decision-maker.")
    assert quotes_without_statement('He said "the budget is capped now".', {"rep_note"})
    assert not quotes_without_statement('He said "the budget is capped now".', {"statement"})
    assert not quotes_without_statement('A "SOC 2" report.', {"rep_note"})  # short term, not a quotation
