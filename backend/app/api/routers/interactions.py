from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response, status
from sqlalchemy import delete
from sqlmodel import Session, select

from app.api.deps import MemoryDep, PolicyDep, SessionDep, WorkerDep, get_owned, reference_owned, require_customer
from app.api.errors import ConflictError, ServiceUnavailableError, not_found
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import (
    InteractionCreate,
    InteractionRead,
    InteractionUpdate,
    MemoryRefRead,
    MemoryWriteRead,
)
from app.db.types import utcnow
from app.domain.enums import Channel
from app.domain.models import Commitment, Deal, Interaction, InteractionParticipant, MemorySourceRef, Stakeholder
from app.memory.sync import (
    get_memory_write,
    mark_interaction_changed,
    mark_interaction_deleted,
    request_retry,
)

router = APIRouter(prefix="/api/customers/{customer_id}/interactions", tags=["interactions"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=100, min_length=8)]


def _memory_read(session: Session, interaction_id: str) -> MemoryWriteRead | None:
    mw = get_memory_write(session, interaction_id)
    if mw is None:
        return None
    refs = session.exec(select(MemorySourceRef).where(MemorySourceRef.memory_write_id == mw.id)
                        .order_by(MemorySourceRef.created_at, MemorySourceRef.id)).all()
    return MemoryWriteRead(
        status=mw.status, bank_id=mw.bank_id, document_id=mw.document_id, attempts=mw.attempts,
        last_error=mw.last_error, last_attempt_at=mw.last_attempt_at, stored_at=mw.stored_at,
        memory_refs=[MemoryRefRead(bank_id=r.bank_id, memory_id=r.memory_id) for r in refs],
    )


def _read(session: Session, interaction: Interaction) -> InteractionRead:
    participant_ids = session.exec(
        select(InteractionParticipant.stakeholder_id)
        .where(InteractionParticipant.interaction_id == interaction.id)
        .order_by(InteractionParticipant.stakeholder_id)
    ).all()
    return InteractionRead(
        id=interaction.id, customer_id=interaction.customer_id, deal_id=interaction.deal_id,
        occurred_at=interaction.occurred_at, channel=interaction.channel, title=interaction.title,
        notes=interaction.notes, participant_ids=list(participant_ids),
        memory=_memory_read(session, interaction.id),
        created_at=interaction.created_at, updated_at=interaction.updated_at,
    )


def _set_participants(session: Session, interaction: Interaction, stakeholder_ids: list[str]) -> None:
    unique_ids = list(dict.fromkeys(stakeholder_ids))
    for sid in unique_ids:
        reference_owned(session, Stakeholder, sid, interaction.customer_id, "participant_ids")
    session.exec(delete(InteractionParticipant).where(InteractionParticipant.interaction_id == interaction.id))
    for sid in unique_ids:
        session.add(InteractionParticipant(interaction_id=interaction.id, stakeholder_id=sid,
                                           customer_id=interaction.customer_id))


def schedule_memory_job(background: BackgroundTasks, worker, mw) -> None:
    """Fast path after commit. The durable worker picks the row up anyway if this is lost."""
    if worker is None or mw is None:
        return
    if mw.status == "pending":
        background.add_task(worker.process_write, mw.id)
    elif mw.status == "delete_pending":
        background.add_task(worker.process_delete, mw.id)


@router.get("", response_model=Page[InteractionRead])
def list_interactions(
    customer_id: str,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    deal_id: str | None = None,
    channel: Channel | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
):
    require_customer(session, customer_id)
    stmt = select(Interaction).where(Interaction.customer_id == customer_id)
    if deal_id:
        stmt = stmt.where(Interaction.deal_id == deal_id)
    if channel:
        stmt = stmt.where(Interaction.channel == channel)
    if occurred_from:
        stmt = stmt.where(Interaction.occurred_at >= occurred_from)
    if occurred_to:
        stmt = stmt.where(Interaction.occurred_at < occurred_to)
    items, total = paginate(session, stmt.order_by(Interaction.occurred_at.desc(), Interaction.id), page)
    return Page(items=[_read(session, i) for i in items], total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=InteractionRead, status_code=status.HTTP_201_CREATED)
def create_interaction(
    customer_id: str,
    body: InteractionCreate,
    session: SessionDep,
    memory: MemoryDep,
    policy: PolicyDep,
    worker: WorkerDep,
    response: Response,
    background: BackgroundTasks,
    idempotency_key: IdempotencyKey = None,
):
    require_customer(session, customer_id)
    if idempotency_key:
        existing = session.exec(select(Interaction).where(
            Interaction.customer_id == customer_id, Interaction.idempotency_key == idempotency_key)).first()
        if existing is not None:
            same = (existing.occurred_at == body.occurred_at and existing.channel == body.channel
                    and existing.notes == body.notes and existing.deal_id == body.deal_id)
            if not same:
                raise ConflictError("Idempotency-Key was already used for a different interaction")
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotent-Replayed"] = "true"
            return _read(session, existing)
    if body.deal_id:
        reference_owned(session, Deal, body.deal_id, customer_id, "deal_id")

    interaction = Interaction(customer_id=customer_id, idempotency_key=idempotency_key,
                              **body.model_dump(exclude={"participant_ids"}))
    session.add(interaction)
    session.flush()
    _set_participants(session, interaction, body.participant_ids)
    mw = mark_interaction_changed(session, interaction, memory, policy)
    session.commit()  # business record + memory ledger committed before any memory I/O
    schedule_memory_job(background, worker, mw)
    return _read(session, interaction)


@router.get("/{interaction_id}", response_model=InteractionRead)
def get_interaction(customer_id: str, interaction_id: str, session: SessionDep):
    require_customer(session, customer_id)
    return _read(session, get_owned(session, Interaction, interaction_id, customer_id, "Interaction"))


@router.patch("/{interaction_id}", response_model=InteractionRead)
def update_interaction(
    customer_id: str,
    interaction_id: str,
    body: InteractionUpdate,
    session: SessionDep,
    memory: MemoryDep,
    policy: PolicyDep,
    worker: WorkerDep,
    background: BackgroundTasks,
):
    require_customer(session, customer_id)
    interaction = get_owned(session, Interaction, interaction_id, customer_id, "Interaction")
    changes = body.model_dump(exclude_unset=True)
    participant_ids = changes.pop("participant_ids", None)
    if "deal_id" in changes and changes["deal_id"] != interaction.deal_id:
        if changes["deal_id"] is not None:
            reference_owned(session, Deal, changes["deal_id"], customer_id, "deal_id")
        linked = session.exec(select(Commitment.id).where(
            Commitment.source_interaction_id == interaction_id,
            Commitment.deal_id != changes["deal_id"]).limit(1)).first() if changes["deal_id"] else None
        if linked:
            raise ConflictError("Commitments sourced from this interaction belong to its current deal")
    for field, value in changes.items():
        setattr(interaction, field, value)
    if participant_ids is not None:
        _set_participants(session, interaction, participant_ids)
    interaction.updated_at = utcnow()
    session.add(interaction)
    mw = mark_interaction_changed(session, interaction, memory, policy)
    session.commit()
    schedule_memory_job(background, worker, mw)
    return _read(session, interaction)


@router.delete("/{interaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_interaction(
    customer_id: str,
    interaction_id: str,
    session: SessionDep,
    policy: PolicyDep,
    worker: WorkerDep,
    background: BackgroundTasks,
):
    require_customer(session, customer_id)
    interaction = get_owned(session, Interaction, interaction_id, customer_id, "Interaction")
    if session.exec(select(Commitment.id).where(Commitment.source_interaction_id == interaction_id).limit(1)).first():
        raise ConflictError("Commitments reference this interaction as their source; delete or relink them first",
                            details=[{"related": "commitments"}])
    session.exec(delete(InteractionParticipant).where(InteractionParticipant.interaction_id == interaction_id))
    session.delete(interaction)
    session.flush()  # take the write lock before touching the ledger
    # Tombstone: the memory ledger row can never be written again, and any write already in
    # flight hands over to deletion when it finishes.
    mw = mark_interaction_deleted(session, interaction_id, policy)
    session.commit()
    schedule_memory_job(background, worker, mw)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{interaction_id}/memory", response_model=MemoryWriteRead)
def get_interaction_memory(customer_id: str, interaction_id: str, session: SessionDep):
    require_customer(session, customer_id)
    get_owned(session, Interaction, interaction_id, customer_id, "Interaction")
    result = _memory_read(session, interaction_id)
    if result is None:
        raise not_found("Memory record")
    return result


@router.post("/{interaction_id}/memory/sync", response_model=MemoryWriteRead,
             status_code=status.HTTP_202_ACCEPTED,
             summary="Retry storing this interaction in memory (idempotent)")
def sync_interaction(
    customer_id: str,
    interaction_id: str,
    session: SessionDep,
    memory: MemoryDep,
    policy: PolicyDep,
    worker: WorkerDep,
    background: BackgroundTasks,
    response: Response,
):
    require_customer(session, customer_id)
    interaction = get_owned(session, Interaction, interaction_id, customer_id, "Interaction")
    if memory.backend == "disabled":
        raise ServiceUnavailableError("Memory is disabled (MEMORY_BACKEND=disabled)", code="memory_unavailable")
    mw = get_memory_write(session, interaction_id) or mark_interaction_changed(session, interaction, memory, policy)
    scheduled = request_retry(session, mw, policy)
    session.commit()
    if scheduled:
        schedule_memory_job(background, worker, mw)
    else:
        response.status_code = status.HTTP_200_OK  # already stored and unchanged: nothing to do
    return _memory_read(session, interaction_id)
