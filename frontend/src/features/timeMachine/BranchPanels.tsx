import type { EvidenceSource, MemoryEvidenceStatus, Signal, TimelineEvent } from '../../api/types'
import { DATE_BASIS, EVIDENCE_KIND, TIMELINE_KIND } from '../../lib/evidence'
import { formatDate, formatDateTime } from '../../lib/format'
import { useInspector } from '../../lib/inspector'
import { memoryLine } from '../../lib/memory'
import { Badge } from '../../ui/Badge'
import { EvidenceBlock, EvidenceMark } from '../../ui/EvidenceMark'
import { StatusNotice } from '../../ui/StatusNotice'
import { SignalLedger } from '../deal/SignalLedger'
import styles from './TimeMachine.module.css'

/** Evidence recorded by the branch point: exactly the refs the strategy maps may cite. */
export function KnownThen({ sources, signals, branchAt }: {
  sources: EvidenceSource[]
  signals: Signal[] | null
  branchAt: string
}) {
  const { open, item } = useInspector()
  const note = `Evidence known at ${formatDateTime(branchAt)}.`
  const items = sources.filter((s) => s.source_type !== 'signal')
  const substantive = items.some((s) => s.kind === 'rep_note' || s.kind === 'memory' || s.source_type === 'commitment')
  return (
    <>
      {!substantive ? (
        <StatusNotice tone="info" title="Little was recorded by this date">
          No rep note, linked memory or commitment was recorded by {formatDate(branchAt)}. Only the deal record and the
          rules evaluated at that date are available.
        </StatusNotice>
      ) : null}
      <ul className={styles.evidenceList}>
        {items.map((s) => {
          const key = `tm-evidence:${s.ref}`
          const current = s.source_type === 'deal'
          return (
            <EvidenceBlock key={s.ref} meta={EVIDENCE_KIND[s.kind]} as="li">
              <p className={styles.markRow}>
                <EvidenceMark meta={EVIDENCE_KIND[s.kind]} detail={s.occurred_at ? formatDate(s.occurred_at) ?? undefined : undefined} />
                {current ? <Badge tone="caution" glyph="!">Current values, not history</Badge> : null}
                <button type="button" className={styles.refButton} aria-pressed={item?.key === key}
                  onClick={() => open({ kind: 'evidence', source: s, key, note })}
                  aria-label={`Inspect evidence ${s.ref}`}>
                  {s.ref}
                </button>
              </p>
              <p className={styles.excerpt}>{s.excerpt}</p>
            </EvidenceBlock>
          )
        })}
      </ul>
      <h4 className={styles.subhead}>Rules evaluated at this date</h4>
      {signals ? <SignalLedger signals={signals} customerSignals={[]} /> : <p className={styles.muted}>Loading rule results…</p>}
    </>
  )
}

/** Recorded events after the branch point, up to now. Absence of records is not proof nothing happened. */
export function ActuallyFollowed({ events, branchAt, asOf }: { events: TimelineEvent[]; branchAt: string; asOf: string }) {
  const { resolve, open } = useInspector()
  if (!events.length) {
    return (
      <p className={styles.empty}>
        No events are recorded between {formatDateTime(branchAt)} and {formatDateTime(asOf)}. This means nothing was
        recorded, not that nothing happened.
      </p>
    )
  }
  return (
    <ol className={styles.followed}>
      {events.map((e) => {
        const record = e.source ? resolve(e.source.type, e.source.id) : undefined
        return (
          <li key={e.id} className={styles.followedItem}>
            <span className={styles.eventDate}>{formatDateTime(e.at)}</span>
            <span className={styles.eventBody}>
              <EvidenceMark meta={TIMELINE_KIND[e.evidence_kind]} detail={DATE_BASIS[e.date_basis]} />
              <span className={styles.eventTitle}>{e.title}</span>
            </span>
            {record && e.source ? (
              <button type="button" className={styles.linkButton}
                onClick={() => open({ kind: 'record', record, key: `${e.source!.type}:${e.source!.id}` })}>
                Inspect
              </button>
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * Explicit memory opt-in. Recall costs a Hindsight read, so it is never triggered by loading, a date change or an
 * anchor change: the opt-in applies to the current branch point and anchor only.
 */
export function MemoryControl({ enabled, onChange, status, busy }: {
  enabled: boolean
  onChange: (enabled: boolean) => void
  status: MemoryEvidenceStatus | null
  busy: boolean
}) {
  return (
    <div className={styles.memory}>
      <label className={styles.memoryToggle}>
        <input type="checkbox" checked={enabled} onChange={(e) => onChange(e.target.checked)} disabled={busy}
          aria-describedby="tm-memory-help" />
        <span>Include Hindsight memory for this date</span>
      </label>
      <p id="tm-memory-help" className={styles.muted}>
        Runs one Hindsight recall for the evidence below (a small usage charge). Only memories linked to an interaction
        that occurred by the branch point are used. Changing the date or anchor switches this off again.
      </p>
      {status ? (
        <p className={`${styles.memoryStatus} ${status.status === 'unavailable' ? styles.memoryDown : ''}`} role="status">
          {memoryLine(status)}
        </p>
      ) : null}
    </div>
  )
}
