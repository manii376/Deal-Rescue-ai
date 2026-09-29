"""SQLite tables: the source of truth for exact business records.

Ownership is enforced twice: by the API (path-scoped lookups) and by the database
through composite foreign keys such as (deal_id, customer_id) -> deals(id, customer_id),
so a child record can never point at another customer's deal, interaction or stakeholder.
"""

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, ForeignKeyConstraint, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.db.types import UTCDateTime, utcnow


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _created() -> datetime:
    return utcnow()


class Customer(SQLModel, table=True):
    __tablename__ = "customers"

    id: str = Field(default_factory=lambda: new_id("cus"), primary_key=True, max_length=40)
    name: str = Field(max_length=200, index=True)
    industry: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, sa_type=Text)
    is_synthetic: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)


class Deal(SQLModel, table=True):
    __tablename__ = "deals"
    __table_args__ = (
        UniqueConstraint("id", "customer_id", name="uq_deals_id_customer"),
        Index("ix_deals_customer_status", "customer_id", "status"),
    )

    id: str = Field(default_factory=lambda: new_id("deal"), primary_key=True, max_length=40)
    customer_id: str = Field(foreign_key="customers.id", index=True, max_length=40)
    title: str = Field(max_length=200)
    stage: str = Field(max_length=32)
    status: str = Field(default="open", max_length=32)
    value_minor: int | None = Field(default=None)
    currency: str | None = Field(default=None, max_length=3)
    expected_close_date: date | None = Field(default=None)
    owner_name: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)


class Stakeholder(SQLModel, table=True):
    __tablename__ = "stakeholders"
    __table_args__ = (
        UniqueConstraint("id", "customer_id", name="uq_stakeholders_id_customer"),
        Index("ix_stakeholders_customer_name", "customer_id", "name"),
    )

    id: str = Field(default_factory=lambda: new_id("stk"), primary_key=True, max_length=40)
    customer_id: str = Field(foreign_key="customers.id", max_length=40)
    name: str = Field(max_length=200)
    role: str | None = Field(default=None, max_length=200)
    influence: str = Field(default="unknown", max_length=16)
    email: str | None = Field(default=None, max_length=254)
    priorities: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)


class Interaction(SQLModel, table=True):
    __tablename__ = "interactions"
    __table_args__ = (
        UniqueConstraint("id", "customer_id", name="uq_interactions_id_customer"),
        UniqueConstraint("customer_id", "idempotency_key", name="uq_interactions_idempotency"),
        ForeignKeyConstraint(["deal_id", "customer_id"], ["deals.id", "deals.customer_id"],
                             name="fk_interactions_deal_same_customer"),
        Index("ix_interactions_customer_occurred", "customer_id", "occurred_at"),
        Index("ix_interactions_deal_occurred", "deal_id", "occurred_at"),
    )

    id: str = Field(default_factory=lambda: new_id("int"), primary_key=True, max_length=40)
    customer_id: str = Field(foreign_key="customers.id", max_length=40)
    deal_id: str | None = Field(default=None, max_length=40)
    occurred_at: datetime = Field(sa_type=UTCDateTime, nullable=False)
    channel: str = Field(max_length=16)
    title: str | None = Field(default=None, max_length=200)
    notes: str = Field(sa_type=Text)
    idempotency_key: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)


class InteractionParticipant(SQLModel, table=True):
    __tablename__ = "interaction_participants"
    __table_args__ = (
        ForeignKeyConstraint(["interaction_id", "customer_id"],
                             ["interactions.id", "interactions.customer_id"],
                             ondelete="CASCADE", name="fk_participants_interaction_same_customer"),
        ForeignKeyConstraint(["stakeholder_id", "customer_id"],
                             ["stakeholders.id", "stakeholders.customer_id"],
                             name="fk_participants_stakeholder_same_customer"),
        Index("ix_participants_stakeholder", "stakeholder_id"),
    )

    interaction_id: str = Field(primary_key=True, max_length=40)
    stakeholder_id: str = Field(primary_key=True, max_length=40)
    customer_id: str = Field(foreign_key="customers.id", max_length=40)


class Commitment(SQLModel, table=True):
    __tablename__ = "commitments"
    __table_args__ = (
        ForeignKeyConstraint(["deal_id", "customer_id"], ["deals.id", "deals.customer_id"],
                             name="fk_commitments_deal_same_customer"),
        ForeignKeyConstraint(["source_interaction_id", "customer_id"],
                             ["interactions.id", "interactions.customer_id"],
                             name="fk_commitments_interaction_same_customer"),
        Index("ix_commitments_deal_status", "deal_id", "status"),
        Index("ix_commitments_customer_due", "customer_id", "due_date"),
        Index("ix_commitments_source_interaction", "source_interaction_id"),
    )

    id: str = Field(default_factory=lambda: new_id("com"), primary_key=True, max_length=40)
    customer_id: str = Field(foreign_key="customers.id", max_length=40)
    deal_id: str = Field(max_length=40)
    source_interaction_id: str | None = Field(default=None, max_length=40)
    description: str = Field(max_length=1000)
    owner_party: str = Field(max_length=16)
    owner_name: str | None = Field(default=None, max_length=200)
    due_date: date | None = Field(default=None)
    status: str = Field(default="open", max_length=16)
    completed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)


class MemoryWrite(SQLModel, table=True):
    """Ledger of what was (or should be) written to Hindsight for one source record.

    One row per source record. ``document_id`` is deterministic, so retries replace the
    same Hindsight document instead of adding duplicates; ``content_hash`` lets an
    unchanged record skip re-writing entirely. ``source_id`` is deliberately not a
    foreign key so the row outlives a deleted interaction until its memory is removed.
    """

    __tablename__ = "memory_writes"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_memory_writes_source"),
        UniqueConstraint("bank_id", "document_id", name="uq_memory_writes_document"),
        Index("ix_memory_writes_customer_status", "customer_id", "status"),
        Index("ix_memory_writes_status_next_attempt", "status", "next_attempt_at"),  # schema v2
    )

    id: str = Field(default_factory=lambda: new_id("mw"), primary_key=True, max_length=40)
    customer_id: str = Field(foreign_key="customers.id", max_length=40)
    source_type: str = Field(max_length=32)
    source_id: str = Field(max_length=40)
    bank_id: str = Field(max_length=64)
    document_id: str = Field(max_length=120)
    status: str = Field(max_length=16)
    attempts: int = Field(default=0)
    content_hash: str | None = Field(default=None, max_length=64)
    stored_hash: str | None = Field(default=None, max_length=64)
    last_error: str | None = Field(default=None, max_length=500)
    last_attempt_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    stored_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
    # -- schema v2 (M3): durable, lease-based synchronisation -------------------------
    # A worker owns a row only while claim_token matches and the lease has not expired.
    claim_token: str | None = Field(default=None, max_length=36)
    lease_expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    # Automatic retries: allowed while attempts < max_attempts and next_attempt_at is due.
    next_attempt_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    max_attempts: int = Field(default=5, sa_column_kwargs={"server_default": "5"})
    # "unavailable" (transient, auto-retried) | "rejected" | "unexpected" (manual retry only)
    last_error_kind: str | None = Field(default=None, max_length=16)
    # Tombstone: the source record was deleted; the row may never return to a write state.
    source_deleted_at: datetime | None = Field(default=None, sa_type=UTCDateTime)


class MemorySourceRef(SQLModel, table=True):
    """A Hindsight memory that was extracted from one of our source records.

    bank_id and memory_id are always stored together: memory ids are bank-scoped
    (verified in M1: looking an id up in another bank returns 404).
    """

    __tablename__ = "memory_source_refs"
    __table_args__ = (
        UniqueConstraint("bank_id", "memory_id", name="uq_memory_source_refs_memory"),
        ForeignKeyConstraint(["memory_write_id"], ["memory_writes.id"], ondelete="CASCADE",
                             name="fk_memory_source_refs_write"),
        Index("ix_memory_source_refs_source", "source_type", "source_id"),
    )

    id: str = Field(default_factory=lambda: new_id("msr"), primary_key=True, max_length=40)
    memory_write_id: str = Field(max_length=40)
    customer_id: str = Field(foreign_key="customers.id", max_length=40)
    bank_id: str = Field(max_length=64)
    memory_id: str = Field(max_length=64)
    document_id: str = Field(max_length=120)
    source_type: str = Field(max_length=32)
    source_id: str = Field(max_length=40)
    created_at: datetime = Field(default_factory=_created, sa_type=UTCDateTime, nullable=False)
