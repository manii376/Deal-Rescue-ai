"""Mixed fact/conclusion claims are split so a conclusion is never labelled as recorded (fake provider; no model)."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.claim_checks import split_mixed_claim
from app.ai.providers.ollama import BRIEFING_SYSTEM_PROMPT
from app.ai.schemas import AIStatus, BriefingRequest, Claim, DealBriefing, DealContext, EvidenceItem, GenerationInfo
from app.ai.service import AIService

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DEAL = DealContext(customer_id="cus_a", deal_id="deal_a", customer_name="Aurora Logistics", deal_title="Pune pilot",
                   stage="proposal", status="open")
# D1 is the exact text the evidence assembler produces for the no_stakeholders_recorded signal.
EVIDENCE = [
    EvidenceItem(ref="R1", kind="recorded", text="Deal 'Pune pilot' for Aurora Logistics: stage proposal, status open, "
                 "value not recorded, expected close not recorded, owner not recorded.",
                 source_type="deal", source_id="deal_a"),
    EvidenceItem(ref="R2", kind="recorded", text="Commitment (owner: us): send SOC 2 Type II report; due 2026-09-25; "
                 "status open.", source_type="commitment", source_id="com_1"),
    EvidenceItem(ref="D1", kind="signal", text="No stakeholders recorded. The customer has open deals but no "
                 "stakeholders; decision-makers cannot be assessed.", source_type="signal",
                 source_id="no_stakeholders_recorded:cus_a"),
    EvidenceItem(ref="N1", kind="rep_note", text="call (rep note): CFO Priya Raman said the budget is capped at "
                 "USD 42,000.", source_type="interaction", source_id="int_1"),
]


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, claims):
        self.claims = claims

    async def status(self):
        return AIStatus(provider="fake", implemented=True, available=True)

    async def generate_briefing(self, request):
        return DealBriefing(claims=self.claims, generated=GenerationInfo(provider="fake", model="fake-1",
                                                                         generated_at=NOW))


def enforce(claims):
    service = AIService(FakeProvider(claims), timeout_seconds=5, max_retries=0)
    return asyncio.run(service.generate_briefing(BriefingRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW)))


def rows(result):
    return [(c.kind, c.text, c.citations) for c in result.claims]


# -- the observed live case -------------------------------------------------------------------


def test_observed_mixed_stakeholder_claim_is_split_into_fact_and_inference():
    result = enforce([Claim(text="The deal has no stakeholders recorded, making decision-makers unassessable.",
                            kind="recorded", citations=["D1"])])
    assert rows(result) == [
        ("recorded", "The deal has no stakeholders recorded.", ["D1"]),
        ("inference", "This makes decision-makers unassessable.", ["D1"]),
    ]
    assert result.relabelled_claims == 1 and result.unsupported_claims == 0
    assert all(not c.grounding_issues for c in result.claims)


def test_correctly_separated_claims_pass_unchanged():
    claims = [
        Claim(text="No stakeholders are recorded for this customer.", kind="recorded", citations=["D1"]),
        Claim(text="The records may not be enough to assess who makes the decision (unconfirmed).",
              kind="inference", citations=["D1"]),
    ]
    result = enforce(claims)
    assert rows(result) == [(c.kind, c.text, c.citations) for c in claims]
    assert result.relabelled_claims == 0


# -- claims that must NOT be changed ------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "The SOC 2 Type II report commitment was due 2026-09-25 and is still open.",
    "Stage is proposal, status open, value not recorded.",
    "The commitment to send the SOC 2 Type II report is open; it was due 2026-09-25.",
])
def test_purely_recorded_claim_keeps_its_kind(text):
    result = enforce([Claim(text=text, kind="recorded", citations=["R1", "R2"])])
    assert rows(result) == [("recorded", text, ["R1", "R2"])]
    assert result.relabelled_claims == 0


def test_supported_rep_note_claim_keeps_its_kind():
    text = "The rep noted that CFO Priya Raman said the budget is capped at USD 42,000."
    result = enforce([Claim(text=text, kind="rep_note", citations=["N1"])])
    assert rows(result) == [("rep_note", text, ["N1"])]


def test_cannot_be_assessed_wording_alone_is_not_relabelled():
    # Vocabulary alone is not a connector: the signal's own wording, restated, stays a recorded claim.
    text = "No stakeholders are recorded; decision-makers cannot be assessed from the records."
    assert split_mixed_claim(Claim(text=text, kind="recorded", citations=["D1"])) == [
        Claim(text=text, kind="recorded", citations=["D1"])]


@pytest.mark.parametrize("text", [
    "No reply is recorded so far, and the commitment is still open.",
    "The customer is making a shortlist, per the rep note.",
    'The rep wrote: "budget is capped, so the pilot waits".',
])
def test_non_connectors_and_quoted_text_are_not_split(text):
    claim = Claim(text=text, kind="rep_note", citations=["N1"])
    assert split_mixed_claim(claim) == [claim]


@pytest.mark.parametrize("kind", ["inference", "statement", "unsupported"])
def test_only_recorded_and_rep_note_claims_are_split(kind):
    claim = Claim(text="No stakeholders are recorded, making decision-makers unassessable.", kind=kind)
    assert split_mixed_claim(claim) == [claim]


# -- related mixed forms ------------------------------------------------------------------------


def test_mixed_rep_note_claim_keeps_the_note_and_marks_the_conclusion_as_inference():
    result = enforce([Claim(text="CFO Priya Raman said the budget is capped at USD 42,000, suggesting price "
                                 "sensitivity.", kind="rep_note", citations=["N1"])])
    assert rows(result) == [
        ("rep_note", "CFO Priya Raman said the budget is capped at USD 42,000.", ["N1"]),
        ("inference", "This suggests price sensitivity.", ["N1"]),
    ]


@pytest.mark.parametrize("text, fact, conclusion", [
    ("The deal has no expected close date, so timing cannot be assessed.",
     "The deal has no expected close date.", "As a result, timing cannot be assessed."),
    ("The deal has no expected close date; therefore the timeline is unclear.",
     "The deal has no expected close date.", "As a result, the timeline is unclear."),
    ("The deal owner is not recorded, which means accountability is unclear.",
     "The deal owner is not recorded.", "This means accountability is unclear."),
    ("The deal value is not recorded — leaving the forecast uncertain.",
     "The deal value is not recorded.", "This leaves the forecast uncertain."),
])
def test_consequence_connectors_split(text, fact, conclusion):
    parts = split_mixed_claim(Claim(text=text, kind="recorded", citations=["R1"]))
    assert [(p.kind, p.text, p.citations) for p in parts] == [
        ("recorded", fact, ["R1"]), ("inference", conclusion, ["R1"])]


def test_too_short_fact_part_makes_the_whole_claim_an_inference():
    parts = split_mixed_claim(Claim(text="Nothing recorded, so risk is high.", kind="recorded", citations=["D1"]))
    assert [(p.kind, p.text) for p in parts] == [("inference", "Nothing recorded, so risk is high.")]


# -- the split never bypasses the existing checks --------------------------------------------------


def test_split_conclusion_still_goes_through_grounding():
    result = enforce([Claim(text="No stakeholders are recorded, making Northwind the likely decision-maker.",
                            kind="recorded", citations=["D1"])])
    fact, conclusion = result.claims
    assert (fact.kind, fact.citations) == ("recorded", ["D1"])
    assert (conclusion.kind, conclusion.citations) == ("unsupported", [])
    assert any("northwind" in issue for issue in conclusion.grounding_issues)
    assert result.unsupported_claims == 1


def test_split_fact_still_needs_matching_evidence_kind():
    # A "recorded" fact citing only a rep note is relabelled as before; the conclusion is an inference.
    result = enforce([Claim(text="CFO Priya Raman said the budget is capped at USD 42,000, meaning the pilot "
                                 "must stay small.", kind="recorded", citations=["N1", "X9"])])
    assert [(c.kind, c.citations) for c in result.claims] == [("inference", ["N1"]), ("inference", ["N1"])]
    assert result.rejected_citations == ["X9"]
    assert result.relabelled_claims == 2


def test_prompt_asks_for_one_kind_per_claim_and_forbids_absence_as_fact():
    assert "Never join a recorded fact and a conclusion in one claim" in BRIEFING_SYSTEM_PROMPT
    assert "Do not claim it does not exist" in BRIEFING_SYSTEM_PROMPT


# -- a missing seller-side deal owner does not support customer-contact conclusions (briefing path) --------------------


def test_briefing_owner_gap_conclusion_becomes_unsupported_and_the_fact_is_kept():
    result = enforce([Claim(text="No deal owner is recorded, so the sales rep cannot determine who to contact next.",
                            kind="recorded", citations=["R1"])])
    fact, conclusion = result.claims
    assert (fact.kind, fact.text, fact.citations) == ("recorded", "No deal owner is recorded.", ["R1"])
    assert (conclusion.kind, conclusion.citations) == ("unsupported", [])
    assert conclusion.text == "The sales rep cannot determine who to contact next."
    assert "deal owner is seller-side" in conclusion.grounding_issues[0]
    assert result.unsupported_claims == 1 and result.relabelled_claims == 0


def test_briefing_owner_gap_inference_is_not_left_as_an_unconfirmed_inference():
    result = enforce([Claim(text="Because no deal owner is recorded, the rep does not know whom to contact.",
                            kind="inference", citations=["R1"])])
    assert [(c.kind, c.text) for c in result.claims] == [
        ("inference", "No deal owner is recorded."), ("unsupported", "The rep does not know whom to contact.")]


def test_briefing_valid_owner_and_stakeholder_statements_are_unchanged():
    result = enforce([Claim(text="No deal owner is recorded.", kind="recorded", citations=["R1"]),
                      Claim(text="No customer stakeholders are recorded.", kind="recorded", citations=["D1"])])
    assert rows(result) == [("recorded", "No deal owner is recorded.", ["R1"]),
                            ("recorded", "No customer stakeholders are recorded.", ["D1"])]
    assert result.unsupported_claims == 0
