"""Customer-scoped deal-intelligence endpoints (deterministic rules only).

There is deliberately no cross-customer signal or deal listing (see
docs/security-followup-m2.md). ``as_of`` fixes the reference time; the response echoes
it so a result can be reproduced exactly.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime

from app.api.deps import SessionDep, get_owned, reference_owned, require_customer
from app.api.pagination import PageParams, page_params
from app.domain.enums import DealStatus
from app.domain.models import Deal
from app.intelligence.rules import (
    NOT_INCLUDED,
    PRIORITY_ORDER,
    RULE_CATALOG,
    SEVERITY_RANK,
    DealEvaluation,
    IntelConfig,
)
from app.intelligence.schemas import (
    Category,
    DealAttentionList,
    DealAttentionSummary,
    DealIntelligence,
    Nature,
    RuleCatalog,
    Severity,
    Signal,
    SignalList,
    SignalType,
)
from app.intelligence.service import IntelligenceService

router = APIRouter(tags=["intelligence"])

AsOf = Annotated[AwareDatetime | None, Query(description="Reference time (ISO 8601 with timezone). Default: now.")]


def get_intelligence(request: Request) -> IntelligenceService:
    return request.app.state.intelligence


IntelDep = Annotated[IntelligenceService, Depends(get_intelligence)]


def _as_of(value: datetime | None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def _counts(signals: list[Signal]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for s in signals:
        counts[s.severity] += 1
    return counts


def _highest(signals: list[Signal]) -> str | None:
    return max((s.severity for s in signals), key=SEVERITY_RANK.__getitem__, default=None)


@router.get("/api/customers/{customer_id}/intelligence/signals", response_model=SignalList,
            summary="Attention signals for one customer's deals, in priority order")
def list_signals(
    customer_id: str,
    session: SessionDep,
    intel: IntelDep,
    page: Annotated[PageParams, Depends(page_params)],
    as_of: AsOf = None,
    deal_id: str | None = None,
    type: Annotated[list[SignalType] | None, Query()] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    min_severity: Severity | None = None,
    category: Category | None = None,
    nature: Nature | None = None,
):
    require_customer(session, customer_id)
    if deal_id is not None:
        reference_owned(session, Deal, deal_id, customer_id, "deal_id")
    reference = _as_of(as_of)
    evaluation = intel.evaluate_customer(session, customer_id, reference, deal_id=deal_id)
    signals = evaluation.all_signals()
    if deal_id is not None:
        signals = [s for s in signals if s.deal_id == deal_id]
    if type:
        signals = [s for s in signals if s.type in type]
    if severity:
        signals = [s for s in signals if s.severity in severity]
    if min_severity:
        signals = [s for s in signals if SEVERITY_RANK[s.severity] >= SEVERITY_RANK[min_severity]]
    if category:
        signals = [s for s in signals if s.category == category]
    if nature:
        signals = [s for s in signals if s.nature == nature]
    window = signals[page.offset: page.offset + page.limit]
    return SignalList(as_of=reference, business_date=intel.config.business_date(reference),
                      timezone=intel.config.timezone, items=window, total=len(signals),
                      limit=page.limit, offset=page.offset)


def _summary(evaluation: DealEvaluation) -> DealAttentionSummary:
    return DealAttentionSummary(
        deal_id=evaluation.deal.id, title=evaluation.deal.title, stage=evaluation.deal.stage,
        status=evaluation.deal.status, highest_severity=_highest(evaluation.signals),
        signal_counts=_counts(evaluation.signals), signal_types=sorted({s.type for s in evaluation.signals}),
        last_meaningful_activity_at=evaluation.activity.last_meaningful_activity_at,
    )


@router.get("/api/customers/{customer_id}/intelligence/deals", response_model=DealAttentionList,
            summary="One customer's deals ordered by attention needed")
def list_deal_attention(
    customer_id: str,
    session: SessionDep,
    intel: IntelDep,
    page: Annotated[PageParams, Depends(page_params)],
    as_of: AsOf = None,
    status: DealStatus | None = None,
    min_severity: Severity | None = None,
    only_with_signals: bool = False,
):
    require_customer(session, customer_id)
    reference = _as_of(as_of)
    summaries = [_summary(e) for e in intel.evaluate_customer(session, customer_id, reference).deals]
    if status:
        summaries = [s for s in summaries if s.status == status]
    if only_with_signals:
        summaries = [s for s in summaries if s.highest_severity is not None]
    if min_severity:
        floor = SEVERITY_RANK[min_severity]
        summaries = [s for s in summaries if s.highest_severity and SEVERITY_RANK[s.highest_severity] >= floor]
    summaries.sort(key=lambda s: (-SEVERITY_RANK.get(s.highest_severity or "", 0), -s.signal_counts["high"],
                                  -s.signal_counts["medium"], -s.signal_counts["low"], s.deal_id))
    return DealAttentionList(as_of=reference, business_date=intel.config.business_date(reference),
                             timezone=intel.config.timezone, items=summaries[page.offset: page.offset + page.limit],
                             total=len(summaries), limit=page.limit, offset=page.offset)


@router.get("/api/customers/{customer_id}/deals/{deal_id}/intelligence", response_model=DealIntelligence,
            summary="Activity, commitments and signals for one deal")
def get_deal_intelligence(customer_id: str, deal_id: str, session: SessionDep, intel: IntelDep, as_of: AsOf = None):
    require_customer(session, customer_id)
    get_owned(session, Deal, deal_id, customer_id, "Deal")
    reference = _as_of(as_of)
    evaluation = intel.evaluate_customer(session, customer_id, reference, deal_id=deal_id)
    [deal_eval] = evaluation.deals
    return DealIntelligence(
        as_of=reference, business_date=intel.config.business_date(reference), timezone=intel.config.timezone,
        customer_id=customer_id, deal_id=deal_id, title=deal_eval.deal.title, stage=deal_eval.deal.stage,
        status=deal_eval.deal.status, is_closed=deal_eval.activity.stall_threshold_days is None,
        highest_severity=_highest(deal_eval.signals), signal_counts=_counts(deal_eval.signals),
        activity=deal_eval.activity, commitments=deal_eval.commitments, signals=deal_eval.signals,
        customer_signals=evaluation.customer_signals,
    )


@router.get("/api/intelligence/rules", response_model=RuleCatalog,
            summary="Rule catalogue, thresholds and priority order (no customer data)")
def rule_catalog(intel: IntelDep):
    config: IntelConfig = intel.config
    return RuleCatalog(thresholds=config.as_dict(), priority_order=PRIORITY_ORDER, rules=RULE_CATALOG,
                       not_included=NOT_INCLUDED)
