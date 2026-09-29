"""Fixed Deal Time Machine strategy catalogue and rule-derived evidence maps (TM2; plan §2 and §7).

Deterministic: SQLite records, M4 rules and the point-in-time evidence pack only. No model, no writes. Strategies are
hypothetical alternatives, never predictions: offering one means its trigger applies to the recorded evidence, not
that it would have worked. Evidence maps cite only refs known at ``branch_at``; the "as of now" part of a trigger
(``earlier_follow_up``) explains why the scenario is worth exploring and is never cited as known-then evidence.

Adding a template = adding a ``_Spec`` to ``CATALOGUE`` (and its id to ``StrategyTemplateId``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.ai.evidence import EvidenceSource
from app.domain.models import Commitment, Deal, Interaction, Stakeholder
from app.intelligence.rules import IntelConfig
from app.intelligence.schemas import Signal
from app.intelligence.service import IntelligenceService
from app.memory.service import MemoryService
from app.timemachine.schemas import (
    BranchView,
    HypotheticalCommitment,
    StrategyCatalogue,
    StrategyChoice,
    StrategyEvidenceMap,
    StrategyTemplate,
    StrategyTemplateId,
)
from app.timemachine.timeline import build_branch_view


class InvalidAnchorError(ValueError):
    """``anchor_ref`` is not a selectable anchor at this branch point."""


@dataclass
class _Context:
    """Everything a template may look at, all restricted to what was recorded by ``branch_at`` except ``signals_now``."""

    branch: BranchView
    config: IntelConfig
    deal: Deal
    interactions_then: list[Interaction]      # this deal, occurred_at <= branch_at
    stakeholders_then: list[Stakeholder]      # this customer, created_at <= branch_at
    commitments_then: dict[str, Commitment]   # this deal, created_at <= branch_at
    signals_now: list[Signal]                 # M4 at as_of (point in time): used only to explain an offer
    meaningful_ids: set[str]                  # this deal's interactions on meaningful channels (all dates)

    def day(self, value: datetime) -> str:
        return value.date().isoformat()

    def refs(self, predicate: Callable[[EvidenceSource], bool]) -> list[str]:
        return [s.ref for s in self.branch.known_then if predicate(s)]

    def ref_of(self, source_type: str, source_id: str) -> str | None:
        return next((s.ref for s in self.branch.known_then
                     if s.source_type == source_type and s.source_id == source_id), None)

    def signal_refs(self, types: set[str]) -> list[str]:
        return self.refs(lambda s: s.source_type == "signal" and (s.source_id or "").split(":")[0] in types)

    def memory_refs_for(self, interaction_id: str) -> list[str]:
        return self.refs(lambda s: s.kind == "memory" and s.source_id == interaction_id)

    def gaps(self, types: set[str]) -> list[str]:
        """Missing-information signals known then, as '<title> (<ref or rule id>)'; never guesses."""
        out = []
        for sig in self.branch.signals_then:
            if sig.nature == "missing_information" and sig.type in types:
                out.append(f"{sig.title} ({self.ref_of('signal', sig.id) or sig.type})")
        return out

    def ordered(self, refs: list[str]) -> list[str]:
        """Unique refs in evidence order (deterministic)."""
        position = {s.ref: i for i, s in enumerate(self.branch.known_then)}
        return sorted(dict.fromkeys(r for r in refs if r in position), key=position.__getitem__)


@dataclass(frozen=True)
class _Outcome:
    offered: bool
    reasons: list[str]
    offered_because: list[str]
    evidence_map: StrategyEvidenceMap | None = None
    anchor_candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Spec:
    id: StrategyTemplateId
    label: str
    description: str
    needs_anchor: bool
    evaluate: Callable[[_Context, str | None], _Outcome]


# -- earlier_follow_up ---------------------------------------------------------------------------------


def _earlier_follow_up(ctx: _Context, _anchor: str | None) -> _Outcome:
    cfg, branch = ctx.config, ctx.branch
    meaningful = [i for i in ctx.interactions_then if i.channel in cfg.meaningful_channels]
    if not meaningful:
        return _Outcome(False, [f"No meaningful interaction ({', '.join(sorted(cfg.meaningful_channels))}) was "
                                f"recorded by {ctx.day(branch.branch_at)}, so there is nothing to follow up on."], [])
    last = max(meaningful, key=lambda i: (i.occurred_at, i.id))
    later_meaningful = [e for e in branch.followed if e.kind == "interaction" and e.source is not None
                        and e.source.id in ctx.meaningful_ids]
    stalled_now = any(s.type == "stalled_deal" for s in ctx.signals_now)
    if later_meaningful and not stalled_now:
        return _Outcome(False, [f"A meaningful interaction was recorded after {ctx.day(branch.branch_at)} and the "
                                f"deal is not stalled as of {ctx.day(branch.as_of)}."], [])

    last_ref = ctx.ref_of("interaction", last.id)
    because = [r for r in [last_ref] if r] or [f"interaction:{last.id}"]
    reasons = [f"The last meaningful interaction by {ctx.day(branch.branch_at)} was a {last.channel} on "
               f"{ctx.day(last.occurred_at)}" + (f" ({last_ref})." if last_ref else ".")]
    if stalled_now:
        because.append("stalled_deal")
        reasons.append(f"As of {ctx.day(branch.as_of)} the stall rule (stalled_deal) applies to this deal.")
    if not later_meaningful:
        reasons.append(f"No meaningful interaction is recorded between {ctx.day(branch.branch_at)} and "
                       f"{ctx.day(branch.as_of)}.")

    days = max(1, cfg.stall_days(ctx.deal.stage) // 2)
    related = ([last_ref] if last_ref else []) + ctx.memory_refs_for(last.id) + \
        ctx.signal_refs({"stalled_deal", "no_recorded_activity"})
    return _Outcome(True, reasons, because, StrategyEvidenceMap(
        strategy_id="earlier_follow_up", related=ctx.ordered(related),
        gaps=ctx.gaps({"missing_expected_close_date", "missing_deal_owner"}),
        implied_commitments=[HypotheticalCommitment(
            owner_party="us",
            description=f"Schedule the next customer contact within {days} day(s) of {ctx.day(last.occurred_at)} "
                        f"(half the {cfg.stall_days(ctx.deal.stage)}-day stall threshold for stage "
                        f"'{ctx.deal.stage}'; a rule parameter, not advice).")]))


# -- address_requirement_early ---------------------------------------------------------------------------


def _anchor_candidates(ctx: _Context) -> list[str]:
    """Rep notes, linked memories and commitments known then. The template never detects requirements itself."""
    return ctx.refs(lambda s: s.kind in ("rep_note", "memory")
                    or (s.source_type == "commitment" and s.kind == "recorded"))


def _address_requirement_early(ctx: _Context, anchor: str | None) -> _Outcome:
    candidates = _anchor_candidates(ctx)
    if not candidates:
        return _Outcome(False, [f"No rep note, linked memory or commitment was recorded by "
                                f"{ctx.day(ctx.branch.branch_at)} to anchor a requirement."], [])
    reasons = [f"{len(candidates)} recorded item(s) known by {ctx.day(ctx.branch.branch_at)} can anchor a "
               "requirement. You choose which one; the requirement is never detected automatically."]
    if anchor is None:
        return _Outcome(True, reasons + ["Choose an anchor to see the evidence map."], candidates,
                        anchor_candidates=tuple(candidates))
    if anchor not in candidates:
        raise InvalidAnchorError(f"{anchor} is not a rep note, linked memory or commitment known at this date")

    src = next(s for s in ctx.branch.known_then if s.ref == anchor)
    related = [anchor]
    commitment_ids: list[str] = []
    if src.source_type == "commitment" and src.source_id:
        commitment_ids.append(src.source_id)
        c = ctx.commitments_then.get(src.source_id)
        if c is not None and c.source_interaction_id:
            related += [r for r in [ctx.ref_of("interaction", c.source_interaction_id)] if r]
            related += ctx.memory_refs_for(c.source_interaction_id)
    elif src.source_id:  # a rep note or a memory: both point at the interaction
        related += [r for r in [ctx.ref_of("interaction", src.source_id)] if r] + ctx.memory_refs_for(src.source_id)
        commitment_ids += [c.id for c in ctx.commitments_then.values() if c.source_interaction_id == src.source_id]
    related += [ctx.ref_of("commitment", cid) for cid in commitment_ids if ctx.ref_of("commitment", cid)]
    related += [s.ref for s in ctx.branch.known_then if s.source_type == "signal"
                and any(r.type == "commitment" and r.id in commitment_ids for r in s.related_records)]

    close = ctx.deal.expected_close_date
    milestone = (f"before the expected close date ({close.isoformat()}; current value, no change history)"
                 if close else "before the next milestone (no milestone date is recorded)")
    return _Outcome(True, reasons + [f"Anchor: {anchor} ({src.kind.replace('_', ' ')})."], candidates,
                    StrategyEvidenceMap(
                        strategy_id="address_requirement_early", anchor_ref=anchor, related=ctx.ordered(related),
                        gaps=ctx.gaps({"commitment_undated", "missing_expected_close_date"}),
                        implied_commitments=[HypotheticalCommitment(
                            owner_party="us", description=f"Provide what the requirement in {anchor} asks for "
                                                          f"{milestone}.")]),
                    anchor_candidates=tuple(candidates))


# -- identify_decision_maker -----------------------------------------------------------------------------


def _identify_decision_maker(ctx: _Context, _anchor: str | None) -> _Outcome:
    day = ctx.day(ctx.branch.branch_at)
    no_stakeholders = next((s for s in ctx.branch.signals_then if s.type == "no_stakeholders_recorded"), None)
    high = [s for s in ctx.stakeholders_then if s.influence == "high"]
    stakeholder_refs = [r for r in (ctx.ref_of("stakeholder", s.id) for s in ctx.stakeholders_then) if r]
    if high:
        names = ", ".join(sorted(ctx.ref_of("stakeholder", s.id) or s.id for s in high))
        return _Outcome(False, [f"A stakeholder with influence 'high' was recorded by {day} ({names})."], [])
    if no_stakeholders is not None:
        ref = ctx.ref_of("signal", no_stakeholders.id)
        because = [ref or no_stakeholders.type]
        reasons = [f"No stakeholders were recorded for the customer by {day} (rule no_stakeholders_recorded)."]
    elif not ctx.stakeholders_then:  # e.g. a closed deal: the M4 rule only fires for open deals
        because = [f"customer:{ctx.deal.customer_id}"]
        reasons = [f"No stakeholders were recorded for the customer by {day}."]
    else:
        # Refs when the (capped) evidence pack holds them; otherwise the record ids themselves.
        because = stakeholder_refs or [f"stakeholder:{s.id}" for s in ctx.stakeholders_then]
        reasons = [f"{len(ctx.stakeholders_then)} stakeholder(s) were recorded by {day}, none with influence 'high'."]
    related = stakeholder_refs + ctx.signal_refs({"no_stakeholders_recorded"})
    return _Outcome(True, reasons, because, StrategyEvidenceMap(
        strategy_id="identify_decision_maker", related=ctx.ordered(related),
        gaps=ctx.gaps({"no_stakeholders_recorded"}),
        implied_commitments=[HypotheticalCommitment(
            owner_party="us", description="Identify the customer's decision-maker and record them with their "
                                          "priorities and influence.")]))


CATALOGUE: tuple[_Spec, ...] = (
    _Spec("earlier_follow_up", "Follow up sooner after the last interaction",
          "Scenario: after the last meaningful interaction recorded by the branch date, contact the customer again "
          "sooner than the records show.", False, _earlier_follow_up),
    _Spec("address_requirement_early", "Address a recorded requirement sooner",
          "Scenario: act earlier on a requirement recorded in a rep note, linked memory or commitment that you choose "
          "from the evidence known at the branch date.", True, _address_requirement_early),
    _Spec("identify_decision_maker", "Identify the decision-maker earlier",
          "Scenario: identify and record the customer's decision-maker and their priorities earlier.", False,
          _identify_decision_maker),
)


def _template(spec: _Spec, outcome: _Outcome) -> StrategyTemplate:
    return StrategyTemplate(
        id=spec.id, label=spec.label, description=spec.description, offered=outcome.offered,
        offered_because=outcome.offered_because if outcome.offered else [],
        not_offered_reason=None if outcome.offered else " ".join(outcome.reasons), needs_anchor=spec.needs_anchor)


async def build_strategy_catalogue(session: Session, customer_id: str, deal_id: str, branch_at: datetime,
                                   as_of: datetime, *, intelligence: IntelligenceService,
                                   memory: MemoryService | None = None,
                                   anchor_ref: str | None = None) -> StrategyCatalogue:
    """Evaluate every template at ``branch_at``. Raises BranchAfterAsOfError / InvalidAnchorError / ValueError."""
    branch = await build_branch_view(session, customer_id, deal_id, branch_at, as_of, intelligence=intelligence,
                                     memory=memory)
    branch_at, as_of = branch.branch_at, branch.as_of
    deal = session.get(Deal, deal_id)
    interactions = session.exec(select(Interaction).where(
        Interaction.customer_id == customer_id, Interaction.deal_id == deal_id)).all()
    ctx = _Context(
        branch=branch, config=intelligence.config, deal=deal,
        interactions_then=[i for i in interactions if i.occurred_at <= branch_at],
        stakeholders_then=[s for s in session.exec(select(Stakeholder).where(
            Stakeholder.customer_id == customer_id)).all() if s.created_at <= branch_at],
        commitments_then={c.id: c for c in session.exec(select(Commitment).where(
            Commitment.customer_id == customer_id, Commitment.deal_id == deal_id)).all() if c.created_at <= branch_at},
        signals_now=intelligence.evaluate_customer(session, customer_id, as_of, deal_id=deal_id,
                                                   point_in_time=True).all_signals(),
        meaningful_ids={i.id for i in interactions if i.channel in intelligence.config.meaningful_channels})

    if anchor_ref is not None and anchor_ref not in _anchor_candidates(ctx):
        raise InvalidAnchorError(f"{anchor_ref} is not a rep note, linked memory or commitment known at this date")
    choices = []
    for spec in CATALOGUE:
        outcome = spec.evaluate(ctx, anchor_ref if spec.needs_anchor else None)
        choices.append(StrategyChoice(template=_template(spec, outcome), reasons=outcome.reasons,
                                      anchor_candidates=list(outcome.anchor_candidates),
                                      evidence_map=outcome.evidence_map))
    return StrategyCatalogue(customer_id=customer_id, deal_id=deal_id, branch_at=branch_at, as_of=as_of,
                             strategies=choices, sources=branch.known_then, memory=branch.memory)
