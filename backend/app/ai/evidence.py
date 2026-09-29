"""Deterministic, customer-and-deal-scoped evidence assembly for AI operations.

Evidence refs (what the model may cite):
  R*  recorded SQLite records: the deal, its commitments, the customer's stakeholders
  D*  deterministic M4 signals for the deal (and customer-level signals)
  N*  interaction notes on this deal, written by the sales rep ("rep_note"; NOT customer statements)
  M*  Hindsight memories, only if linked to a still-existing interaction of THIS deal via our own
      (bank_id, memory_id) ledger and still current; unlinked Cloud summaries are excluded

Every query is filtered by the validated customer (and deal). Memory recall runs through the
MemoryService (the customer's own bank). If recall fails the pack says so and continues with
SQLite evidence; nothing is substituted for semantic recall.

``point_in_time=True`` (Deal Time Machine; default off, so briefings are unchanged) restricts the pack to what
was recorded by ``as_of``: commitments and stakeholders entered by then, notes that occurred by then, memories
linked to such notes, and M4 signals evaluated in point-in-time mode. Deal fields have no change history, so the
deal item says it shows current values. A commitment's status is only stated when it is known for that date.
``memory=None`` skips recall entirely (no memory service is contacted); the status is ``not_requested``.
In point-in-time mode a linked memory is eligible only if its source interaction occurred by ``as_of``; others
are counted in ``excluded_after_cutoff``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.ai.schemas import BriefingRequest, DealContext, EvidenceItem, EvidenceKind
from app.domain.models import Commitment, Customer, Deal, Interaction, Stakeholder
from app.intelligence.service import IntelligenceService
from app.memory.provenance import link_hits
from app.memory.service import MemoryService
from app.memory.types import MemoryOperationError, MemoryRef, MemoryUnavailableError, RecallRequest

MAX_NOTES = 6
MAX_COMMITMENTS = 8
MAX_STAKEHOLDERS = 5
MAX_SIGNALS = 8
MAX_MEMORIES = 6
MAX_TEXT_CHARS = 600


class SourceRecord(BaseModel):
    type: str
    id: str


class EvidenceSource(BaseModel):
    """Safe metadata the UI needs to show and open the evidence behind a citation."""

    ref: str
    kind: EvidenceKind
    source_type: str
    source_id: str | None
    occurred_at: datetime | None = None
    excerpt: str = Field(description="Exactly the text the model was given")
    memory_ref: MemoryRef | None = None
    related_records: list[SourceRecord] = Field(default_factory=list,
                                                description="Records behind a deterministic signal")


class MemoryEvidenceStatus(BaseModel):
    """What happened to memory for one evidence pack.

    used: recall succeeded and linked, current memories were included. no_relevant_memories: recall succeeded but
    nothing was eligible (see the exclusion counts). unavailable: recall was attempted and failed (reason given).
    disabled: the memory backend is switched off. not_requested: the caller did not ask for memory (no service was
    contacted); e.g. a Deal Time Machine branch view without include_memory.
    """

    status: Literal["used", "no_relevant_memories", "unavailable", "disabled", "not_requested"]
    reason: str | None = None
    included: int = 0
    excluded_unlinked: int = 0
    excluded_stale: int = 0
    excluded_other_deal: int = 0
    excluded_deleted_sources: int = 0
    excluded_foreign_bank: int = 0
    excluded_after_cutoff: int = Field(0, description="Point-in-time views only: linked memories whose source "
                                                      "interaction occurred after the cutoff (or has no date)")


class EvidencePack(BaseModel):
    request: BriefingRequest
    sources: list[EvidenceSource]
    memory: MemoryEvidenceStatus
    sufficient: bool
    insufficient_reason: str | None = None


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1] + "…"


def _money(value_minor: int | None, currency: str | None) -> str:
    if value_minor is None or currency is None:
        return "not recorded"
    return f"{currency} {value_minor / 100:,.2f}".replace(".00", "")


class _Builder:
    def __init__(self):
        self.items: list[EvidenceItem] = []
        self.sources: list[EvidenceSource] = []
        self._counters: dict[str, int] = {}

    def add(self, prefix: str, kind: EvidenceKind, source_type: str, source_id: str | None, text: str, *,
            occurred_at: datetime | None = None, memory_ref: MemoryRef | None = None,
            related: list[SourceRecord] | None = None) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        ref = f"{prefix}{self._counters[prefix]}"
        text = _clip(text)
        self.items.append(EvidenceItem(ref=ref, kind=kind, text=text, source_type=source_type,
                                       source_id=source_id, occurred_at=occurred_at, memory_ref=memory_ref))
        self.sources.append(EvidenceSource(ref=ref, kind=kind, source_type=source_type, source_id=source_id,
                                           occurred_at=occurred_at, excerpt=text, memory_ref=memory_ref,
                                           related_records=related or []))
        return ref

    def count(self, prefix: str) -> int:
        return self._counters.get(prefix, 0)


MEMORY_NOT_REQUESTED = "Memory recall was not requested for this view; only SQLite records were used."


def _status_then(c: Commitment, as_of: datetime) -> str:
    """The commitment's status at ``as_of`` if the records establish it; never a later status."""
    if c.status == "open":
        return "open"
    if c.status == "done" and c.completed_at is not None:
        return "done" if c.completed_at <= as_of else "open"
    return "not recorded for this date (no status-change date is stored)"


async def assemble_briefing_evidence(session: Session, customer_id: str, deal_id: str, as_of: datetime, *,
                                     memory: MemoryService | None, intelligence: IntelligenceService,
                                     point_in_time: bool = False) -> EvidencePack:
    customer = session.get(Customer, customer_id)
    deal = session.get(Deal, deal_id)
    if customer is None or deal is None or deal.customer_id != customer_id:
        raise ValueError("deal does not belong to this customer")  # routes validate first; defence in depth

    b = _Builder()
    b.add("R", "recorded", "deal", deal.id,
          ("Current values (no change history): " if point_in_time else "") +
          f"Deal '{deal.title}' for {customer.name}: stage {deal.stage}, status {deal.status}, value "
          f"{_money(deal.value_minor, deal.currency)}, expected close "
          f"{deal.expected_close_date.isoformat() if deal.expected_close_date else 'not recorded'}, owner "
          f"{deal.owner_name or 'not recorded'}.")

    commitment_stmt = select(Commitment).where(Commitment.customer_id == customer_id, Commitment.deal_id == deal_id)
    if point_in_time:
        commitment_stmt = commitment_stmt.where(Commitment.created_at <= as_of)
    commitments = session.exec(
        commitment_stmt
        .order_by((Commitment.status != "open"), Commitment.due_date.is_(None), Commitment.due_date, Commitment.id)
        .limit(MAX_COMMITMENTS)).all()
    for c in commitments:
        owner = c.owner_party + (f", {c.owner_name}" if c.owner_name else "")
        b.add("R", "recorded", "commitment", c.id,
              f"Commitment (owner: {owner}): {c.description}; due "
              f"{c.due_date.isoformat() if c.due_date else 'no due date'}; status "
              f"{_status_then(c, as_of) if point_in_time else c.status}.")

    stakeholder_stmt = select(Stakeholder).where(Stakeholder.customer_id == customer_id)
    if point_in_time:
        stakeholder_stmt = stakeholder_stmt.where(Stakeholder.created_at <= as_of)
    stakeholders = session.exec(
        stakeholder_stmt
        .order_by(Stakeholder.name, Stakeholder.id).limit(MAX_STAKEHOLDERS)).all()
    for s in stakeholders:
        b.add("R", "recorded", "stakeholder", s.id,
              f"Stakeholder: {s.name}" + (f", {s.role}" if s.role else "") +
              f"; influence {s.influence}; recorded priorities: {', '.join(s.priorities) or 'none'}.")

    evaluation = intelligence.evaluate_customer(session, customer_id, as_of, deal_id=deal_id,
                                                point_in_time=point_in_time)
    signals = list(evaluation.customer_signals) + [s for e in evaluation.deals for s in e.signals]
    for sig in signals[:MAX_SIGNALS]:
        b.add("D", "signal", "signal", sig.id, f"{sig.title}. {sig.explanation}",
              related=[SourceRecord(type=r.type, id=r.id) for r in sig.sources])

    notes = session.exec(
        select(Interaction).where(Interaction.customer_id == customer_id, Interaction.deal_id == deal_id,
                                  Interaction.occurred_at <= as_of)
        .order_by(Interaction.occurred_at.desc(), Interaction.id).limit(MAX_NOTES)).all()
    for i in notes:
        b.add("N", "rep_note", "interaction", i.id,
              f"{i.channel} (rep note)" + (f" '{i.title}'" if i.title else "") + f": {i.notes}",
              occurred_at=i.occurred_at)

    if memory is None:
        memory_status = MemoryEvidenceStatus(status="not_requested", reason=MEMORY_NOT_REQUESTED)
    else:
        memory_status = await _add_memories(b, session, customer_id, deal, memory,
                                            known_at=as_of if point_in_time else None)

    substantive = b.count("N") + b.count("M") + len(commitments)
    request = BriefingRequest(
        deal=DealContext(customer_id=customer_id, deal_id=deal.id, customer_name=customer.name,
                         deal_title=deal.title, stage=deal.stage, status=deal.status, value_minor=deal.value_minor,
                         currency=deal.currency, expected_close_date=deal.expected_close_date),
        evidence=b.items, as_of=as_of)
    return EvidencePack(
        request=request, sources=b.sources, memory=memory_status, sufficient=substantive > 0,
        insufficient_reason=None if substantive else
        "No interaction notes, commitments or linked memories are recorded for this deal.")


async def _add_memories(b: _Builder, session: Session, customer_id: str, deal: Deal,
                        memory: MemoryService, known_at: datetime | None = None) -> MemoryEvidenceStatus:
    if memory.backend == "disabled":
        return MemoryEvidenceStatus(status="disabled", reason=(await memory.status()).reason)
    query = f"{deal.title}: requirements, budget, stakeholders, objections, risks, decisions and commitments"
    try:
        hits = await memory.recall_customer_evidence(
            RecallRequest(customer_id=customer_id, query=query, deal_id=deal.id, max_results=20))
    except MemoryUnavailableError as exc:
        return MemoryEvidenceStatus(status="unavailable", reason=exc.reason)
    except MemoryOperationError as exc:
        return MemoryEvidenceStatus(status="unavailable", reason=exc.reason)

    linked, deleted, foreign = link_hits(session, memory, customer_id, hits)
    status = MemoryEvidenceStatus(status="no_relevant_memories", excluded_deleted_sources=deleted,
                                  excluded_foreign_bank=foreign)
    seen: set[str] = set()
    for hit in linked:
        if hit.provenance != "linked" or hit.source is None:
            status.excluded_unlinked += 1
        elif not hit.source.memory_is_current:
            status.excluded_stale += 1
        elif hit.source.deal_id != deal.id:
            status.excluded_other_deal += 1
        elif known_at is not None and (hit.source.occurred_at is None or hit.source.occurred_at > known_at):
            # Point in time: eligibility is the linked interaction's recorded occurred_at (from SQLite), never the
            # memory's retrieval or storage time. Without that date, eligibility cannot be established.
            status.excluded_after_cutoff += 1
        elif hit.text.strip().lower() in seen or status.included >= MAX_MEMORIES:
            continue
        else:
            seen.add(hit.text.strip().lower())
            b.add("M", "memory", "memory", hit.source.source_id, hit.text, occurred_at=hit.source.occurred_at,
                  memory_ref=hit.ref)
            status.included += 1
    if status.included:
        status.status = "used"
    return status
