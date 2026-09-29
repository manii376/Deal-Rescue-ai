# M2 completion report — backend foundation (2026-09-28)

## Summary

The backend now has a real domain model, validated CRUD APIs for five entities, and two
service boundaries (memory, AI) that let the app run fully **without any API key** today.

| Area | State |
|---|---|
| Domain + SQLite persistence, FKs, indexes, migrations | **Implemented and tested** |
| CRUD APIs (customers, deals, stakeholders, interactions, commitments) | **Implemented and tested** |
| Ownership / cross-customer protection (API + database) | **Implemented and tested** |
| MemoryService boundary, per-customer bank routing, sync ledger, idempotent writes | **Implemented**; tested with an in-memory fake **and** against the real Hindsight 0.10.1 server in no-LLM `chunks` mode |
| Hindsight LLM extraction, reflect, observations | **Not verified** (no API key; see M1 final report) |
| AIService (timeouts, retries, evidence enforcement) | **Implemented and tested** with fake providers |
| AI provider "none" | **Implemented** (explicit unavailable status) |
| AI providers "anthropic", "ollama" | **Planned only** — recognised by config, report "not implemented" |
| Frontend typed API client | **Implemented** (type-checked, builds); no new UI |

No paid API call was made. No Docker volume was deleted. Earlier reports were not modified.

## Architecture

```
routes (app/api/routers)          validation, ownership, HTTP mapping only
  │  SessionDep ─────────────────► SQLite (app/domain/models.py) — source of truth
  │  MemoryDep  ─► MemoryService   app/memory/service.py (Protocol)
  │                 ├ DisabledMemoryService (default)
  │                 └ HindsightMemoryService ─► app/services/hindsight_memory.py ─► Hindsight
  │                    BankRouter: customer -> "<prefix><customer_id>", outcomes bank
  │               app/memory/sync.py: memory_writes ledger + memory_source_refs
  └  AIDep      ─► AIService (timeouts, retries, evidence rules) ─► AIProvider (Protocol)
                                                                     └ UnavailableProvider (none / planned)
```

Reused from M0/M1: `config.py` (extended), `services/hindsight_memory.py` (+`delete_document`),
health endpoints, compose file, test fixtures (rewritten for isolation), frontend status page.

## Domain model

| Table | Key fields | Integrity |
|---|---|---|
| `customers` | id `cus_…`, name, industry, notes, is_synthetic | index on name |
| `deals` | customer_id, title, stage, status, value_minor + currency, expected_close_date | FK customer; unique(id, customer_id); index(customer_id, status) |
| `stakeholders` | customer_id, name, role, influence, email, priorities (JSON) | FK customer; unique(id, customer_id) |
| `interactions` | customer_id, deal_id?, occurred_at (UTC), channel, title, notes, idempotency_key | **composite FK (deal_id, customer_id) → deals**; unique(customer_id, idempotency_key); indexes on (customer_id, occurred_at), (deal_id, occurred_at) |
| `interaction_participants` | interaction_id, stakeholder_id, customer_id | composite FKs to interaction **and** stakeholder of the same customer |
| `commitments` | customer_id, deal_id, source_interaction_id?, description, owner_party, due_date, status, completed_at | composite FKs to deal and source interaction of the same customer; indexes (deal_id, status), (customer_id, due_date) |
| `memory_writes` | source (type, id), bank_id, document_id, status, attempts, content_hash, stored_hash, last_error | unique(source), unique(bank_id, document_id) |
| `memory_source_refs` | **bank_id + memory_id**, document_id, source (type, id), customer_id | unique(bank_id, memory_id) |
| `schema_migrations` | version, applied_at | — |

Conventions: prefixed random ids; timestamps stored as UTC and returned timezone-aware (`…Z`);
naive datetimes rejected; money as integer minor units + ISO currency; enum values validated at the API.
`PRAGMA foreign_keys=ON` is set on every connection (verified by test).

**Schema evolution:** `app/db/migrations.py` — versioned, backup-before-upgrade, refuses
unversioned or newer databases. Rules for adding a migration are in its docstring; the upgrade path is
tested with a simulated v2 migration.

**Delete policy:** deletes that would orphan history are refused with 409 (customer with children,
deal with interactions/commitments, stakeholder who participated, interaction that sources a commitment).

## API endpoints

All errors: `{"error": {"code", "message", "details"}}`. Codes used: `not_found` (404),
`conflict` / `integrity_error` (409), `validation_error` / `invalid_reference` (422),
`memory_backend_error` (502), `memory_unavailable` (503), `internal_error` (500). Validation errors never
echo submitted input.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/health/dependencies` | Hindsight reachability diagnostic (M0) |
| GET | `/api/system/capabilities` | database version, memory status, AI status |
| GET | `/api/system/memory` | memory backend status |
| GET/POST | `/api/customers` | filters `q`, `is_synthetic`; pagination |
| GET/PATCH/DELETE | `/api/customers/{cid}` | |
| GET/POST | `/api/customers/{cid}/deals` | filters `status`, `stage` |
| GET/PATCH/DELETE | `/api/customers/{cid}/deals/{id}` | merged-record validation on PATCH |
| GET | `/api/deals` | read-only, all customers; filters `status`, `stage` |
| GET/POST | `/api/customers/{cid}/stakeholders` | filters `q`, `influence` |
| GET/PATCH/DELETE | `/api/customers/{cid}/stakeholders/{id}` | |
| GET/POST | `/api/customers/{cid}/interactions` | filters `deal_id`, `channel`, `occurred_from/to`; `Idempotency-Key` header |
| GET/PATCH/DELETE | `/api/customers/{cid}/interactions/{id}` | response includes memory write status |
| GET | `/api/customers/{cid}/interactions/{id}/memory` | ledger + `(bank_id, memory_id)` refs |
| POST | `/api/customers/{cid}/interactions/{id}/memory/sync` | idempotent retry; 503 if memory disabled |
| GET/POST | `/api/customers/{cid}/commitments` | filters `deal_id`, `status`, `owner_party`, `due_before`, `overdue` |
| GET/PATCH/DELETE | `/api/customers/{cid}/commitments/{id}` | `completed_at` managed from status |
| POST | `/api/customers/{cid}/memory/recall` | 503 when unavailable; hits carry bank/memory ids + verified source |
| POST | `/api/customers/{cid}/memory/reflect` | 503 when unavailable; result kind `inference` |

AI operations have **no HTTP endpoints yet** (by design: they need evidence assembly, which is M5
work). Their contracts and service are in `app/ai/`.

## Memory boundary details

* **Isolation:** one bank per customer (`HINDSIGHT_CUSTOMER_BANK_PREFIX` + customer id); tags only
  filter within a bank (e.g. by deal). Recall additionally drops any hit whose metadata names another
  customer (defence in depth; logged as an error).
* **No fallback:** disabled/unreachable memory → 503 `memory_unavailable`; never SQLite keyword search.
* **Commit first:** the interaction and its `memory_writes` row commit together; the Hindsight write
  runs afterwards (background task). Failures set `status=failed` + sanitized `last_error` and never
  touch the business record.
* **Idempotency:** deterministic document id `interaction:<id>` (Hindsight replaces the document);
  content hash skips unchanged writes; a conditional UPDATE claim prevents concurrent double writes;
  `Idempotency-Key` makes client retries of POST return the original interaction.
* **Retries/timeouts:** bounded (`MEMORY_MAX_ATTEMPTS`) exponential backoff for connectivity, 429 and
  5xx-gateway errors only; LLM/auth errors are not retried. Per-request timeout `HINDSIGHT_TIMEOUT_SECONDS`.
* **Source references:** `memory_source_refs` stores `(bank_id, memory_id)` for every memory extracted
  from an interaction; recall/reflect link hits to their source only through this ledger.
* **Deletion:** deleting an interaction deletes its Hindsight document; failure is recorded as `delete_failed`.

## AI boundary details

* Typed Pydantic contracts for briefing, objection analysis, follow-up draft, strategy comparison.
* Every output item carries an epistemic kind; drafts and strategy comparisons are `hypothetical`;
  objections are always `inference`.
* AIService enforces: unknown citations removed and reported; `recorded`/`statement` claims without
  matching evidence relabelled `inference`; strategy verdicts only ever weakened; unknown strategies dropped;
  timeout + bounded retries on transient errors.
* Ollama adapter design (planned): `httpx` to `/api/chat` with JSON-schema `format`, `/api/tags` for status,
  model from `OLLAMA_MODEL` (no implicit pulls). Documented in `app/ai/providers/__init__.py`.

## Tests

| Command | Exit | Result |
|---|---|---|
| `cd backend && uv run pytest -q -rs` | **0** | **74 passed, 3 skipped** (opt-in live tests) |
| `HINDSIGHT_LIVE=1 uv run pytest -q -rs tests/test_hindsight_live.py` (Hindsight running on a placeholder key, no LLM) | **0** | 2 passed, 1 skipped (`HINDSIGHT_LIVE_LLM`, needs a real key) |
| `cd frontend && npm run build` | **0** | tsc + vite build |
| `cd frontend && npm run lint` | **0** | oxlint clean |
| Manual: uvicorn with no keys + curl | — | health 200; CRUD 201; recall 503 `memory_unavailable`; cross-customer 404; server log contains no notes content |

New test files (M2): `test_crud_customers_deals.py`, `test_crud_stakeholders_interactions_commitments.py`,
`test_ownership.py`, `test_memory_service.py` (+ `fakes.py`), `test_ai_service.py`,
`test_migrations_and_errors.py`, `test_no_keys_startup.py`; `test_hindsight_live.py` gained
`test_memory_service_against_real_server_without_llm`; `conftest.py` rewritten so every test uses its own
SQLite file and a keyless config independent of `.env`.

Coverage of the M2 requirements:
- success paths, invalid data, missing records, relationship integrity, cross-customer attempts (path,
  body references, PATCH, listings, direct DB inserts);
- startup + full CRUD with no Anthropic key, both with memory disabled and with Hindsight configured but unreachable;
- memory unavailable/failed/retry/idempotency/deletion paths; AI unavailable/planned/timeout/retry paths.

The live no-LLM test additionally confirmed on the real server: 53-character bank ids containing `_` are
accepted; retain → one `(bank_id, memory_id)` ref; unchanged resync does not rewrite (attempts stays 1);
recall hit maps to its source interaction; delete removes the document.

## Files changed in M2

Backend (new): `app/db/{engine,migrations,types}.py`, `app/domain/{models,enums}.py`,
`app/api/{deps,errors,pagination,schemas}.py`, `app/api/routers/{customers,deals,stakeholders,interactions,commitments,memory,system}.py`,
`app/memory/{types,service,hindsight,sync}.py`, `app/ai/{schemas,provider,service}.py`, `app/ai/providers/__init__.py`,
package `__init__.py` files, the test files above.
Backend (modified): `app/config.py`, `app/main.py` (app factory), `app/services/hindsight_memory.py`
(`delete_document`), `tests/conftest.py`, `tests/test_hindsight_live.py` (test added), `pyproject.toml` / `uv.lock` (`sqlmodel` 0.0.47).
Frontend: `src/api/types.ts`, `src/api/client.ts` (new), `src/App.tsx` (uses the client; shows memory/AI status).
Repo: `.env.example` (new variables), `.gitignore` (`backend/data/`, WAL/backup files), `README.md`,
this report. `docs/ui-ux-direction.md` and all earlier reports are unchanged.

## Limitations

1. No LLM-backed behaviour is verified: Hindsight extraction, reflect, observations, latency (blocked on a key).
2. The Anthropic and Ollama providers are not implemented; AI operations have no HTTP endpoints yet.
3. Background memory writes use FastAPI BackgroundTasks in the API process: a crash mid-write leaves the
   row `in_progress` (reclaimable after 10 min via `/memory/sync`) or `pending` (needs a manual sync). There
   is no periodic retry worker yet.
4. `delete_failed` memory rows of deleted interactions are not exposed through the API for retry.
5. The outcomes bank is configured and routed but nothing writes to it until outcomes exist (M6).
6. SQLite + WAL suits one backend process; multiple workers would need care (or Postgres).
7. `/api/deals` lists all customers' deals — acceptable for a single-team demo; a multi-tenant deployment
   would need user/tenant authorization (no auth exists in the app yet).

## Future integration steps

1. **Anthropic adapter:** add `app/ai/providers/anthropic.py` implementing `AIProvider` with the
   official SDK, select with `AI_PROVIDER=anthropic`; verify the model id with `models.retrieve` first.
2. **Ollama adapter:** `app/ai/providers/ollama.py` per the design above; `AI_PROVIDER=ollama`, `OLLAMA_MODEL`.
3. **Hindsight with LLM:** set `ANTHROPIC_API_KEY`, `MEMORY_BACKEND=hindsight`; run the M1 spike and the
   live tests with `HINDSIGHT_LIVE_LLM=1`; add observation-isolation probes for per-customer banks.
4. **Evidence assembly (M5):** build `EvidenceItem`s from SQL records (recorded), quotes (statement) and
   recall hits (memory), then expose AI endpoints that return 503 `ai_unavailable` when the provider is off.
5. **Outcomes (M6):** `retain_outcome` on the outcomes bank through the same ledger pattern.
