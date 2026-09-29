# M4 completion report — deterministic Deal Intelligence Engine (2026-09-28)

Earlier reports are unchanged. No LLM call, no Hindsight call, no fake credential and no Docker
volume change was involved. Nothing in M4 reads memory; all rules run over SQLite records only.

## Summary

| Area | State |
|---|---|
| Stalled-deal detection (per-stage thresholds, meaningful channels, closed deals, future timestamps) | **Implemented, tested** |
| Commitment monitoring (overdue, due soon, undated; done/cancelled never flagged) | **Implemented, tested** |
| Completeness / unresolved work (missing fields, no stakeholders, open commitments on closed deals) | **Implemented, tested** |
| Customer-scoped API with filters, pagination, `as_of` | **Implemented, tested** |
| Transparent priority ordering; rule catalogue endpoint | **Implemented, tested** |
| Fixed query budget (4 SELECTs per evaluation, independent of deal count) | **Implemented, tested** |
| Typed frontend client for the new endpoints | **Implemented** (type-checked); no UI |
| Win probability, objection/intent inference, cross-customer workspace, LLM features | **Not implemented by design** |

## Design

```
app/intelligence/
  rules.py     pure functions: snapshots + config + as_of -> signals (no DB, no clock, no LLM)
  service.py   loads one customer's records in 4 queries, runs the rules
  schemas.py   Signal, SignalList, DealIntelligence, DealAttentionList, RuleCatalog
app/api/routers/intelligence.py   thin HTTP layer (validation, ownership, filtering, pagination)
```

* **Reference time:** every endpoint accepts `as_of` (ISO 8601 **with** timezone; naive values are rejected with
  422). Default is the current time. Responses echo `as_of`, the derived `business_date`, the `timezone` and
  `rules_version` so any result can be reproduced exactly.
* **Time semantics:** inactivity compares elapsed time (`as_of - last activity >= N days`). Date-only fields
  (`due_date`, `expected_close_date`) are compared with the business date (`as_of` converted to `INTEL_TIMEZONE`);
  a commitment due on the business date is "due today", not overdue.
* **Determinism:** rules sort their inputs and their output (stable tie-breaks on ids); signal ids are
  `"<type>:<record id>"`. Same records + configuration + `as_of` → identical output (tested, including shuffled
  input order and repeated API calls).
* **Scope:** everything is evaluated per validated customer. There is no unscoped signal or deal listing; the
  scope-security guard now also covers `Signal`, `SignalList`, `DealIntelligence`, `DealAttentionList` and
  `DealAttentionSummary`. The only unscoped endpoint is the rule catalogue, which contains no customer data.

## Implemented rules

Nature distinguishes a **finding** (a negative fact), **missing_information** (cannot be assessed; not
evidence of a problem) and **data_quality** (records that look wrong).

| Signal | Applies to | Condition | Severity | Nature |
|---|---|---|---|---|
| `stalled_deal` | open deals | `as_of − latest meaningful interaction ≥ stall days(stage)` | high if ≥ 2× threshold, else medium | finding |
| `no_recorded_activity` | open deals | no meaningful interaction at or before `as_of` | medium if deal age ≥ threshold, else low | missing_information |
| `commitment_overdue` | open commitments, open deals | `due_date < business date` | high if owner is `us`, medium if `customer` | finding |
| `commitment_due_soon` | open commitments, open deals | `0 ≤ due_date − business date ≤ INTEL_DUE_SOON_DAYS` | medium if due today/tomorrow, else low | finding |
| `commitment_undated` | open commitments, open deals | no `due_date` | low | missing_information |
| `close_date_passed` | open deals | `expected_close_date < business date` | medium | finding |
| `open_commitment_on_closed_deal` | won / lost / no_decision deals | commitment still `open` | low | finding |
| `missing_expected_close_date` / `missing_deal_value` / `missing_deal_owner` | open deals | field empty | low | missing_information |
| `no_stakeholders_recorded` | customers with ≥ 1 open deal | zero stakeholders | low | missing_information |
| `future_dated_activity` | all deals | interaction `occurred_at > as_of` (excluded from activity) | low | data_quality |

Details:
* **Meaningful activity** = interactions linked to the deal with channel in `INTEL_MEANINGFUL_CHANNELS`
  (default call, meeting, email, message). `note` and `other` are internal and never reset inactivity. Interactions
  without a deal do not count as deal activity.
* **Closed deals** (`won`, `lost`, `no_decision`) produce no stall, no-activity, close-date or missing-field
  signals; their open commitments are reported once as `open_commitment_on_closed_deal` (with `past_due` in the facts),
  never as overdue. Done and cancelled commitments never produce signals on any deal.
* **Timestamps:** `occurred_at` is required by the schema, so "missing activity timestamp" means no qualifying
  interaction (→ `no_recorded_activity`). Future-dated interactions are excluded from activity and reported. A deal
  created after `as_of` is treated as age 0 (`as_of` does not reconstruct history).
* Each signal carries: `type`, `category`, `nature`, `severity`, `title`, `explanation`, `rule {id, version,
  description, thresholds}`, `facts` (the compared values: dates, days inactive/overdue/until due, owner, …),
  `sources` (customer/deal/interaction/commitment ids, including the commitment's source interaction) and `urgency`.
* No hidden-objection, sentiment or intent inference; no scores; no prediction.

## Priority (documented, also served by `GET /api/intelligence/rules`)

1. severity: high > medium > low
2. nature: finding > missing_information > data_quality
3. urgency, larger first — stalled: days inactive − threshold; no activity: deal age in days; overdue: days
   overdue; due soon: `due_soon_days − days until due`; close date passed: days past; others 0
4. deal id, signal type, signal id (stable tie-break)

The per-deal attention list orders deals by highest severity, then number of high/medium/low signals, then deal id.

## Default thresholds (all configurable, validated at startup)

| Setting | Default |
|---|---|
| `INTEL_STALL_DAYS_BY_STAGE` | discovery 21, qualification 14, proposal 10, negotiation 7, closing 5 |
| `INTEL_STALL_DAYS_DEFAULT` | 14 (stages not in the map) |
| `INTEL_MEANINGFUL_CHANNELS` | call, meeting, email, message |
| `INTEL_DUE_SOON_DAYS` | 7 (inclusive) |
| `INTEL_TIMEZONE` | UTC (any IANA zone) |

Unknown stages/channels, out-of-range values and unknown time zones stop the app at startup with a clear
message. `tzdata` was added as a dependency because Windows Python has no IANA database.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/customers/{cid}/intelligence/signals` | filters: `deal_id` (must belong to the customer → else 422 `invalid_reference`), `type` (repeatable), `severity` (repeatable), `min_severity`, `category`, `nature`; `limit`/`offset`; `as_of` |
| GET | `/api/customers/{cid}/intelligence/deals` | per-deal summary ordered by attention; filters `status`, `min_severity`, `only_with_signals`; pagination; `as_of` |
| GET | `/api/customers/{cid}/deals/{did}/intelligence` | activity summary, commitment counts, deal signals, customer-level signals; 404 for another customer's deal |
| GET | `/api/intelligence/rules` | rule catalogue, current thresholds, priority order, what is deliberately not included |

Unknown customer → 404 `not_found`; naive `as_of`, unknown signal types or severities → 422 `validation_error`
(existing error envelope).

## Tests

| Command | Exit | Result |
|---|---|---|
| `cd backend && uv run pytest -q -rs` | **0** | **154 passed, 7 skipped** (the 7 opt-in live Hindsight tests; not run in M4) |
| `tests/test_intelligence_rules.py` + `tests/test_intelligence_api.py`, 3 runs | 0 ×3 | 52/52 each |
| `cd frontend && npm run build` / `npm run lint` | **0** / **0** | |
| Manual: backend with no keys, memory disabled, temp DB, curl | — | overdue (high), stalled (medium), missing-information (low) signals with explanations; rules endpoint 200 |

New tests (52): `tests/test_intelligence_rules.py` (39 pure-rule tests) and `tests/test_intelligence_api.py` (13).
Coverage includes exact boundaries (threshold − 1 s / = threshold / 2× threshold; due −1, 0, +1, +2, +7, +8 days;
close date = today vs yesterday; deal age at the no-activity boundary), per-stage and default thresholds,
non-meaningful channels, future-dated interactions (and one exactly at `as_of`), all three closed statuses,
done/cancelled commitments, undated commitments, owner-based severity, three time zones (UTC, Asia/Kolkata,
America/Los_Angeles) including a day-boundary case, equal results for the same instant in different offsets, naive
`as_of` rejected, priority order, determinism with shuffled inputs, deterministic unique ids, filters and pagination,
source references that resolve to the customer's records, customer isolation, other customer's deal as filter (422)
or path (404), unknown customer/deal, no unscoped listing, `as_of` moving signals forward/backward, completing a
commitment removing its signal, constant 4-query budget (1 deal vs 12 deals), thresholds/channels/timezone from
environment, invalid configuration failing startup, and correct results with Hindsight configured but unreachable.
`tests/test_scope_security.py` was extended (stronger), not weakened.

## Files changed in M4

Backend (new): `app/intelligence/{__init__,rules,service,schemas}.py`, `app/api/routers/intelligence.py`,
`tests/test_intelligence_rules.py`, `tests/test_intelligence_api.py`.
Backend (modified): `app/config.py` (INTEL_* settings + validation), `app/main.py` (service + router),
`tests/test_scope_security.py` (guard covers intelligence schemas), `pyproject.toml` / `uv.lock` (`tzdata`).
Frontend: `src/api/types.ts`, `src/api/client.ts` (typed intelligence functions; no UI).
Repo: `README.md`, `.env.example`, this report. No schema migration was needed (read-only feature).

## Limitations

1. `as_of` sets the reference time but does not reconstruct history: records are evaluated as currently stored
   (e.g. a deal's current stage and status apply to a past `as_of`).
2. Only interactions linked to a deal count as that deal's activity; customer-level interactions are ignored.
3. Severity policies are fixed in code (thresholds are configurable, severity mapping is not).
4. Signals are computed on request (no caching); cost is linear in the customer's records, with a constant number
   of queries.
5. There is no cross-customer workspace view; a multi-customer docket needs authentication first.
6. `DealAttentionSummary` counts deal-level signals only; customer-level signals (e.g. no stakeholders) are returned
   separately.

## Not implemented (planned for later milestones)

LLM briefings, objection analysis, follow-up drafts, strategy comparison (AI boundary exists, provider "none"),
cross-deal learning and outcomes (M6), the Deal Time Machine, the product UI, authentication.
