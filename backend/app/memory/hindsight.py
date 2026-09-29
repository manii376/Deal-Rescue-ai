"""MemoryService backed by Hindsight (one bank per customer + shared outcomes bank).

All Hindsight I/O goes through app/services/hindsight_memory.HindsightMemory.

Reliability:
  * timeouts: HINDSIGHT_TIMEOUT_SECONDS per request (client-level);
  * retries: bounded (MEMORY_MAX_ATTEMPTS) and only for connectivity / 429 / 503 errors.
    Retrying a retain is safe because the document id is deterministic and every retain
    is sent with update_mode="replace";
  * errors surface as MemoryUnavailableError / MemoryOperationError with safe messages.

Verification status (see docs/m2-completion-report.md): routing, retain in "chunks"
mode, source-ref listing and recall were exercised against a real server without an
LLM. LLM extraction and reflect are NOT verified (no API key available).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC

from app.memory.service import BankRouter
from app.memory.types import (
    EvidenceHit,
    InteractionMemoryPayload,
    MemoryOperationError,
    MemoryRef,
    MemoryStatus,
    MemoryUnavailableError,
    RecallRequest,
    ReflectionResult,
    ReflectRequest,
    RetainResult,
)
from app.services.hindsight_memory import (
    HindsightMemory,
    MemoryRecord,
    MemoryRequestError,
    deal_tag,
    kind_tag,
)
from app.services.hindsight_memory import (
    MemoryUnavailableError as ClientUnavailable,
)
from app.services.hindsight_memory import customer_tag as _customer_tag

logger = logging.getLogger("deal_rescue.memory")

CUSTOMER_RETAIN_MISSION = (
    "Sales CRM memory for ONE customer. Keep requirements, budgets, stakeholders and their "
    "roles, commitments, objections, decisions and the dates they were stated or changed."
)
CUSTOMER_REFLECT_MISSION = (
    "I answer questions about one customer using only recorded memories. I say when the "
    "evidence is missing or contradictory and never invent statements."
)
RETRYABLE_STATUS = {429, 502, 503, 504}
# Documented Hindsight Cloud errors that must never be retried automatically. Messages are
# fixed text (no response body), so nothing from the server or the key can leak into logs.
NON_RETRYABLE_EXPLAINED = {
    401: ("auth_failed", "Hindsight authentication failed: API key invalid, expired or revoked"),
    402: ("no_credits", "Hindsight Cloud credits exhausted: add credits, then retry"),
    403: ("forbidden", "Hindsight refused access: the API key is not permitted for this bank or operation"),
}
DOCUMENT_FORMAT = "interaction.v2"

BankClientFactory = Callable[[str], HindsightMemory]


class HindsightMemoryService:
    backend = "hindsight"

    def __init__(self, settings, router: BankRouter, client_factory: BankClientFactory | None = None,
                 retry_base_delay: float = 0.5):
        self.settings = settings
        self.router = router
        self._max_attempts = settings.memory_max_attempts
        self._retry_base_delay = retry_base_delay
        self._extraction_mode = settings.hindsight_extraction_mode
        self.deployment = settings.hindsight_deployment
        self.base_url = settings.hindsight_effective_base_url
        # Cloud key only in cloud mode, only to the Cloud URL (Settings.hindsight_client_key).
        api_key = settings.hindsight_client_key()
        self._factory: BankClientFactory = client_factory or (
            lambda bank_id: HindsightMemory(self.base_url, bank_id, api_key=api_key,
                                            timeout_seconds=settings.hindsight_timeout_seconds)
        )
        self._clients: dict[str, HindsightMemory] = {}
        self._ensured: set[str] = set()
        self._ensure_lock = asyncio.Lock()

    # -- plumbing ------------------------------------------------------------

    def _client(self, bank_id: str) -> HindsightMemory:
        if bank_id not in self._clients:
            self._clients[bank_id] = self._factory(bank_id)
        return self._clients[bank_id]

    async def _with_retries(self, operation: str, func):
        delay = self._retry_base_delay
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await func()
            except ClientUnavailable as exc:
                last = MemoryUnavailableError(str(exc))
            except MemoryRequestError as exc:
                if exc.status in NON_RETRYABLE_EXPLAINED:
                    kind, meaning = NON_RETRYABLE_EXPLAINED[exc.status]
                    raise MemoryOperationError(f"{meaning} (HTTP {exc.status}) during {operation}", kind=kind) from None
                if exc.status not in RETRYABLE_STATUS:
                    raise MemoryOperationError(str(exc)) from None
                last = MemoryUnavailableError(str(exc))
            if attempt < self._max_attempts:
                logger.info("memory %s attempt %d/%d failed; retrying", operation, attempt, self._max_attempts)
                await asyncio.sleep(delay)
                delay *= 2
        raise last

    async def _ensure_customer_bank(self, bank_id: str) -> None:
        if bank_id in self._ensured:
            return
        async with self._ensure_lock:
            if bank_id in self._ensured:
                return
            await self._with_retries("create_bank", lambda: self._client(bank_id).ensure_bank(
                name=f"Deal Rescue customer memory ({bank_id})",
                retain_mission=CUSTOMER_RETAIN_MISSION,
                reflect_mission=CUSTOMER_REFLECT_MISSION,
                retain_extraction_mode=self._extraction_mode,
            ))
            self._ensured.add(bank_id)

    @staticmethod
    def _to_hit(bank_id: str, rec: MemoryRecord) -> EvidenceHit:
        return EvidenceHit(
            ref=MemoryRef(bank_id=bank_id, memory_id=rec.id),
            text=rec.text,
            memory_type=rec.type,
            document_id=rec.document_id,
            source_type=rec.metadata.get("source_type"),
            source_id=rec.metadata.get("source_id"),
            occurred_start=rec.occurred_start,
            mentioned_at=rec.mentioned_at,
        )

    @staticmethod
    def render_interaction(payload: InteractionMemoryPayload) -> str:
        """Memory document text. Contains only this customer's own records."""
        when = payload.occurred_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"Customer: {payload.customer_name}"]
        if payload.deal_title:
            lines.append(f"Deal: {payload.deal_title}")
        lines.append(f"{payload.channel.capitalize()} on {when}" + (f": {payload.title}" if payload.title else ""))
        if payload.participants:
            lines.append("Participants: " + "; ".join(payload.participants))
        lines.append("Notes:")
        lines.append(payload.notes)
        return "\n".join(lines)

    # -- MemoryService ---------------------------------------------------------

    async def status(self) -> MemoryStatus:
        probe = self._client(self.router.outcomes_bank_id)
        notes = [f"Deployment: {self.deployment}.",
                 "Customer isolation uses one bank per customer; tags only filter within a bank."]
        version = None
        if self.deployment == "cloud":
            notes.append("Hindsight Cloud runs fact extraction and reflect server-side; usage is billed to the "
                         "account's credits (HTTP 402 when exhausted).")
            try:
                await self._with_retries("status probe", probe.probe)
                reason = None
            except MemoryOperationError as exc:
                reason = exc.reason if exc.kind != "rejected" else "Hindsight Cloud rejected the status probe"
            except MemoryUnavailableError:
                reason = f"Hindsight Cloud is not reachable at {self.base_url}"
        else:
            health = await probe.health()
            version = health.api_version
            if self._extraction_mode == "chunks":
                notes.append("HINDSIGHT_EXTRACTION_MODE=chunks: memories are stored without LLM extraction (test mode).")
            else:
                notes.append("Retain and reflect need the server's LLM key; this is not checked until a write.")
            if not health.reachable:
                reason = f"Hindsight is not reachable at {self.base_url}"
            elif not health.healthy:
                reason = "Hindsight is reachable but reports unhealthy"
            else:
                reason = None
        return MemoryStatus(backend="hindsight", available=reason is None, reason=reason,
                            customer_bank_prefix=self.router.customer_bank_prefix,
                            outcomes_bank_id=self.router.outcomes_bank_id,
                            server_version=version, notes=notes)

    async def retain_interaction(self, payload: InteractionMemoryPayload,
                                 content_hash: str | None = None) -> RetainResult:
        # The bank comes from the validated owner of the record, never from its text.
        bank_id = self.router.customer_bank(payload.customer_id)
        document_id = self.router.interaction_document_id(payload.interaction_id)
        await self._ensure_customer_bank(bank_id)
        tags = [_customer_tag(payload.customer_id), kind_tag("interaction")]
        if payload.deal_id:
            tags.append(deal_tag(payload.deal_id))
        metadata = {
            "customer_id": payload.customer_id,
            "source_type": "interaction",
            "source_id": payload.interaction_id,
            "occurred_at": payload.occurred_at.astimezone(UTC).isoformat(),
            "document_format": DOCUMENT_FORMAT,
        }
        if payload.deal_id:
            metadata["deal_id"] = payload.deal_id
        if content_hash:
            metadata["content_hash"] = content_hash
        client = self._client(bank_id)
        # update_mode="replace": re-retaining the same document id replaces its content and
        # memories (verified on Hindsight 0.10.1 in no-LLM chunks mode by the M3 live test).
        outcome = await self._with_retries("retain", lambda: client.retain(
            self.render_interaction(payload),
            document_id=document_id,
            tags=tags,
            timestamp=payload.occurred_at,
            context=f"{payload.channel} notes",
            metadata=metadata,
            update_mode="replace",
        ))
        records = await self._with_retries("list_memories", lambda: client.list_memories(document_id=document_id))
        return RetainResult(bank_id=bank_id, document_id=document_id,
                            memory_refs=[MemoryRef(bank_id=bank_id, memory_id=r.id) for r in records],
                            usage=outcome.usage)

    async def forget_interaction(self, customer_id: str, interaction_id: str) -> bool:
        bank_id = self.router.customer_bank(customer_id)
        document_id = self.router.interaction_document_id(interaction_id)
        return await self._with_retries(
            "delete_document", lambda: self._client(bank_id).delete_document(document_id))

    async def recall_customer_evidence(self, request: RecallRequest) -> list[EvidenceHit]:
        bank_id = self.router.customer_bank(request.customer_id)
        tags, match = ([deal_tag(request.deal_id)], "all_strict") if request.deal_id else ([], "any")
        try:
            records = await self._with_retries("recall", lambda: self._client(bank_id).recall(
                request.query, tags=tags, tags_match=match))
        except MemoryOperationError as exc:
            if "HTTP 404" in exc.reason:  # bank not created yet: nothing retained for this customer
                return []
            raise
        hits = []
        for rec in records[: request.max_results]:
            owner = rec.metadata.get("customer_id")
            if owner is not None and owner != request.customer_id:
                # Should be impossible with per-customer banks; never return it.
                logger.error("memory %s in bank %s has foreign customer metadata; dropped", rec.id, bank_id)
                continue
            hits.append(self._to_hit(bank_id, rec))
        return hits

    async def reflect_customer(self, request: ReflectRequest) -> ReflectionResult:
        bank_id = self.router.customer_bank(request.customer_id)
        client = self._client(bank_id)
        outcome = await self._with_retries("reflect", lambda: client.reflect(
            request.question, tags=[], tags_match="any"))
        evidence, unresolved = [], []
        for fact in outcome.evidence:
            # ReflectFact has no tags/document/metadata; resolve the id inside this bank.
            full = await self._with_retries("get_memory", lambda fid=fact.id: client.get_memory(fid))
            if full is None:
                unresolved.append(fact.id)
            else:
                evidence.append(self._to_hit(bank_id, full))
        return ReflectionResult(text=outcome.text, evidence=evidence, unresolved_memory_ids=unresolved,
                                usage=outcome.usage)

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
