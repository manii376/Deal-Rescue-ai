"""Grounding check and extended briefing enforcement (fake provider; no model)."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.grounding import extract_names, extract_numbers, grounding_issues
from app.ai.schemas import AIStatus, BriefingRequest, Claim, DealBriefing, DealContext, EvidenceItem, GenerationInfo
from app.ai.service import AIService

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DEAL = DealContext(customer_id="cus_a", deal_id="deal_a", customer_name="Aurora Logistics", deal_title="Pune pilot",
                   stage="proposal", status="open")
EVIDENCE = [
    EvidenceItem(ref="R1", kind="recorded", text="Deal 'Pune pilot' for Aurora Logistics: value USD 42,000, "
                 "expected close 2026-10-31.", source_type="deal", source_id="deal_a"),
    EvidenceItem(ref="R2", kind="recorded", text="Commitment (owner: us): send SOC 2 Type II report; due 2026-09-25; "
                 "status open.", source_type="commitment", source_id="com_1"),
    EvidenceItem(ref="D1", kind="signal", text="Commitment overdue by 3 day(s). 'send SOC 2 Type II report' (we) "
                 "was due 2026-09-25.", source_type="signal", source_id="commitment_overdue:com_1"),
    EvidenceItem(ref="N1", kind="rep_note", text="call (rep note): CFO Priya Raman said the budget is capped at "
                 "USD 42,000.", source_type="interaction", source_id="int_1"),
    EvidenceItem(ref="M1", kind="memory", text="Priya Raman requires a SOC 2 Type II report before signing.",
                 source_type="memory", source_id="int_1"),
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


def summary(result):
    return [(c.kind, c.citations) for c in result.claims]


# -- extraction ------------------------------------------------------------------------------


def test_number_extraction_normalises_formats():
    assert extract_numbers("USD 42,000 and 42k, 3 days, 20%, 1.50, due 2026-09-25") == \
        {"42000", "3", "20", "1.5", "2026", "9", "25"}


def test_name_extraction_skips_sentence_starts_and_months():
    names = extract_names("Aurora may choose Northwind. The CFO met Priya Raman in September about SOC 2.")
    assert names == {"northwind", "cfo", "priya", "raman", "soc"}


# -- enforcement --------------------------------------------------------------------------------


def test_supported_claims_keep_their_kind_and_citations():
    result = enforce([
        Claim(text="The deal is worth USD 42,000 and expected to close on 2026-10-31.", kind="recorded",
              citations=["R1"]),
        Claim(text="The SOC 2 Type II report commitment is overdue by 3 days.", kind="recorded", citations=["D1"]),
        Claim(text="Priya Raman said the budget is capped at USD 42,000.", kind="rep_note", citations=["N1"]),
        Claim(text="Priya Raman requires a SOC 2 Type II report before signing.", kind="rep_note", citations=["M1"]),
    ])
    assert summary(result) == [("recorded", ["R1"]), ("recorded", ["D1"]), ("rep_note", ["N1"]), ("rep_note", ["M1"])]
    assert result.unsupported_claims == 0 and result.relabelled_claims == 0


def test_invented_name_in_cited_claim_is_unsupported_and_uncited():
    result = enforce([Claim(text="Aurora is about to choose the competitor Northwind.", kind="inference",
                            citations=["D1"])])
    [claim] = result.claims
    assert claim.kind == "unsupported" and claim.citations == []
    assert claim.grounding_issues == ["name 'northwind' does not appear in the cited evidence"]
    assert result.unsupported_claims == 1


def test_number_not_in_cited_evidence_is_unsupported():
    result = enforce([Claim(text="The budget is capped at USD 310,000.", kind="rep_note", citations=["N1"])])
    assert result.claims[0].kind == "unsupported"
    assert "number '310000'" in result.claims[0].grounding_issues[0]


def test_number_present_elsewhere_but_not_in_the_cited_item_is_unsupported():
    # 42,000 is in R1/N1 but the claim cites only R2
    result = enforce([Claim(text="We must send the report before the USD 42,000 deal closes.", kind="recorded",
                            citations=["R2"])])
    assert result.claims[0].kind == "unsupported"


def test_uncited_claim_with_invented_entity_is_unsupported():
    result = enforce([Claim(text="Legal at Contoso is reviewing the contract.", kind="inference", citations=[])])
    assert result.claims[0].kind == "unsupported"


def test_uncited_generic_inference_stays_inference():
    result = enforce([Claim(text="The overdue report may be slowing the deal down.", kind="inference", citations=[])])
    assert summary(result) == [("inference", [])]


def test_rep_notes_are_never_customer_statements():
    result = enforce([Claim(text="Priya Raman said the budget is capped at USD 42,000.", kind="statement",
                            citations=["N1"])])
    assert summary(result) == [("inference", ["N1"])] and result.relabelled_claims == 1


def test_recorded_label_needs_recorded_or_signal_evidence():
    result = enforce([Claim(text="Priya Raman said the budget is capped at USD 42,000.", kind="recorded",
                            citations=["N1"])])
    assert summary(result) == [("inference", ["N1"])]


def test_unknown_citations_are_removed_and_reported():
    result = enforce([Claim(text="The deal is worth USD 42,000.", kind="recorded", citations=["R1", "R9", "X1"])])
    assert summary(result) == [("recorded", ["R1"])] and result.rejected_citations == ["R9", "X1"]


def test_claim_citing_only_unknown_refs_is_not_presented_as_fact():
    result = enforce([Claim(text="The deal is worth USD 42,000.", kind="recorded", citations=["R9"])])
    [claim] = result.claims
    assert claim.kind in ("inference", "unsupported") and claim.citations == []


def test_provider_supplied_unsupported_kind_loses_citations():
    result = enforce([Claim(text="The deal is worth USD 42,000.", kind="unsupported", citations=["R1"])])
    assert summary(result) == [("unsupported", [])]


@pytest.mark.parametrize("text", [
    "The deal value of USD 42,000 is recorded.",           # passes lexically...
])
def test_lexical_pass_is_not_semantic_proof(text):
    # ...even if the meaning were wrong, the check cannot tell: it only guards numbers/names.
    assert grounding_issues(text, [EVIDENCE[0].text]) == []


# -- sentence-initial names (regression for the Stage 2 gap) --------------------------------------


N1_TEXT = EVIDENCE[3].text  # "call (rep note): CFO Priya Raman said the budget is capped at USD 42,000."


@pytest.mark.parametrize("claim", [
    "Northwind is evaluating the deal.",
    "Borealis is also evaluating this vendor.",
    "The budget is capped at USD 42,000. Contoso may undercut the price.",
    "- Globex asked for a discount.",
])
def test_invented_sentence_initial_name_is_flagged(claim):
    issues = grounding_issues(claim, [N1_TEXT])
    assert len(issues) == 1 and issues[0].startswith("name '")


def test_invented_sentence_initial_name_downgrades_the_claim():
    result = enforce([Claim(text="Northwind is about to win this deal.", kind="inference", citations=["N1"])])
    [claim] = result.claims
    assert claim.kind == "unsupported" and claim.citations == []
    assert claim.grounding_issues == ["name 'northwind' does not appear in the cited evidence"]


@pytest.mark.parametrize("claim,evidence", [
    ("Priya Raman said the budget is capped at USD 42,000.", [N1_TEXT]),        # name in cited evidence
    ("Aurora needs the SOC 2 Type II report before signing.", [EVIDENCE[1].text]),  # customer name = context
    ("Raman is the CFO.", [N1_TEXT]),
])
def test_known_sentence_initial_names_are_not_flagged(claim, evidence):
    assert grounding_issues(claim, evidence, [DEAL.customer_name, DEAL.deal_title]) == []


@pytest.mark.parametrize("claim", [
    "The budget is capped at USD 42,000.",
    "Currently the budget is capped at USD 42,000.",
    "However, the CFO capped the budget.",
    "Overall the deal looks at risk.",
    "Budget is capped at USD 42,000.",
    "Signing depends on the budget cap.",
    "Pricing was not discussed.",
    "Delayed paperwork may slow the deal.",
    "Procurement has not been engaged.",
    "Key risk: the report is overdue.",
    "Next step: send the report.",
    "It is unclear who approves the deal.",
    "Documentation is incomplete.",
    "Risks include the overdue report.",
])
def test_ordinary_sentence_starters_are_not_flagged(claim):
    assert grounding_issues(claim, [N1_TEXT]) == []


@pytest.mark.parametrize("claim,fragment", [
    ("The CFO said the budget is capped at USD 50,000.", "number '50000'"),
    ("Priya Raman and Marcus Lee approved the budget.", "name 'lee'"),
    ("Northwind offered USD 30,000.", "number '30000'"),
])
def test_unsupported_names_and_numbers_are_still_flagged(claim, fragment):
    assert any(fragment in issue for issue in grounding_issues(claim, [N1_TEXT]))


def test_known_gap_is_documented_common_word_names_at_sentence_start():
    # "Summit" is not in the common-word list, so it is caught; a name that is also an ordinary
    # word ("Morning Ventures") at sentence start is the documented remaining gap.
    assert grounding_issues("Summit is evaluating the deal.", [N1_TEXT]) != []
    assert grounding_issues("Morning is a good time to call.", [N1_TEXT]) == []


# -- possessives (regression for the live-run false positive) ------------------------------------


M2_TEXT = "Aurora Logistics requires a SOC 2 Type II report before signing. | Involving: the CFO of Aurora Logistics"


@pytest.mark.parametrize("apostrophe", ["'", "\u2019"])
def test_possessive_of_a_supported_name_is_not_flagged(apostrophe):
    claim = f"Aurora Logistics requires a SOC 2 Type II report, per the CFO{apostrophe}s requirement."
    assert grounding_issues(claim, [M2_TEXT]) == []


@pytest.mark.parametrize("claim", [
    "Priya Raman's budget is capped at USD 42,000.",
    "Priya Raman\u2019s budget is capped at USD 42,000.",
    "The budget is Priya's decision.",
])
def test_possessive_person_names_match_evidence(claim):
    assert grounding_issues(claim, [N1_TEXT]) == []


@pytest.mark.parametrize("claim,fragment", [
    ("Northwind's offer undercuts ours.", "name 'northwind'"),
    ("Northwind\u2019s offer undercuts ours.", "name 'northwind'"),
    ("The CFO's cap is USD 50,000.", "number '50000'"),
    ("Contoso's legal team is reviewing the contract.", "name 'contoso'"),
])
def test_unsupported_names_and_numbers_in_possessive_form_are_still_flagged(claim, fragment):
    assert any(fragment in issue for issue in grounding_issues(claim, [N1_TEXT]))


def test_possessive_claim_keeps_its_displayed_text_and_citations():
    text = "Aurora Logistics requires a SOC 2 Type II report, per the CFO's requirement."
    result = enforce([Claim(text=text, kind="rep_note", citations=["M1", "N1"])])  # N1 names the CFO
    [claim] = result.claims
    assert claim.text == text and claim.kind == "rep_note" and claim.citations == ["M1", "N1"]
