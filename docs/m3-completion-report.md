# M3 completion report — reliable Hindsight memory pipeline (2026-09-28)

Earlier reports are unchanged. No Anthropic key was available; **no LLM request was made** and no fake
credential was used. The backend stayed on 127.0.0.1; no Docker volume was deleted.

## Summary

| Area | State |
|---|---|
| Interaction → customer bank synchronisation, commit-first | **Implemented, tested** (fake + real server, no-LLM mode) |
| Idempotency, concurrency, lost-update and resurrection protection | **Implemented, tested** (deterministic race tests) |
| Durable recovery across restarts; bounded retries; failure states | **Implemented, tested** (fake restart test + real outage/restart) |
| Retry of failed writes **and deletions** via API | **Implemented, tested** |
| Per-customer isolation and evidence provenance | **Implemented, tested** (fake + real server, no-LLM mode) |
| Update → replace of the Hindsight document | **Verified on Hindsight 0.10.1** in no-LLM mode |
| LLM fact extraction, reflect, observations, LLM latency | **NOT verified — BLOCKED** (no API key) |

## What was wrong with the M2 design (found during inspection)

1. **Not durable:** writes ran only as in-process `BackgroundTasks`; a restart lost pending work, and an
   interrupted job could only be reclaimed manually after 10 minutes. No automatic retries.
2. **Deleted memories could be resurrected:** deleting an interaction while its retain was in flight let the
   deletion run first and the retain re-create the document, then mark the ledger `stored`.
3. **Lost-update window:** a finishing sync read-then-wrote the ledger, so an edit committed in between could
   be marked `stored` with a stale hash.
4. **Deletion failures were unreachable:** `delete_failed` rows of deleted interactions had no API.
5. The memory document had no customer/deal context; recall hits carried no verified timestamp.

## Design (implemented)

### Ledger state machine (`app/memory/sync.py`, `memory_writes` schema v2)

```
pending ──claim──► in_progress ──ok──► stored ─(edit)─► pending
   ▲                   │  └──ok but content changed──► pending
   │                   ├──fail (transient)──► failed ──backoff due──► claim again (≤ max_attempts)
   │                   ├──fail (rejected/unexpected)──► failed (explicit retry only)
   │                   └──source deleted meanwhile──► delete_pending
   └─ explicit retry (fresh budget)
delete_pending ──claim──► deleting ──ok──► deleted
                              └──fail──► delete_failed ──backoff / explicit retry──► deleting
```

* **Claims and leases:** a job claims its row with a conditional `UPDATE` that sets a random `claim_token`
  and `lease_expires_at`; every later transition is `UPDATE … WHERE claim_token = :token`. A worker whose lease
  was taken over cannot overwrite newer state. The lease must exceed
  `HINDSIGHT_TIMEOUT_SECONDS × MEMORY_MAX_ATTEMPTS` (validated at startup).
* **Atomic completion:** success sets `stored` only if `content_hash` still equals what was written, else
  `pending`; if the source was deleted meanwhile it becomes `delete_pending`. Source refs are replaced in the
  same transaction.
* **Tombstone:** deleting an interaction sets `source_deleted_at` in the same transaction as the delete. A
  tombstoned row can never be claimed for writing. Rows never sent to memory go straight to `deleted`.
* **Bounded retries:** only transient ("unavailable") failures are retried automatically, with exponential
  backoff (`MEMORY_RETRY_BASE_SECONDS`, capped at `MEMORY_RETRY_MAX_SECONDS`) until
  `attempts == max_attempts`. Rejected/unexpected failures and exhausted rows need an explicit retry, which grants
  a fresh budget (`max_attempts += MEMORY_SYNC_MAX_ATTEMPTS`). Jobs whose lease expired with no attempts left are
  marked failed instead of being reclaimed forever. Backoff counts all attempts on the row (write + delete).
* **Cancellation:** a job cancelled at shutdown releases its claim (back to `pending`, attempt not counted). The
  release is synchronous because under level-triggered cancellation (anyio) a further `await` would itself be
  cancelled — this bug was found by a test and fixed.
* **Durable worker:** `MemorySyncWorker` starts with the app when `MEMORY_BACKEND=hindsight` and
  `MEMORY_WORKER_ENABLED=true`. Its first pass recovers pending, due, lease-expired and delete-pending rows; then
  it polls (`MEMORY_WORKER_POLL_SECONDS`) and wakes early after writes. The request-time fast path
  (`BackgroundTasks`) is kept for latency; claims make the two safe to run together. DB work in the worker runs
  in a thread so SQLite locks never block the event loop.
* **Rows created while memory was disabled** (`disabled`) are picked up automatically once the backend is
  enabled.

### Update/replace semantics (actual Hindsight API)

Every retain uses the fixed document id `interaction:<id>` with `update_mode="replace"` (now passed explicitly).
Verified on the real server (no-LLM mode): after an edit, the document holds exactly one memory with the new
text, the old memory id is gone, and our source refs point at the new id. Customer-name and deal-title changes
are part of the document, so renaming re-queues the affected interactions (deal stage changes do not).

### Memory document and provenance

Document text: `Customer: <name>`, `Deal: <title>` (if any), channel + UTC time + title, same-customer
participants, notes. Metadata: `customer_id`, `deal_id`, `source_type`, `source_id`, `occurred_at`,
`content_hash`, `document_format=interaction.v2`. The bank is always
`router.customer_bank(interaction.customer_id)` from the validated SQLite owner; the worker also checks that it
matches the ledger's `bank_id`/`document_id` (a changed `HINDSIGHT_CUSTOMER_BANK_PREFIX` fails as "rejected"
instead of writing to another bank).

Recall/reflect hits now carry `provenance` (`linked` only when `(bank_id, memory_id)` is in our
`memory_source_refs` for this customer) and `source {source_type, source_id, occurred_at, deal_id,
memory_is_current}` with the timestamp taken from SQLite. Hits from any other bank are dropped
(`excluded_foreign_bank`), hits whose source was deleted are dropped (`excluded_deleted_sources`), and a hit whose
record changed after it was written is flagged `memory_is_current: false`. There is still no keyword-search
fallback.

## API changes

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/customers/{cid}/memory/writes` | Ledger for this customer (filters `status`, `source_id`), includes deleted sources |
| GET | `/api/customers/{cid}/memory/writes/{id}` | One ledger entry |
| POST | `/api/customers/{cid}/memory/writes/{id}/retry` | Retry a failed write or deletion (202; 200 if nothing to do; 503 if memory disabled) |
| GET | `/api/system/memory/queue` | Counts by status, due work, rows needing manual retry, worker running — no customer data |

Changed responses: recall/reflect hits gained `provenance` and richer `source`; `MemoryWriteStatus` gained
`deleting`. The scope-security guard now also covers `MemoryLedgerEntry` and `SourceLink` (both only under
`/api/customers/{customer_id}`). The typed frontend client was updated accordingly (no UI changes).

## Schema v2

`memory_writes` gained `claim_token`, `lease_expires_at`, `next_attempt_at`, `max_attempts` (default 5),
`last_error_kind`, `source_deleted_at` and index `ix_memory_writes_status_next_attempt`. Additive migration;
v1 rows interrupted mid-write become `pending`. Tested by migrating a database built from the captured v1 schema
(`tests/fixtures/schema_v1.sql`) and comparing every table's columns, nullability and indexes with a fresh v2
database.

## No-LLM Hindsight mode (new finding)

Hindsight 0.10.1 has an official `HINDSIGHT_API_LLM_PROVIDER=none` mode (read in the server source and run):
no key required, retain is forced to chunks mode, observations/consolidation are disabled and reflect returns
HTTP 400. M1/M2 had started the server with a placeholder key instead; that is no longer needed or used. Run with
`HINDSIGHT_LLM_PROVIDER=none docker compose up -d` (the compose file already reads this variable).

Consequence for testing: against such a server, "LLM-mode" retains silently store raw chunks, so LLM checks would
falsely pass. The LLM live test now asks the server for its resolved bank config and **fails as BLOCKED** if chunks
mode is forced.

## Tests

| Command | Exit | Result |
|---|---|---|
| `cd backend && uv run pytest -q -rs` | **0** | **102 passed, 7 skipped** (all 7 are opt-in live tests) |
| `HINDSIGHT_LIVE=1 uv run pytest -rs tests/test_hindsight_live.py` (Hindsight with `provider=none`, no key) | **0** | **6 passed, 1 skipped** (LLM test) |
| `HINDSIGHT_LIVE=1 HINDSIGHT_LIVE_LLM=1 … -k with_llm` | 1 | **BLOCKED**: "the Hindsight server has no LLM (chunks mode forced)" — *not* a pass |
| `tests/test_memory_pipeline_m3.py` run 10× after the cancellation fix | 0 ×10 | 21/21 each run (before the fix: intermittent failure, see Design) |
| `cd frontend && npm run build` / `npm run lint` | **0** / **0** | typed client compiles |

109 tests collected in total (M2 follow-up had 84).

New deterministic tests (`tests/test_memory_pipeline_m3.py`, 21): document contains only own-customer context and
provenance metadata; update replaces document and refs; bank resolved from owner not content; changed bank routing
rejected; 5 concurrent jobs → 1 write; delete during in-flight write never resurrects (and a stale job cannot
claim the tombstoned row); edit during in-flight write not lost; cancelled job releases its claim; transient
failures back off and stop at the bound, then explicit retry; rejected failures wait for explicit retry; delete
failures retried automatically and manually; deleting a never-synced interaction makes no memory call; exhausted
expired leases become failed; restart recovers lost fast-path and crashed jobs with exactly one write each; recall
isolated per customer with provenance and staleness flag; foreign-bank / deleted-source hits dropped, unlinked
observations kept as "unlinked"; ledger endpoints customer-scoped and queue endpoint free of customer data;
customer/deal rename re-syncs (stage change does not); CRUD unaffected when every memory call fails; v1→v2
migration; source refs store bank + memory id + source.

New live tests (real Hindsight 0.10.1, no-LLM mode): update replaces the document (1 memory, new text, new id,
refs updated); customer isolation both directions; **outage then restart** (unreachable server → `failed`;
restart against the real server with the worker on → `stored`, attempts 2); reflect without an LLM is an
explicit 502 `memory_backend_error` (server HTTP 400), not fabricated text.

Updated existing tests (intentional behaviour changes, not weakened): expected memory text now includes the
customer header; `source` assertions check the identity fields plus the new provenance fields; the generic
migration test now uses "current version + 1" instead of a hard-coded 2; conftest disables the background worker
loop so tests drive it explicitly.

Manual end-to-end (real processes, temporary database): backend running with the worker while Hindsight was
**stopped** → interaction saved (CRUD 200), memory `failed`, queue shows it; Hindsight started (`provider=none`)
→ the running worker stored it about 32 s later **without restarting the app** (attempt 9 of 30), and recall
returned a linked hit with source id and timestamp. The server log contained no interaction notes. The test bank
and database were deleted afterwards; Hindsight was stopped.

## Synchronisation guarantees (as implemented and tested)

1. Saving, editing or deleting an interaction never fails or rolls back because of memory.
2. At most one job works on a ledger row at a time; retries and concurrent triggers write each version once.
3. One Hindsight document per interaction; an edit replaces it; unchanged content is never re-sent.
4. The final state reflects the latest committed content (edits during a write re-queue it).
5. A deleted interaction's memory is removed and cannot be re-created by any queued, in-flight or retried job.
6. Work survives process restarts; transient failures retry automatically within a bound; everything else is
   visible in the ledger and can be retried explicitly, including deletions.
7. Each customer's memories live only in that customer's bank; recall/reflect only query that bank and drop
   anything that is not provably this customer's.

## Known limitations

1. **Single process:** leases make multiple workers safe in principle, but SQLite + in-process worker is designed
   for one backend process. Not tested with multiple processes.
2. **At-least-once toward Hindsight:** if the process dies after Hindsight accepted a retain but before the ledger
   was updated, the job is retried; `update_mode=replace` makes the repeat harmless (same document).
3. Backoff grows with all attempts on a row, so a deletion after several write attempts waits longer.
4. Renaming a customer re-sends every interaction of that customer (LLM cost once extraction is enabled).
5. Unlinked hits (e.g. future observations) cannot be traced to a record; they are labelled `unlinked`. With an
   LLM, observations derived from a deleted interaction might persist until consolidation updates them — unverified.
6. `/api/system/memory/queue` and the ledger have no authentication (the whole app has none; localhost only).
7. The outcomes bank is routed/configured but unused until M6.

## Unverified (LLM-dependent) — blocked on an API key

* LLM fact extraction on retain (multiple facts per document, entity linking) and how `replace` behaves for them.
* Reflect results and `based_on` evidence ids → `get_memory` mapping on a real server.
* Observation consolidation and whether observations stay isolated per customer bank; observation history.
* Real LLM retain/reflect latency and cost.
* `claude-haiku-4-5` accessibility for Hindsight and `claude-opus-5` for the app (account access).

To run them later: set `ANTHROPIC_API_KEY`, `HINDSIGHT_LLM_PROVIDER=anthropic`, empty `HINDSIGHT_EXTRACTION_MODE`,
`docker compose up -d --force-recreate`, then
`HINDSIGHT_LIVE=1 HINDSIGHT_LIVE_LLM=1 uv run pytest -rs tests/test_hindsight_live.py` and the M1 spike.

## Files changed in M3

Backend: `app/memory/sync.py` (rewritten), `app/memory/hindsight.py`, `app/memory/service.py`,
`app/memory/types.py`, `app/services/hindsight_memory.py` (`update_mode`), `app/domain/models.py`,
`app/domain/enums.py`, `app/db/migrations.py` (v2), `app/config.py`, `app/main.py`, `app/api/deps.py`,
`app/api/routers/{interactions,memory,customers,deals}.py`.
Tests: `tests/test_memory_pipeline_m3.py` (new), `tests/fixtures/schema_v1.sql` (new), `tests/fakes.py`,
`tests/conftest.py`, `tests/test_hindsight_live.py`, `tests/test_memory_service.py`,
`tests/test_migrations_and_errors.py`, `tests/test_scope_security.py`.
Frontend: `src/api/types.ts`, `src/api/client.ts`. Repo: `README.md`, `.env.example`, this report.
`docker-compose.yml`, `docs/ui-ux-direction.md` and all earlier reports are unchanged.
