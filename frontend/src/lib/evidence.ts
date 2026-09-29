// Evidence taxonomy (docs/ui-ux-direction.md §6.1): one place that maps kinds to label, glyph and style.
// Components pass a kind; they never choose colours themselves. Meaning never relies on colour alone.

import type { ClaimKind, DateBasis, EvidenceKind, SignalNature, TimelineEvidenceKind } from '../api/types'

/** Visual family; each maps to CSS in ui/EvidenceMark.module.css. */
export type MarkStyle = 'recorded' | 'repnote' | 'statement' | 'inference' | 'hypothetical' | 'unsupported' | 'missing' | 'quality'

export interface MarkMeta {
  label: string
  glyph: string
  style: MarkStyle
  description: string
}

export const CLAIM_KIND: Record<ClaimKind, MarkMeta> = {
  recorded: { label: 'Recorded', glyph: '■', style: 'recorded', description: 'Stated in recorded deal data or a deterministic signal' },
  rep_note: { label: 'Rep note', glyph: '✎', style: 'repnote', description: "From notes written by our sales rep — the rep's account, not the customer's words" },
  statement: { label: 'Customer statement', glyph: '❝', style: 'statement', description: 'A verbatim customer statement' },
  inference: { label: 'Inference · unconfirmed', glyph: '◇', style: 'inference', description: 'Concluded by the model; not directly stated in the evidence' },
  unsupported: {
    label: 'Unsupported',
    glyph: '⚠',
    style: 'unsupported',
    description: 'Mentions numbers or names that are not in its evidence; citations were removed',
  },
}

export const EVIDENCE_KIND: Record<EvidenceKind, MarkMeta> = {
  recorded: { label: 'Recorded', glyph: '■', style: 'recorded', description: 'Recorded deal data' },
  signal: { label: 'Deterministic signal', glyph: '■', style: 'recorded', description: 'Rule-based finding computed from recorded data' },
  rep_note: { label: 'Rep note', glyph: '✎', style: 'repnote', description: "Interaction note written by our sales rep (not the customer's words)" },
  memory: { label: 'Memory · linked', glyph: '✎', style: 'repnote', description: 'Hindsight memory extracted from a rep note, linked to a current source record' },
  statement: { label: 'Customer statement', glyph: '❝', style: 'statement', description: 'A verbatim customer statement' },
  outcome: { label: 'Recorded outcome', glyph: '■', style: 'recorded', description: 'A recorded deal outcome' },
}

export const SIGNAL_NATURE: Record<SignalNature, MarkMeta> = {
  finding: { label: 'Finding', glyph: '■', style: 'recorded', description: 'A negative fact derived from recorded data' },
  missing_information: {
    label: 'Missing information',
    glyph: '○',
    style: 'missing',
    description: 'Something cannot be assessed because a record or field is absent — not evidence of a problem',
  },
  data_quality: { label: 'Data quality', glyph: '◌', style: 'quality', description: 'Records that look wrong and were excluded or should be fixed' },
}

// -- Deal Time Machine (TM3) ------------------------------------------------------------------------------

/** A strategy scenario: hypothetical, exploratory, never a prediction (docs/ui-ux-direction.md §6.1). */
export const SCENARIO: MarkMeta = {
  label: 'Scenario · exploratory',
  glyph: '⧉',
  style: 'hypothetical',
  description: 'A hypothetical alternative compared against recorded evidence; not a prediction',
}

export const HYPOTHETICAL_COMMITMENT: MarkMeta = {
  label: 'Hypothetical commitment',
  glyph: '⧉',
  style: 'hypothetical',
  description: 'A commitment this scenario would create; it does not exist in the records',
}

/** Timeline events carry recorded / rep_note / signal kinds only; rule checkpoints are computed dates. */
export const TIMELINE_KIND: Record<TimelineEvidenceKind, MarkMeta> = {
  recorded: EVIDENCE_KIND.recorded,
  rep_note: EVIDENCE_KIND.rep_note,
  signal: { label: 'Rule checkpoint', glyph: '◆', style: 'recorded', description: 'A date computed by a deterministic rule from recorded data' },
}

/** What an event's date means. Entry dates are not event dates; computed dates are not recorded dates. */
export const DATE_BASIS: Record<DateBasis, string> = {
  occurred: 'occurred',
  due: 'due date',
  completed: 'marked done',
  entered_in_system: 'entered in system',
  rule_computed: 'computed by rule',
}
