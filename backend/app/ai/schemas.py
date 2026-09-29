"""Provider-neutral request/response contracts for AI operations.

Every generated item carries an explicit epistemic kind and cites evidence by the
``ref`` values supplied in the request. AIService (app/ai/service.py) enforces the
evidence rules after the provider answers, whichever provider it is.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.memory.types import MemoryRef

# recorded: exact SQLite fields; signal: deterministic M4 finding; rep_note: an interaction note written by
# the sales rep (never a verbatim customer statement); memory: a Hindsight fact linked to a current source
# record; statement: a verbatim customer quote (no source of these exists yet); outcome: a recorded outcome.
EvidenceKind = Literal["recorded", "signal", "rep_note", "statement", "memory", "outcome"]
# unsupported: the model asserted it but the cited evidence does not contain its numbers/names.
ClaimKind = Literal["recorded", "rep_note", "statement", "inference", "unsupported"]


class EvidenceItem(BaseModel):
    """One piece of evidence the model may cite. ``ref`` is local to the request (e.g. "R3")."""

    ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")
    kind: EvidenceKind
    text: str = Field(min_length=1, max_length=4000)
    source_type: Literal["deal", "interaction", "commitment", "stakeholder", "memory", "outcome", "signal"]
    source_id: str | None = None
    occurred_at: datetime | None = None
    memory_ref: MemoryRef | None = None


class DealContext(BaseModel):
    customer_id: str
    deal_id: str
    customer_name: str
    deal_title: str
    stage: str
    status: str
    value_minor: int | None = None
    currency: str | None = None
    expected_close_date: date | None = None


class GenerationInfo(BaseModel):
    provider: str
    model: str | None
    generated_at: datetime


class _EvidenceRequest(BaseModel):
    deal: DealContext
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=200)
    as_of: datetime


class Claim(BaseModel):
    text: str
    kind: ClaimKind
    citations: list[str] = []
    grounding_issues: list[str] = Field(default_factory=list,
                                        description="Why AIService downgraded this claim (empty if it passed)")


class CitedText(BaseModel):
    text: str
    citations: list[str] = []
    issues: list[str] = Field(default_factory=list,
                              description="Why AIService downgraded or removed this item (empty if it passed)")


# -- deal briefing -------------------------------------------------------------


class BriefingRequest(_EvidenceRequest):
    pass


class DealBriefing(BaseModel):
    claims: list[Claim]
    missing_evidence: list[str] = Field(default_factory=list, description="What we do not know that matters")
    rejected_citations: list[str] = []
    relabelled_claims: int = Field(0, description="Claims downgraded to 'inference' for lacking matching evidence, "
                                    "or conclusions split off a recorded/rep-note claim")
    unsupported_claims: int = Field(0, description="Claims whose numbers/names are absent from their evidence")
    generated: GenerationInfo


# -- objections ----------------------------------------------------------------


class ObjectionAnalysisRequest(_EvidenceRequest):
    pass


class ObjectionHypothesis(BaseModel):
    kind: Literal["inference"] = "inference"
    statement: str
    supporting: list[CitedText] = []
    contradicting: list[CitedText] = []
    what_would_confirm: str


class ObjectionAnalysis(BaseModel):
    hypotheses: list[ObjectionHypothesis]
    rejected_citations: list[str] = []
    generated: GenerationInfo


# -- follow-up draft -----------------------------------------------------------


class FollowUpRequest(_EvidenceRequest):
    purpose: str = Field(min_length=1, max_length=500)
    recipient_stakeholder_id: str | None = None
    tone: Literal["formal", "neutral", "warm"] = "neutral"


class FollowUpDraft(BaseModel):
    kind: Literal["hypothetical"] = "hypothetical"
    subject: str
    body: str
    citations: list[str] = []
    rejected_citations: list[str] = []
    generated: GenerationInfo


# -- strategy comparison (Deal Time Machine) -------------------------------------


class StrategyOption(BaseModel):
    # 32 (was 16): fits Deal Time Machine template ids such as "address_requirement_early". Widening only.
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    description: str = Field(min_length=1, max_length=1000)


class StrategyComparisonRequest(_EvidenceRequest):
    # min 1 (was 2): the Deal Time Machine assesses one strategy per call (plan §6). Widening only.
    strategies: list[StrategyOption] = Field(min_length=1, max_length=4)
    strategy_context: list[Annotated[str, Field(max_length=600)]] = Field(
        default_factory=list, max_length=12,
        description="Server-rendered, rule-derived context (data, not instructions); never client free text")
    rule_next_steps: list[Annotated[str, Field(max_length=600)]] = Field(
        default_factory=list, max_length=6,
        description="Server-derived hypothetical next steps (trusted rule text). Used only as extra grounding basis "
                    "for proposed next steps; never evidence, never recorded commitments")


class StrategyAssessment(BaseModel):
    strategy_id: str
    supporting: list[CitedText] = []
    contradicting: list[CitedText] = []
    evidence_gaps: list[str] = []
    commitments_created: list[str] = []
    verdict: Literal["supported", "mixed", "unsupported"]
    rejected_items: int = Field(0, ge=0, description="Cited items AIService removed (grounding/outcome checks)")
    check_notes: list[str] = Field(default_factory=list,
                                   description="What AIService removed or marked, and why (no removed text is kept)")


STRATEGY_DISCLAIMER = (
    "Exploratory comparison. Scenarios are not predictions; they show what the recorded "
    "evidence does and does not support."
)


class StrategyComparison(BaseModel):
    kind: Literal["hypothetical"] = "hypothetical"
    disclaimer: str = STRATEGY_DISCLAIMER
    assessments: list[StrategyAssessment]
    rejected_citations: list[str] = []
    adjusted_verdicts: int = 0
    generated: GenerationInfo


# -- status --------------------------------------------------------------------


class AIStatus(BaseModel):
    provider: str
    implemented: bool
    available: bool
    model: str | None = None
    reason: str | None = None
