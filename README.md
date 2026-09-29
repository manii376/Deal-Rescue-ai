# hack_with_hyderabad3.0
AI-powered sales intelligence agent using Hindsight persistent memory to recall customer interactions, identify stalled deals, and learn from past outcomes. It features AI deal briefings, personalized follow-ups, similar-deal recall, evidence-based recommendations, and a Deal Time Machine to explore alternative sales strategies.

> **Status: M4.5 (Hindsight Cloud verified).** CRUD, deterministic deal intelligence (M4) and the durable
> per-customer memory pipeline (M3) work **without any API key**. Memory can run on the self-hosted Docker
> server (default) or on **Hindsight Cloud**, where LLM-backed retain, recall, reflect and customer isolation
> were verified live with synthetic data. No win probabilities, no LLM features in the app yet.
> See `docs/m4.5-completion-report.md`. All data is synthetic.

## Layout

```
backend/
  app/domain/      SQLModel tables (source of truth), enums
  app/db/          engine (SQLite, FKs on), versioned migrations, UTC datetime type
  app/api/         routers, schemas, error envelope, pagination
  app/memory/      MemoryService boundary: disabled | hindsight (bank per customer), sync ledger
  app/intelligence/ deterministic rules (pure functions) + service + schemas
  app/ai/          AIProvider boundary + AIService (evidence rules); only "none" implemented
  app/services/hindsight_memory.py   the only module importing the Hindsight client
  scripts/         M1 spike + no-LLM checks
  tests/           pytest (unit + opt-in live)
frontend/          Vite + React + TS; typed API client in src/api/ (status page only)
docs/              direction, notes and milestone reports
docker-compose.yml Hindsight 0.10.1 (pinned by digest), bound to 127.0.0.1
```

## Quick start without any API key

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 20+.

```bash
cp .env.example .env            # defaults: AI_PROVIDER=none, MEMORY_BACKEND=disabled

cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8010    # API docs: http://127.0.0.1:8010/docs

cd ../frontend
npm install
npm run dev                     # http://127.0.0.1:5180 (proxies /api to the backend)
```

The database is created at `backend/data/deal_rescue.db` on first start (gitignored).
`GET /api/system/capabilities` reports what works: database, memory backend, AI provider.
Docker is not needed in this mode.

## Enabling memory (Hindsight)

Prerequisites: Docker Desktop (~4 GB free for the image).

**Without an API key (no-LLM mode)** — stores and searches raw interaction text; no fact extraction,
no observations, reflect returns an error:

```bash
# in .env: MEMORY_BACKEND=hindsight   HINDSIGHT_EXTRACTION_MODE=chunks   HINDSIGHT_LLM_PROVIDER=none
docker compose up -d
docker compose ps               # wait for "(healthy)", ~30 s
```

**With Hindsight Cloud** (LLM extraction and reflect run server-side; billed to your Cloud credits):

```bash
# in .env: MEMORY_BACKEND=hindsight   HINDSIGHT_DEPLOYMENT=cloud   HINDSIGHT_API_KEY=hsk_...   (no Docker needed)
# leave HINDSIGHT_EXTRACTION_MODE empty. A missing key starts the app with memory "disabled".
```

HTTP 401/402/403 from Cloud (bad key / credits exhausted / not permitted) are recorded in the memory ledger and
never retried automatically; fix the cause, then use the ledger's retry endpoint.

**With an Anthropic key on the self-hosted server** (LLM extraction; not yet verified in this project):
`ANTHROPIC_API_KEY=<key>`, `HINDSIGHT_LLM_PROVIDER=anthropic`, leave `HINDSIGHT_EXTRACTION_MODE` empty, then
`docker compose up -d --force-recreate`.

Each customer gets a private bank `<HINDSIGHT_CUSTOMER_BANK_PREFIX><customer_id>`; outcomes will use
the shared `HINDSIGHT_OUTCOMES_BANK_ID` (M6).

### How memory synchronisation behaves

* Saving an interaction never waits for or depends on Hindsight; the memory write happens afterwards.
* The ledger (`GET /api/customers/{cid}/memory/writes`) shows every write/deletion, its status, attempts,
  last error and next automatic retry. `POST …/memory/writes/{id}/retry` retries a failed write *or deletion*.
* Transient failures (Hindsight down, 429/5xx) retry automatically with backoff up to
  `MEMORY_SYNC_MAX_ATTEMPTS`; other failures wait for an explicit retry.
* Stopping the backend loses nothing: on start the worker resumes pending, due and interrupted jobs.
* `GET /api/system/memory/queue` gives counts only (no customer data).

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///…/backend/data/deal_rescue.db` | Business records (sqlite:/// only) |
| `AI_PROVIDER` | `none` | `none` (implemented, reports unavailable); `anthropic`, `ollama` planned |
| `AI_TIMEOUT_SECONDS` / `AI_MAX_RETRIES` | `60` / `2` | Applied by AIService to every provider |
| `ANTHROPIC_API_KEY` | empty | Planned Anthropic adapter + Hindsight's internal LLM key |
| `ANTHROPIC_MODEL` | `claude-opus-5` | App model (account access unverified) |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://127.0.0.1:11434` / empty | Planned local-model adapter |
| `INTEL_STALL_DAYS_DEFAULT` / `INTEL_STALL_DAYS_BY_STAGE` | `14` / JSON per stage | Inactivity thresholds |
| `INTEL_MEANINGFUL_CHANNELS` | `["call","meeting","email","message"]` | Channels that count as activity |
| `INTEL_DUE_SOON_DAYS` / `INTEL_TIMEZONE` | `7` / `UTC` | Due-soon window; timezone defining "today" |
| `MEMORY_BACKEND` | `disabled` | `disabled` or `hindsight` |
| `MEMORY_MAX_ATTEMPTS` | `3` | Quick retries inside one sync job (connectivity/429/5xx) |
| `MEMORY_WORKER_ENABLED` / `MEMORY_WORKER_POLL_SECONDS` | `true` / `5` | Durable sync worker |
| `MEMORY_SYNC_MAX_ATTEMPTS` | `5` | Automatic job attempts before an explicit retry is needed |
| `MEMORY_RETRY_BASE_SECONDS` / `MEMORY_RETRY_MAX_SECONDS` | `30` / `3600` | Exponential backoff |
| `MEMORY_LEASE_SECONDS` | `600` | Job lease; must exceed timeout × `MEMORY_MAX_ATTEMPTS` |
| `HINDSIGHT_CUSTOMER_BANK_PREFIX` | `deal-rescue-cust-` | Per-customer bank prefix |
| `HINDSIGHT_OUTCOMES_BANK_ID` | `deal-rescue-outcomes` | Shared outcomes bank |
| `HINDSIGHT_EXTRACTION_MODE` | empty | Empty with an LLM; `chunks` for the no-LLM mode |
| `HINDSIGHT_DEPLOYMENT` | `self_hosted` | `self_hosted` or `cloud` |
| `HINDSIGHT_CLOUD_BASE_URL` | `https://api.hindsight.vectorize.io` | Cloud endpoint (https required) |
| `HINDSIGHT_BASE_URL` / `HINDSIGHT_TIMEOUT_SECONDS` | `http://127.0.0.1:8888` / `120` | Hindsight client |
| `HINDSIGHT_API_KEY` | empty | Hindsight Cloud key (`hsk_…`), sent only in cloud mode |
| `HINDSIGHT_SELF_HOSTED_API_KEY` | empty | Only if a self-hosted server enables API-key auth |
| `HINDSIGHT_LLM_PROVIDER` / `HINDSIGHT_LLM_MODEL` | `anthropic` / `claude-haiku-4-5` | Hindsight container; `none` = no-LLM mode. Not `claude-opus-5-5`/`claude-fable-5-1` |
| `HINDSIGHT_SKIP_LLM_VERIFICATION` | `false` | Hindsight container: skip startup LLM probe |
| `HINDSIGHT_BANK_ID` | `deal-rescue-demo` | M1 spike script only |

The app logs only whether a secret is set, never its value, and does not log interaction notes.

## API overview

All errors use `{"error": {"code", "message", "details"}}`. Lists take `limit` (1–200) and `offset`.
Customer-owned records are only reachable under their customer path; another customer's id returns 404,
and a body reference to another customer's record returns 422 `invalid_reference`. No endpoint mixes
several customers' records in one response (enforced by `tests/test_scope_security.py`).
**There is no authentication yet:** path scoping prevents accidental mixing, it does not decide who may
read which customer. Do not expose the backend beyond localhost.

```
/api/health, /api/health/dependencies, /api/system/capabilities, /api/system/memory
/api/customers[/{customer_id}]               (the list returns summaries without notes)
/api/customers/{cid}/deals[/{id}]            (deals are only listed per customer)
/api/customers/{cid}/stakeholders[/{id}]
/api/customers/{cid}/interactions[/{id}]     (+ /memory, /memory/sync; Idempotency-Key header on POST)
/api/customers/{cid}/commitments[/{id}]      (?overdue=true)
/api/customers/{cid}/memory/recall | /reflect   (503 memory_unavailable when memory is off)
/api/customers/{cid}/memory/writes[/{id}[/retry]] (synchronisation ledger; retries writes and deletions)
/api/system/memory, /api/system/memory/queue     (status; counts only)
/api/customers/{cid}/intelligence/signals       (attention signals; filters, pagination, ?as_of=)
/api/customers/{cid}/intelligence/deals         (this customer's deals ordered by attention)
/api/customers/{cid}/deals/{id}/intelligence    (activity, commitments and signals for one deal)
/api/intelligence/rules                         (rule catalogue and thresholds; no customer data)
```

## Tests

```bash
cd backend
uv run pytest -rs                                            # everything except live tests
HINDSIGHT_LIVE=1 uv run pytest -rs tests/test_hindsight_live.py    # needs Hindsight (no-LLM mode is enough)
HINDSIGHT_LIVE=1 HINDSIGHT_LIVE_LLM=1 uv run pytest -rs tests/test_hindsight_live.py  # also a real key
HINDSIGHT_CLOUD_LIVE=1 uv run pytest -s tests/test_hindsight_cloud_live.py  # Hindsight Cloud; spends a few cents
cd ../frontend && npm run build && npm run lint
```

## Hindsight scripts (M1)

```bash
cd backend
uv run python scripts/hindsight_spike.py --report spike-reports/run.json             # needs key
uv run python scripts/hindsight_spike.py --extraction-mode chunks --cleanup          # no LLM calls
uv run python scripts/hindsight_nollm_checks.py bank-layout                         # no LLM calls
```

## Schema changes

`app/db/migrations.py` versions the SQLite schema (`schema_migrations` table). New databases are created
and stamped; older ones are backed up to `<db>.bak-v<N>-<timestamp>` and upgraded by numbered migration
functions. The rules for adding a migration are in that module's docstring.

## Stop

```bash
docker compose stop          # keeps memory data
# docker compose down -v     # DELETES Hindsight data volumes; only if you really mean it
```
