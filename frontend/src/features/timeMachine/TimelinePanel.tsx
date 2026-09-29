import { useState, type FormEvent } from 'react'
import type { DealTimeline, TimelineEvent } from '../../api/types'
import { DATE_BASIS, TIMELINE_KIND } from '../../lib/evidence'
import { formatDate, formatDateTime } from '../../lib/format'
import { useInspector } from '../../lib/inspector'
import { Button } from '../../ui/Button'
import { EvidenceMark } from '../../ui/EvidenceMark'
import { eventsUpToNow, fromUtcInput, isAfter, sameInstant, toUtcInput } from './branchTime'
import styles from './TimeMachine.module.css'

/** Visual track (decorative; the list below is the accessible equivalent). Real date scale, no heights or scores. */
function Track({ timeline, branchAt }: { timeline: DealTimeline; branchAt: string }) {
  const times = [...timeline.events.map((e) => Date.parse(e.at)), Date.parse(timeline.as_of), Date.parse(branchAt)]
  const min = Math.min(...times)
  const max = Math.max(...times)
  const span = Math.max(max - min, 1)
  const x = (iso: string) => 24 + ((Date.parse(iso) - min) / span) * 952
  const now = x(timeline.as_of)
  const branch = x(branchAt)
  return (
    <svg className={styles.track} viewBox="0 0 1000 64" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <line x1={24} x2={now} y1={32} y2={32} className={styles.trackPast} />
      {max > Date.parse(timeline.as_of) ? <line x1={now} x2={976} y1={32} y2={32} className={styles.trackAfter} /> : null}
      {timeline.events.map((e) => {
        const cx = x(e.at)
        if (e.kind === 'rule_checkpoint') {
          return <rect key={e.id} x={cx - 5} y={27} width={10} height={10} transform={`rotate(45 ${cx} 32)`} className={styles.tickRule} />
        }
        if (e.evidence_kind === 'rep_note') return <circle key={e.id} cx={cx} cy={32} r={5} className={styles.tickNote} />
        return <rect key={e.id} x={cx - 4} y={28} width={8} height={8} className={styles.tickRecorded} />
      })}
      <line x1={branch} x2={branch} y1={6} y2={58} className={styles.branchLine} />
      <line x1={now} x2={now} y1={6} y2={58} className={styles.nowLine} />
    </svg>
  )
}

function EventItem({ event, selected, onBranch, asOf }: {
  event: TimelineEvent
  selected: boolean
  onBranch: () => void
  asOf: string
}) {
  const { resolve, open } = useInspector()
  const future = isAfter(event.at, asOf)
  const record = event.source ? resolve(event.source.type, event.source.id) : undefined
  return (
    <li className={`${styles.event} ${selected ? styles.eventSelected : ''}`}>
      <span className={styles.eventDate}>{formatDateTime(event.at)}</span>
      <span className={styles.eventBody}>
        <EvidenceMark meta={TIMELINE_KIND[event.evidence_kind]} detail={DATE_BASIS[event.date_basis]} />
        <span className={styles.eventTitle}>{event.title}</span>
      </span>
      <span className={styles.eventActions}>
        {record && event.source ? (
          <button type="button" className={styles.linkButton}
            onClick={() => open({ kind: 'record', record, key: `${event.source!.type}:${event.source!.id}` })}>
            Inspect
          </button>
        ) : null}
        {future ? (
          <span className={styles.muted}>after now</span>
        ) : (
          <button type="button" className={styles.branchButton} aria-pressed={selected} onClick={onBranch}
            aria-label={`Branch at ${formatDateTime(event.at)}: ${event.title}`}>
            {selected ? 'Branch point' : 'Branch here'}
          </button>
        )}
      </span>
    </li>
  )
}

/** The recorded timeline with branch-point controls. Never offers a branch after NOW. */
export function TimelinePanel({ timeline, branchAt, onBranch }: {
  timeline: DealTimeline
  branchAt: string
  onBranch: (iso: string) => void
}) {
  const [draft, setDraft] = useState(() => toUtcInput(branchAt))
  const [error, setError] = useState<string | null>(null)
  const past = eventsUpToNow(timeline)
  const index = past.findIndex((e) => sameInstant(e.at, branchAt))
  const prev = [...past].reverse().find((e) => isAfter(branchAt, e.at))
  const next = past.find((e) => isAfter(e.at, branchAt))

  const choose = (iso: string) => {
    setError(null)
    setDraft(toUtcInput(iso))
    onBranch(iso)
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const iso = fromUtcInput(draft)
    if (!iso) return setError('Enter a complete date and time (UTC).')
    if (isAfter(iso, timeline.as_of)) return setError(`The branch point cannot be after now (${formatDateTime(timeline.as_of)}).`)
    choose(iso)
  }

  return (
    <div className={styles.timelinePanel}>
      {timeline.events.length ? <Track timeline={timeline} branchAt={branchAt} /> : null}
      <p className={styles.legend} aria-hidden="true">
        <span>● rep note</span> <span>■ recorded</span> <span>◆ computed by rule</span> <span>│ branch point</span> <span>┆ now</span>
      </p>

      <div className={styles.controls}>
        <Button size="sm" icon="chevronLeft" disabled={!prev} onClick={() => prev && choose(prev.at)}>Previous event</Button>
        <Button size="sm" iconAfter="chevronRight" disabled={!next} onClick={() => next && choose(next.at)}>Next event</Button>
        <form className={styles.dateForm} onSubmit={submit} noValidate>
          <label htmlFor="tm-branch-at" className={styles.dateLabel}>Branch point (UTC)</label>
          <input id="tm-branch-at" type="datetime-local" value={draft} max={toUtcInput(timeline.as_of)}
            onChange={(e) => setDraft(e.target.value)} aria-describedby="tm-branch-help"
            aria-invalid={error ? true : undefined} className={styles.dateInput} />
          <Button size="sm" type="submit">Go to date</Button>
        </form>
      </div>
      <p id="tm-branch-help" className={styles.muted}>
        {index >= 0 ? `Event ${index + 1} of ${past.length} up to now.` : 'A date between recorded events.'} Dates are
        in UTC; the branch point cannot be later than now ({formatDate(timeline.as_of)}).
      </p>
      {error ? <p className={styles.fieldError} role="alert">{error}</p> : null}

      {timeline.events.length === 0 ? (
        <p className={styles.empty}>No dated records exist for this deal yet. You can still pick a date; only the deal
          record and the rules evaluated at that date will be shown.</p>
      ) : (
        <ol className={styles.events} aria-label="Recorded timeline, oldest first">
          {timeline.events.map((e) => (
            <EventItem key={e.id} event={e} selected={sameInstant(e.at, branchAt)} asOf={timeline.as_of} onBranch={() => choose(e.at)} />
          ))}
        </ol>
      )}
    </div>
  )
}
