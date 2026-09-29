from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from pydantic import ValidationError
from sqlmodel import select

from app.api.deps import MemoryDep, PolicyDep, SessionDep, WorkerDep, get_owned, require_customer
from app.api.errors import ConflictError, ValidationFailedError
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import DealCreate, DealRead, DealUpdate
from app.db.types import utcnow
from app.domain.enums import DealStage, DealStatus
from app.domain.models import Commitment, Deal, Interaction
from app.memory.sync import mark_context_changed

# Deals are only listable per customer. There is deliberately no cross-customer deal
# listing: without authentication, nothing can decide which customers a caller may see.
router = APIRouter(prefix="/api/customers/{customer_id}/deals", tags=["deals"])


def _customer_deals(customer_id: str, status_: DealStatus | None, stage: DealStage | None):
    """The only way to build a deal list query: the customer filter is not optional."""
    stmt = select(Deal).where(Deal.customer_id == customer_id)
    if status_:
        stmt = stmt.where(Deal.status == status_)
    if stage:
        stmt = stmt.where(Deal.stage == stage)
    return stmt


@router.get("", response_model=Page[DealRead])
def list_deals(
    customer_id: str,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    status_: Annotated[DealStatus | None, Query(alias="status")] = None,
    stage: DealStage | None = None,
):
    require_customer(session, customer_id)
    stmt = _customer_deals(customer_id, status_, stage)
    items, total = paginate(session, stmt.order_by(Deal.created_at.desc(), Deal.id), page)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=DealRead, status_code=status.HTTP_201_CREATED)
def create_deal(customer_id: str, body: DealCreate, session: SessionDep):
    require_customer(session, customer_id)
    deal = Deal(customer_id=customer_id, **body.model_dump())
    session.add(deal)
    session.commit()
    session.refresh(deal)
    return deal


@router.get("/{deal_id}", response_model=DealRead)
def get_deal(customer_id: str, deal_id: str, session: SessionDep):
    require_customer(session, customer_id)
    return get_owned(session, Deal, deal_id, customer_id, "Deal")


@router.patch("/{deal_id}", response_model=DealRead)
def update_deal(customer_id: str, deal_id: str, body: DealUpdate, session: SessionDep, memory: MemoryDep,
                policy: PolicyDep, worker: WorkerDep, background: BackgroundTasks):
    require_customer(session, customer_id)
    deal = get_owned(session, Deal, deal_id, customer_id, "Deal")
    changes = body.model_dump(exclude_unset=True)
    retitled = "title" in changes and changes["title"] != deal.title
    for field, value in changes.items():
        setattr(deal, field, value)
    # Validate the merged record (e.g. money pair) with the create schema's rules.
    try:
        DealCreate.model_validate(deal.model_dump(include=set(DealCreate.model_fields)))
    except ValidationError as exc:
        session.rollback()
        raise ValidationFailedError("Resulting deal is invalid",
                                    details=[{"msg": e["msg"]} for e in exc.errors()]) from None
    deal.updated_at = utcnow()
    session.add(deal)
    requeued = (mark_context_changed(session, memory, customer_id=customer_id, deal_id=deal_id, policy=policy)
                if retitled else 0)
    session.commit()
    if requeued and worker is not None:
        background.add_task(worker.run_once)
    session.refresh(deal)
    return deal


@router.delete("/{deal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deal(customer_id: str, deal_id: str, session: SessionDep):
    require_customer(session, customer_id)
    deal = get_owned(session, Deal, deal_id, customer_id, "Deal")
    in_use = [name for name, model in (("interactions", Interaction), ("commitments", Commitment))
              if session.exec(select(model.id).where(model.deal_id == deal_id).limit(1)).first()]
    if in_use:
        raise ConflictError("Deal still has related records; delete them first",
                            details=[{"related": r} for r in in_use])
    session.delete(deal)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
