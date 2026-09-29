"""Durable, lease-based synchronisation of interactions to memory (M3).

State lives in SQLite (``memory_writes``), so nothing is lost when the process stops:
on startup the MemorySyncWorker picks up every row that is pending, due for retry,
interrupted mid-job (expired lease) or waiting for deletion.

Guarantees
----------
* Commit first: the interaction and its ledger row are saved in one business
  transaction (``mark_interaction_changed`` / ``mark_interaction_deleted``). Memory I/O
  happens afterwards and can never roll that back.
* One writer: a job must *claim* its row with a conditional UPDATE that sets a random
  ``claim_token`` and a lease. Every later transition is ``UPDATE … WHERE claim_token =
  :token``, so a worker whose lease was taken over cannot overwrite newer state.
* No duplicates: one deterministic Hindsight document per interaction, written with
  ``update_mode="replace"``; unchanged content (``stored_hash == content_hash``) is never
  re-written.
* No lost updates: an edit during a write only changes ``content_hash``; the finishing
  job atomically sets ``stored`` if the hashes match, otherwise back to ``pending``.
* No resurrection: deleting an interaction sets a tombstone (``source_deleted_at``). A row
  with a tombstone can never be claimed for writing, and a write that was already in flight
  hands over to deletion when it finishes (``delete_pending``) instead of ``stored``.
* Bounded retries: automatic retries only for transient ("unavailable") failures, with
  exponential backoff, until ``attempts == max_attempts``. Rejected/unexpected failures and
  exhausted rows wait for an explicit retry (API), which grants a fresh budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, and_, case, delete, func, insert, or_, select, update
from sqlmodel import Session
from sqlmodel import select as sm_select

from app.db.types import utcnow
from app.domain.models import (
    Customer,
    Deal,
    Interaction,
    InteractionParticipant,
    MemorySourceRef,
    MemoryWrite,
    Stakeholder,
    new_id,
)
from app.memory.service import MemoryService
from app.memory.types import InteractionMemoryPayload, MemoryOperationError, MemoryUnavailableError

logger = logging.getLogger("deal_rescue.memory.sync")

SOURCE_INTERACTION = "interaction"
MAX_ERROR_LEN = 300
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class SyncPolicy:
    max_attempts: int = 5
    lease: timedelta = timedelta(minutes=10)
    backoff_base: timedelta = timedelta(seconds=30)
    backoff_max: timedelta = timedelta(hours=1)

    @classmethod
    def from_settings(cls, settings) -> SyncPolicy:
        return cls(
            max_attempts=settings.memory_sync_max_attempts,
            lease=timedelta(seconds=settings.memory_lease_seconds),
            backoff_base=timedelta(seconds=settings.memory_retry_base_seconds),
            backoff_max=timedelta(seconds=settings.memory_retry_max_seconds),
        )

    def backoff(self, attempts: int) -> timedelta:
        return min(self.backoff_max, self.backoff_base * (2 ** max(0, attempts - 1)))


def _safe_error(reason: str) -> str:
    return reason[:MAX_ERROR_LEN]


# -- payload -------------------------------------------------------------------


def build_payload(session: Session, interaction: Interaction) -> InteractionMemoryPayload:
    """Build the memory document from this interaction's own customer's rows only."""
    customer = session.get(Customer, interaction.customer_id)
    deal = session.get(Deal, interaction.deal_id) if interaction.deal_id else None
    if deal is not None and deal.customer_id != interaction.customer_id:  # impossible via composite FK
        raise ValueError("interaction deal belongs to another customer")
    rows = session.exec(
        sm_select(Stakeholder)
        .join(InteractionParticipant, InteractionParticipant.stakeholder_id == Stakeholder.id)
        .where(InteractionParticipant.interaction_id == interaction.id,
               Stakeholder.customer_id == interaction.customer_id)
        .order_by(Stakeholder.name, Stakeholder.id)
    ).all()
    participants = [f"{s.name} ({s.role})" if s.role else s.name for s in rows]
    return InteractionMemoryPayload(
        customer_id=interaction.customer_id, customer_name=customer.name,
        deal_id=interaction.deal_id, deal_title=deal.title if deal else None,
        interaction_id=interaction.id, occurred_at=interaction.occurred_at, channel=interaction.channel,
        title=interaction.title, notes=interaction.notes, participants=participants,
    )


def payload_hash(payload: InteractionMemoryPayload) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_memory_write(session: Session, interaction_id: str) -> MemoryWrite | None:
    return session.exec(sm_select(MemoryWrite).where(
        MemoryWrite.source_type == SOURCE_INTERACTION, MemoryWrite.source_id == interaction_id)).first()


# -- business-transaction side (called inside the route's transaction) -------------------
#
# These run after the route has flushed its own writes, so the SQLite write lock is held
# until commit and a worker cannot change the row between our read and our write.


def mark_interaction_changed(session: Session, interaction: Interaction, memory: MemoryService,
                             policy: SyncPolicy | None = None, now: datetime | None = None) -> MemoryWrite:
    policy = policy or SyncPolicy()
    now = now or utcnow()
    session.flush()
    digest = payload_hash(build_payload(session, interaction))
    mw = get_memory_write(session, interaction.id)
    if mw is None:
        mw = MemoryWrite(customer_id=interaction.customer_id, source_type=SOURCE_INTERACTION,
                         source_id=interaction.id, bank_id=memory.router.customer_bank(interaction.customer_id),
                         document_id=memory.router.interaction_document_id(interaction.id),
                         status="disabled" if memory.backend == "disabled" else "pending",
                         content_hash=digest, max_attempts=policy.max_attempts, next_attempt_at=now)
        session.add(mw)
        return mw
    if mw.source_deleted_at is not None:
        raise RuntimeError("memory ledger row is tombstoned for an existing interaction")
    changed = mw.content_hash != digest
    mw.content_hash = digest
    mw.updated_at = now
    if mw.status in ("in_progress",):
        pass  # the running job compares hashes when it finishes and re-queues itself
    elif mw.stored_hash == digest and mw.status in ("stored", "pending", "failed", "disabled"):
        mw.status = "stored"  # content is back to what memory already holds
        mw.next_attempt_at = None
    elif memory.backend == "disabled":
        mw.status = "disabled"
    elif changed or mw.status == "disabled":
        mw.status = "pending"
        mw.next_attempt_at = now
        mw.max_attempts = mw.attempts + policy.max_attempts  # new content, fresh retry budget
    session.add(mw)
    return mw


def mark_context_changed(session: Session, memory: MemoryService, *, customer_id: str,
                         deal_id: str | None = None, policy: SyncPolicy | None = None) -> int:
    """Customer name or deal title is part of each memory document; re-queue affected rows."""
    stmt = sm_select(Interaction).where(Interaction.customer_id == customer_id)
    if deal_id is not None:
        stmt = stmt.where(Interaction.deal_id == deal_id)
    changed = 0
    for interaction in session.exec(stmt).all():
        mw = mark_interaction_changed(session, interaction, memory, policy)
        changed += mw.status == "pending"
    return changed


def mark_interaction_deleted(session: Session, interaction_id: str, policy: SyncPolicy | None = None,
                             now: datetime | None = None) -> MemoryWrite | None:
    """Tombstone the ledger row of an interaction being deleted in this transaction."""
    policy = policy or SyncPolicy()
    now = now or utcnow()
    mw = get_memory_write(session, interaction_id)
    if mw is None:
        return None
    mw.source_deleted_at = now
    mw.updated_at = now
    if mw.status == "in_progress":
        pass  # the in-flight write will hand over to deletion when it finishes
    elif mw.attempts == 0 and mw.stored_hash is None:
        mw.status = "deleted"  # nothing ever reached memory
        mw.next_attempt_at = None
    else:
        mw.status = "delete_pending"
        mw.next_attempt_at = now
        mw.max_attempts = mw.attempts + policy.max_attempts
    session.add(mw)
    return mw


def request_retry(session: Session, mw: MemoryWrite, policy: SyncPolicy | None = None,
                  now: datetime | None = None) -> bool:
    """Explicit retry (API). Returns True if a job is (or already was) scheduled."""
    policy = policy or SyncPolicy()
    now = now or utcnow()
    if mw.status in ("in_progress", "deleting"):
        return True
    if mw.source_deleted_at is not None:
        if mw.status == "deleted":
            return False
        mw.status = "delete_pending"
    else:
        if mw.status == "stored" and mw.stored_hash == mw.content_hash:
            return False
        mw.status = "pending"
    mw.next_attempt_at = now
    mw.max_attempts = mw.attempts + policy.max_attempts
    mw.updated_at = now
    session.add(mw)
    return True


# -- worker side: atomic transitions (each in its own short transaction) --------------


def _lease_expired(now: datetime):
    return or_(MemoryWrite.lease_expires_at.is_(None), MemoryWrite.lease_expires_at < now)


def _auto_retry_due(now: datetime):
    return and_(MemoryWrite.last_error_kind == "unavailable",
                MemoryWrite.attempts < MemoryWrite.max_attempts,
                or_(MemoryWrite.next_attempt_at.is_(None), MemoryWrite.next_attempt_at <= now))


def write_claimable(now: datetime):
    return and_(
        MemoryWrite.source_deleted_at.is_(None),
        or_(
            MemoryWrite.status.in_(["pending", "disabled"]),
            and_(MemoryWrite.status == "failed", _auto_retry_due(now)),
            and_(MemoryWrite.status == "in_progress", _lease_expired(now),
                 MemoryWrite.attempts < MemoryWrite.max_attempts),
        ),
    )


def delete_claimable(now: datetime):
    return and_(
        MemoryWrite.source_deleted_at.is_not(None),
        or_(
            MemoryWrite.status == "delete_pending",
            and_(MemoryWrite.status == "delete_failed", _auto_retry_due(now)),
            and_(MemoryWrite.status.in_(["deleting", "in_progress"]), _lease_expired(now),
                 MemoryWrite.attempts < MemoryWrite.max_attempts),
        ),
    )


def _claim_row(engine: Engine, mw_id: str, condition, new_status: str, policy: SyncPolicy,
           now: datetime) -> tuple[str, int] | None:
    token = str(uuid.uuid4())
    with engine.begin() as conn:
        result = conn.execute(
            update(MemoryWrite).where(MemoryWrite.id == mw_id, condition)
            .values(status=new_status, claim_token=token, lease_expires_at=now + policy.lease,
                    attempts=MemoryWrite.attempts + 1, last_attempt_at=now, updated_at=now)
        )
        if result.rowcount != 1:
            return None
        attempts = conn.execute(select(MemoryWrite.attempts).where(MemoryWrite.id == mw_id)).scalar_one()
    return token, attempts


def claim_write(engine: Engine, mw_id: str, policy: SyncPolicy, now: datetime) -> tuple[str, int] | None:
    return _claim_row(engine, mw_id, write_claimable(now), "in_progress", policy, now)


def claim_delete(engine: Engine, mw_id: str, policy: SyncPolicy, now: datetime) -> tuple[str, int] | None:
    return _claim_row(engine, mw_id, delete_claimable(now), "deleting", policy, now)


def finish_write(engine: Engine, mw_id: str, token: str, digest: str, bank_id: str, document_id: str,
                 memory_ids: list[str], now: datetime) -> str | None:
    """Record a successful retain. Returns the new status, or None if the lease was lost."""
    new_status = case(
        (MemoryWrite.source_deleted_at.is_not(None), "delete_pending"),
        (MemoryWrite.content_hash == digest, "stored"),
        else_="pending",
    )
    with engine.begin() as conn:
        result = conn.execute(
            update(MemoryWrite).where(MemoryWrite.id == mw_id, MemoryWrite.claim_token == token)
            .values(status=new_status, stored_hash=digest, stored_at=now, last_error=None, last_error_kind=None,
                    claim_token=None, lease_expires_at=None, next_attempt_at=now, updated_at=now)
        )
        if result.rowcount != 1:
            return None
        row = conn.execute(select(MemoryWrite.status, MemoryWrite.customer_id, MemoryWrite.source_id)
                           .where(MemoryWrite.id == mw_id)).one()
        conn.execute(delete(MemorySourceRef).where(MemorySourceRef.memory_write_id == mw_id))
        if memory_ids:
            conn.execute(insert(MemorySourceRef), [
                {"id": new_id("msr"), "memory_write_id": mw_id, "customer_id": row.customer_id,
                 "bank_id": bank_id, "memory_id": mid, "document_id": document_id,
                 "source_type": SOURCE_INTERACTION, "source_id": row.source_id, "created_at": now}
                for mid in memory_ids
            ])
        if row.status == "stored":
            conn.execute(update(MemoryWrite).where(MemoryWrite.id == mw_id).values(next_attempt_at=None))
    return row.status


def fail_job(engine: Engine, mw_id: str, token: str, *, deleting: bool, kind: str, reason: str,
             attempts: int, policy: SyncPolicy, now: datetime) -> str | None:
    retry_at = now + policy.backoff(attempts) if kind == "unavailable" else None
    if deleting:
        new_status = "delete_failed"
    else:
        # A write that fails after its source was deleted still goes to deletion: the
        # request may have reached Hindsight before failing (e.g. a timeout).
        new_status = case((MemoryWrite.source_deleted_at.is_not(None), "delete_pending"), else_="failed")
    next_at = case((MemoryWrite.source_deleted_at.is_not(None), now), else_=retry_at) if not deleting else retry_at
    with engine.begin() as conn:
        result = conn.execute(
            update(MemoryWrite).where(MemoryWrite.id == mw_id, MemoryWrite.claim_token == token)
            .values(status=new_status, last_error=_safe_error(reason), last_error_kind=kind,
                    claim_token=None, lease_expires_at=None, next_attempt_at=next_at, updated_at=now)
        )
        if result.rowcount != 1:
            return None
        return conn.execute(select(MemoryWrite.status).where(MemoryWrite.id == mw_id)).scalar_one()


def finish_delete(engine: Engine, mw_id: str, token: str, now: datetime) -> str | None:
    with engine.begin() as conn:
        result = conn.execute(
            update(MemoryWrite).where(MemoryWrite.id == mw_id, MemoryWrite.claim_token == token)
            .values(status="deleted", last_error=None, last_error_kind=None, claim_token=None,
                    lease_expires_at=None, next_attempt_at=None, updated_at=now)
        )
        if result.rowcount != 1:
            return None
        conn.execute(delete(MemorySourceRef).where(MemorySourceRef.memory_write_id == mw_id))
    return "deleted"


def release_claim(engine: Engine, mw_id: str, token: str, now: datetime) -> str | None:
    """Give a claimed job back to the queue without counting it as a failure (shutdown)."""
    with engine.begin() as conn:
        result = conn.execute(
            update(MemoryWrite).where(MemoryWrite.id == mw_id, MemoryWrite.claim_token == token)
            .values(status=case((MemoryWrite.source_deleted_at.is_not(None), "delete_pending"), else_="pending"),
                    attempts=MemoryWrite.attempts - 1, claim_token=None, lease_expires_at=None,
                    next_attempt_at=now, updated_at=now)
        )
        if result.rowcount != 1:
            return None
        return conn.execute(select(MemoryWrite.status).where(MemoryWrite.id == mw_id)).scalar_one()


def expire_exhausted_leases(engine: Engine, now: datetime) -> int:
    """Jobs whose lease expired with no attempts left become failed (manual retry only).

    Prevents a job that crashes the process from being reclaimed forever.
    """
    with engine.begin() as conn:
        result = conn.execute(
            update(MemoryWrite)
            .where(MemoryWrite.status.in_(["in_progress", "deleting"]), _lease_expired(now),
                   MemoryWrite.attempts >= MemoryWrite.max_attempts)
            .values(status=case((MemoryWrite.source_deleted_at.is_not(None), "delete_failed"), else_="failed"),
                    last_error="job did not finish before its lease expired (attempts exhausted)",
                    last_error_kind="unexpected", claim_token=None, lease_expires_at=None,
                    next_attempt_at=None, updated_at=now)
        )
        return result.rowcount


def due_jobs(engine: Engine, now: datetime, limit: int) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        writes = conn.execute(select(MemoryWrite.id).where(write_claimable(now))
                              .order_by(MemoryWrite.next_attempt_at, MemoryWrite.id).limit(limit)).scalars().all()
        deletes = conn.execute(select(MemoryWrite.id).where(delete_claimable(now))
                               .order_by(MemoryWrite.next_attempt_at, MemoryWrite.id).limit(limit)).scalars().all()
    return [(i, "delete") for i in deletes] + [(i, "write") for i in writes]


def queue_counts(engine: Engine, now: datetime) -> dict[str, object]:
    with engine.connect() as conn:
        by_status = dict(conn.execute(select(MemoryWrite.status, func.count())
                                      .group_by(MemoryWrite.status)).all())
        due_writes = conn.execute(select(func.count()).where(write_claimable(now))).scalar_one()
        due_deletes = conn.execute(select(func.count()).where(delete_claimable(now))).scalar_one()
        waiting_manual = conn.execute(select(func.count()).where(
            MemoryWrite.status.in_(["failed", "delete_failed"]),
            or_(MemoryWrite.last_error_kind != "unavailable", MemoryWrite.last_error_kind.is_(None),
                MemoryWrite.attempts >= MemoryWrite.max_attempts))).scalar_one()
    return {"by_status": by_status, "due_writes": due_writes, "due_deletes": due_deletes,
            "needs_manual_retry": waiting_manual}


# -- the worker -----------------------------------------------------------------------


def _error_kind(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, MemoryUnavailableError):
        return "unavailable", exc.reason
    if isinstance(exc, MemoryOperationError):
        return getattr(exc, "kind", "rejected"), exc.reason
    return "unexpected", f"unexpected error ({type(exc).__name__})"


class MemorySyncWorker:
    """Processes the memory_writes ledger. Safe to run next to request-time fast paths:
    every job claims its row first, so a job runs at most once at a time."""

    def __init__(self, engine: Engine, memory: MemoryService, policy: SyncPolicy,
                 poll_seconds: float = 5.0, batch_size: int = 20, clock: Clock = utcnow):
        self.engine = engine
        self.memory = memory
        self.policy = policy
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size
        self.clock = clock
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- jobs --------------------------------------------------------------------

    def _load_payload(self, mw_id: str) -> tuple[InteractionMemoryPayload | None, str | None]:
        with Session(self.engine) as session:
            mw = session.get(MemoryWrite, mw_id)
            interaction = session.get(Interaction, mw.source_id) if mw else None
            if mw is None or interaction is None:
                return None, "source interaction no longer exists"
            router = self.memory.router
            # The bank is derived from the interaction's validated owner and must match the
            # ledger; content never influences routing.
            if (interaction.customer_id != mw.customer_id
                    or router.customer_bank(interaction.customer_id) != mw.bank_id
                    or router.interaction_document_id(interaction.id) != mw.document_id):
                return None, ("bank routing mismatch: ledger bank/document differs from the current "
                              "customer bank routing (was HINDSIGHT_CUSTOMER_BANK_PREFIX changed?)")
            return build_payload(session, interaction), None

    async def process_write(self, mw_id: str) -> str | None:
        """Claim and run one write job. Returns the resulting status (None: not claimable)."""
        claimed = await asyncio.to_thread(claim_write, self.engine, mw_id, self.policy, self.clock())
        if claimed is None:
            return None
        token, attempts = claimed
        try:
            payload, problem = await asyncio.to_thread(self._load_payload, mw_id)
            if payload is None:
                raise MemoryOperationError(problem)
            digest = payload_hash(payload)
            result = await self.memory.retain_interaction(payload, content_hash=digest)
        except asyncio.CancelledError:
            # Synchronous on purpose: under level-triggered cancellation (anyio cancel scopes)
            # any further await would be cancelled too and the claim would stay held.
            release_claim(self.engine, mw_id, token, self.clock())
            raise
        except Exception as exc:
            kind, reason = _error_kind(exc)
            status = await asyncio.to_thread(fail_job, self.engine, mw_id, token, deleting=False, kind=kind,
                                             reason=reason, attempts=attempts, policy=self.policy, now=self.clock())
            if kind == "unexpected":
                logger.exception("memory write job %s failed unexpectedly", mw_id)
            else:
                logger.warning("memory write job %s failed (%s)", mw_id, kind)
            if status == "delete_pending":
                self.kick()
            return status
        status = await asyncio.to_thread(
            finish_write, self.engine, mw_id, token, digest, result.bank_id, result.document_id,
            [r.memory_id for r in result.memory_refs], self.clock())
        logger.info("memory write job %s -> %s (%d refs)", mw_id, status, len(result.memory_refs))
        if status in ("pending", "delete_pending"):
            self.kick()
        return status

    async def process_delete(self, mw_id: str) -> str | None:
        claimed = await asyncio.to_thread(claim_delete, self.engine, mw_id, self.policy, self.clock())
        if claimed is None:
            return None
        token, attempts = claimed
        with Session(self.engine) as session:
            mw = session.get(MemoryWrite, mw_id)
            customer_id, interaction_id = mw.customer_id, mw.source_id
        try:
            await self.memory.forget_interaction(customer_id, interaction_id)
        except asyncio.CancelledError:
            # Synchronous on purpose: under level-triggered cancellation (anyio cancel scopes)
            # any further await would be cancelled too and the claim would stay held.
            release_claim(self.engine, mw_id, token, self.clock())
            raise
        except Exception as exc:
            kind, reason = _error_kind(exc)
            status = await asyncio.to_thread(fail_job, self.engine, mw_id, token, deleting=True, kind=kind,
                                             reason=reason, attempts=attempts, policy=self.policy, now=self.clock())
            logger.warning("memory delete job %s failed (%s)", mw_id, kind)
            return status
        status = await asyncio.to_thread(finish_delete, self.engine, mw_id, token, self.clock())
        logger.info("memory delete job %s -> %s", mw_id, status)
        return status

    async def process_interaction(self, interaction_id: str) -> str | None:
        with Session(self.engine) as session:
            mw = get_memory_write(session, interaction_id)
            mw_id = mw.id if mw else None
        return await self.process_write(mw_id) if mw_id else None

    async def run_once(self) -> dict[str, int]:
        """Process every job that is due now (bounded by batch size per pass)."""
        counts = {"write": 0, "delete": 0, "expired": 0}
        now = self.clock()
        counts["expired"] = await asyncio.to_thread(expire_exhausted_leases, self.engine, now)
        seen: set[str] = set()
        while True:
            jobs = [j for j in await asyncio.to_thread(due_jobs, self.engine, self.clock(), self.batch_size)
                    if j[0] not in seen]
            if not jobs:
                return counts
            for mw_id, kind in jobs:
                seen.add(mw_id)  # at most one job per row per pass: no hot loops
                if kind == "write":
                    await self.process_write(mw_id)
                else:
                    await self.process_delete(mw_id)
                counts[kind] += 1

    # -- lifecycle ----------------------------------------------------------------

    def kick(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        logger.info("memory sync worker started (poll every %ss)", self.poll_seconds)
        while not self._stopping:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("memory sync worker pass failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass
            self._wake.clear()

    async def start(self) -> None:
        if not self.running:
            self._stopping = False
            self._task = asyncio.create_task(self._loop(), name="memory-sync-worker")

    async def stop(self, grace_seconds: float = 5.0) -> None:
        self._stopping = True
        self.kick()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=grace_seconds)
        except TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


def _claim(engine: Engine, mw_id: str) -> bool:
    """Claim a row for writing with the default policy (kept for the M2 concurrency test)."""
    return claim_write(engine, mw_id, SyncPolicy(), utcnow()) is not None
