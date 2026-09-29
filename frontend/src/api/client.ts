// Typed client for the Deal Rescue backend. The only module that calls fetch().

import type {
  BranchView,
  BriefingResponse,
  Capabilities,
  Commitment,
  CommitmentCreate,
  CommitmentStatus,
  CommitmentUpdate,
  Customer,
  CustomerCreate,
  CustomerSummary,
  CustomerUpdate,
  Deal,
  DealAttentionList,
  DealCreate,
  DealStage,
  DealStatus,
  DealIntelligence,
  DealTimeline,
  DealUpdate,
  Health,
  Interaction,
  InteractionCreate,
  InteractionUpdate,
  MemoryLedgerEntry,
  MemoryQueueStatus,
  MemoryWrite,
  MemoryWriteStatus,
  Page,
  PageQuery,
  Severity,
  SignalCategory,
  SignalList,
  SignalNature,
  SignalType,
  StrategyCatalogue,
  TimeMachineAssessment,
  TimeMachineAssessRequest,
  RecallResponse,
  ReflectResponse,
  Stakeholder,
  StakeholderCreate,
  StakeholderUpdate,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(status: number, code: string, message: string, details: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  /** Memory or AI is not available right now (not a bug; show the reason). */
  get isUnavailable(): boolean {
    return this.status === 503
  }
}

// Any object of optional scalar filters; undefined/null values are omitted.
type Query = object

const BASE = '/api'

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query as Record<string, unknown>)) {
    if (value !== undefined && value !== null) params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; query?: Query; headers?: Record<string, string>; signal?: AbortSignal } = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(withQuery(`${BASE}${path}`, options.query), {
      method,
      headers: {
        Accept: 'application/json',
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'network_error', 'Backend is unreachable', null)
  }
  if (response.status === 204) return undefined as T
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const body = payload as { error?: { code?: string; message?: string; details?: unknown } } | null
    throw new ApiError(
      response.status,
      body?.error?.code ?? `http_${response.status}`,
      body?.error?.message ?? response.statusText,
      body?.error?.details ?? null,
    )
  }
  return payload as T
}

const enc = encodeURIComponent
const customerPath = (customerId: string) => `/customers/${enc(customerId)}`

/** CRUD helpers for a collection nested under a customer. */
function nested<T, C, U, F extends object = object>(collection: string) {
  const base = (cid: string) => `${customerPath(cid)}/${collection}`
  return {
    list: (cid: string, query?: PageQuery & F, signal?: AbortSignal) =>
      request<Page<T>>('GET', base(cid), { query, signal }),
    get: (cid: string, id: string, signal?: AbortSignal) =>
      request<T>('GET', `${base(cid)}/${enc(id)}`, { signal }),
    create: (cid: string, body: C) => request<T>('POST', base(cid), { body }),
    update: (cid: string, id: string, body: U) => request<T>('PATCH', `${base(cid)}/${enc(id)}`, { body }),
    remove: (cid: string, id: string) => request<void>('DELETE', `${base(cid)}/${enc(id)}`),
  }
}

export const api = {
  health: (signal?: AbortSignal) => request<Health>('GET', '/health', { signal }),
  capabilities: (signal?: AbortSignal) => request<Capabilities>('GET', '/system/capabilities', { signal }),

  customers: {
    list: (query?: PageQuery & { q?: string; is_synthetic?: boolean }, signal?: AbortSignal) =>
      request<Page<CustomerSummary>>('GET', '/customers', { query, signal }),
    get: (id: string, signal?: AbortSignal) => request<Customer>('GET', customerPath(id), { signal }),
    create: (body: CustomerCreate) => request<Customer>('POST', '/customers', { body }),
    update: (id: string, body: CustomerUpdate) => request<Customer>('PATCH', customerPath(id), { body }),
    remove: (id: string) => request<void>('DELETE', customerPath(id)),
  },

  // Deals are listed per customer only; there is no cross-customer deal listing.
  deals: nested<Deal, DealCreate, DealUpdate, { status?: DealStatus; stage?: DealStage }>('deals'),

  stakeholders: nested<Stakeholder, StakeholderCreate, StakeholderUpdate, { q?: string }>('stakeholders'),

  interactions: {
    ...nested<Interaction, InteractionCreate, InteractionUpdate, {
      deal_id?: string
      channel?: string
      occurred_from?: string
      occurred_to?: string
    }>('interactions'),
    /** Pass the same idempotencyKey when retrying a create so it is not duplicated. */
    createIdempotent: (cid: string, body: InteractionCreate, idempotencyKey: string) =>
      request<Interaction>('POST', `${customerPath(cid)}/interactions`, {
        body,
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
    memory: (cid: string, id: string) =>
      request<MemoryWrite>('GET', `${customerPath(cid)}/interactions/${enc(id)}/memory`),
    syncMemory: (cid: string, id: string) =>
      request<MemoryWrite>('POST', `${customerPath(cid)}/interactions/${enc(id)}/memory/sync`),
  },

  commitments: nested<Commitment, CommitmentCreate, CommitmentUpdate, {
    deal_id?: string
    status?: CommitmentStatus
    owner_party?: string
    due_before?: string
    overdue?: boolean
  }>('commitments'),

  /** Deterministic, customer-scoped deal intelligence. Pass asOf (ISO with timezone) for reproducible results. */
  intelligence: {
    signals: (cid: string, query?: PageQuery & {
      as_of?: string
      deal_id?: string
      type?: SignalType
      severity?: Severity
      min_severity?: Severity
      category?: SignalCategory
      nature?: SignalNature
    }, signal?: AbortSignal) =>
      request<SignalList>('GET', `${customerPath(cid)}/intelligence/signals`, { query, signal }),
    deals: (cid: string, query?: PageQuery & {
      as_of?: string
      status?: DealStatus
      min_severity?: Severity
      only_with_signals?: boolean
    }, signal?: AbortSignal) =>
      request<DealAttentionList>('GET', `${customerPath(cid)}/intelligence/deals`, { query, signal }),
    deal: (cid: string, dealId: string, asOf?: string, signal?: AbortSignal) =>
      request<DealIntelligence>('GET', `${customerPath(cid)}/deals/${enc(dealId)}/intelligence`, {
        query: { as_of: asOf },
        signal,
      }),
  },

  /** Deal Time Machine (deterministic; no model). include_memory=true makes the backend do one Hindsight recall. */
  timeMachine: {
    timeline: (cid: string, dealId: string, asOf: string, signal?: AbortSignal) =>
      request<DealTimeline>('GET', `${customerPath(cid)}/deals/${enc(dealId)}/timeline`, { query: { as_of: asOf }, signal }),
    branch: (cid: string, dealId: string, query: { branch_at: string; as_of: string; include_memory?: boolean },
      signal?: AbortSignal) =>
      request<BranchView>('GET', `${customerPath(cid)}/deals/${enc(dealId)}/timeline/branch`, { query, signal }),
    strategies: (cid: string, dealId: string,
      query: { branch_at: string; as_of: string; anchor_ref?: string | null; include_memory?: boolean },
      signal?: AbortSignal) =>
      request<StrategyCatalogue>('GET', `${customerPath(cid)}/deals/${enc(dealId)}/time-machine/strategies`, { query, signal }),
  },

  ai: {
    /** Evidence-grounded briefing, generated on request and never stored. No request body by design. */
    briefing: (cid: string, dealId: string, signal?: AbortSignal) =>
      request<BriefingResponse>('POST', `${customerPath(cid)}/deals/${enc(dealId)}/ai/briefing`, { signal }),
    /** TM4: assess ONE fixed Time Machine strategy (one local model call; one Hindsight recall only if include_memory). */
    timeMachineAssess: (cid: string, dealId: string, body: TimeMachineAssessRequest, signal?: AbortSignal) =>
      request<TimeMachineAssessment>('POST', `${customerPath(cid)}/deals/${enc(dealId)}/ai/time-machine/assess`,
        { body, signal }),
  },

  memory: {
    recall: (cid: string, body: { query: string; deal_id?: string; max_results?: number }) =>
      request<RecallResponse>('POST', `${customerPath(cid)}/memory/recall`, { body }),
    reflect: (cid: string, body: { question: string }) =>
      request<ReflectResponse>('POST', `${customerPath(cid)}/memory/reflect`, { body }),
    /** Synchronisation ledger, including rows whose source interaction was deleted. */
    writes: (cid: string, query?: PageQuery & { status?: MemoryWriteStatus; source_id?: string }) =>
      request<Page<MemoryLedgerEntry>>('GET', `${customerPath(cid)}/memory/writes`, { query }),
    write: (cid: string, writeId: string) =>
      request<MemoryLedgerEntry>('GET', `${customerPath(cid)}/memory/writes/${enc(writeId)}`),
    retryWrite: (cid: string, writeId: string) =>
      request<MemoryLedgerEntry>('POST', `${customerPath(cid)}/memory/writes/${enc(writeId)}/retry`),
    /** Counts only; no customer data. */
    queue: (signal?: AbortSignal) => request<MemoryQueueStatus>('GET', '/system/memory/queue', { signal }),
  },
}
