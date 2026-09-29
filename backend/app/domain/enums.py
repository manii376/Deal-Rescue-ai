"""Allowed values for enumerated string columns (validated at the API boundary)."""

from typing import Literal

DealStage = Literal["discovery", "qualification", "proposal", "negotiation", "closing"]
DealStatus = Literal["open", "won", "lost", "no_decision"]
Influence = Literal["low", "medium", "high", "unknown"]
Channel = Literal["call", "meeting", "email", "message", "note", "other"]
OwnerParty = Literal["us", "customer"]
CommitmentStatus = Literal["open", "done", "cancelled"]

# memory_writes.status lifecycle:
#   disabled      memory backend off; nothing written (can be synced later)
#   pending       needs a write (new or changed content)
#   in_progress   a worker has claimed it
#   stored        Hindsight accepted the write; stored_hash == content that was written
#   failed        last attempt failed; retried automatically only if transient and within max_attempts
#   delete_pending  source deleted; its memory document must be removed
#   deleting      a worker has claimed the deletion
#   deleted       memory document removed (or was never written)
#   delete_failed last deletion attempt failed; retried like "failed"
MemoryWriteStatus = Literal[
    "disabled", "pending", "in_progress", "stored", "failed",
    "delete_pending", "deleting", "deleted", "delete_failed",
]
