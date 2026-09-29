"""Typed contracts for the Deal Time Machine (TM0; see docs/deal-time-machine-plan.md §5).

Separation is structural, not stylistic:

* **Actual history**: ``TimelineEvent``, ``DealTimeline`` and ``BranchView`` hold only what SQLite and the
  deterministic M4 rules record. Their evidence kinds are limited to recorded / rep_note / signal; they cannot
  carry an inference or a hypothetical item.
* **Rule-derived strategy support**: ``StrategyTemplate`` and ``StrategyEvidenceMap`` (``basis="rule_derived"``)
  come from a fixed catalogue and deterministic rules. Implied commitments are always ``hypothetical``.
* **Hypothetical AI content**: only inside ``TimeMachineAssessment.comparison``, which reuses the existing
  ``StrategyComparison`` contract (``kind="hypothetical"``, fixed disclaimer, verdicts only weakened).

Validators enforce these rules so a service bug cannot produce a mixed payload. Nothing here is persisted.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.ai.evidence import EvidenceSource, MemoryEvidenceStatus, SourceRecord
from app.ai.schemas import STRATEGY_DISCLAIMER, StrategyComparison, StrategyOption
from app.intelligence.schemas import Signal, SignalType

# Refs use the same pattern as app.ai.schemas.EvidenceItem.ref.
REF_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,15}$"

# Fixed limitations shown with every timeline (plan §1 and §7): the data model has no change history.
TIMELINE_LIMITATIONS: tuple[str, ...] = (
    "Deal fields are shown as currently stored; their change history is not recorded.",
    "'Entered in system' dates are when a record was created, not when the event happened.",
    "No comparable deals with recorded outcomes exist, so no outcome evidence is available.",
)

TIME_MACHINE_NOTE = (
    "Exploratory comparison. Strategies are hypothetical and are compared only against the evidence recorded up "
    "to the selected date. Nothing here predicts an outcome. AI statements are checked against their cited "
    "evidence (lexically, not as proof). Nothing is saved."
)

# -- actual recorded history ------------------------------------------------------------------------

TimelineEventKind = Literal["interaction", "commitment_created", "commitment_due", "commitment_completed",
                            "stakeholder_recorded", "deal_entered", "rule_checkpoint"]
TimelineEvidenceKind = Literal["recorded", "rep_note", "signal"]  # never inference / hypothetical
DateBasis = Literal["occurred", "due", "completed", "entered_in_system", "rule_computed"]

# The only valid (evidence kind, date basis, source type) for each event kind.
_EVENT_RULES: dict[str, tuple[str, str, str | None]] = {
    "interaction": ("rep_note", "occurred", "interaction"),
    "commitment_created": ("recorded", "entered_in_system", "commitment"),
    "commitment_due": ("recorded", "due", "commitment"),
    "commitment_completed": ("recorded", "completed", "commitment"),
    "stakeholder_recorded": ("recorded", "entered_in_system", "stakeholder"),
    "deal_entered": ("recorded", "entered_in_system", "deal"),
    "rule_checkpoint": ("signal", "rule_computed", None),
}


class TimelineEvent(BaseModel):
    """One dated entry of the recorded history. Built deterministically; never written by a model."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200,
                    description="Stable: '<kind>:<source id>' or 'rule:<rule id>:<deal id>'")
    kind: TimelineEventKind
    evidence_kind: TimelineEvidenceKind
    at: AwareDatetime
    date_basis: DateBasis
    title: str = Field(min_length=1, max_length=300)
    source: SourceRecord | None = Field(None, description="The record behind the event; None for rule checkpoints")
    rule_id: SignalType | None = None

    @model_validator(mode="after")
    def _consistent(self) -> TimelineEvent:
        evidence_kind, basis, source_type = _EVENT_RULES[self.kind]
        if self.evidence_kind != evidence_kind or self.date_basis != basis:
            raise ValueError(f"{self.kind} events must have evidence_kind={evidence_kind} and date_basis={basis}")
        if source_type is None:
            if self.rule_id is None or self.source is not None:
                raise ValueError("rule_checkpoint events need rule_id and no source record")
        elif self.source is None or self.source.type != source_type or self.rule_id is not None:
            raise ValueError(f"{self.kind} events need a '{source_type}' source record and no rule_id")
        return self


def _sorted_unique(events: list[TimelineEvent]) -> None:
    ids = [e.id for e in events]
    if len(set(ids)) != len(ids):
        raise ValueError("timeline event ids must be unique")
    if [(e.at, e.id) for e in events] != sorted((e.at, e.id) for e in events):
        raise ValueError("timeline events must be sorted by (at, id)")


class DealTimeline(BaseModel):
    """The deal's recorded history up to (and scheduled items after) ``as_of`` = NOW."""

    customer_id: str
    deal_id: str
    as_of: AwareDatetime
    events: list[TimelineEvent] = Field(default_factory=list)
    data_notes: list[str] = Field(default_factory=list,
                                  description="Deterministic data-quality notes, e.g. a record entered late")
    limitations: list[str] = Field(default_factory=lambda: list(TIMELINE_LIMITATIONS))

    @model_validator(mode="after")
    def _ordered(self) -> DealTimeline:
        _sorted_unique(self.events)
        return self


class BranchView(BaseModel):
    """What was recorded at ``branch_at`` and what the records show afterwards. No model involved."""

    customer_id: str
    deal_id: str
    branch_at: AwareDatetime
    as_of: AwareDatetime
    known_then: list[EvidenceSource] = Field(description="Exactly the evidence an assessment at branch_at may cite")
    followed: list[TimelineEvent] = Field(description="Recorded events with branch_at < at <= as_of")
    signals_then: list[Signal] = Field(default_factory=list, description="M4 signals evaluated at branch_at")
    memory: MemoryEvidenceStatus

    @model_validator(mode="after")
    def _separated(self) -> BranchView:
        if self.branch_at > self.as_of:
            raise ValueError("branch_at must not be after as_of")
        refs = [s.ref for s in self.known_then]
        if len(set(refs)) != len(refs):
            raise ValueError("known_then refs must be unique")
        for source in self.known_then:
            if source.occurred_at is not None and source.occurred_at > self.branch_at:
                raise ValueError(f"{source.ref} occurred after branch_at and cannot be known then")
        _sorted_unique(self.followed)
        for event in self.followed:
            if not self.branch_at < event.at <= self.as_of:
                raise ValueError(f"followed event {event.id} is outside (branch_at, as_of]")
        return self


# -- predefined strategies and rule-derived evidence maps -------------------------------------------

StrategyTemplateId = Literal["earlier_follow_up", "address_requirement_early", "identify_decision_maker"]


class StrategyTemplate(BaseModel):
    """A catalogue entry. Whether it is offered is decided by deterministic triggers, with the reasons."""

    id: StrategyTemplateId
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    offered: bool
    offered_because: list[str] = Field(default_factory=list,
                                       description="Evidence refs or rule ids that triggered the offer")
    not_offered_reason: str | None = None
    needs_anchor: bool = False

    @model_validator(mode="after")
    def _explained(self) -> StrategyTemplate:
        if self.offered and (not self.offered_because or self.not_offered_reason):
            raise ValueError("an offered strategy needs offered_because and no not_offered_reason")
        if not self.offered and (self.offered_because or not self.not_offered_reason):
            raise ValueError("a strategy that is not offered needs not_offered_reason and no offered_because")
        return self

    def as_option(self) -> StrategyOption:
        """The existing AI contract for this template (description rendered server-side, never free text)."""
        return StrategyOption(id=self.id, description=self.description)


class HypotheticalCommitment(BaseModel):
    """A commitment the strategy would create. Never a recorded commitment."""

    kind: Literal["hypothetical"] = "hypothetical"
    owner_party: Literal["us", "customer"]
    description: str = Field(min_length=1, max_length=500)


class StrategyEvidenceMap(BaseModel):
    """Deterministic, model-free support for one strategy at one branch point."""

    basis: Literal["rule_derived"] = "rule_derived"
    strategy_id: StrategyTemplateId
    anchor_ref: str | None = Field(None, pattern=REF_PATTERN)
    related: list[str] = Field(default_factory=list, description="Evidence refs from BranchView.known_then")
    gaps: list[str] = Field(default_factory=list, description="From missing_information signals; not guesses")
    implied_commitments: list[HypotheticalCommitment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs(self) -> StrategyEvidenceMap:
        bad = [r for r in self.related if not re.fullmatch(REF_PATTERN, r)]
        if bad:
            raise ValueError(f"invalid evidence refs: {bad}")
        if self.anchor_ref is not None and self.anchor_ref not in self.related:
            raise ValueError("anchor_ref must be one of the related refs")
        return self


class StrategyChoice(BaseModel):
    """One catalogue entry at a branch point (TM2, additive): the template, why it is or is not offered, and its
    rule-derived evidence map. The map is None when the template is not offered, or when it needs an anchor the
    user has not chosen yet (``anchor_candidates`` lists the refs that may be chosen)."""

    template: StrategyTemplate
    reasons: list[str] = Field(default_factory=list, description="Plain-language, rule-derived explanation")
    anchor_candidates: list[str] = Field(default_factory=list, description="Refs selectable as the anchor")
    evidence_map: StrategyEvidenceMap | None = None

    @model_validator(mode="after")
    def _consistent(self) -> StrategyChoice:
        if self.evidence_map is not None:
            if not self.template.offered:
                raise ValueError("a strategy that is not offered has no evidence map")
            if self.evidence_map.strategy_id != self.template.id:
                raise ValueError("evidence_map is for a different strategy")
            if self.template.needs_anchor and self.evidence_map.anchor_ref is None:
                raise ValueError("this strategy's evidence map needs an anchor_ref")
        if self.evidence_map is not None and self.evidence_map.anchor_ref is not None                 and self.evidence_map.anchor_ref not in self.anchor_candidates:
            raise ValueError("anchor_ref must be one of anchor_candidates")
        if not self.reasons:
            raise ValueError("every strategy needs a reason")
        return self


class StrategyCatalogue(BaseModel):
    """The fixed catalogue evaluated at ``branch_at`` (TM2, additive). Strategies are hypothetical alternatives,
    never predictions; evidence maps cite only ``sources`` (the evidence known at ``branch_at``)."""

    kind: Literal["hypothetical"] = "hypothetical"
    disclaimer: str = STRATEGY_DISCLAIMER
    customer_id: str
    deal_id: str
    branch_at: AwareDatetime
    as_of: AwareDatetime
    strategies: list[StrategyChoice]
    sources: list[EvidenceSource] = Field(description="The point-in-time evidence the maps cite")
    memory: MemoryEvidenceStatus

    @model_validator(mode="after")
    def _cited(self) -> StrategyCatalogue:
        if self.branch_at > self.as_of:
            raise ValueError("branch_at must not be after as_of")
        ids = [c.template.id for c in self.strategies]
        if len(set(ids)) != len(ids):
            raise ValueError("strategy ids must be unique")
        known = {src.ref for src in self.sources}
        for src in self.sources:
            if src.occurred_at is not None and src.occurred_at > self.branch_at:
                raise ValueError(f"{src.ref} occurred after branch_at and cannot be cited")
        for c in self.strategies:
            refs = list(c.anchor_candidates) + (c.evidence_map.related if c.evidence_map else [])
            unknown = [r for r in refs if r not in known]
            if unknown:
                raise ValueError(f"refs not in sources: {sorted(set(unknown))}")
        return self


# -- request and optional AI assessment -------------------------------------------------------------


class TimeMachineAssessRequest(BaseModel):
    """Body of the TM4 assess route. No free text: the strategy comes from the catalogue, the evidence from the
    server. ``as_of`` defaults to now; ``include_memory`` is the same explicit opt-in as the TM1/TM2 routes."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: StrategyTemplateId
    branch_at: AwareDatetime
    anchor_ref: str | None = Field(None, pattern=REF_PATTERN)
    as_of: AwareDatetime | None = None
    include_memory: bool = False


class TimeMachineAssessment(BaseModel):
    """One strategy assessed at one branch point. AI content lives only in ``comparison`` (hypothetical)."""

    status: Literal["generated", "insufficient_evidence"]
    customer_id: str
    deal_id: str
    branch_at: AwareDatetime
    as_of: AwareDatetime
    strategy: StrategyOption
    evidence_map: StrategyEvidenceMap
    comparison: StrategyComparison | None = Field(None, description="None unless status is generated")
    sources: list[EvidenceSource] = Field(description="The point-in-time evidence the model could cite")
    memory: MemoryEvidenceStatus
    insufficient_reason: str | None = None
    note: str = TIME_MACHINE_NOTE

    @model_validator(mode="after")
    def _consistent(self) -> TimeMachineAssessment:
        if self.branch_at > self.as_of:
            raise ValueError("branch_at must not be after as_of")
        if self.evidence_map.strategy_id != self.strategy.id:
            raise ValueError("evidence_map is for a different strategy")
        if self.status == "generated":
            if self.comparison is None or self.insufficient_reason is not None:
                raise ValueError("a generated assessment needs a comparison and no insufficient_reason")
        elif self.comparison is not None or not self.insufficient_reason:
            raise ValueError("an insufficient_evidence assessment needs insufficient_reason and no comparison")

        known = {s.ref for s in self.sources}
        for source in self.sources:
            if source.occurred_at is not None and source.occurred_at > self.branch_at:
                raise ValueError(f"{source.ref} occurred after branch_at and cannot be cited")
        unknown = [r for r in self.evidence_map.related if r not in known]
        if self.comparison is not None:
            if [a.strategy_id for a in self.comparison.assessments] != [self.strategy.id]:
                raise ValueError("comparison must contain exactly one assessment, for this strategy")
            for item in (*self.comparison.assessments[0].supporting, *self.comparison.assessments[0].contradicting):
                unknown += [c for c in item.citations if c not in known]
        if unknown:
            raise ValueError(f"citations/refs not in sources: {sorted(set(unknown))}")
        return self

