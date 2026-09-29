import type { ReactNode } from 'react'
import type { Severity } from '../api/types'
import styles from './Badge.module.css'

export type BadgeTone = 'neutral' | 'signal' | 'caution' | 'quiet' | 'ok' | 'data'

/** A compact status label. The text always carries the meaning; tone and glyph only reinforce it. */
export function Badge({ tone = 'neutral', glyph, children, title }: {
  tone?: BadgeTone
  glyph?: string
  children: ReactNode
  title?: string
}) {
  return (
    <span className={`${styles.badge} ${styles[tone]}`} title={title}>
      {glyph ? <span aria-hidden="true" className={styles.glyph}>{glyph}</span> : null}
      {children}
    </span>
  )
}

const SEVERITY: Record<Severity, { tone: BadgeTone; glyph: string; label: string }> = {
  high: { tone: 'signal', glyph: '▲', label: 'High' },
  medium: { tone: 'caution', glyph: '◆', label: 'Medium' },
  low: { tone: 'quiet', glyph: '○', label: 'Low' },
}

/** Deterministic rule severity (never a score or a prediction). */
export function SeverityBadge({ severity, suffix }: { severity: Severity | null; suffix?: string }) {
  if (!severity) return <Badge tone="neutral" glyph="–">No signals</Badge>
  const s = SEVERITY[severity]
  return <Badge tone={s.tone} glyph={s.glyph}>{s.label}{suffix ? ` ${suffix}` : ''}</Badge>
}

/** Synthetic demo records are always labelled as such. */
export function SyntheticBadge() {
  return <Badge tone="data" glyph="◌" title="Created for demonstration; not a real customer">Synthetic demo data</Badge>
}
