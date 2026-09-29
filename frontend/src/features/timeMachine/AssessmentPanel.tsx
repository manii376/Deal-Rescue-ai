import type { AIStatus, CitedText, EvidenceSource, StrategyAssessment, TimeMachineAssessment } from '../../api/types'
import type { ApiError } from '../../api/client'
import { HYPOTHETICAL_COMMITMENT } from '../../lib/evidence'
import { formatDate, formatDateTime } from '../../lib/format'
import { memoryLine } from '../../lib/memory'
import { Badge } from '../../ui/Badge'
import { Button } from '../../ui/Button'
import { EvidenceBlock, EvidenceMark } from '../../ui/EvidenceMark'
import { StatusNotice } from '../../ui/StatusNotice'
import { RefChip } from './RefChip'
import styles from './TimeMachine.module.css'
import type { AssessEntry } from './useAssessments'

const AI_ASSESSMENT = {
  label: 'AI assessment · hypothetical', glyph: '⧉', style: 'hypothetical' as const,
  description: 'Exploratory reasoning by the local model over the evidence known then; not a prediction',
}

/** Neutral wording: the verdict is about the rationale, never about the outcome. */
const VERDICT: Record<StrategyAssessment['verdict'], string> = {
  supported: 'Rationale supported by recorded evidence',
  mixed: 'Mixed evidence',
  unsupported: 'Not supported by recorded evidence',
}

function Items({ title, items, sources, note, empty }: {
  title: string
  items: CitedText[]
  sources: Map<string, EvidenceSource>
  note: string
  empty: string
}) {
  return (
    <div className={styles.cell}>
      <h5 className={styles.cellTitle}>{title}</h5>
      {items.length ? (
        <ul className={styles.aiItems}>
          {items.map((item, i) => {
            const accepted = item.citations.length > 0 && item.issues.length === 0
            return (
              <li key={i} className={accepted ? undefined : styles.aiRejected}>
                <p className={accepted ? undefined : styles.struck}>{item.text}</p>
                {accepted ? (
                  <span className={styles.chips}>
                    {item.citations.map((c) => <RefChip key={c} value={c} sources={sources} knownNote={note} />)}
                  </span>
                ) : (
                  <ul className={styles.issues} aria-label="Why this was not accepted as grounded">
                    {item.issues.map((issue) => <li key={issue}>{issue}</li>)}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      ) : <p className={styles.muted}>{empty}</p>}
    </div>
  )
}

function Generated({ data }: { data: TimeMachineAssessment }) {
  const comparison = data.comparison
  if (!comparison || comparison.assessments.length !== 1) return null // the backend never returns this; stay safe
  const [a] = comparison.assessments
  const sources = new Map(data.sources.map((s) => [s.ref, s]))
  const note = `Evidence known at ${formatDateTime(data.branch_at)}.`
  return (
    <div className={styles.aiResult}>
      <p className={styles.markRow}>
        <EvidenceMark meta={AI_ASSESSMENT} />
        <Badge tone="neutral">{VERDICT[a.verdict]}</Badge>
      </p>
      <p className={styles.muted}>{comparison.disclaimer}</p>
      <div className={styles.cells}>
        <Items title="Why it could have been considered" items={a.supporting} sources={sources} note={note}
          empty="No supporting evidence was cited." />
        <Items title="What argues against it" items={a.contradicting} sources={sources} note={note}
          empty="No contradicting evidence was cited." />
        <div className={styles.cell}>
          <h5 className={styles.cellTitle}>Missing evidence</h5>
          {a.evidence_gaps.length
            ? <ul className={styles.reasons}>{a.evidence_gaps.map((g) => <li key={g}>{g}</li>)}</ul>
            : <p className={styles.muted}>None listed.</p>}
        </div>
        <div className={styles.cell}>
          <h5 className={styles.cellTitle}>Possible next steps</h5>
          {a.commitments_created.length ? (
            <ul className={styles.stack}>
              {a.commitments_created.map((c) => (
                <EvidenceBlock key={c} meta={HYPOTHETICAL_COMMITMENT} as="li">
                  <p className={styles.markRow}><EvidenceMark meta={HYPOTHETICAL_COMMITMENT} detail="not a recorded commitment" /></p>
                  <p>{c}</p>
                </EvidenceBlock>
              ))}
            </ul>
          ) : <p className={styles.muted}>None listed.</p>}
        </div>
      </div>

      <h5 className={styles.cellTitle}>Checks applied</h5>
      <ul className={styles.reasons}>
        <li>{a.rejected_items} item{a.rejected_items === 1 ? '' : 's'} removed or not accepted by the checks.</li>
        {a.check_notes.map((n) => <li key={n}>{n}</li>)}
        <li>
          {comparison.rejected_citations.length
            ? <>Citations not in the evidence known then (rejected): <span className={styles.mono}>{comparison.rejected_citations.join(', ')}</span></>
            : 'No citations were rejected.'}
        </li>
        {comparison.adjusted_verdicts ? <li>The verdict was weakened by the evidence rules.</li> : null}
        <li>{memoryLine(data.memory)}</li>
      </ul>
      <p className={styles.aiNote}>{data.note}</p>
      <p className={styles.muted}>
        Generated {formatDateTime(comparison.generated.generated_at)} by {comparison.generated.provider}
        {comparison.generated.model ? ` (${comparison.generated.model})` : ''} · evidence as of{' '}
        {formatDate(data.branch_at)} · not saved.
      </p>
    </div>
  )
}

function Failure({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const retry = <Button size="sm" icon="refresh" onClick={onRetry}>Try again</Button>
  switch (error.code) {
    case 'ai_unavailable':
      return (
        <StatusNotice tone="warning" title="The local AI provider is unavailable" action={retry} live>
          {error.message} The timeline, evidence and strategy maps above still work without it.
        </StatusNotice>
      )
    case 'ai_provider_error':
      return (
        <StatusNotice tone="error" title="The assessment could not be generated or validated" action={retry} live>
          {error.message}. Nothing from this attempt is shown or saved.
        </StatusNotice>
      )
    case 'network_error':
      return <StatusNotice tone="error" title="The backend is unreachable" action={retry} live>{error.message}</StatusNotice>
    case 'strategy_not_applicable':
    case 'invalid_anchor':
    case 'validation_error':
    case 'not_found':
    case 'scope_mismatch':
      return <StatusNotice tone="error" title="This assessment request was not accepted" live>{error.message}</StatusNotice>
    default:
      return <StatusNotice tone="error" title="The assessment request failed" action={retry} live>{error.message}</StatusNotice>
  }
}

/**
 * The AI part of one strategy row. The deterministic map above it never depends on this.
 * `blockedReason` explains why the action is unavailable (not offered, anchor needed, another request running).
 */
export function AssessmentPanel({ entry, blockedReason, includeMemory, ai, onAssess, onCancel }: {
  entry: AssessEntry | undefined
  blockedReason: string | null
  includeMemory: boolean
  ai: AIStatus | null
  onAssess: () => void
  onCancel: () => void
}) {
  const loading = entry?.status === 'loading'
  return (
    <section className={styles.ai} aria-label="AI assessment">
      <div className={styles.aiActions}>
        <Button size="sm" variant="secondary" onClick={onAssess} disabled={Boolean(blockedReason) || loading} busy={loading}>
          {loading ? 'Assessing…' : entry?.status === 'ok' ? 'Assess again' : 'Assess with local AI'}
        </Button>
        {loading ? <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button> : null}
        <span className={styles.muted}>
          {blockedReason ?? `One local model call${ai?.model ? ` (${ai.model})` : ''}, about 30 s, using only the evidence known at the branch point`}
          {!blockedReason ? (includeMemory ? ', plus one Hindsight recall (a small usage charge).' : '. Memory is not included.') : ''}
          {!blockedReason ? ' Not saved.' : ''}
        </span>
      </div>
      {ai && !ai.available && !entry ? (
        <p className={styles.muted}>The local AI reported itself unavailable ({ai.reason ?? 'no reason given'}); you can still try.</p>
      ) : null}
      {loading ? <p className={styles.muted} role="status">Assessing this strategy with the local model…</p> : null}
      {entry?.status === 'error' ? <Failure error={entry.error} onRetry={onAssess} /> : null}
      {entry?.status === 'ok' && entry.data.status === 'insufficient_evidence' ? (
        <StatusNotice tone="warning" title="Not enough recorded evidence to assess this strategy" live>
          {entry.data.insufficient_reason ?? 'The evidence known at the branch point is insufficient.'} The model was
          not called. {memoryLine(entry.data.memory)}
        </StatusNotice>
      ) : null}
      {entry?.status === 'ok' && entry.data.status === 'generated' ? (
        <div role="status" aria-live="polite"><Generated data={entry.data} /></div>
      ) : null}
    </section>
  )
}
