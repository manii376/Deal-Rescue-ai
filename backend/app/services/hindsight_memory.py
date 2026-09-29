"""The only module that talks to Hindsight.

Everything else in the app uses the small dataclasses defined here, so the
generated client (``hindsight-client`` 0.10.1) can change without touching
routers or services. Only async client methods are used: the client's sync
wrappers call ``loop.run_until_complete`` and cannot run inside FastAPI's
event loop.

Verified against hindsight-client 0.10.1 and server image 0.10.1
(see docs/hindsight-notes.md).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import aiohttp
from hindsight_client import Hindsight
from hindsight_client_api.exceptions import ApiException

TagsMatch = Literal["any", "all", "any_strict", "all_strict", "exact"]

# Customer-scoped reads must exclude untagged memories, so the default is a
# strict mode ("any" would also return untagged memories).
DEFAULT_SCOPED_MATCH: TagsMatch = "all_strict"

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"hsk_[A-Za-z0-9_\-]+"),  # Hindsight Cloud API keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
]


def customer_tag(customer_id: str) -> str:
    return f"customer:{customer_id}"


def deal_tag(deal_id: str) -> str:
    return f"deal:{deal_id}"


def kind_tag(kind: str) -> str:
    return f"kind:{kind}"


class HindsightMemoryError(Exception):
    """Base class for Hindsight failures. Messages are safe to log and show."""


class MemoryUnavailableError(HindsightMemoryError):
    """Hindsight could not be reached (not running, wrong URL, timeout)."""


class MemoryRequestError(HindsightMemoryError):
    """Hindsight answered with an error status."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class HealthStatus:
    reachable: bool
    healthy: bool
    detail: dict[str, Any] = field(default_factory=dict)
    api_version: str | None = None


@dataclass(frozen=True)
class RetainOutcome:
    success: bool
    items_count: int
    latency_seconds: float
    operation_id: str | None
    usage: dict[str, Any] | None


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    text: str
    type: str | None
    document_id: str | None
    tags: list[str]
    metadata: dict[str, str]
    context: str | None
    occurred_start: str | None
    occurred_end: str | None
    mentioned_at: str | None


@dataclass(frozen=True)
class ReflectOutcome:
    text: str
    evidence: list[MemoryRecord]
    mental_model_ids: list[str]
    directive_ids: list[str]
    usage: dict[str, Any] | None


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return {"value": str(value)}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _record(item: Any) -> MemoryRecord:
    """Normalise RecallResult, ReflectFact and list_memories items."""
    get = item.get if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
    return MemoryRecord(
        id=str(get("id")),
        text=get("text") or "",
        type=get("type") or get("fact_type"),
        document_id=get("document_id"),
        tags=list(get("tags") or []),
        metadata=dict(get("metadata") or {}),
        context=get("context"),
        occurred_start=_iso(get("occurred_start")),
        occurred_end=_iso(get("occurred_end")),
        mentioned_at=_iso(get("mentioned_at")),
    )


class HindsightMemory:
    """Async facade over the Hindsight client for one memory bank."""

    def __init__(
        self,
        base_url: str,
        bank_id: str,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bank_id = bank_id
        self._secrets = [api_key] if api_key else []
        self._client = Hindsight(base_url=self.base_url, api_key=api_key, timeout=timeout_seconds)
        # The generated low-level APIs do not inherit the client timeout; pass it per call.
        self._timeout = timeout_seconds

    async def __aenter__(self) -> HindsightMemory:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, operation: str, coro: Any) -> Any:
        try:
            return await coro
        except ApiException as exc:
            body = _redact(str(exc.body or "")[:300], self._secrets)
            raise MemoryRequestError(
                f"Hindsight {operation} failed with HTTP {exc.status}: {body}", exc.status
            ) from None
        except aiohttp.ClientResponseError as exc:
            # Raised by client helpers that call aiohttp directly (e.g. create_bank uses
            # raise_for_status). It is an HTTP answer, not a connectivity failure, so keep its
            # status (401/402/403 must not be retried as "unavailable"). Fixed text: no URL, body or key.
            raise MemoryRequestError(f"Hindsight {operation} failed with HTTP {exc.status}", exc.status) from None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise MemoryUnavailableError(
                f"Hindsight {operation} failed: cannot reach {self.base_url} ({type(exc).__name__})"
            ) from None

    # -- Service ---------------------------------------------------------------

    async def health(self) -> HealthStatus:
        """Never raises for connectivity problems; reports them instead."""
        try:
            detail = await self._call("health", self._client.monitoring.health_endpoint_health_get(_request_timeout=self._timeout))
        except MemoryUnavailableError:
            return HealthStatus(reachable=False, healthy=False)
        except MemoryRequestError as exc:
            return HealthStatus(reachable=True, healthy=False, detail={"error": str(exc)})
        detail_dict = _to_dict(detail) or {}
        version = None
        try:
            version = (await self._call("version", self._client.aget_version())).api_version
        except HindsightMemoryError:
            pass
        healthy = str(detail_dict.get("status", "")).lower() in {"healthy", "ok"}
        return HealthStatus(reachable=True, healthy=healthy, detail=detail_dict, api_version=version)

    async def probe(self) -> None:
        """Cheapest authenticated call (list at most one bank); raises on auth/credit/network errors.

        Used for Hindsight Cloud status, where an unauthenticated /health is not documented.
        The response (bank names) is discarded.
        """
        await self._call("list_banks", self._client.banks.list_banks(limit=1, _request_timeout=self._timeout))

    async def ensure_bank(
        self,
        name: str,
        retain_mission: str,
        reflect_mission: str,
        *,
        retain_extraction_mode: str | None = None,
        enable_observations: bool | None = None,
    ) -> None:
        # None leaves the server default in place. "chunks" extraction stores
        # text without calling the LLM (verified in the 0.10.1 server source).
        await self._call(
            "create_bank",
            self._client.acreate_bank(
                self.bank_id,
                name=name,
                retain_mission=retain_mission,
                reflect_mission=reflect_mission,
                retain_extraction_mode=retain_extraction_mode,
                enable_observations=enable_observations,
            ),
        )

    async def delete_bank(self) -> None:
        await self._call("delete_bank", self._client.adelete_bank(self.bank_id))

    # -- Write -----------------------------------------------------------------

    async def retain(
        self,
        content: str,
        *,
        document_id: str,
        tags: list[str],
        timestamp: datetime | None = None,
        context: str | None = None,
        metadata: dict[str, str] | None = None,
        retain_async: bool = False,
        update_mode: Literal["replace", "append"] | None = None,
    ) -> RetainOutcome:
        if not content.strip():
            raise ValueError("content must not be empty")
        if not tags:
            raise ValueError("tags are required so the memory can be scoped")
        started = time.perf_counter()
        response = await self._call(
            "retain",
            self._client.aretain(
                self.bank_id,
                content,
                timestamp=timestamp,
                context=context,
                document_id=document_id,
                metadata=metadata,
                tags=tags,
                update_mode=update_mode,
                retain_async=retain_async,
            ),
        )
        return RetainOutcome(
            success=bool(response.success),
            items_count=int(response.items_count or 0),
            latency_seconds=time.perf_counter() - started,
            operation_id=response.operation_id,
            usage=_to_dict(response.usage),
        )

    # -- Read ------------------------------------------------------------------

    async def recall(
        self,
        query: str,
        *,
        tags: list[str],
        tags_match: TagsMatch = DEFAULT_SCOPED_MATCH,
        types: list[str] | None = None,
        budget: str = "mid",
        max_tokens: int = 4096,
    ) -> list[MemoryRecord]:
        response = await self._call(
            "recall",
            self._client.arecall(
                self.bank_id,
                query,
                types=types,
                budget=budget,
                max_tokens=max_tokens,
                tags=tags,
                tags_match=tags_match,
            ),
        )
        return [_record(r) for r in (response.results or [])]

    async def reflect(
        self,
        query: str,
        *,
        tags: list[str],
        tags_match: TagsMatch = DEFAULT_SCOPED_MATCH,
        budget: str = "low",
    ) -> ReflectOutcome:
        response = await self._call(
            "reflect",
            self._client.areflect(
                self.bank_id,
                query,
                budget=budget,
                tags=tags,
                tags_match=tags_match,
                include_facts=True,
            ),
        )
        based_on = response.based_on
        return ReflectOutcome(
            text=response.text or "",
            evidence=[_record(m) for m in (based_on.memories or [])] if based_on else [],
            mental_model_ids=[str(m.id) for m in (based_on.mental_models or [])] if based_on else [],
            directive_ids=[str(d.id) for d in (based_on.directives or [])] if based_on else [],
            usage=_to_dict(response.usage),
        )

    async def list_memories(
        self,
        *,
        document_id: str | None = None,
        tags: list[str] | None = None,
        tags_match: TagsMatch | None = None,
        memory_type: str | None = None,
    ) -> list[MemoryRecord]:
        # The convenience wrapper has no document/tag filters, so this uses the
        # generated MemoryApi.list_memories (GET /v1/default/banks/{id}/memories/list).
        response = await self._call(
            "list_memories",
            self._client.memory.list_memories(
                self.bank_id,
                type=memory_type,
                document_id=document_id,
                tags=tags,
                tags_match=tags_match,
                _request_timeout=self._timeout,
            ),
        )
        return [_record(item) for item in (response.items or [])]

    async def delete_document(self, document_id: str) -> bool:
        """DELETE /v1/default/banks/{id}/documents/{document_id} (removes its memories).

        Returns False if the document (or bank) does not exist.
        """
        try:
            await self._call(
                "delete_document", self._client.documents.delete_document(self.bank_id, document_id,
                                                      _request_timeout=self._timeout)
            )
        except MemoryRequestError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        """GET /v1/default/banks/{id}/memories/{memory_id}; None if not in this bank (404).

        Reflect evidence (ReflectFact) has no tags/document_id/metadata, so this is
        how an evidence id is mapped back to its source record.
        """
        try:
            raw = await self._call("get_memory", self._client.memory.get_memory(self.bank_id, memory_id,
                                                                  _request_timeout=self._timeout))
        except MemoryRequestError as exc:
            if exc.status == 404:
                return None
            raise
        return _record(raw)

    async def observation_history(self, memory_id: str) -> Any | None:
        """GET /v1/default/banks/{id}/memories/{memory_id}/history.

        Returns the raw payload (the generated client types it as ``object``),
        or None when the memory has no history (404).
        """
        try:
            return await self._call(
                "observation_history",
                self._client.memory.get_observation_history(self.bank_id, memory_id, _request_timeout=self._timeout),
            )
        except MemoryRequestError as exc:
            if exc.status == 404:
                return None
            raise


def from_settings(settings: Any) -> HindsightMemory:
    return HindsightMemory(
        base_url=settings.hindsight_effective_base_url,
        bank_id=settings.hindsight_bank_id,
        api_key=settings.hindsight_client_key(),
        timeout_seconds=settings.hindsight_timeout_seconds,
    )


__all__ = [
    "DEFAULT_SCOPED_MATCH",
    "HealthStatus",
    "HindsightMemory",
    "HindsightMemoryError",
    "MemoryRecord",
    "MemoryRequestError",
    "MemoryUnavailableError",
    "ReflectOutcome",
    "RetainOutcome",
    "customer_tag",
    "deal_tag",
    "from_settings",
    "kind_tag",
]
