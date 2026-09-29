"""Deterministic Deal Time Machine timeline and branch view (TM1; docs/deal-time-machine-plan.md §5–§7).

Built only from SQLite records and the M4 rules. No model, no memory writes, and (unless a memory service is
passed explicitly) no memory recall. Independent of FastAPI so it can be unit-tested directly.

Dates and what they mean (``TimelineEvent.date_basis``):
* ``occurred``: an interaction's ``occurred_at``;
* ``entered_in_system``: a record's ``created_at``, i.e. when it was entered, not when the thing happened;
* ``due``: a commitment's ``due_date`` at 00:00 in the business timezone (``INTEL_TIMEZONE``);
* ``completed``: a done commitment's recorded ``completed_at``;
* ``rule_computed``: the date a deterministic rule threshold is reached (stall threshold only).

Nothing is invented: a record without a reliable date produces no event, and the omission is stated in
``data_notes``. Deal fields have no change history, so no past stage/value/owner is ever shown.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.ai.evidence import SourceRecord, assemble_briefing_evidence
from app.domain.models import Commitment, Customer, Deal, Interaction, Stakeholder
from app.intelligence.rules import TERMINAL_STATUSES, IntelConfig
from app.intelligence.service import IntelligenceService
from app.memory.service import MemoryService
from app.timemachine.schemas import BranchView, DealTimeline, TimelineEvent

_TITLE_MAX = 300


class BranchAfterAsOfError(ValueError):
    """``branch_at`` is later than the evaluation time ``as_of``."""


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _title(text: str) -> str:
    return text[:_TITLE_MAX]


def _day_start(day: date, timezone: str) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(timezone))


def _owner(c: Commitment) -> str:
    return ("us" if c.owner_party == "us" else "customer") + (f", {c.owner_name}" if c.owner_name else "")


def _load(session: Session, customer_id: str, deal_id: str) -> Deal:
    customer = session.get(Customer, customer_id)
    deal = session.get(Deal, deal_id)
    if customer is None or deal is None or deal.customer_id != customer_id:
        raise ValueError("deal does not belong to this customer")  # routes validate first; defence in depth
    return deal


def _interaction_events(interactions: Iterable[Interaction]) -> list[TimelineEvent]:
    return [
        TimelineEvent(
            id=f"interaction:{i.id}", kind="interaction", evidence_kind="rep_note", at=i.occurred_at,
            date_basis="occurred",
            title=_title(f"{i.channel.capitalize()} (rep note)" + (f": {_short(i.title)}" if i.title else "")),
            source=SourceRecord(type="interaction", id=i.id))
        for i in interactions
    ]


def _commitment_events(commitments: Iterable[Commitment], as_of: datetime, timezone: str,
                       notes: list[str]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    undated = done_without_date = cancelled = 0
    for c in commitments:
        source = SourceRecord(type="commitment", id=c.id)
        label = f"({_owner(c)}): {_short(c.description)}"
        events.append(TimelineEvent(
            id=f"commitment_created:{c.id}", kind="commitment_created", evidence_kind="recorded", at=c.created_at,
            date_basis="entered_in_system", title=_title(f"Commitment entered {label}"), source=source))
        if c.due_date is not None:
            # A scheduled due date may lie after as_of; it is a recorded date, not a prediction.
            events.append(TimelineEvent(
                id=f"commitment_due:{c.id}", kind="commitment_due", evidence_kind="recorded",
                at=_day_start(c.due_date, timezone), date_basis="due", title=_title(f"Commitment due {label}"),
                source=source))
        else:
            undated += 1
        if c.status == "done":
            if c.completed_at is None:
                done_without_date += 1
            elif c.completed_at <= as_of:
                events.append(TimelineEvent(
                    id=f"commitment_completed:{c.id}", kind="commitment_completed", evidence_kind="recorded",
                    at=c.completed_at, date_basis="completed", title=_title(f"Commitment marked done {label}"),
                    source=source))
        elif c.status == "cancelled":
            cancelled += 1
    if undated:
        notes.append(f"{undated} commitment(s) have no due date, so no due event is shown for them.")
    if done_without_date:
        notes.append(f"{done_without_date} commitment(s) are marked done without a recorded completion date; "
                     "no completion event is shown for them.")
    if cancelled:
        notes.append(f"{cancelled} commitment(s) are cancelled; the cancellation date is not recorded, so no "
                     "cancellation event is shown.")
    return events


def _stall_checkpoint(deal: Deal, interactions: list[Interaction], cfg: IntelConfig,
                      notes: list[str]) -> list[TimelineEvent]:
    """The date the M4 stall rule's threshold is reached after the last meaningful interaction (open deals)."""
    if deal.status in TERMINAL_STATUSES:
        return []
    meaningful = [i for i in interactions if i.channel in cfg.meaningful_channels]
    if not meaningful:
        return []
    last = max(meaningful, key=lambda i: (i.occurred_at, i.id))
    threshold = cfg.stall_days(deal.stage)
    notes.append(f"The stall checkpoint uses the deal's current stage ('{deal.stage}', {threshold} days); stage "
                 "history is not recorded.")
    return [TimelineEvent(
        id=f"rule:stalled_deal:{deal.id}", kind="rule_checkpoint", evidence_kind="signal",
        at=last.occurred_at + timedelta(days=threshold), date_basis="rule_computed", rule_id="stalled_deal",
        title=_title(f"Stall threshold: {threshold} days after the last meaningful interaction "
                     f"({last.channel}), stage '{deal.stage}'"))]


def build_timeline(session: Session, customer_id: str, deal_id: str, as_of: datetime,
                   config: IntelConfig) -> DealTimeline:
    """The deal's recorded history as of ``as_of``: records entered by then and interactions that occurred by then,
    plus recorded due dates and the stall checkpoint, which may lie after ``as_of``."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    deal = _load(session, customer_id, deal_id)
    notes: list[str] = []
    events: list[TimelineEvent] = []

    all_interactions = session.exec(select(Interaction).where(
        Interaction.customer_id == customer_id, Interaction.deal_id == deal_id)).all()
    interactions = [i for i in all_interactions if i.occurred_at <= as_of]
    later = len(all_interactions) - len(interactions)
    if later:
        notes.append(f"{later} interaction(s) are dated after {as_of.date().isoformat()} and are not shown.")
    events += _interaction_events(interactions)

    if deal.created_at <= as_of:
        events.append(TimelineEvent(
            id=f"deal_entered:{deal.id}", kind="deal_entered", evidence_kind="recorded", at=deal.created_at,
            date_basis="entered_in_system", title="Deal record entered in the system",
            source=SourceRecord(type="deal", id=deal.id)))
    else:
        notes.append(f"The deal record was entered after {as_of.date().isoformat()}.")
    if interactions:
        first = min(i.occurred_at for i in interactions)
        if first < deal.created_at:
            notes.append(f"Deal record entered {deal.created_at.date().isoformat()}, after the first recorded "
                         f"interaction ({first.date().isoformat()}); the entry date is not the deal's start.")

    commitments = session.exec(select(Commitment).where(
        Commitment.customer_id == customer_id, Commitment.deal_id == deal_id)).all()
    events += _commitment_events([c for c in commitments if c.created_at <= as_of], as_of, config.timezone, notes)

    stakeholders = session.exec(select(Stakeholder).where(Stakeholder.customer_id == customer_id)).all()
    events += [
        TimelineEvent(
            id=f"stakeholder_recorded:{s.id}", kind="stakeholder_recorded", evidence_kind="recorded", at=s.created_at,
            date_basis="entered_in_system",
            title=_title(f"Stakeholder entered: {_short(s.name, 80)}" + (f", {_short(s.role, 80)}" if s.role else "")),
            source=SourceRecord(type="stakeholder", id=s.id))
        for s in stakeholders if s.created_at <= as_of
    ]
    if stakeholders:
        notes.append("Stakeholders are recorded for the customer, not per deal.")

    events += _stall_checkpoint(deal, interactions, config, notes)
    events.sort(key=lambda e: (e.at, e.id))
    return DealTimeline(customer_id=customer_id, deal_id=deal_id, as_of=as_of, events=events, data_notes=notes)


async def build_branch_view(session: Session, customer_id: str, deal_id: str, branch_at: datetime, as_of: datetime,
                            *, intelligence: IntelligenceService,
                            memory: MemoryService | None = None) -> BranchView:
    """What was recorded at ``branch_at`` (the evidence an assessment may cite) and what the records show from
    then until ``as_of``.

    ``memory=None``: no recall; known_then holds SQLite evidence only and memory.status is ``not_requested``.
    With a memory service: one bounded recall (the assembler's existing query, client, timeout and retries) in
    the customer's own bank; only memories linked through the provenance ledger to a current, existing interaction
    of this deal that occurred by ``branch_at`` are included. A failed recall gives memory.status ``unavailable``
    and the SQLite evidence is still returned (the same convention as the briefing).
    """
    if branch_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("branch_at and as_of must be timezone-aware")
    if branch_at > as_of:
        raise BranchAfterAsOfError("branch_at must not be after as_of")
    timeline = build_timeline(session, customer_id, deal_id, as_of, intelligence.config)
    pack = await assemble_briefing_evidence(session, customer_id, deal_id, branch_at, memory=memory,
                                            intelligence=intelligence, point_in_time=True)
    evaluation = intelligence.evaluate_customer(session, customer_id, branch_at, deal_id=deal_id, point_in_time=True)
    return BranchView(
        customer_id=customer_id, deal_id=deal_id, branch_at=branch_at, as_of=as_of, known_then=pack.sources,
        followed=[e for e in timeline.events if branch_at < e.at <= as_of],
        signals_then=evaluation.all_signals(), memory=pack.memory)
