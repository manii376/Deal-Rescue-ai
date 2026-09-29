# Security follow-up after M2 — unscoped customer data (2026-09-28)

`docs/m2-completion-report.md` is unchanged; this document records the follow-up.

## Trigger

M2 limitation 7: `GET /api/deals` returned every customer's deals. The app has no authentication.

## Audit method

Every operation in the generated OpenAPI schema was listed with its path scope and the schemas its 2xx
responses can contain (transitively). Customer-owned schemas: `CustomerRead`, `DealRead`,
`StakeholderRead`, `InteractionRead`, `CommitmentRead`, `MemoryWriteRead`, `MemoryRefRead`,
`EvidenceHitRead`, `RecallResponse`, `ReflectResponse`. Data-access code behind scoped routes was also
re-read (list filters, `get_owned`, `reference_owned`, idempotency lookup, memory source-ref linking).

## Findings

| # | Endpoint | Finding | Action |
|---|---|---|---|
| 1 | `GET /api/deals` | Unscoped; returns deals of all customers in one response | **Removed** |
| 2 | `GET /api/customers` | The directory is needed as the entry point, but each item was a full `CustomerRead`, so one response carried **every customer's free-text `notes`** | List now returns `CustomerSummary` (no `notes`); notes only via `GET /api/customers/{id}` |
| 3 | `POST /api/customers` | Returns only the record just created | No change (allow-listed in the guard) |
| 4 | `/api/health`, `/api/health/dependencies`, `/api/system/capabilities`, `/api/system/memory` | No customer records (bank *prefix* only, no ids) | No change |
| 5 | All `/api/customers/{customer_id}/…` routes | Every query filters by the path customer; foreign ids → 404; foreign body references → 422; filters such as `?deal_id=<other customer's deal>` return an empty page | No change needed |

## Changes

Backend
- `app/api/routers/deals.py`: removed `global_router` / `list_all_deals`. The filter helper was replaced by
  `_customer_deals(customer_id, …)`, which builds the query *from* the customer id, so a deal list query
  without the customer filter cannot be assembled through it.
- `app/main.py`: no longer mounts the removed router.
- `app/api/schemas.py`: new `CustomerSummary`; `CustomerRead` extends it with `notes`.
- `app/api/routers/customers.py`: list uses `Page[CustomerSummary]`.

Frontend (typed client only)
- `src/api/client.ts`: removed `deals.listAll`; `customers.list` returns `Page<CustomerSummary>`.
- `src/api/types.ts`: `CustomerSummary`; `Customer` extends it.

Docs: `README.md` API overview (no cross-customer deal list; explicit "no authentication" warning).

## What was deliberately not done

- **No authentication or authorization was added**, and a client-supplied customer id is not treated as
  either. Path scoping only guarantees that each response concerns exactly one, explicitly named customer;
  anyone who can reach the API can still read any customer by id. The backend must stay on localhost.
- **No replacement cross-customer view.** The Intelligence Workspace (UI direction §7.1) will need a
  "deals needing attention" docket across customers; that must be built together with real user/tenant
  scoping, not as an open listing. Until then the frontend has to iterate the customer directory and
  call scoped endpoints.
- No other endpoints or behaviour changed.

## Regression tests (`backend/tests/test_scope_security.py`, 7 tests)

1. **OpenAPI guard:** no operation outside `/api/customers/{customer_id}` may return a customer-owned
   schema (only `POST /api/customers` is allow-listed).
2. **Guard self-test:** re-adding `GET /api/deals` → `Page[DealRead]` or a directory returning
   `Page[CustomerRead]` makes the guard report exactly those two operations.
3. `/api/deals` (with and without filters, including `?customer_id=`) → 404 envelope, no customer data in
   the body, and absent from OpenAPI.
4. Customer-scoped deal listing returns exactly that customer's deal ids and totals; filters that match only
   the other customer's deals return an empty page; pagination never spills into another customer.
5. Unknown/odd customer scopes (`cus_does_not_exist`, `*`, `%2A`, SQL-like text) → 404, never a fallback list.
6. The customer directory never contains `notes` (seeded with "CONFIDENTIAL" notes); the scoped detail
   endpoint still returns them.
7. OpenAPI documents the directory as `CustomerSummary` without a `notes` property.

Existing test `test_deal_filters_and_global_list` was rewritten as `test_deal_filters_are_customer_scoped`
(it previously asserted the removed endpoint worked).

## Results

| Command | Exit | Result |
|---|---|---|
| `cd backend && uv run pytest -q -rs` | **0** | **81 passed, 3 skipped** (opt-in live Hindsight tests) |
| `cd frontend && npm run build` | **0** | type-check + build |
| `cd frontend && npm run lint` | **0** | clean |

No paid API calls, no Docker commands, no volume changes, no M3 work.
