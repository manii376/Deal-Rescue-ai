"""In-memory stand-in for app.services.hindsight_memory.HindsightMemory (one per bank).

Implements only the methods HindsightMemoryService uses. Behaviour mirrors what M1
verified on the real server: retain with an existing document_id replaces that
document; memory ids are unique per bank; get_memory in another bank returns None.
"""

import itertools
from dataclasses import dataclass, field

from app.services.hindsight_memory import (
    HealthStatus,
    MemoryRecord,
    MemoryRequestError,
    MemoryUnavailableError,
    ReflectOutcome,
    RetainOutcome,
)

_ids = itertools.count(1)


@dataclass
class FakeServer:
    banks: dict[str, dict[str, list[MemoryRecord]]] = field(default_factory=dict)  # bank -> doc -> records
    created_banks: list[str] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)  # (operation, bank)
    fail_next: list[Exception] = field(default_factory=list)  # raised by the next retain/recall/reflect
    healthy: bool = True
    update_modes: list[str | None] = field(default_factory=list)  # update_mode of every retain
    # If set, retain waits here after being called (to hold a write "in flight" in race tests).
    retain_gate: object | None = None  # asyncio.Event
    retain_started: object | None = None  # asyncio.Event set when a gated retain begins

    def factory(self, bank_id: str) -> "FakeBank":
        return FakeBank(self, bank_id)

    def retain_count(self) -> int:
        return sum(1 for op, _ in self.calls if op == "retain")

    def all_records(self, bank_id: str) -> list[MemoryRecord]:
        return [r for recs in self.banks.get(bank_id, {}).values() for r in recs]


class FakeBank:
    def __init__(self, server: FakeServer, bank_id: str):
        self.server = server
        self.bank_id = bank_id

    def _maybe_fail(self, op: str) -> None:
        self.server.calls.append((op, self.bank_id))
        if self.server.fail_next:
            raise self.server.fail_next.pop(0)

    async def health(self) -> HealthStatus:
        return HealthStatus(reachable=True, healthy=self.server.healthy, api_version="fake")

    async def ensure_bank(self, name, retain_mission, reflect_mission, **_) -> None:
        self.server.calls.append(("ensure_bank", self.bank_id))
        self.server.created_banks.append(self.bank_id)
        self.server.banks.setdefault(self.bank_id, {})

    async def retain(self, content, *, document_id, tags, timestamp=None, context=None, metadata=None,
                     retain_async=False, update_mode=None) -> RetainOutcome:
        self._maybe_fail("retain")
        self.server.update_modes.append(update_mode)
        if self.server.retain_gate is not None:
            if self.server.retain_started is not None:
                self.server.retain_started.set()
            await self.server.retain_gate.wait()
        if update_mode not in (None, "replace"):
            raise AssertionError("the fake only models replace semantics")
        record = MemoryRecord(id=f"mem-{next(_ids)}", text=content, type="world", document_id=document_id,
                              tags=list(tags), metadata=dict(metadata or {}), context=context,
                              occurred_start=None, occurred_end=None,
                              mentioned_at=timestamp.isoformat() if timestamp else None)
        self.server.banks.setdefault(self.bank_id, {})[document_id] = [record]  # replace semantics
        return RetainOutcome(success=True, items_count=1, latency_seconds=0.0, operation_id=None, usage=None)

    async def list_memories(self, *, document_id=None, tags=None, tags_match=None, memory_type=None):
        self.server.calls.append(("list_memories", self.bank_id))
        docs = self.server.banks.get(self.bank_id, {})
        return list(docs.get(document_id, [])) if document_id else self.server.all_records(self.bank_id)

    async def recall(self, query, *, tags, tags_match="all_strict", **_) -> list[MemoryRecord]:
        self._maybe_fail("recall")
        if self.bank_id not in self.server.banks:
            raise MemoryRequestError("Hindsight recall failed with HTTP 404: bank not found", 404)
        recs = self.server.all_records(self.bank_id)
        if tags:
            recs = [r for r in recs if set(tags) <= set(r.tags)]
        return recs

    async def reflect(self, query, *, tags, tags_match="all_strict", budget="low") -> ReflectOutcome:
        self._maybe_fail("reflect")
        recs = self.server.all_records(self.bank_id)
        evidence = [MemoryRecord(id=r.id, text=r.text, type=r.type, document_id=None, tags=[], metadata={},
                                 context=None, occurred_start=None, occurred_end=None, mentioned_at=None)
                    for r in recs]
        # plus one cited id that does not exist in this bank
        evidence.append(MemoryRecord(id="mem-ghost", text="?", type="world", document_id=None, tags=[],
                                     metadata={}, context=None, occurred_start=None, occurred_end=None,
                                     mentioned_at=None))
        return ReflectOutcome(text=f"Answer from {len(recs)} memories", evidence=evidence,
                              mental_model_ids=[], directive_ids=[], usage=None)

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        self.server.calls.append(("get_memory", self.bank_id))
        return next((r for r in self.server.all_records(self.bank_id) if r.id == memory_id), None)

    async def delete_document(self, document_id: str) -> bool:
        self._maybe_fail("delete_document")
        return self.server.banks.get(self.bank_id, {}).pop(document_id, None) is not None

    async def aclose(self) -> None:
        return None


def unavailable() -> MemoryUnavailableError:
    return MemoryUnavailableError("Hindsight retain failed: cannot reach http://fake (ClientConnectorError)")


def llm_auth_failure() -> MemoryRequestError:
    return MemoryRequestError("Hindsight retain failed with HTTP 500: AuthenticationError 401", 500)
