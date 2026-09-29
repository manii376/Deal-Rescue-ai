"""Provider-neutral types for the memory boundary."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MemoryUnavailableError(Exception):
    """Memory cannot be used right now (disabled, unreachable, misconfigured).

    ``reason`` is safe to show to users and to log: it never contains secrets or
    customer content.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class MemoryOperationError(Exception):
    """The memory backend answered but the operation failed (non-retryable).

    ``kind`` is recorded in the sync ledger (max 16 chars): "rejected" (generic),
    "auth_failed" (HTTP 401), "no_credits" (HTTP 402, Hindsight Cloud credits exhausted),
    "forbidden" (HTTP 403). None of these are retried automatically.
    """

    def __init__(self, reason: str, kind: str = "rejected"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


class MemoryRef(BaseModel):
    """A memory id is only meaningful together with its bank."""

    bank_id: str
    memory_id: str


class MemoryStatus(BaseModel):
    backend: Literal["disabled", "hindsight"]
    available: bool
    reason: str | None = None
    customer_bank_prefix: str | None = None
    outcomes_bank_id: str | None = None
    server_version: str | None = None
    notes: list[str] = []


class InteractionMemoryPayload(BaseModel):
    """Everything written to memory for one interaction.

    Built only from SQLite rows of the interaction's own customer (the path-validated
    owner); participants are guaranteed same-customer by composite foreign keys.
    """

    customer_id: str
    customer_name: str
    deal_id: str | None
    deal_title: str | None
    interaction_id: str
    occurred_at: datetime
    channel: str
    title: str | None
    notes: str
    participants: list[str] = Field(default_factory=list, description="'Name (role)' strings")


class RetainResult(BaseModel):
    bank_id: str
    document_id: str
    memory_refs: list[MemoryRef]
    usage: dict | None = Field(default=None, description="Token usage reported by Hindsight (diagnostics)")


class RecallRequest(BaseModel):
    customer_id: str
    query: str = Field(min_length=1, max_length=500)
    deal_id: str | None = None
    max_results: int = Field(default=10, ge=1, le=50)


class EvidenceHit(BaseModel):
    ref: MemoryRef
    text: str
    memory_type: str | None
    document_id: str | None
    source_type: str | None
    source_id: str | None
    occurred_start: str | None = None
    mentioned_at: str | None = None


class ReflectRequest(BaseModel):
    customer_id: str
    question: str = Field(min_length=1, max_length=1000)


class ReflectionResult(BaseModel):
    kind: Literal["inference"] = "inference"
    text: str
    evidence: list[EvidenceHit]
    unresolved_memory_ids: list[str] = Field(
        default_factory=list, description="Evidence ids the backend cited but that could not be looked up"
    )
    usage: dict | None = Field(default=None, description="Token usage reported by Hindsight (diagnostics)")
