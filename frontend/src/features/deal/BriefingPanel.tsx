import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../../api/client'
import type { AIStatus, BriefingResponse, EvidenceSource } from '../../api/types'
import { CLAIM_KIND, EVIDENCE_KIND } from '../../lib/evidence'
import { formatDateTime } from '../../lib/format'
import { memoryLine } from '../../lib/memory'
import { useInspector } from '../../lib/inspector'
import { Button } from '../../ui/Button'
import { Citation } from '../../ui/Citation'
import { EvidenceBlock, EvidenceMark } from '../../ui/EvidenceMark'
import { StatusNotice } from '../../ui/StatusNotice'
import styles from './Deal.module.css'

type BriefingState =
  | { status: 'idle' }
  | { status: 'loading'; startedAt: number }
  | { status: 'ok'; data: BriefingResponse; seconds: number }
  | { status: 'error'; error: ApiError }

function SourceChips({ refs, sources }: { refs: string[]; sources: Map<string, EvidenceSource> }) {
  const { open, item } = useInspector()
  return (
    <span className={styles.chips}>
      {refs.map((ref) => {
        const src = sources.get(ref)
        if (!src) return <span key={ref} className={styles.mono}>{ref}</span>
        const key = `evidence:${ref}`
        return (
          <Citation key={ref} refId={ref} pressed={item?.key === key}
            description={`${EVIDENCE_KIND[src.kind].label}: ${src.excerpt.slice(0, 80)}`}
            onOpen={() => open({ kind: 'evidence', source: src, key })} />
        )
      })}
    </span>
  )
}

function Result({ data, seconds }: { data: BriefingResponse; seconds: number }) {
  const sources = new Map(data.sources.map((s) => [s.ref, s]))
  const allRefs = data.sources.map((s) => s.ref)
  if (data.status === 'insufficient_evidence' || !data.briefing) {
    return (
      <>
        <StatusNotice tone="warning" title="Not enough evidence for a briefing" live>
          {data.insufficient_reason ?? 'The recorded evidence is not sufficient.'} No briefing was generated and the
          model was not called.
        </StatusNotice>
        <p className={styles.rowMeta}>Evidence available: <SourceChips refs={allRefs} sources={sources} /></p>
        <p className={styles.rowMeta}>{memoryLine(data.memory)}</p>
      </>
    )
  }
  const b = data.briefing
  return (
    <div aria-live="polite">
      <ol className={styles.stack}>
        {b.claims.map((claim, i) => (
          <EvidenceBlock key={i} meta={CLAIM_KIND[claim.kind]} as="li">
            <p className={styles.markLine}><EvidenceMark meta={CLAIM_KIND[claim.kind]} /></p>
            <p className={`${styles.claimText} ${claim.kind === 'unsupported' ? styles.struck : ''}`}>{claim.text}</p>
            {claim.citations.length ? (
              <p className={styles.rowMeta}>Evidence: <SourceChips refs={claim.citations} sources={sources} /></p>
            ) : (
              <p className={styles.rowMeta}>No supporting evidence cited.</p>
            )}
            {claim.grounding_issues.length ? (
              <ul className={styles.issues} aria-label="Grounding issues">
                {claim.grounding_issues.map((g) => <li key={g}>{g}</li>)}
              </ul>
            ) : null}
          </EvidenceBlock>
        ))}
      </ol>

      {b.missing_evidence.length ? (
        <>
          <h3 className={styles.subhead}>Missing evidence</h3>
          <ul className={styles.plainList}>{b.missing_evidence.map((m) => <li key={m}>{m}</li>)}</ul>
        </>
      ) : null}

      <h3 className={styles.subhead}>Checks applied</h3>
      <ul className={styles.plainList}>
        <li>{b.unsupported_claims} claim{b.unsupported_claims === 1 ? '' : 's'} marked unsupported by the grounding check.</li>
        <li>{b.relabelled_claims} claim{b.relabelled_claims === 1 ? '' : 's'} relabelled as inference for lacking matching evidence.</li>
        <li>
          {b.rejected_citations.length
            ? <>Rejected citations (not in the evidence): <span className={styles.mono}>{b.rejected_citations.join(', ')}</span></>
            : 'No citations were rejected.'}
        </li>
        <li>{memoryLine(data.memory)}</li>
      </ul>
      <p className={styles.note}>{data.note}</p>

      <details className={styles.details}>
        <summary>All evidence given to the model ({data.sources.length})</summary>
        <p className={styles.rowMeta}><SourceChips refs={allRefs} sources={sources} /></p>
      </details>

      <p className={styles.small}>
        Generated {formatDateTime(b.generated.generated_at)} by {b.generated.provider}
        {b.generated.model ? ` (${b.generated.model})` : ''} in {seconds.toFixed(1)} s · evidence as of{' '}
        {formatDateTime(data.as_of)} · not saved.
      </p>
    </div>
  )
}

function ErrorNotice({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const retry = <Button size="sm" icon="refresh" onClick={onRetry}>Try again</Button>
  if (error.code === 'ai_unavailable') {
    return (
      <StatusNotice tone="warning" title="Local AI is unavailable" action={retry} live>
        {error.message} Deal data and signals remain available.
      </StatusNotice>
    )
  }
  if (error.code === 'ai_provider_error') {
    return (
      <StatusNotice tone="error" title="The model's output could not be used" action={retry}>
        {error.message}. Nothing was shown or saved.
      </StatusNotice>
    )
  }
  if (error.code === 'network_error') {
    return <StatusNotice tone="error" title="The backend is unreachable" action={retry}>{error.message}</StatusNotice>
  }
  return <StatusNotice tone="error" title="Briefing request failed" action={retry}>{error.message}</StatusNotice>
}

/** On-demand, evidence-grounded briefing. No question input; nothing is stored or sent. */
export function BriefingPanel({ customerId, dealId, ai }: { customerId: string; dealId: string; ai: AIStatus | null }) {
  const [state, setState] = useState<BriefingState>({ status: 'idle' })
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => () => controllerRef.current?.abort(), [])

  const generate = () => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const startedAt = performance.now()
    setState({ status: 'loading', startedAt })
    api.ai
      .briefing(customerId, dealId, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return
        if (data.customer_id !== customerId || data.deal_id !== dealId) {
          setState({ status: 'error', error: new ApiError(0, 'scope_mismatch', 'Response was for a different deal', null) })
          return
        }
        setState({ status: 'ok', data, seconds: (performance.now() - startedAt) / 1000 })
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setState({ status: 'error', error: err instanceof ApiError ? err : new ApiError(0, 'client_error', String(err), null) })
      })
  }

  const unavailable = ai && !ai.available
  return (
    <>
      {unavailable && state.status === 'idle' ? (
        <StatusNotice tone="warning" title="Local AI is not available right now">
          {ai.reason ?? 'The AI provider reported itself unavailable.'} You can still try; deal data and signals work
          without it.
        </StatusNotice>
      ) : null}
      <p className={styles.briefingActions}>
        <Button variant="primary" onClick={generate} disabled={state.status === 'loading'} busy={state.status === 'loading'}>
          {state.status === 'loading' ? 'Generating…' : state.status === 'ok' ? 'Generate again' : 'Generate briefing'}
        </Button>
        <span className={styles.small}>
          Uses only this deal's recorded evidence. Runs on the local model ({ai?.model ?? 'model not reported'}); may take
          up to about 30 s. Not saved.
        </span>
      </p>
      {state.status === 'loading' ? (
        <div role="status" aria-live="polite" className={styles.small}>Generating briefing from recorded evidence…</div>
      ) : null}
      {state.status === 'error' ? <ErrorNotice error={state.error} onRetry={generate} /> : null}
      {state.status === 'ok' ? <Result data={state.data} seconds={state.seconds} /> : null}
    </>
  )
}
