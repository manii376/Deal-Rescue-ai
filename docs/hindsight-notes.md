# Hindsight integration notes (M1)

Status legend: **VERIFIED-RUN** = observed in a real call on 2026-09-28;
**VERIFIED-SOURCE** = read in the installed client or the pinned server image source;
**DOCS** = stated by official docs only; **UNVERIFIED** = not yet confirmed.

## 1. Versions

| Component | Version | How pinned | Status |
|---|---|---|---|
| Server image | `ghcr.io/vectorize-io/hindsight:0.10.1` | tag + digest `sha256:b4d3b76f363aa40cf348450e7f8f52a50653008731b99824b14623a196182e73` in `docker-compose.yml` | VERIFIED-RUN (`/version` → `api_version: 0.10.1`) |
| Latest GitHub release | v0.10.1 (2026-09-21) | n/a | VERIFIED (GitHub API) |
| Python client | `hindsight-client==0.10.1` | exact pin in `backend/pyproject.toml` + `uv.lock` | VERIFIED (PyPI + installed) |
| npm client | `@vectorize-io/hindsight-client` 0.10.1 | not used (frontend never talks to Hindsight) | VERIFIED (npm registry) |
| Anthropic SDK | `anthropic` 1.8.0 | `uv.lock` | installed; not called yet |

Image tags have no `v` prefix (`0.10.1` exists, `v0.10.1` does not). The image is ~3.6 GB
and bundles local embedding (`BAAI/bge-small-en-v1.5`) and reranker models.

## 2. Server behaviour

| Fact | Status |
|---|---|
| API on container port 8888, control-plane UI on 9999; container binds `0.0.0.0` internally (`HINDSIGHT_API_HOST=0.0.0.0`). Compose publishes both on `127.0.0.1` only (`docker port` → `127.0.0.1:8888`, `127.0.0.1:9999`). | VERIFIED-RUN |
| **Refuses to start without an LLM key.** With `HINDSIGHT_API_LLM_API_KEY` empty the process raises `ValueError: LLM API key is required` and the container restart-loops. (The docs page claims the server starts without a key — that is wrong for 0.10.1.) | VERIFIED-RUN |
| Startup LLM probe (`verify_llm`) only logs a warning on failure; `HINDSIGHT_API_SKIP_LLM_VERIFICATION=true` disables it. | VERIFIED-SOURCE + RUN |
| Healthy ~30 s after start on this machine (embedded Postgres "pg0" + local models). | VERIFIED-RUN |
| `GET /health` → `{"status":"healthy","database":"connected",...}`; `GET /version` → `{"api_version":"0.10.1","features":{...}}`. | VERIFIED-RUN |
| Anthropic provider: default model `claude-haiku-4-5` (`config.py`). | VERIFIED-SOURCE; log line `LLM: provider=anthropic, model=claude-haiku-4-5` VERIFIED-RUN |
| Data persists in `/home/hindsight/.pg0` (named volume `hindsight-data`). | VERIFIED-SOURCE (volume mounted; persistence across restarts not yet exercised → UNVERIFIED) |

### Authentication

- **Default: no API auth.** Our client calls succeeded without any key. VERIFIED-RUN.
- Optional built-in auth: `HINDSIGHT_API_TENANT_EXTENSION=hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension`
  with `HINDSIGHT_API_TENANT_API_KEY=<secret>`; the client sends `Authorization: Bearer <api_key>`.
  VERIFIED-SOURCE only — **not enabled or exercised**. Current mitigation: localhost-only port binding.

## 3. Anthropic model verification

| Item | Result | Status |
|---|---|---|
| Current model IDs | `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5` (also `claude-opus-5-5`, `claude-fable-5-1`) | From Anthropic's current model table (cached 2026-06-24) — not checked against your account |
| App model (`ANTHROPIC_MODEL`) | default `claude-opus-5` | UNVERIFIED for your account (no key available); verify with `client.models.retrieve(...)` in M2 |
| Hindsight model (`HINDSIGHT_LLM_MODEL`) | `claude-haiku-4-5` (Hindsight's own default) | Compatibility VERIFIED-SOURCE; **not run** with a real key |

Why the Hindsight model matters: the 0.10.1 Anthropic provider (`engine/providers/anthropic_llm.py`)

- sends structured-output requests with a **forced** `tool_choice: {"type": "tool", ...}`, which Anthropic
  rejects (HTTP 400) on `claude-opus-5-5` and `claude-fable-5-1`. Do not configure those for Hindsight.
- does **not** send `temperature` or `thinking`, so it avoids the sampling-parameter 400s on newer models.
- `claude-haiku-4-5`, `claude-sonnet-5` and `claude-opus-5` accept forced tool choice per the current API
  reference; haiku is the cheapest and is Hindsight's default.

## 4. Client API (hindsight-client 0.10.1)

`Hindsight(base_url, api_key=None, timeout=300.0, user_agent=None, max_attempts=3)` — VERIFIED-SOURCE.

- Every convenience method has an async twin prefixed `a` (`aretain`, `arecall`, `areflect`, …).
- The **sync** methods call `loop.run_until_complete` and **must not be used inside FastAPI**; our module
  uses only async methods and `aclose()`.
- Low-level generated APIs (all async): `client.memory`, `.banks`, `.documents`, `.entities`,
  `.operations`, `.monitoring`, … Transport is aiohttp; errors are `hindsight_client_api.exceptions.ApiException`
  (`.status`, `.body`), connection errors are `aiohttp.ClientError`.
- Recall/reflect retry on 429/503 (`max_attempts`); **writes are never retried by the client**.

Signatures we rely on (copied from the installed package):

```python
aretain(bank_id, content, timestamp: datetime | None = None, context=None, document_id=None,
        metadata: dict[str, str] | None = None, entities=None, resolve_entities=None,
        tags: list[str] | None = None, update_mode=None, retain_async=False, operation_id=None)
        -> RetainResponse  # fields: success, bank_id, items_count, var_async, operation_id, operation_ids, usage

arecall(bank_id, query, types=None, max_tokens=4096, budget='mid', trace=False, query_timestamp=None,
        include_entities=False, include_chunks=False, include_source_facts=False, tags=None,
        tags_match: 'any'|'all'|'any_strict'|'all_strict'|'exact' = 'any', tag_groups=None,
        prefer_observations=False, min_scores=None, temporal_window=None, ...)
        -> RecallResponse  # results: list[RecallResult], trace, entities, chunks, source_facts

areflect(bank_id, query, budget='low', context=None, max_tokens=None, response_schema=None, tags=None,
         tags_match='any', include_facts=False, include_tool_calls=False, fact_types=None, ...)
         -> ReflectResponse  # text, based_on{memories, mental_models, directives}, structured_output, usage, trace

acreate_bank(bank_id, name=None, mission=None, retain_mission=None, retain_extraction_mode=None,
             enable_observations=None, reflect_mission=None, ...)  -> BankProfileResponse
adelete_bank(bank_id)
client.memory.list_memories(bank_id, type=None, q=None, consolidation_state=None, state=None,
                            document_id=None, entity_id=None, tags=None, tags_match=None, ...)
                            -> ListMemoryUnitsResponse  # items, total, limit, offset
client.memory.get_observation_history(bank_id, memory_id) -> object
client.monitoring.health_endpoint_health_get()
```

`RecallResult` fields: `id, text, type, entities, context, occurred_start, occurred_end, mentioned_at,
document_id, metadata, chunk_id, tags, source_fact_ids, scores, attachments`.
`ReflectFact` fields (items of `based_on.memories`): `id, text, type, context, occurred_start, occurred_end`
— note: **no `document_id`, `tags` or `metadata`**, so mapping reflect evidence back to our SQL rows needs
a follow-up lookup by memory id (UNVERIFIED which endpoint is best; `client.memory.get_memory` exists).

Gaps in the convenience wrapper (VERIFIED-SOURCE): `retain()` has no `observation_scopes` argument
(only `retain_batch` item dicts do); `list_memories()` wrapper lacks `document_id`/`tags` filters, so
we call the generated `client.memory.list_memories` directly.

Endpoints: `/health`, `/version`, `/v1/default/banks/{bank}/memories` (retain),
`/memories/recall`, `/reflect`, `/memories/list`, `/memories/{id}`, `/memories/{id}/history`.

## 5. Observed request/response examples (secrets removed, synthetic data)

Retain (chunks-mode bank):

```python
await client.aretain("deal-rescue-demo-spike-chunks",
    "[SYNTHETIC DEMO DATA, run 5abf493d] Discovery call with Aurora Logistics. CFO Priya Raman said ...",
    timestamp=<now - 2 days>, context="sales call notes",
    document_id="interaction:spike-a-5abf493d:1",
    metadata={"customer_id": "spike-a-5abf493d", "interaction_id": "interaction:spike-a-5abf493d:1",
              "source": "rep_note", "synthetic": "true"},
    tags=["customer:spike-a-5abf493d", "deal:spike-a-5abf493d-deal", "kind:interaction"])
# -> success=True, items_count=1, operation_id=None, usage all zeros (no LLM in chunks mode)
```

Recall with `tags=["customer:spike-a-5abf493d"], tags_match="all_strict"` returned:

```json
{"id": "3218cbce-fcf7-4a68-8ea0-03aa482380d4", "type": "world",
 "text": "[SYNTHETIC DEMO DATA, run 5abf493d] Discovery call with Aurora Logistics. ...",
 "document_id": "interaction:spike-a-5abf493d:1",
 "tags": ["kind:interaction", "customer:spike-a-5abf493d", "deal:spike-a-5abf493d-deal"],
 "metadata": {"source": "rep_note", "synthetic": "true", "customer_id": "spike-a-5abf493d",
              "interaction_id": "interaction:spike-a-5abf493d:1"},
 "context": "sales call notes", "occurred_start": null, "occurred_end": null,
 "mentioned_at": "2026-09-26T12:59:43.712486+00:00"}
```

Observed: tags, metadata, context and document_id round-trip unchanged; the retain `timestamp` is
returned as `mentioned_at` (not `occurred_start`, which stayed null in chunks mode).

Retain / reflect with an invalid LLM key → `HTTP 500`,
`{"detail":"Fact extraction failed: 1/1 chunks failed. First failures: chunk 0: AuthenticationError: Error code: 401 ... invalid x-api-key"}`.
Our module maps this to `MemoryRequestError` with the key pattern redacted.

Full JSON: `docs/spike-results/run1-default-no-credentials.json`, `docs/spike-results/run2-chunks-no-llm.json`.

## 6. Tag isolation (one-bank design)

Run `5abf493d`, bank in `chunks` extraction mode, observations disabled, `tags_match="all_strict"`:

| Probe | Results | Owners |
|---|---|---|
| A-scope, query about B's facts (Borealis, on-prem, 310,000) | 1 | A only |
| B-scope, query about A's facts (Aurora, SOC 2, 42,000) | 1 | B only |
| A-scope, generic query | 1 | A only |
| B-scope, generic query | 1 | B only |
| Unscoped control (`tags=[]`, `any`) | 2 | A and B |
| `list_memories(tags=[B], all_strict)` / `(document_id=A doc)` | 1 / 1 | B / A |

**Verified:** strict tag filtering isolates raw `world` memories per customer, even under adversarial
queries, and the unscoped control proves the filter (not the query wording) does the isolating.

**Not yet verified (needs a real LLM key):** isolation of LLM-extracted facts and, most importantly, of
**observations** — consolidated beliefs built across memories, which could mix customers. That is the
remaining gate on the one-bank decision. The spike run to settle it:
`uv run python scripts/hindsight_spike.py --observation-wait 30` (checks C05–C08b).

## 7. Latency

| Operation | Measured | Status |
|---|---|---|
| Retain, chunks mode (no LLM), n=5 | min 0.10 s, median 0.13 s, max 0.25 s | VERIFIED-RUN — **not representative** of production retains |
| Retain with LLM extraction | — | UNVERIFIED (needs key). Expect seconds; measure with the default-mode spike |
| Recall, n=6 | 0.13–0.48 s (median ~0.25 s) | VERIFIED-RUN, chunks bank, tiny data set |
| Reflect | — | UNVERIFIED (needs key) |

## 8. Open questions

1. Observation isolation across customer tags (see §6) and whether `observation_scopes="per_tag"` via
   `retain_batch` is needed.
2. Real retain/reflect latency with `claude-haiku-4-5`.
3. `based_on.memories` lacks tags/document_id: confirm `client.memory.get_memory(bank, id)` returns them.
4. Whether `update_mode="append"` vs separate documents is better for requirement changes (M3).
5. Data persistence across `docker compose down` / `up` (volume mounted, not exercised).
