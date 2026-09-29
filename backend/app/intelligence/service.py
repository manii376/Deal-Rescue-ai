"""Loads one customer's records and runs the deterministic rules.

Query budget: exactly four SELECTs per evaluation (deals, interactions, commitments,
stakeholder count), independent of how many deals the customer has; no N+1.
Everything is scoped by the (already validated) customer id.

``point_in_time=True`` (Deal Time Machine, default off) evaluates only what was recorded by ``as_of``:
interactions that occurred by then, commitments and stakeholders entered by then (``created_at``), and a
commitment completed after ``as_of`` (per its recorded ``completed_at``) counts as still open. Deal fields
have no change history, so they are always evaluated as currently stored.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.domain.models import Commitment, Deal, Interaction, Stakeholder
from app.intelligence.rules import (
    TERMINAL_STATUSES,
    CommitmentSnap,
    DealEvaluation,
    DealSnap,
    IntelConfig,
    InteractionSnap,
    evaluate_customer_level,
    evaluate_deal,
    priority_key,
)
from app.intelligence.schemas import Signal


@dataclass
class CustomerEvaluation:
    as_of: datetime
    deals: list[DealEvaluation]
    customer_signals: list[Signal]

    def all_signals(self) -> list[Signal]:
        signals = list(self.customer_signals)
        for evaluation in self.deals:
            signals.extend(evaluation.signals)
        return sorted(signals, key=priority_key)


class IntelligenceService:
    def __init__(self, config: IntelConfig):
        self.config = config

    def evaluate_customer(self, session: Session, customer_id: str, as_of: datetime,
                          deal_id: str | None = None, *, point_in_time: bool = False) -> CustomerEvaluation:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")

        deal_stmt = select(Deal.id, Deal.customer_id, Deal.title, Deal.stage, Deal.status, Deal.value_minor,
                           Deal.currency, Deal.expected_close_date, Deal.owner_name, Deal.created_at
                           ).where(Deal.customer_id == customer_id)
        interaction_stmt = select(Interaction.id, Interaction.deal_id, Interaction.channel, Interaction.occurred_at
                                  ).where(Interaction.customer_id == customer_id, Interaction.deal_id.is_not(None))
        commitment_stmt = select(Commitment.id, Commitment.deal_id, Commitment.description, Commitment.owner_party,
                                 Commitment.owner_name, Commitment.due_date, Commitment.status,
                                 Commitment.source_interaction_id, Commitment.created_at, Commitment.completed_at
                                 ).where(Commitment.customer_id == customer_id)
        if deal_id is not None:
            deal_stmt = deal_stmt.where(Deal.id == deal_id)
            interaction_stmt = interaction_stmt.where(Interaction.deal_id == deal_id)
            commitment_stmt = commitment_stmt.where(Commitment.deal_id == deal_id)

        deals = [DealSnap(*row) for row in session.exec(deal_stmt.order_by(Deal.id)).all()]
        interactions_by_deal: dict[str, list[InteractionSnap]] = defaultdict(list)
        for row in session.exec(interaction_stmt).all():
            snap = InteractionSnap(*row)
            if point_in_time and snap.occurred_at > as_of:
                continue
            interactions_by_deal[snap.deal_id].append(snap)
        commitments_by_deal: dict[str, list[CommitmentSnap]] = defaultdict(list)
        for *fields, created_at, completed_at in session.exec(commitment_stmt).all():
            snap = CommitmentSnap(*fields)
            if point_in_time:
                if created_at > as_of:
                    continue
                if snap.status == "done" and completed_at is not None and completed_at > as_of:
                    snap = replace(snap, status="open")
            commitments_by_deal[snap.deal_id].append(snap)
        stakeholder_stmt = select(func.count()).select_from(Stakeholder).where(Stakeholder.customer_id == customer_id)
        if point_in_time:
            stakeholder_stmt = select(Stakeholder.created_at).where(Stakeholder.customer_id == customer_id)
            stakeholder_count = sum(1 for created in session.exec(stakeholder_stmt).all() if created <= as_of)
        else:
            stakeholder_count = session.exec(stakeholder_stmt).one()

        evaluations = [
            evaluate_deal(deal, interactions_by_deal.get(deal.id, ()), commitments_by_deal.get(deal.id, ()),
                          as_of, self.config)
            for deal in deals
        ]
        has_open = any(d.status not in TERMINAL_STATUSES for d in deals)
        return CustomerEvaluation(as_of=as_of, deals=evaluations,
                                  customer_signals=evaluate_customer_level(customer_id, stakeholder_count, has_open))
