"""API request/response schemas.

Inputs forbid unknown fields, so a client cannot smuggle ``customer_id`` or other
ownership fields into a body; ownership always comes from the URL path.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, ClassVar

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.domain.enums import Channel, CommitmentStatus, DealStage, DealStatus, Influence, MemoryWriteStatus, OwnerParty

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
Currency = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z]{3}$"), AfterValidator(str.upper)]
RecordId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FUTURE = timedelta(days=1)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateModel(InputModel):
    """Partial update. Fields listed in NOT_NULL may be omitted but not set to null."""

    NOT_NULL: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _no_null_for_required(self):
        bad = [f for f in self.NOT_NULL if f in self.model_fields_set and getattr(self, f) is None]
        if bad:
            raise ValueError(f"fields cannot be null: {', '.join(bad)}")
        return self


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _check_money(value_minor: int | None, currency: str | None) -> None:
    if (value_minor is None) != (currency is None):
        raise ValueError("value_minor and currency must be provided together")


# -- Customers -----------------------------------------------------------------


class CustomerCreate(InputModel):
    name: Name
    industry: ShortText | None = None
    notes: LongText | None = None
    is_synthetic: bool = False


class CustomerUpdate(UpdateModel):
    NOT_NULL = ("name", "is_synthetic")
    name: Name | None = None
    industry: ShortText | None = None
    notes: LongText | None = None
    is_synthetic: bool | None = None


class CustomerSummary(ReadModel):
    """Directory entry returned by the (unscoped) customer list.

    Deliberately excludes free-text ``notes``: one list response must not carry every
    customer's private content. Notes are only returned by GET /api/customers/{id}.
    """

    id: str
    name: str
    industry: str | None
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime


class CustomerRead(CustomerSummary):
    notes: str | None


# -- Deals ---------------------------------------------------------------------


class DealCreate(InputModel):
    title: Name
    stage: DealStage
    status: DealStatus = "open"
    value_minor: int | None = Field(default=None, ge=0, le=10**15, description="Amount in minor units (cents)")
    currency: Currency | None = None
    expected_close_date: date | None = None
    owner_name: Name | None = None

    @model_validator(mode="after")
    def _money(self):
        _check_money(self.value_minor, self.currency)
        return self


class DealUpdate(UpdateModel):
    NOT_NULL = ("title", "stage", "status")
    title: Name | None = None
    stage: DealStage | None = None
    status: DealStatus | None = None
    value_minor: int | None = Field(default=None, ge=0, le=10**15)
    currency: Currency | None = None
    expected_close_date: date | None = None
    owner_name: Name | None = None


class DealRead(ReadModel):
    id: str
    customer_id: str
    title: str
    stage: str
    status: str
    value_minor: int | None
    currency: str | None
    expected_close_date: date | None
    owner_name: str | None
    created_at: datetime
    updated_at: datetime


# -- Stakeholders --------------------------------------------------------------


def _validate_email(value: str | None) -> str | None:
    if value is not None and not _EMAIL.match(value):
        raise ValueError("invalid email address")
    return value


Priorities = Annotated[list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]],
                       Field(max_length=20)]


class StakeholderCreate(InputModel):
    name: Name
    role: Name | None = None
    influence: Influence = "unknown"
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] | None = None
    priorities: Priorities = []

    _email = field_validator("email")(_validate_email)


class StakeholderUpdate(UpdateModel):
    NOT_NULL = ("name", "influence", "priorities")
    name: Name | None = None
    role: Name | None = None
    influence: Influence | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] | None = None
    priorities: Priorities | None = None

    _email = field_validator("email")(_validate_email)


class StakeholderRead(ReadModel):
    id: str
    customer_id: str
    name: str
    role: str | None
    influence: str
    email: str | None
    priorities: list[str]
    created_at: datetime
    updated_at: datetime


# -- Interactions --------------------------------------------------------------


def _not_far_future(value: datetime | None) -> datetime | None:
    if value is not None and value > datetime.now(UTC) + MAX_FUTURE:
        raise ValueError("occurred_at cannot be more than 1 day in the future")
    return value


ParticipantIds = Annotated[list[RecordId], Field(max_length=50)]


class InteractionCreate(InputModel):
    occurred_at: AwareDatetime
    channel: Channel
    notes: LongText
    title: Name | None = None
    deal_id: RecordId | None = None
    participant_ids: ParticipantIds = []

    _future = field_validator("occurred_at")(_not_far_future)


class InteractionUpdate(UpdateModel):
    NOT_NULL = ("occurred_at", "channel", "notes", "participant_ids")
    occurred_at: AwareDatetime | None = None
    channel: Channel | None = None
    notes: LongText | None = None
    title: Name | None = None
    deal_id: RecordId | None = None
    participant_ids: ParticipantIds | None = None

    _future = field_validator("occurred_at")(_not_far_future)


class MemoryRefRead(BaseModel):
    bank_id: str
    memory_id: str


class MemoryWriteRead(BaseModel):
    status: MemoryWriteStatus
    bank_id: str | None
    document_id: str | None
    attempts: int
    last_error: str | None
    last_attempt_at: datetime | None
    stored_at: datetime | None
    memory_refs: list[MemoryRefRead] = []


class InteractionRead(ReadModel):
    id: str
    customer_id: str
    deal_id: str | None
    occurred_at: datetime
    channel: str
    title: str | None
    notes: str
    participant_ids: list[str]
    memory: MemoryWriteRead | None
    created_at: datetime
    updated_at: datetime


# -- Commitments ---------------------------------------------------------------


class CommitmentCreate(InputModel):
    deal_id: RecordId
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    owner_party: OwnerParty
    owner_name: Name | None = None
    due_date: date | None = None
    status: CommitmentStatus = "open"
    source_interaction_id: RecordId | None = None


class CommitmentUpdate(UpdateModel):
    NOT_NULL = ("description", "owner_party", "status")
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None
    owner_party: OwnerParty | None = None
    owner_name: Name | None = None
    due_date: date | None = None
    status: CommitmentStatus | None = None


class CommitmentRead(ReadModel):
    id: str
    customer_id: str
    deal_id: str
    source_interaction_id: str | None
    description: str
    owner_party: str
    owner_name: str | None
    due_date: date | None
    status: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# -- Memory --------------------------------------------------------------------


class RecallBody(InputModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    deal_id: RecordId | None = None
    max_results: int = Field(default=10, ge=1, le=50)


class ReflectBody(InputModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
