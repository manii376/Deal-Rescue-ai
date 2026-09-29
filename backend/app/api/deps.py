from typing import Annotated, TypeVar

from fastapi import Depends, Request
from sqlmodel import Session, SQLModel

from app.ai.service import AIService
from app.api.errors import InvalidReferenceError, not_found
from app.db.engine import get_session
from app.domain.models import Customer
from app.memory.service import MemoryService
from app.memory.sync import MemorySyncWorker, SyncPolicy

SessionDep = Annotated[Session, Depends(get_session)]

M = TypeVar("M", bound=SQLModel)


def get_memory_service(request: Request) -> MemoryService:
    return request.app.state.memory


def get_ai_service(request: Request) -> AIService:
    return request.app.state.ai


def get_sync_policy(request: Request) -> SyncPolicy:
    return request.app.state.sync_policy


def get_memory_worker(request: Request) -> MemorySyncWorker | None:
    """None when MEMORY_BACKEND=disabled."""
    return request.app.state.memory_worker


MemoryDep = Annotated[MemoryService, Depends(get_memory_service)]
PolicyDep = Annotated[SyncPolicy, Depends(get_sync_policy)]
WorkerDep = Annotated[MemorySyncWorker | None, Depends(get_memory_worker)]
AIDep = Annotated[AIService, Depends(get_ai_service)]


def require_customer(session: Session, customer_id: str) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise not_found("Customer")
    return customer


def get_owned(session: Session, model: type[M], record_id: str, customer_id: str, entity: str) -> M:
    """Fetch a record only if it belongs to ``customer_id``.

    A record owned by another customer yields the same 404 as a missing one, so the
    response never confirms that another customer's id exists.
    """
    record = session.get(model, record_id)
    if record is None or record.customer_id != customer_id:
        raise not_found(entity)
    return record


def reference_owned(session: Session, model: type[M], record_id: str, customer_id: str, field: str) -> M:
    """Like get_owned, for ids supplied in a request body (422 invalid_reference)."""
    record = session.get(model, record_id)
    if record is None or record.customer_id != customer_id:
        raise InvalidReferenceError(field)
    return record
