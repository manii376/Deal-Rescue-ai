// Types mirroring backend/app/api/schemas.py and system endpoints.
// UI components import these types and the functions in ./client.ts only;
// they never call fetch() or know which AI/memory provider is behind the API.

export type ISODateTime = string // e.g. "2026-09-20T04:30:00Z"
export type ISODate = string // e.g. "2026-12-01"

export type DealStage = 'discovery' | 'qualification' | 'proposal' | 'negotiation' | 'closing'
export type DealStatus = 'open' | 'won' | 'lost' | 'no_decision'
export type Influence = 'low' | 'medium' | 'high' | 'unknown'
export type Channel = 'call' | 'meeting' | 'email' | 'message' | 'note' | 'other'
export type OwnerParty = 'us' | 'customer'
export type CommitmentStatus = 'open' | 'done' | 'cancelled'
export type MemoryWriteStatus =
  | 'disabled'
  | 'pending'
  | 'in_progress'
  | 'stored'
  | 'failed'
  | 'delete_pending'
  | 'deleting'
  | 'deleted'
  | 'delete_failed'

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface PageQuery {
  limit?: number
  offset?: number
}

/** Directory entry from GET /customers. Has no notes: private content is only on Customer. */
export interface CustomerSummary {
  id: string
  name: string
  industry: string | null
  is_synthetic: boolean
  created_at: ISODateTime
  updated_at: ISODateTime
}
export interface Customer extends CustomerSummary {
  notes: string | null
}
export interface CustomerCreate {
  name: string
  industry?: string | null
  notes?: string | null
  is_synthetic?: boolean
}
export type CustomerUpdate = Partial<CustomerCreate>

export interface Deal {
  id: string
  customer_id: string
  title: string
  stage: DealStage
  status: DealStatus
  value_minor: number | null
  currency: string | null
  expected_close_date: ISODate | null
  owner_name: string | null
  created_at: ISODateTime
  updated_at: ISODateTime
}
export interface DealCreate {
  title: string
  stage: DealStage
  status?: DealStatus
  value_minor?: number | null
  currency?: string | null
  expected_close_date?: ISODate | null
  owner_name?: string | null
}
export type DealUpdate = Partial<DealCreate>

export interface Stakeholder {
  id: string
  customer_id: string
  name: string
  role: string | null
  influence: Influence
  email: string | null
  priorities: string[]
  created_at: ISODateTime
  updated_at: ISODateTime
}
export interface StakeholderCreate {
  name: string
  role?: string | null
  influence?: Influence
  email?: string | null
  priorities?: string[]
}
export type StakeholderUpdate = Partial<StakeholderCreate>

export interface MemoryRef {
  bank_id: string
  memory_id: string
}
export interface MemoryWrite {
  status: MemoryWriteStatus
  bank_id: string | null
  document_id: string | null
  attempts: number
  last_error: string | null
  last_attempt_at: ISODateTime | null
  stored_at: ISODateTime | null
  memory_refs: MemoryRef[]
}

export interface Interaction {
  id: string
  customer_id: string
  deal_id: string | null
  occurred_at: ISODateTime
  channel: Channel
  title: string | null
  notes: string
  participant_ids: string[]
  memory: MemoryWrite | null
  created_at: ISODateTime
  updated_at: ISODateTime
}
export interface InteractionCreate {
  occurred_at: ISODateTime // must include a timezone
  channel: Channel
  notes: string
  title?: string | null
  deal_id?: string | null
  participant_ids?: string[]
}
export type InteractionUpdate = Partial<InteractionCreate>

export interface Commitment {
  id: string
  customer_id: string
  deal_id: string
  source_interaction_id: string | null
  description: string
  owner_party: OwnerParty
  owner_name: string | null
  due_date: ISODate | null
  status: CommitmentStatus
  completed_at: ISODateTime | null
  created_at: ISODateTime
  updated_at: ISODateTime
}
export interface CommitmentCreate {
  deal_id: string
  description: string
  owner_party: OwnerParty
  owner_name?: string | null
  due_date?: ISODate | null
  status?: CommitmentStatus
  source_interaction_id?: string | null
}
export type CommitmentUpdate = Partial<Omit<CommitmentCreate, 'deal_id' | 'source_interaction_id'>>

export interface EvidenceHit {
  ref: MemoryRef
  text: string
  memory_type: string | null
  document_id: string | null
  source_type: string | null
  source_id: string | null
  occurred_start: string | null
  mentioned_at: string | null
  /** 'linked': traced to one of our records via the ledger; 'unlinked': no verified source. */
  provenance: 'linked' | 'unlinked'
  source: SourceLink | null
}
export interface SourceLink {
  source_type: string
  source_id: string
  occurred_at: ISODateTime | null // from our database, not from memory
  deal_id: string | null
  memory_is_current: boolean // false: the record changed after this memory was written
}
export interface RecallResponse {
  query: string
  hits: EvidenceHit[]
  excluded_deleted_sources: number
  excluded_foreign_bank: number
}
export interface ReflectResponse {
  kind: 'inference'
  text: string
  evidence: EvidenceHit[]
  unresolved_memory_ids: string[]
  excluded_deleted_sources: number
  excluded_foreign_bank: number
}

export interface MemoryLedgerEntry {
  id: string
  source_type: string
  source_id: string
  status: MemoryWriteStatus
  bank_id: string
  document_id: string
  attempts: number
  max_attempts: number
  last_error: string | null
  /** unavailable: transient (auto-retried). Others need an explicit retry. no_credits = Hindsight Cloud HTTP 402. */
  last_error_kind: 'unavailable' | 'rejected' | 'unexpected' | 'auth_failed' | 'no_credits' | 'forbidden' | null
  last_attempt_at: ISODateTime | null
  next_attempt_at: ISODateTime | null
  stored_at: ISODateTime | null
  source_deleted_at: ISODateTime | null
  is_current: boolean
  memory_ref_count: number
  auto_retry_scheduled: boolean
}
export interface MemoryQueueStatus {
  backend: string
  worker_running: boolean
  by_status: Record<string, number>
  due_writes: number
  due_deletes: number
  needs_manual_retry: number
}

export interface Health {
  status: string
  service: string
  version: string
}
export interface MemoryStatus {
  backend: 'disabled' | 'hindsight'
  available: boolean
  reason: string | null
  customer_bank_prefix: string | null
  outcomes_bank_id: string | null
  server_version: string | null
  notes: string[]
}
export interface AIStatus {
  provider: string
  implemented: boolean
  available: boolean
  model: string | null
  reason: string | null
}
export interface Capabilities {
  database: { available: boolean; schema_version: number | null; expected_schema_version: number }
  memory: MemoryStatus
  ai: AIStatus
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: unknown }
}

// -- Deterministic deal intelligence (M4) ------------------------------------------------
export type Severity = 'high' | 'medium' | 'low'
export type SignalNature = 'finding' | 'missing_information' | 'data_quality'
export type SignalCategory = 'activity' | 'commitment' | 'deal' | 'unresolved_work' | 'data_quality'
export type SignalType =
  | 'stalled_deal'
  | 'no_recorded_activity'
  | 'commitment_overdue'
  | 'commitment_due_soon'
  | 'commitment_undated'
  | 'close_date_passed'
  | 'open_commitment_on_closed_deal'
  | 'missing_expected_close_date'
  | 'missing_deal_value'
  | 'missing_deal_owner'
  | 'no_stakeholders_recorded'
  | 'future_dated_activity'
export type FactValue = string | number | boolean | null

export interface Signal {
  id: string
  type: SignalType
  category: SignalCategory
  nature: SignalNature
  severity: Severity
  customer_id: string
  deal_id: string | null
  title: string
  explanation: string
  rule: { id: SignalType; version: string; description: string; thresholds: Record<string, FactValue> }
  facts: Record<string, FactValue>
  sources: { type: 'customer' | 'deal' | 'interaction' | 'commitment'; id: string }[]
  urgency: number
}
interface Evaluated {
  as_of: ISODateTime
  business_date: ISODate
  timezone: string
  rules_version: string
}
export interface SignalList extends Evaluated {
  items: Signal[]
  total: number
  limit: number
  offset: number
}
export interface DealAttentionSummary {
  deal_id: string
  title: string
  stage: DealStage
  status: DealStatus
  highest_severity: Severity | null
  signal_counts: Record<Severity, number>
  signal_types: SignalType[]
  last_meaningful_activity_at: ISODateTime | null
}
export interface DealAttentionList extends Evaluated {
  items: DealAttentionSummary[]
  total: number
  limit: number
  offset: number
}
export interface DealIntelligence extends Evaluated {
  customer_id: string
  deal_id: string
  title: string
  stage: DealStage
  status: DealStatus
  is_closed: boolean
  highest_severity: Severity | null
  signal_counts: Record<Severity, number>
  activity: {
    last_meaningful_activity_at: ISODateTime | null
    last_meaningful_interaction_id: string | null
    days_since_meaningful_activity: number | null
    stall_threshold_days: number | null
    meaningful_interaction_count: number
    non_meaningful_interaction_count: number
    future_dated_interaction_count: number
  }
  commitments: { open: number; overdue: number; due_soon: number; undated: number; done: number; cancelled: number }
  signals: Signal[]
  customer_signals: Signal[]
}

// -- AI deal briefing (M5; mirrors backend BriefingResponse and related schemas) ------------------
/** Evidence kinds the backend may send. rep_note = the sales rep's account, never the customer's words. */
export type EvidenceKind = 'recorded' | 'signal' | 'rep_note' | 'statement' | 'memory' | 'outcome'
/** unsupported = the backend's grounding check found numbers/names absent from the cited evidence. */
export type ClaimKind = 'recorded' | 'rep_note' | 'statement' | 'inference' | 'unsupported'

export interface Claim {
  text: string
  kind: ClaimKind
  citations: string[]
  grounding_issues: string[]
}
export interface GenerationInfo {
  provider: string
  model: string | null
  generated_at: ISODateTime
}
export interface DealBriefing {
  claims: Claim[]
  missing_evidence: string[]
  rejected_citations: string[]
  relabelled_claims: number
  unsupported_claims: number
  generated: GenerationInfo
}
export interface EvidenceSource {
  ref: string
  kind: EvidenceKind
  source_type: string
  source_id: string | null
  occurred_at: ISODateTime | null
  excerpt: string
  memory_ref: MemoryRef | null
  related_records: { type: string; id: string }[]
}
export interface MemoryEvidenceStatus {
  /** not_requested: the caller did not ask for memory (no service contacted), e.g. a branch view without include_memory. */
  status: 'used' | 'no_relevant_memories' | 'unavailable' | 'disabled' | 'not_requested'
  reason: string | null
  included: number
  excluded_unlinked: number
  excluded_stale: number
  excluded_other_deal: number
  excluded_deleted_sources: number
  excluded_foreign_bank: number
  /** Point-in-time views: linked memories whose source interaction occurred after the cutoff. */
  excluded_after_cutoff: number
}
export interface BriefingResponse {
  status: 'generated' | 'insufficient_evidence'
  customer_id: string
  deal_id: string
  as_of: ISODateTime
  briefing: DealBriefing | null
  sources: EvidenceSource[]
  memory: MemoryEvidenceStatus
  insufficient_reason: string | null
  note: string
}

// -- Deal Time Machine contracts (TM0; mirrors backend/app/timemachine/schemas.py) ---------------------
// Types only: no routes exist yet. Actual history can only carry recorded / rep_note / signal kinds;
// AI content appears only inside a StrategyComparison, which is always hypothetical.
export type TimelineEventKind =
  | 'interaction'
  | 'commitment_created'
  | 'commitment_due'
  | 'commitment_completed'
  | 'stakeholder_recorded'
  | 'deal_entered'
  | 'rule_checkpoint'
export type TimelineEvidenceKind = 'recorded' | 'rep_note' | 'signal'
export type DateBasis = 'occurred' | 'due' | 'completed' | 'entered_in_system' | 'rule_computed'

export interface TimelineEvent {
  id: string
  kind: TimelineEventKind
  evidence_kind: TimelineEvidenceKind
  at: ISODateTime
  date_basis: DateBasis
  title: string
  source: { type: string; id: string } | null
  rule_id: SignalType | null
}
export interface DealTimeline {
  customer_id: string
  deal_id: string
  as_of: ISODateTime
  events: TimelineEvent[]
  data_notes: string[]
  limitations: string[]
}
export interface BranchView {
  customer_id: string
  deal_id: string
  branch_at: ISODateTime
  as_of: ISODateTime
  known_then: EvidenceSource[]
  followed: TimelineEvent[]
  signals_then: Signal[]
  memory: MemoryEvidenceStatus
}

export type StrategyTemplateId = 'earlier_follow_up' | 'address_requirement_early' | 'identify_decision_maker'
export interface StrategyTemplate {
  id: StrategyTemplateId
  label: string
  description: string
  offered: boolean
  offered_because: string[]
  not_offered_reason: string | null
  needs_anchor: boolean
}
export interface HypotheticalCommitment {
  kind: 'hypothetical'
  owner_party: OwnerParty
  description: string
}
export interface StrategyEvidenceMap {
  basis: 'rule_derived'
  strategy_id: StrategyTemplateId
  anchor_ref: string | null
  related: string[]
  gaps: string[]
  implied_commitments: HypotheticalCommitment[]
}

export interface StrategyOption {
  id: string
  description: string
}
export interface CitedText {
  text: string
  citations: string[]
  /** Why the backend downgraded or removed this item; empty if it passed. */
  issues: string[]
}
export interface StrategyAssessment {
  strategy_id: string
  supporting: CitedText[]
  contradicting: CitedText[]
  evidence_gaps: string[]
  commitments_created: string[]
  verdict: 'supported' | 'mixed' | 'unsupported'
  rejected_items: number
  /** TM4: what the backend removed or marked, and why (removed text is not kept). */
  check_notes: string[]
}
export interface StrategyComparison {
  kind: 'hypothetical'
  disclaimer: string
  assessments: StrategyAssessment[]
  rejected_citations: string[]
  adjusted_verdicts: number
  generated: GenerationInfo
}
/** TM4: POST …/ai/time-machine/assess. No free text; as_of defaults to now; include_memory is the explicit opt-in. */
export interface TimeMachineAssessRequest {
  strategy_id: StrategyTemplateId
  branch_at: ISODateTime
  anchor_ref?: string | null
  as_of?: ISODateTime | null
  include_memory?: boolean
}
export interface TimeMachineAssessment {
  status: 'generated' | 'insufficient_evidence'
  customer_id: string
  deal_id: string
  branch_at: ISODateTime
  as_of: ISODateTime
  strategy: StrategyOption
  evidence_map: StrategyEvidenceMap
  comparison: StrategyComparison | null
  sources: EvidenceSource[]
  memory: MemoryEvidenceStatus
  insufficient_reason: string | null
  note: string
}

/** TM2: one catalogue entry at a branch point. evidence_map is null when not offered or no anchor is chosen yet. */
export interface StrategyChoice {
  template: StrategyTemplate
  reasons: string[]
  anchor_candidates: string[]
  evidence_map: StrategyEvidenceMap | null
}
/** TM2: GET …/time-machine/strategies. Hypothetical alternatives, never predictions; maps cite `sources` only. */
export interface StrategyCatalogue {
  kind: 'hypothetical'
  disclaimer: string
  customer_id: string
  deal_id: string
  branch_at: ISODateTime
  as_of: ISODateTime
  strategies: StrategyChoice[]
  sources: EvidenceSource[]
  memory: MemoryEvidenceStatus
}
