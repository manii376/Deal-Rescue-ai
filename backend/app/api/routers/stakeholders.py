from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func
from sqlmodel import select

from app.api.deps import SessionDep, get_owned, require_customer
from app.api.errors import ConflictError
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import StakeholderCreate, StakeholderRead, StakeholderUpdate
from app.db.types import utcnow
from app.domain.enums import Influence
from app.domain.models import InteractionParticipant, Stakeholder

router = APIRouter(prefix="/api/customers/{customer_id}/stakeholders", tags=["stakeholders"])


@router.get("", response_model=Page[StakeholderRead])
def list_stakeholders(
    customer_id: str,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    influence: Influence | None = None,
):
    require_customer(session, customer_id)
    stmt = select(Stakeholder).where(Stakeholder.customer_id == customer_id)
    if q:
        stmt = stmt.where(func.lower(Stakeholder.name).contains(q.strip().lower()))
    if influence:
        stmt = stmt.where(Stakeholder.influence == influence)
    items, total = paginate(session, stmt.order_by(Stakeholder.name, Stakeholder.id), page)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=StakeholderRead, status_code=status.HTTP_201_CREATED)
def create_stakeholder(customer_id: str, body: StakeholderCreate, session: SessionDep):
    require_customer(session, customer_id)
    stakeholder = Stakeholder(customer_id=customer_id, **body.model_dump())
    session.add(stakeholder)
    session.commit()
    session.refresh(stakeholder)
    return stakeholder


@router.get("/{stakeholder_id}", response_model=StakeholderRead)
def get_stakeholder(customer_id: str, stakeholder_id: str, session: SessionDep):
    require_customer(session, customer_id)
    return get_owned(session, Stakeholder, stakeholder_id, customer_id, "Stakeholder")


@router.patch("/{stakeholder_id}", response_model=StakeholderRead)
def update_stakeholder(customer_id: str, stakeholder_id: str, body: StakeholderUpdate, session: SessionDep):
    require_customer(session, customer_id)
    stakeholder = get_owned(session, Stakeholder, stakeholder_id, customer_id, "Stakeholder")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(stakeholder, field, value)
    stakeholder.updated_at = utcnow()
    session.add(stakeholder)
    session.commit()
    session.refresh(stakeholder)
    return stakeholder


@router.delete("/{stakeholder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stakeholder(customer_id: str, stakeholder_id: str, session: SessionDep):
    require_customer(session, customer_id)
    stakeholder = get_owned(session, Stakeholder, stakeholder_id, customer_id, "Stakeholder")
    if session.exec(select(InteractionParticipant).where(
            InteractionParticipant.stakeholder_id == stakeholder_id).limit(1)).first():
        raise ConflictError("Stakeholder participated in recorded interactions; history is preserved",
                            details=[{"related": "interactions"}])
    session.delete(stakeholder)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
