# M1 final report — 2026-09-28 (run 2)

Supersedes nothing: `docs/m1-test-report.md` (run 1) is unchanged. This report covers the follow-up run.

## Headline

**M1 is still not complete. Blocker: no Anthropic API key is configured.** Per instructions, no LLM
request was made in this run. Everything that can be verified without an LLM has now been verified:
container persistence, evidence-id → source mapping, and the per-customer bank layout. LLM-backed
retain, reflect, observation isolation and real LLM latency remain **not run**.

## 1. Configuration inspected (values never printed)

| Variable | Where | State |
|---|---|---|
| `ANTHROPIC_API_KEY` | `.env` | **empty** |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` | process environment | unset |
| `ant` CLI profile (`~/.config/anthropic`) | — | not installed / absent |
| `HINDSIGHT_API_KEY` | `.env` | empty (not needed: server auth is off, ports bound to 127.0.0.1) |
| `ANTHROPIC_MODEL` | `.env` | `claude-opus-5` |
| `HINDSIGHT_LLM_PROVIDER` / `HINDSIGHT_LLM_MODEL` | `.env` → compose | `anthropic` / `claude-haiku-4-5` |

Files inspected and found unchanged since run 1: `.env.example`, `docker-compose.yml`,
`backend/app/config.py`, `backend/app/services/hindsight_memory.py`, `backend/scripts/hindsight_spike.py`,
`backend/tests/test_hindsight_live.py`, `docs/hindsight-notes.md`, `docs/m1-test-report.md`.

### Model identifiers

| Model | Used for | Status |
|---|---|---|
| `claude-opus-5` | app (`ANTHROPIC_MODEL`, not called before M2) | Listed in Anthropic's current model table. **Account access unverified** (needs key). |
| `claude-haiku-4-5` | Hindsight internal LLM | Listed in the current model table; is Hindsight 0.10.1's own anthropic default (server source). Compatible at code level with Hindsight's forced `tool_choice` (unlike `claude-opus-5-5` / `claude-fable-5-1`, which reject it). **Runtime unverified** (needs key). |

No model identifier was changed.

## 2. What you need to configure

Set in the repo-root `.env` (gitignored; never commit it):

```
ANTHROPIC_API_KEY=<your key>          # used by the app AND passed to Hindsight as HINDSIGHT_API_LLM_API_KEY
```

Optional overrides (defaults shown, already in `.env`): `ANTHROPIC_MODEL=claude-opus-5`,
`HINDSIGHT_LLM_MODEL=claude-haiku-4-5`. Then recreate the container so it picks up the key:
`docker compose up -d --force-recreate`.

## 3. Tests executed in this run

Hindsight was started with a placeholder key **only so the server would boot** (0.10.1 refuses to start
with an empty key) and `HINDSIGHT_SKIP_LLM_VERIFICATION=true`. All test banks used `chunks` extraction,
which stores text **without calling the LLM** (verified in server source, run 1), with observations
disabled. No result below says anything about LLM extraction, reflect, or observations.

| Command | Exit code | Result |
|---|---|---|
| `uv run pytest -q -rs` (unit) | **0** | 19 passed, 2 skipped (live tests, by design) |
| `HINDSIGHT_LIVE=1 uv run pytest -q -rs tests/test_hindsight_live.py` | **0** | 1 passed (no-LLM spike), 1 skipped (`HINDSIGHT_LIVE_LLM` needs a key) |
| `scripts/hindsight_nollm_checks.py bank-layout` | **0** | L1–L6 all PASSED |
| `scripts/hindsight_nollm_checks.py persist-write` | **0** | P1 PASSED |
| `docker compose restart hindsight` → `persist-verify` | **0** | P2, P3 PASSED (same container id) |
| `docker compose up -d --force-recreate` → `persist-verify --cleanup` | **0** | P2, P3 PASSED (new container id, same named volume) |

New code in this run: `HindsightMemory.get_memory()` (+1 unit test) and
`backend/scripts/hindsight_nollm_checks.py`. No existing report was overwritten.

### Persistence (VERIFIED for raw memories)

| Check | After `restart` | After container **recreate** |
|---|---|---|
| Memory ids, texts, tags identical to what was written | PASSED | PASSED |
| Recall returns both persisted memory ids (2/2) | PASSED | PASSED |

Container ids: `eb5ba9399a62` (restart kept it) → `c8f98bde937e` (recreated). Volume
`hack_with_hyderabad30_hindsight-data` was never removed. No `down -v` or volume deletion was run.
The test bank was deleted afterwards via the API. Not covered: persistence of observations and of
LLM-extracted facts (none exist yet), and Docker Desktop restarts.

### Evidence-id mapping and bank layout (VERIFIED, raw memories)

| # | Check | Result |
|---|---|---|
| L1 | Recall id → `get_memory` → `document_id` + `metadata.source_record` + tags (shared bank) | PASSED |
| L2 | Same mapping inside a per-customer bank | PASSED |
| L3 | A memory id looked up in another customer's bank returns 404 | PASSED |
| L4 | Customer B's bank returns 0 of A's memories even with **no tag filter** and a query about A | PASSED |
| L5 | Shared outcomes bank recall (`kind:outcome`, `all_strict`) returns the recorded outcome with `document_id=outcome:<deal>` | PASSED |
| L6 | Identical content in two banks gets distinct memory ids | PASSED |

Consequence for the app: an evidence reference must store **(bank_id, memory_id)**, not memory_id alone.
Reflect evidence (`based_on.memories`) lacks tags/document_id (run 1, client source), so mapping reflect
evidence will use `get_memory(bank, id)` — the mechanism is verified here on recall ids; on reflect ids it
is **not yet run**.

Sanitized example (`get_memory`):

```json
{"id": "993b79a0-ce01-4f5d-a2d2-74aed1a0442c", "type": "world",
 "text": "[SYNTHETIC DEMO DATA, run 53af6983] Aurora: budget capped at USD 42,000; SOC 2 required.",
 "document_id": "interaction:layout-a-53af6983:1",
 "tags": ["customer:layout-a-53af6983", "deal:layout-a-53af6983-deal", "kind:interaction"],
 "metadata": {"synthetic": "true", "customer_id": "layout-a-53af6983",
              "source_record": "interaction:layout-a-53af6983:1"},
 "context": "sales call notes", "occurred_start": null, "occurred_end": null,
 "mentioned_at": "2026-09-28T13:14:57.998984+00:00"}
```

Reports: `docs/spike-results/run4-bank-layout-nollm.json`, `run5*-persist*.json` (scanned: no key material).

## 4. Tests NOT run (and why)

| Required check | Status | Reason |
|---|---|---|
| 1. Real LLM-backed retain | **NOT RUN** | No API key |
| 2. Recall of LLM-extracted, customer-scoped memories | **NOT RUN** | depends on 1 (raw-memory recall passed in run 1) |
| 3. Reflect success + actual evidence ids | **NOT RUN** | No API key |
| 4. Evidence ids → source records | **PARTIAL** | Verified for recall ids (L1/L2); reflect ids need a key |
| 5. Observation isolation | **NOT RUN / UNVERIFIED** | Consolidation calls the LLM |
| 6. Container persistence | **VERIFIED** (raw memories) | — |
| 7. Real retain and reflect latency | **NOT RUN** | No API key (run 1 chunks-mode timings are not representative) |

## 5. Bank architecture recommendation

Evidence available now:
- One bank + strict tags isolates **raw** memories (run 1).
- Whether **observations** stay per-customer in one bank is **unknown**. Tags alone are not claimed to isolate them.
- Per-customer banks isolate **by construction** (L3, L4: no filter needed), map evidence correctly (L2),
  and a shared outcomes bank supports cross-deal lookup (L5) — all verified on raw memories.

**Recommendation (needs your approval): per-customer banks + one shared outcomes bank.**

```
deal-rescue-cust-<customer_id>   interactions, statements, requirement changes, commitments
                                 (observations here can only ever mix facts of ONE customer)
deal-rescue-outcomes             one document per closed deal: outcome, recorded actions, reason,
                                 non-sensitive deal attributes (industry, size band, stage history);
                                 cross-deal observations here are intended
```

Why choose it before the observation test: it removes the leakage risk regardless of how consolidation
behaves, whereas the one-bank design can only be shown safe by testing, and switching later would mean
re-retaining data. Costs: bank id must be stored with every evidence reference; one retain per bank for
outcomes; per-customer bank configuration must be applied on creation.

Still to confirm with a key: LLM extraction + observations + reflect inside a per-customer bank, and that
outcome observations do not reproduce customer-confidential text beyond what the outcome document holds.
If the one-bank observation test later passes, both designs are viable; the recommendation stays because
it needs no tag discipline to be safe.

## 6. Remaining blockers

1. **`ANTHROPIC_API_KEY`** (only blocker for M1 completion).
2. After adding it, run:
   ```bash
   docker compose up -d --force-recreate
   cd backend
   uv run python scripts/hindsight_spike.py --observation-wait 30 --report spike-reports/run6-llm.json
   HINDSIGHT_LIVE=1 HINDSIGHT_LIVE_LLM=1 uv run pytest -q -rs tests/test_hindsight_live.py
   ```
   The observation-isolation and per-customer-bank LLM checks still need to be written into the spike
   (reflect evidence → `get_memory` mapping, observation leak probes in one bank vs per-customer banks,
   latency n≥5 for retain and reflect). This is intentionally not written yet, pending your approval of
   the bank design.

Hindsight container state at end of run: **stopped** (it was running on a placeholder key).
