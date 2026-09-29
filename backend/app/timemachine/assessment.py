"""Optional AI assessment of one fixed strategy at a branch point (TM4; docs/deal-time-machine-plan.md §6–§7).

Order of operations (the route has already checked customer/deal ownership and the request schema):
  1. branch_at <= as_of;
  2. the deterministic catalogue WITHOUT memory (SQLite only, cheap): the strategy must be offered and a required
     anchor must be one of its candidates. Exception: an anchor on a memory (M*) can only exist in a memory pack,
     so with include_memory its check is deferred to step 4 (other strategies' triggers do not depend on memory);
  3. provider availability (no evidence work, and no memory recall, if the AI is down);
  4. the evidence pack: the step-2 catalogue, or, with include_memory, the catalogue rebuilt once WITH memory (the
     single recall of this request; TM1 provenance and cutoff rules apply). The offer and anchor are re-checked;
  5. insufficient evidence (no rep note, linked memory or commitment known then): return, no model call;
  6. one model call through AIService.compare_strategies (citations, grounding, outcome-language and verdict checks);
  7. the result is validated again by TimeMachineAssessment (every citation in the pack, nothing after branch_at).

The model sees only the point-in-time evidence and the rule-derived evidence map. It never sees the catalogue's
"as of now" reasons (e.g. that the deal is stalled today), because those describe what happened after branch_at.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

from app.ai.evidence import EvidenceSource
from app.ai.provider import AIOperationError, AIUnavailableError
from app.ai.schemas import DealContext, EvidenceItem, StrategyComparisonRequest, StrategyOption
from app.ai.service import AIService
from app.domain.models import Customer, Deal
from app.intelligence.service import IntelligenceService
from app.memory.service import MemoryService
from app.timemachine.schemas import (
    StrategyCatalogue,
    StrategyChoice,
    StrategyEvidenceMap,
    TimeMachineAssessment,
    TimeMachineAssessRequest,
)
from app.timemachine.strategies import InvalidAnchorError, build_strategy_catalogue
from app.timemachine.timeline import BranchAfterAsOfError

INSUFFICIENT_REASON = "No rep note, linked memory or commitment was recorded by the branch date."


class StrategyNotApplicableError(ValueError):
    """The requested strategy is not offered at this branch point."""


class AnchorRequiredError(ValueError):
    """The strategy needs an anchor and none was given."""


def _choice(catalogue: StrategyCatalogue, strategy_id: str) -> StrategyChoice:
    return next(c for c in catalogue.strategies if c.template.id == strategy_id)


def _require_offered(choice: StrategyChoice, anchor_ref: str | None) -> StrategyEvidenceMap:
    if not choice.template.offered:
        raise StrategyNotApplicableError(choice.template.id)
    if choice.template.needs_anchor and anchor_ref is None:
        raise AnchorRequiredError(choice.template.id)
    if choice.evidence_map is None:  # defensive: an offered strategy with its anchor always has a map
        raise StrategyNotApplicableError(choice.template.id)
    return choice.evidence_map


def _option(choice: StrategyChoice, anchor_ref: str | None) -> StrategyOption:
    """The existing AI contract, rendered server-side from the catalogue (never client text)."""
    description = choice.template.description + (f" Requirement anchor chosen by the user: {anchor_ref}." if anchor_ref else "")
    return StrategyOption(id=choice.template.id, description=description)


def _context(evidence_map: StrategyEvidenceMap) -> list[str]:
    """Rule-derived, point-in-time context for the prompt. Never the 'as of now' offer reasons."""
    lines = [f"Rule-derived related evidence: {', '.join(evidence_map.related) or 'none'}."]
    if evidence_map.anchor_ref:
        lines.append(f"The requirement to address is stated in {evidence_map.anchor_ref}; use only that item for it.")
    lines += [f"Known gap (missing information): {g}" for g in evidence_map.gaps]
    lines += [f"Rule-derived next step (hypothetical, owner {c.owner_party}): {c.description}"
              for c in evidence_map.implied_commitments]
    return lines


def _evidence(sources: list[EvidenceSource]) -> list[EvidenceItem]:
    """The model's evidence is exactly the pack's sources (excerpt = the text; nothing is re-read or added)."""
    return [EvidenceItem(ref=s.ref, kind=s.kind, text=s.excerpt, source_type=s.source_type, source_id=s.source_id,
                         occurred_at=s.occurred_at, memory_ref=s.memory_ref) for s in sources]


def is_sufficient(sources: list[EvidenceSource]) -> bool:
    return any(s.kind in ("rep_note", "memory") or s.source_type == "commitment" for s in sources)


async def assess_strategy(session: Session, customer_id: str, deal_id: str, request: TimeMachineAssessRequest,
                          as_of: datetime, *, intelligence: IntelligenceService, ai: AIService,
                          memory: MemoryService | None) -> TimeMachineAssessment:
    """Raises BranchAfterAsOfError, StrategyNotApplicableError, AnchorRequiredError, InvalidAnchorError,
    AIUnavailableError or AIOperationError. ``memory`` is None unless the request opted in."""
    branch_at = request.branch_at
    if branch_at > as_of:
        raise BranchAfterAsOfError("branch_at must not be after as_of")
    anchor = request.anchor_ref
    use_memory = request.include_memory and memory is not None
    deferred = use_memory and anchor is not None and anchor.startswith("M")

    # 2. deterministic eligibility on SQLite only
    base = await build_strategy_catalogue(session, customer_id, deal_id, branch_at, as_of, intelligence=intelligence,
                                          memory=None, anchor_ref=None if deferred else anchor)
    if not deferred:
        _require_offered(_choice(base, request.strategy_id), anchor)

    # 3. provider availability before any memory recall or model call
    status = await ai.status()
    if not status.available:
        raise AIUnavailableError(status.reason or "AI provider unavailable")

    # 4. the evidence pack (at most one recall)
    catalogue = base
    if use_memory:
        catalogue = await build_strategy_catalogue(session, customer_id, deal_id, branch_at, as_of,
                                                   intelligence=intelligence, memory=memory, anchor_ref=anchor)
    choice = _choice(catalogue, request.strategy_id)
    evidence_map = _require_offered(choice, anchor)
    option = _option(choice, anchor)

    common = dict(customer_id=customer_id, deal_id=deal_id, branch_at=catalogue.branch_at, as_of=catalogue.as_of,
                  strategy=option, evidence_map=evidence_map, sources=catalogue.sources, memory=catalogue.memory)
    # 5. insufficient evidence: no model call
    if not is_sufficient(catalogue.sources):
        return TimeMachineAssessment(status="insufficient_evidence", insufficient_reason=INSUFFICIENT_REASON, **common)

    # 6. one model call
    customer = session.get(Customer, customer_id)
    deal = session.get(Deal, deal_id)
    comparison = await ai.compare_strategies(StrategyComparisonRequest(
        deal=DealContext(customer_id=customer_id, deal_id=deal_id, customer_name=customer.name,
                         deal_title=deal.title, stage=deal.stage, status=deal.status),
        evidence=_evidence(catalogue.sources), as_of=catalogue.branch_at, strategies=[option],
        strategy_context=_context(evidence_map),
        rule_next_steps=[c.description for c in evidence_map.implied_commitments]))
    if [a.strategy_id for a in comparison.assessments] != [option.id]:
        raise AIOperationError("The model did not return an assessment for this strategy")

    # 7. contract validation (citations resolve to the pack; nothing after branch_at)
    try:
        return TimeMachineAssessment(status="generated", comparison=comparison, **common)
    except ValueError as exc:  # pydantic ValidationError is a ValueError
        raise AIOperationError(f"The assessment failed validation: {exc.__class__.__name__}") from None


__all__ = ["AnchorRequiredError", "BranchAfterAsOfError", "InvalidAnchorError", "StrategyNotApplicableError",
           "assess_strategy", "is_sufficient"]
