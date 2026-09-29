# M0 / M1 test report — 2026-09-28

Environment: Windows 11, Docker 29.8, Python 3.12.4 (uv 0.10.0), Node 24.19.
**No Anthropic API key was available** (none in the environment, no `ant` CLI profile), so every
LLM-dependent check below is either SKIPPED or ran against a deliberately invalid placeholder key.

## M0 — scaffold

| Check | Result | Evidence |
|---|---|---|
| Hindsight container starts from `docker compose up -d` (with a key) | PASSED | healthy after ~30 s; `/health` 200 |
| Hindsight ports bound to localhost only | PASSED | `docker port` → `127.0.0.1:8888`, `127.0.0.1:9999` |
| Hindsight starts with empty key | **FAILS by design of Hindsight 0.10.1** | restart loop, `ValueError: LLM API key is required` — a real key is a hard prerequisite |
| Backend `GET /api/health` | PASSED | HTTP 200 `{"status":"ok",...}` on 127.0.0.1:8010 |
| Backend `GET /api/health/dependencies` | PASSED | HTTP 200, `hindsight.healthy=true, api_version=0.10.1`, `anthropic.api_key="missing"` |
| Frontend dev server + `/api` proxy | PASSED | `http://127.0.0.1:5180` serves page; `/api/health` via proxy 200 |
| Frontend `npm run build` (tsc + vite) and `npm run lint` | PASSED | |

Note: ports 8000 and 5173 were already used by unrelated processes on this machine, so the backend
uses **8010** and the frontend **5180** (`strictPort`). Those other processes were not touched.

## Automated tests (`cd backend && uv run pytest -rs`)

| Suite | Result |
|---|---|
| `test_config.py` (9) — defaults, blank keys → missing, secrets absent from repr/describe, invalid URL / bank id rejected | 9 passed |
| `test_api_health.py` (3) — health 200, unreachable Hindsight reported, keys never in response | 3 passed |
| `test_hindsight_memory.py` (6) — unreachable → typed safe error, input validation, redaction, record normalisation | 6 passed |
| `test_hindsight_live.py::test_spike_checks_without_llm` (needs `HINDSIGHT_LIVE=1`) | passed when run live; skipped by default |
| `test_hindsight_live.py::test_spike_checks_with_llm` (needs `HINDSIGHT_LIVE_LLM=1` + key) | **SKIPPED — no key** |

Default run: 18 passed, 2 skipped. Live run: 1 passed, 1 skipped.

## M1 — integration spike (`backend/scripts/hindsight_spike.py`)

Run 1 = default extraction (LLM) with placeholder key. Run 2 = `--extraction-mode chunks`
(Hindsight stores text without calling the LLM — verified in server source), placeholder key.

| # | Check | Run 1 (LLM mode) | Run 2 (chunks mode) | Actual status |
|---|---|---|---|---|
| 1 | Health & connectivity | PASSED | PASSED | **Verified** |
| 2 | Create/use bank | PASSED | PASSED | **Verified** |
| 3 | Retain customer A | FAILED (401 via HTTP 500) | PASSED | Verified without LLM; **LLM extraction blocked on credentials** |
| 4 | Retain customer B | SKIPPED | PASSED | same as 3 |
| 5 | Recall with customer tag filter | SKIPPED | PASSED | Verified on raw memories; extracted facts **unverified** |
| 6 | Cross-customer isolation (both directions + control) | SKIPPED | PASSED (0 leaks) | Verified on raw memories; **observations unverified** |
| 7 | Reflect `include_facts=True` + evidence IDs | SKIPPED | FAILED (401) | **Blocked on credentials**; response schema known from client source only |
| 8a | List memories by document / tag | SKIPPED | PASSED | **Verified** |
| 8b | Observation history endpoint | SKIPPED | UNVERIFIED (no observations exist) | **Unverified**; endpoint exists in client |
| 9 | Retain latency | SKIPPED | PASSED: 0.10–0.25 s | Measured for chunks mode only; **LLM-mode latency unverified** |
| 10a | Invalid configuration rejected | PASSED | PASSED | **Verified** |
| 10b | Unavailable service → safe typed error, no key leak | PASSED | PASSED | **Verified** |
| 10c | Invalid LLM credentials → clean error, no key leak | PASSED | (n/a) | **Verified** (invalid key). Missing key = server won't start (verified manually) |

Reports: `docs/spike-results/run1-default-no-credentials.json`, `docs/spike-results/run2-chunks-no-llm.json`
(scanned: contain no key material).

## Checks that need credentials

C03/C04 in LLM mode, C05–C06 on extracted facts and observations, C07 (reflect), C08b (observation
history) and real retain latency. After adding `ANTHROPIC_API_KEY` to `.env`:

```bash
docker compose up -d --force-recreate
cd backend
uv run python scripts/hindsight_spike.py --observation-wait 30 --report spike-reports/run3-llm.json
HINDSIGHT_LIVE=1 HINDSIGHT_LIVE_LLM=1 uv run pytest -rs tests/test_hindsight_live.py
```

## Decision gate: one bank + tags

**Provisionally supported, not yet approved by the evidence.** Strict tag filters isolate raw
memories. Observation consolidation (LLM) has not been observed; if a run with a real key shows
observations mixing customers, fall back to per-customer banks or `observation_scopes`.

## Assumptions (not verified)

- `claude-opus-5` / `claude-haiku-4-5` are accessible on your Anthropic account.
- Hindsight's forced-`tool_choice` requests succeed on `claude-haiku-4-5` (code-level analysis only).
- Hindsight data persists across container recreation (volume configured, not exercised).
