"""MemoryService: the only way the application talks to long-term memory.

Implementations:
  * DisabledMemoryService  (default; MEMORY_BACKEND=disabled) - every operation raises
    MemoryUnavailableError. There is deliberately no SQLite keyword-search fallback:
    callers must show "memory unavailable", not pretend to have semantic recall.
  * HindsightMemoryService (MEMORY_BACKEND=hindsight) - app/memory/hindsight.py.

Isolation is structural: every customer has its own bank (BankRouter), so a query can
only ever reach one customer's memories. Tags are used for filtering *within* a
customer's bank (e.g. by deal), never as the isolation mechanism.
"""

import re
from typing import Protocol, runtime_checkable

from app.memory.types import (
    EvidenceHit,
    InteractionMemoryPayload,
    MemoryStatus,
    MemoryUnavailableError,
    RecallRequest,
    ReflectionResult,
    ReflectRequest,
    RetainResult,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class BankRouter:
    """Maps customers to their private bank and names the shared outcomes bank."""

    def __init__(self, customer_bank_prefix: str, outcomes_bank_id: str):
        self.customer_bank_prefix = customer_bank_prefix
        self.outcomes_bank_id = outcomes_bank_id

    def customer_bank(self, customer_id: str) -> str:
        if not _SAFE_ID.match(customer_id):
            raise ValueError("customer id contains characters not allowed in a bank id")
        bank_id = f"{self.customer_bank_prefix}{customer_id}"
        if len(bank_id) > 64:
            raise ValueError("customer bank id would exceed 64 characters")
        return bank_id

    @staticmethod
    def interaction_document_id(interaction_id: str) -> str:
        # Deterministic: re-retaining the same interaction replaces this document.
        return f"interaction:{interaction_id}"


@runtime_checkable
class MemoryService(Protocol):
    backend: str
    router: BankRouter

    async def status(self) -> MemoryStatus: ...

    async def retain_interaction(self, payload: InteractionMemoryPayload,
                                 content_hash: str | None = None) -> RetainResult: ...

    async def forget_interaction(self, customer_id: str, interaction_id: str) -> bool: ...

    async def recall_customer_evidence(self, request: RecallRequest) -> list[EvidenceHit]: ...

    async def reflect_customer(self, request: ReflectRequest) -> ReflectionResult: ...

    async def aclose(self) -> None: ...


class DisabledMemoryService:
    backend = "disabled"
    REASON = "Memory is disabled (MEMORY_BACKEND=disabled). Business records are still saved."

    def __init__(self, router: BankRouter, reason: str | None = None):
        self.router = router
        self.REASON = reason or DisabledMemoryService.REASON

    async def status(self) -> MemoryStatus:
        return MemoryStatus(backend="disabled", available=False, reason=self.REASON,
                            customer_bank_prefix=self.router.customer_bank_prefix,
                            outcomes_bank_id=self.router.outcomes_bank_id)

    async def retain_interaction(self, payload: InteractionMemoryPayload,
                                 content_hash: str | None = None) -> RetainResult:
        raise MemoryUnavailableError(self.REASON)

    async def forget_interaction(self, customer_id: str, interaction_id: str) -> bool:
        raise MemoryUnavailableError(self.REASON)

    async def recall_customer_evidence(self, request: RecallRequest) -> list[EvidenceHit]:
        raise MemoryUnavailableError(self.REASON)

    async def reflect_customer(self, request: ReflectRequest) -> ReflectionResult:
        raise MemoryUnavailableError(self.REASON)

    async def aclose(self) -> None:
        return None


def build_memory_service(settings) -> MemoryService:
    router = BankRouter(settings.hindsight_customer_bank_prefix, settings.hindsight_outcomes_bank_id)
    if settings.memory_backend == "hindsight":
        if settings.hindsight_deployment == "cloud" and settings.hindsight_api_key is None:
            # Start normally; interactions stay "disabled" in the ledger and are synchronised
            # automatically once a key is configured and the app restarted.
            return DisabledMemoryService(router, reason=(
                "Hindsight Cloud is selected (HINDSIGHT_DEPLOYMENT=cloud) but HINDSIGHT_API_KEY is not set. "
                "Business records are still saved."))
        from app.memory.hindsight import HindsightMemoryService

        return HindsightMemoryService(settings, router)
    return DisabledMemoryService(router)
