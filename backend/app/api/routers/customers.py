from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy import func
from sqlmodel import select

from app.api.deps import MemoryDep, PolicyDep, SessionDep, WorkerDep, require_customer
from app.api.errors import ConflictError
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import CustomerCreate, CustomerRead, CustomerSummary, CustomerUpdate
from app.db.types import utcnow
from app.domain.models import Commitment, Customer, Deal, Interaction, MemoryWrite, Stakeholder
from app.memory.sync import mark_context_changed

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.get("", response_model=Page[CustomerSummary],
            summary="Customer directory (summaries only; notes require the per-customer endpoint)")
def list_customers(
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    q: Annotated[str | None, Query(max_length=200, description="Case-insensitive name contains")] = None,
    is_synthetic: bool | None = None,
):
    stmt = select(Customer)
    if q:
        stmt = stmt.where(func.lower(Customer.name).contains(q.strip().lower()))
    if is_synthetic is not None:
        stmt = stmt.where(Customer.is_synthetic == is_synthetic)
    items, total = paginate(session, stmt.order_by(Customer.name, Customer.id), page)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(body: CustomerCreate, session: SessionDep):
    customer = Customer(**body.model_dump())
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


@router.get("/{customer_id}", response_model=CustomerRead)
def get_customer(customer_id: str, session: SessionDep):
    return require_customer(session, customer_id)


@router.patch("/{customer_id}", response_model=CustomerRead)
def update_customer(customer_id: str, body: CustomerUpdate, session: SessionDep, memory: MemoryDep,
                    policy: PolicyDep, worker: WorkerDep, background: BackgroundTasks):
    customer = require_customer(session, customer_id)
    changes = body.model_dump(exclude_unset=True)
    renamed = "name" in changes and changes["name"] != customer.name
    for field, value in changes.items():
        setattr(customer, field, value)
    customer.updated_at = utcnow()
    session.add(customer)
    requeued = mark_context_changed(session, memory, customer_id=customer_id, policy=policy) if renamed else 0
    session.commit()
    if requeued and worker is not None:
        background.add_task(worker.run_once)
    session.refresh(customer)
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(customer_id: str, session: SessionDep):
    customer = require_customer(session, customer_id)
    children = {
        "deals": Deal, "stakeholders": Stakeholder, "interactions": Interaction, "commitments": Commitment,
    }
    in_use = [name for name, model in children.items()
              if session.exec(select(model.id).where(model.customer_id == customer_id).limit(1)).first()]
    remembered = session.exec(select(MemoryWrite.id).where(
        MemoryWrite.customer_id == customer_id,
        MemoryWrite.status.not_in(["disabled", "deleted"])).limit(1)).first()
    if in_use or remembered:
        reasons = in_use + (["stored memories"] if remembered else [])
        raise ConflictError("Customer still has related records; delete them first",
                            details=[{"related": r} for r in reasons])
    for mw in session.exec(select(MemoryWrite).where(MemoryWrite.customer_id == customer_id)).all():
        session.delete(mw)
    session.delete(customer)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
