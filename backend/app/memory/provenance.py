"""Evidence provenance for memory hits (shared by the memory API and the AI evidence assembler).

A hit is "linked" only if its (bank_id, memory_id) is recorded in our own
memory_source_refs ledger for this customer; timestamps and deal come from SQLite, not
from memory content. Hits from other banks are dropped; hits whose source was deleted
are dropped; hits whose record changed after the memory was written are flagged
(memory_is_current=False).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Session, select

from app.domain.models import Interaction, MemorySourceRef, MemoryWrite
from app.memory.types import EvidenceHit


class SourceLink(BaseModel):
    source_type: str
    source_id: str
    occurred_at: datetime | None = None  # from the SQLite record, not from memory
    deal_id: str | None = None
    memory_is_current: bool  # False: the record changed after this memory was written


class EvidenceHitRead(EvidenceHit):
    provenance: Literal["linked", "unlinked"]
    source: SourceLink | None = None


def link_hits(session: Session, memory, customer_id: str, hits: list[EvidenceHit]):
    own_bank = memory.router.customer_bank(customer_id)
    out, deleted, foreign = [], 0, 0
    for hit in hits:
        if hit.ref.bank_id != own_bank:
            foreign += 1
            continue
        ref = session.exec(select(MemorySourceRef).where(
            MemorySourceRef.bank_id == hit.ref.bank_id, MemorySourceRef.memory_id == hit.ref.memory_id,
            MemorySourceRef.customer_id == customer_id)).first()
        if ref is None:
            out.append(EvidenceHitRead(**hit.model_dump(), provenance="unlinked"))
            continue
        mw = session.get(MemoryWrite, ref.memory_write_id)
        interaction = session.get(Interaction, ref.source_id)
        if (mw is None or mw.source_deleted_at is not None or interaction is None
                or interaction.customer_id != customer_id):
            deleted += 1
            continue
        out.append(EvidenceHitRead(**hit.model_dump(), provenance="linked", source=SourceLink(
            source_type=ref.source_type, source_id=ref.source_id, occurred_at=interaction.occurred_at,
            deal_id=interaction.deal_id, memory_is_current=mw.stored_hash == mw.content_hash)))
    return out, deleted, foreign
