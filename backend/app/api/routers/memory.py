"""Customer-scoped memory endpoints.

There is no fallback: when the memory backend is disabled or unreachable these return
503 ``memory_unavailable`` rather than substituting SQLite keyword search.

Provenance comes from our own ledger, not from memory content: a hit is "linked" only if
its (bank_id, memory_id) is recorded in memory_source_refs for this customer; the source
timestamp and deal come from the SQLite record. Hits whose source record was deleted are
excluded, and hits from any bank other than this customer's are dropped.
"""

import asyncio
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import (
    MemoryDep,
    PolicyDep,
    SessionDep,
    WorkerDep,
    get_owned,
    reference_owned,
    require_customer,
)
from app.api.errors import AppError, ServiceUnavailableError
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.routers.interactions import schedule_memory_job
from app.api.schemas import RecallBody, ReflectBody
from app.db.types import utcnow
from app.domain.enums import MemoryWriteStatus
from app.domain.models import Deal, Interaction, MemorySourceRef, MemoryWrite
from app.memory.provenance import EvidenceHitRead, SourceLink  # noqa: F401  (re-exported schemas)
from app.memory.provenance import link_hits as _link
from app.memory.sync import queue_counts, request_retry
from app.memory.types import (
    EvidenceHit,
    MemoryOperationError,
    MemoryStatus,
    MemoryUnavailableError,
    RecallRequest,
    ReflectionResult,
    ReflectRequest,
)

router = APIRouter(prefix="/api/customers/{customer_id}/memory", tags=["memory"])


class MemoryBackendError(AppError):
    status_code = 502
    code = "memory_backend_error"


# -- evidence provenance ---------------------------------------------------------


class RecallResponse(BaseModel):
    query: str
    hits: list[EvidenceHitRead]
    excluded_deleted_sources: int = 0
    excluded_foreign_bank: int = 0


class ReflectResponse(ReflectionResult):
    evidence: list[EvidenceHitRead]
    excluded_deleted_sources: int = 0
    excluded_foreign_bank: int = 0


def _unavailable(exc: MemoryUnavailableError) -> ServiceUnavailableError:
    return ServiceUnavailableError(exc.reason, code="memory_unavailable")


@router.post("/recall", response_model=RecallResponse)
async def recall(customer_id: str, body: RecallBody, session: SessionDep, memory: MemoryDep):
    require_customer(session, customer_id)
    if body.deal_id:
        reference_owned(session, Deal, body.deal_id, customer_id, "deal_id")
    try:
        hits = await memory.recall_customer_evidence(RecallRequest(
            customer_id=customer_id, query=body.query, deal_id=body.deal_id, max_results=body.max_results))
    except MemoryUnavailableError as exc:
        raise _unavailable(exc) from None
    except MemoryOperationError as exc:
        raise MemoryBackendError(exc.reason) from None
    linked, deleted, foreign = _link(session, memory, customer_id, hits)
    return RecallResponse(query=body.query, hits=linked, excluded_deleted_sources=deleted,
                          excluded_foreign_bank=foreign)


@router.post("/reflect", response_model=ReflectResponse)
async def reflect(customer_id: str, body: ReflectBody, session: SessionDep, memory: MemoryDep):
    require_customer(session, customer_id)
    try:
        result = await memory.reflect_customer(ReflectRequest(customer_id=customer_id, question=body.question))
    except MemoryUnavailableError as exc:
        raise _unavailable(exc) from None
    except MemoryOperationError as exc:
        raise MemoryBackendError(exc.reason) from None
    linked, deleted, foreign = _link(session, memory, customer_id, result.evidence)
    return ReflectResponse(text=result.text, evidence=linked, unresolved_memory_ids=result.unresolved_memory_ids,
                           excluded_deleted_sources=deleted, excluded_foreign_bank=foreign)


# -- synchronisation ledger ---------------------------------------------------------


class MemoryLedgerEntry(BaseModel):
    id: str
    source_type: str
    source_id: str
    status: MemoryWriteStatus
    bank_id: str
    document_id: str
    attempts: int
    max_attempts: int
    last_error: str | None
    last_error_kind: str | None
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None
    stored_at: datetime | None
    source_deleted_at: datetime | None
    is_current: bool  # memory holds exactly the record's current content
    memory_ref_count: int
    auto_retry_scheduled: bool  # False for failures that need an explicit retry


def _entry(session: Session, mw: MemoryWrite) -> MemoryLedgerEntry:
    refs = session.exec(select(func.count()).select_from(MemorySourceRef)
                        .where(MemorySourceRef.memory_write_id == mw.id)).one()
    auto = (mw.status in ("failed", "delete_failed") and mw.last_error_kind == "unavailable"
            and mw.attempts < mw.max_attempts)
    return MemoryLedgerEntry(
        id=mw.id, source_type=mw.source_type, source_id=mw.source_id, status=mw.status, bank_id=mw.bank_id,
        document_id=mw.document_id, attempts=mw.attempts, max_attempts=mw.max_attempts,
        last_error=mw.last_error, last_error_kind=mw.last_error_kind, last_attempt_at=mw.last_attempt_at,
        next_attempt_at=mw.next_attempt_at, stored_at=mw.stored_at, source_deleted_at=mw.source_deleted_at,
        is_current=mw.source_deleted_at is None and mw.status == "stored" and mw.stored_hash == mw.content_hash,
        memory_ref_count=refs, auto_retry_scheduled=auto,
    )


@router.get("/writes", response_model=Page[MemoryLedgerEntry],
            summary="Memory synchronisation ledger for this customer (includes deleted sources)")
def list_memory_writes(
    customer_id: str,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    status_: Annotated[MemoryWriteStatus | None, Query(alias="status")] = None,
    source_id: str | None = None,
):
    require_customer(session, customer_id)
    stmt = select(MemoryWrite).where(MemoryWrite.customer_id == customer_id)
    if status_:
        stmt = stmt.where(MemoryWrite.status == status_)
    if source_id:
        stmt = stmt.where(MemoryWrite.source_id == source_id)
    items, total = paginate(session, stmt.order_by(MemoryWrite.updated_at.desc(), MemoryWrite.id), page)
    return Page(items=[_entry(session, mw) for mw in items], total=total, limit=page.limit, offset=page.offset)


@router.get("/writes/{write_id}", response_model=MemoryLedgerEntry)
def get_memory_write_entry(customer_id: str, write_id: str, session: SessionDep):
    require_customer(session, customer_id)
    return _entry(session, get_owned(session, MemoryWrite, write_id, customer_id, "Memory write"))


@router.post("/writes/{write_id}/retry", response_model=MemoryLedgerEntry, status_code=status.HTTP_202_ACCEPTED,
             summary="Retry a failed memory write or deletion (grants a fresh attempt budget)")
def retry_memory_write(
    customer_id: str,
    write_id: str,
    session: SessionDep,
    memory: MemoryDep,
    policy: PolicyDep,
    worker: WorkerDep,
    background: BackgroundTasks,
    response: Response,
):
    require_customer(session, customer_id)
    mw = get_owned(session, MemoryWrite, write_id, customer_id, "Memory write")
    if memory.backend == "disabled":
        raise ServiceUnavailableError("Memory is disabled (MEMORY_BACKEND=disabled)", code="memory_unavailable")
    if request_retry(session, mw, policy):
        session.commit()
        schedule_memory_job(background, worker, mw)
    else:
        session.commit()
        response.status_code = status.HTTP_200_OK  # nothing to do
    session.refresh(mw)
    return _entry(session, mw)


# -- system-level (no customer data) --------------------------------------------------

status_router = APIRouter(tags=["system"])


class MemoryQueueStatus(BaseModel):
    backend: str
    worker_running: bool
    by_status: dict[str, int]
    due_writes: int
    due_deletes: int
    needs_manual_retry: int


@status_router.get("/api/system/memory", response_model=MemoryStatus)
async def memory_status(memory: MemoryDep):
    return await memory.status()


@status_router.get("/api/system/memory/queue", response_model=MemoryQueueStatus,
                   summary="Counts only; per-customer details are under /api/customers/{cid}/memory/writes")
async def memory_queue(session: SessionDep, memory: MemoryDep, worker: WorkerDep):
    counts = await asyncio.to_thread(queue_counts, session.get_bind(), utcnow())
    return MemoryQueueStatus(backend=memory.backend, worker_running=bool(worker and worker.running), **counts)
