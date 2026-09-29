# Deal Rescue AI: demo runbook

Verified against the repository and the local database on 2026-09-29 (read-only audit). Nothing in this
runbook creates, edits or deletes records. Figures below come from the current `backend/data/deal_rescue.db`.

## 1. What the demo shows (and the data behind it)

| Record | Current state |
|---|---|
| Customers | `[SYNTHETIC DEMO] Aurora Logistics`, `[SYNTHETIC DEMO] Borealis Foods` |
| Deals | 1: `[SYNTHETIC DEMO] Pune warehouse pilot` (Aurora), stage proposal, status open; **value, expected close date and owner not recorded** |
| Interactions | 1: Aurora, "Budget call" (call, 2026-09-20 10:00 UTC), rep note: CFO said the annual budget is capped at USD 42,000 and a SOC 2 Type II report is mandatory before signing |
| Commitments | 0 |
| Stakeholders | 0 (both customers) |
| Memory ledger | 1 write, status `stored`, in Aurora's own bank; 2 linked memory ids |
| Borealis Foods | no deals, interactions or commitments |

## 2. Prerequisites

* Windows, PowerShell; `uv` (0.10+) and Node/npm installed; `frontend/node_modules` present.
* Ollama installed at `D:\Ollama\app\ollama.exe`, models in `D:\Ollama\models` (user variable `OLLAMA_MODELS`).
  `ollama` is **not on PATH**; use the full path. Model `qwen3:4b` must already be pulled.
* `.env` (repo root, gitignored) already selects: `MEMORY_BACKEND=hindsight`, `HINDSIGHT_DEPLOYMENT=cloud`,
  `AI_PROVIDER=ollama`, `AI_MAX_RETRIES=0`. Do not show `.env` on screen: it contains the Hindsight Cloud key.
* Internet access (Hindsight Cloud recall runs during each briefing).
* Free RAM: the only live run so far had ~0.3 GB free and took 31 s cold. Close browsers/IDEs you don't need.

## 3. Startup (three PowerShell windows)

```powershell
# Window 1: local model (skip if the Ollama tray app is already running)
& "D:\Ollama\app\ollama.exe" serve
# check (another window):
& "D:\Ollama\app\ollama.exe" list          # qwen3:4b must be listed
```

```powershell
# Window 2: backend on 127.0.0.1:8010
cd D:\woxsen\hack_with_hyderabad3.0\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8010
```

```powershell
# Window 3: frontend on 127.0.0.1:5180 (proxies /api to 8010)
cd D:\woxsen\hack_with_hyderabad3.0\frontend
npm run dev
```

Pre-flight checks (any window):

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/health
(Invoke-RestMethod http://127.0.0.1:5180/api/system/capabilities) | ConvertTo-Json -Depth 3
```

Expect `database.available = true`, `memory.backend = hindsight` with `available = true`, and
`ai.provider = ollama` with `available = true, model = qwen3:4b`. If `ai.available` is false, read `ai.reason`.

Optional warm-up: generate one briefing before the audience arrives. It is not saved and creates no records,
but it does perform one Hindsight Cloud **recall** (a metered read; see §7).

Startup side effects: the memory sync worker starts with the backend. With the current ledger (one write,
already `stored`) it has nothing to send, so no Hindsight writes happen. It would only write if new
interactions were created. **Do not create or edit interactions during the demo.**

## 4. Demo sequence and expected results

Open http://127.0.0.1:5180.

1. **Customer workspace.** The left index lists both customers with a "synthetic" label. Click
   *[SYNTHETIC DEMO] Aurora Logistics*. Expected: kicker "Customer workspace · SYNTHETIC DEMO DATA", an opening line
   ("1 deal (1 open)…"), and the attention ledger with one row: the Pune warehouse pilot, "proposal · open",
   highest severity LOW, and the finding types.
2. **Open the deal.** Click *Pune warehouse pilot*. The header shows Stage Proposal, Status Open, and
   "Value not recorded", "Close date not recorded", "Owner not recorded". There is no description field (the
   backend has none).
3. **Signals.** Under *Signals*:
   * *Findings*: **0** ("No negative findings") on 2026-09-29. **Date-dependent:** the proposal-stage stall
     threshold is 10 days and the last call was 2026-09-20, so **from 2026-09-30 (UTC) a `stalled_deal` finding
     appears** here and the workspace row severity changes. Both are correct behaviour; say so rather than being
     surprised by it.
   * *Missing information & data quality* (4): missing owner, missing value, missing expected close date,
     no stakeholders recorded. Click "Inspect rule and N source records" to show the rule id, thresholds and the
     deal record in the inspector.
   * Commitments: "No commitments recorded". Interactions: one "Rep note · call" labelled as the rep's account.
     Stakeholders: "No stakeholders recorded for this customer."
4. **AI briefing.** Click *Generate briefing*. Expect ~30 s (cold) with a "Generating…" status. Result: a numbered
   list of claims, each marked Recorded / Rep note / Inference · unconfirmed (or Unsupported, struck through),
   citation chips (R1 deal, D1–D4 signals, N1 rep note, M1–M2 memories), missing evidence, "Checks applied"
   (unsupported / relabelled claims, rejected citations, memory line), and "Generated … by ollama (qwen3:4b) … not
   saved". Model output varies between runs; don't promise specific wording. Since the mixed-claim fix, a claim like
   "no stakeholders recorded, making decision-makers unassessable" comes back as a recorded fact plus a separate
   inference.
5. **Citations.** Click a chip: the inspector shows the evidence kind, "Exactly the text the model was given", the
   occurred date and the resolved source record (deal, interaction). For an M* chip it shows the memory ref and the
   linked interaction it came from. Esc closes it.
6. **Memory explanation** (the "Memory used…" line in *Checks applied*). Talking points:
   * Each customer has its own Hindsight bank (`deal-rescue-cust-<customer id>`); the backend recalls only from
     the selected customer's bank.
   * A recalled memory is used only if our own ledger maps its (bank, memory id) to an interaction that still
     exists, belongs to **this deal**, and hasn't changed since the memory was written. Those appear as M* evidence
     with a link to the interaction.
   * Hindsight Cloud also returns consolidated "observations"/summaries that have no id in our ledger. We can't
     prove which record they came from, so they are **excluded** and counted ("Excluded: N unlinked summaries").
     The one live run so far: 2 linked memories used, 2 unlinked excluded. Exact counts can change as Cloud
     consolidates.
7. **Borealis Foods / isolation.** Click *[SYNTHETIC DEMO] Borealis Foods*. Expected: "No deals are recorded for
   this customer." (Borealis has no deals, so there is **no Borealis deal or briefing to compare**.) To show
   isolation concretely, paste Aurora's deal under Borealis in the address bar:
   `http://127.0.0.1:5180/#/c/cus_87c4a90d248744abafd9602589d685a4/d/deal_aedb5ab6947d42eb9015198b8314a605`
   Expected: "Deal not found for this customer" (the backend returns 404 because every query is customer-scoped).
   Cross-bank memory isolation was verified live in M4.5 (tests L4/L5) but has no UI of its own.

The whole flow reads data only; the only external calls are the Ollama generation and one Cloud recall per
briefing.

## 5. Recovery checklist

| Symptom | Likely cause | Fix |
|---|---|---|
| Header shows "Backend unreachable"; lists show "Backend is unreachable" | Backend not running / wrong port | Restart window 2; check `http://127.0.0.1:8010/api/health` |
| `npm run dev` fails "Port 5180 is already in use" | An old Vite is still running (strictPort) | Use the running one, or stop it: `Get-CimInstance Win32_Process \| ? CommandLine -match 'vite' \| select ProcessId,CommandLine` then `Stop-Process -Id <pid>` for **this** project's path only |
| Backend fails to bind 8010 | Orphaned uvicorn | Same as above, matching `uvicorn app.main:app` under `hack_with_hyderabad3.0\backend` |
| Briefing: "Local AI is unavailable" | Ollama not running, model missing, or >60 s timeout | Start `ollama serve`; `ollama list`; close memory-hungry apps; click *Try again* (the first call after a cold start is slowest) |
| Briefing: "The model's output could not be used" | Model returned malformed/truncated JSON | *Try again*; nothing was saved |
| Memory line says "Memory unavailable (…)" | No internet / Cloud auth or credit problem (401/402/403) | The briefing still works from database evidence; say so and continue |
| Signals list suddenly shows a `stalled_deal` finding | Demo on/after 2026-09-30 | Expected (see step 3) |
| Page blank or stale after code changes | Browser cache / HMR | Hard refresh (Ctrl+F5) |

Fallback if the model can't run: demo steps 1–3, 5 (signal inspector) and 7 fully without AI, and show the
"Local AI is unavailable" state as the designed failure mode.

## 6. What was verified in this audit

* Data (read-only SQLite query): counts and records in §1.
* Frontend: `npm run build` and `npm run lint` passed at the end of M5 Stage 3; `/api` proxies to 8010; empty
  states exist for no deals, commitments, interactions and stakeholders; the unknown-deal 404 renders "Deal not
  found for this customer".
* Endpoints the flow uses returned 200 against a **copy** of the database with AI and memory disabled
  (M5 Stage 3 smoke); the briefing returned the expected 503 `ai_unavailable` in that mode.
* Backend tests: 281 passed, 9 skipped (live tests are opt-in), after the mixed-claim fix.
* **Not verified here** (by constraint: no model or Cloud calls): steps 4–6 end to end with the current code.
  The only live briefing (before the mixed-claim fix) took 31.1 s, had all citations valid, and used 2 linked
  memories with 2 unlinked excluded. A browser walkthrough is reported to have been done by the user during M5 Stage 3.

## 7. Limitations to disclose honestly

* **All data is synthetic**, and it is thin: one deal, one interaction, no commitments or stakeholders. The briefing
  therefore has little to say beyond the budget/SOC 2 note and missing information.
* **Briefings are not saved** and vary run to run; the local 4B model can mislabel claims. The backend's checks
  (citation validation, name/number grounding, evidence-kind relabelling, mixed fact/inference splitting) reduce
  this but are lexical, not proof of meaning; the inspector exists so a person can check the source.
* **Memory depends on Hindsight Cloud.** Each briefing makes one recall, which is metered (≈$0.75 per million
  output tokens per the M4.5 report; cents per demo). Offline, the briefing falls back to database evidence.
* **Customer isolation for Borealis** can only be shown by the 404 above; Borealis has no deals or memories to
  compare.
* **Signals depend on the current date** (stall rule; see step 3).
* **Stakeholders are per customer, not per deal**, and **deals have no description field**.
* **Not implemented:** objection analysis, follow-up drafts and strategy comparison (Deal Time Machine) with Ollama;
  there's no data-entry UI in this frontend; no automated frontend tests.
* `README.md` is out of date on AI: it still lists Ollama as "planned". This runbook and `.env.example` are current.
* No M5 completion report exists yet in `docs/`.
