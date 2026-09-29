from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import select

from app.api.deps import SessionDep, get_owned, reference_owned, require_customer
from app.api.errors import InvalidReferenceError
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import CommitmentCreate, CommitmentRead, CommitmentUpdate
from app.db.types import utcnow
from app.domain.enums import CommitmentStatus, OwnerParty
from app.domain.models import Commitment, Deal, Interaction

router = APIRouter(prefix="/api/customers/{customer_id}/commitments", tags=["commitments"])


def _apply_status(commitment: Commitment, new_status: str) -> None:
    if new_status == "done" and commitment.completed_at is None:
        commitment.completed_at = utcnow()
    elif new_status != "done":
        commitment.completed_at = None
    commitment.status = new_status


@router.get("", response_model=Page[CommitmentRead])
def list_commitments(
    customer_id: str,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    deal_id: str | None = None,
    status_: Annotated[CommitmentStatus | None, Query(alias="status")] = None,
    owner_party: OwnerParty | None = None,
    due_before: date | None = None,
    overdue: Annotated[bool | None, Query(description="Open commitments whose due date is before today (UTC)")] = None,
):
    require_customer(session, customer_id)
    stmt = select(Commitment).where(Commitment.customer_id == customer_id)
    if deal_id:
        stmt = stmt.where(Commitment.deal_id == deal_id)
    if status_:
        stmt = stmt.where(Commitment.status == status_)
    if owner_party:
        stmt = stmt.where(Commitment.owner_party == owner_party)
    if due_before:
        stmt = stmt.where(Commitment.due_date < due_before)
    if overdue is not None:
        today = datetime.now(UTC).date()
        is_overdue = (Commitment.status == "open") & (Commitment.due_date < today)
        stmt = stmt.where(is_overdue if overdue else ~is_overdue | Commitment.due_date.is_(None))
    stmt = stmt.order_by(Commitment.due_date.is_(None), Commitment.due_date, Commitment.created_at, Commitment.id)
    items, total = paginate(session, stmt, page)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=CommitmentRead, status_code=status.HTTP_201_CREATED)
def create_commitment(customer_id: str, body: CommitmentCreate, session: SessionDep):
    require_customer(session, customer_id)
    reference_owned(session, Deal, body.deal_id, customer_id, "deal_id")
    if body.source_interaction_id:
        source = reference_owned(session, Interaction, body.source_interaction_id, customer_id,
                                 "source_interaction_id")
        if source.deal_id is not None and source.deal_id != body.deal_id:
            raise InvalidReferenceError("source_interaction_id",
                                        "source_interaction_id belongs to a different deal")
    data = body.model_dump(exclude={"status"})
    commitment = Commitment(customer_id=customer_id, **data)
    _apply_status(commitment, body.status)
    session.add(commitment)
    session.commit()
    session.refresh(commitment)
    return commitment


@router.get("/{commitment_id}", response_model=CommitmentRead)
def get_commitment(customer_id: str, commitment_id: str, session: SessionDep):
    require_customer(session, customer_id)
    return get_owned(session, Commitment, commitment_id, customer_id, "Commitment")


@router.patch("/{commitment_id}", response_model=CommitmentRead)
def update_commitment(customer_id: str, commitment_id: str, body: CommitmentUpdate, session: SessionDep):
    require_customer(session, customer_id)
    commitment = get_owned(session, Commitment, commitment_id, customer_id, "Commitment")
    changes = body.model_dump(exclude_unset=True)
    new_status = changes.pop("status", None)
    for field, value in changes.items():
        setattr(commitment, field, value)
    if new_status is not None:
        _apply_status(commitment, new_status)
    commitment.updated_at = utcnow()
    session.add(commitment)
    session.commit()
    session.refresh(commitment)
    return commitment


@router.delete("/{commitment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_commitment(customer_id: str, commitment_id: str, session: SessionDep):
    require_customer(session, customer_id)
    commitment = get_owned(session, Commitment, commitment_id, customer_id, "Commitment")
    session.delete(commitment)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
