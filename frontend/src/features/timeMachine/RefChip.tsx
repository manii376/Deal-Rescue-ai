import type { EvidenceSource } from '../../api/types'
import { EVIDENCE_KIND } from '../../lib/evidence'
import { humanize } from '../../lib/format'
import { useInspector } from '../../lib/inspector'
import { Citation } from '../../ui/Citation'
import { parseRecordRef } from './branchTime'
import styles from './TimeMachine.module.css'

/**
 * One reference from a strategy response. Three shapes, all structured (never parsed from prose):
 * an evidence ref in `sources` (opens the evidence), a "type:id" record ref (opens the record if it is loaded for
 * this deal, otherwise says it cannot be expanded here), or a rule id (shown as a rule).
 */
export function RefChip({ value, sources, knownNote }: {
  value: string
  sources: Map<string, EvidenceSource>
  knownNote: string
}) {
  const { open, item, resolve } = useInspector()
  const src = sources.get(value)
  if (src) {
    const key = `tm-evidence:${value}`
    return (
      <Citation refId={value} pressed={item?.key === key}
        description={`${EVIDENCE_KIND[src.kind].label}: ${src.excerpt.slice(0, 80)}`}
        onOpen={() => open({ kind: 'evidence', source: src, key, note: knownNote })} />
    )
  }
  const record = parseRecordRef(value)
  if (record) {
    const resolved = record.type === 'customer' ? undefined : resolve(record.type, record.id)
    if (resolved) {
      const key = `${record.type}:${record.id}`
      return (
        <button type="button" className={styles.recordRef} aria-pressed={item?.key === key}
          onClick={() => open({ kind: 'record', record: resolved, key })}>
          {humanize(record.type)} record
        </button>
      )
    }
    return (
      <span className={styles.plainRef} title={`${record.type} ${record.id}`}>
        {humanize(record.type)} record <span className={styles.muted}>(not expandable in this view)</span>
      </span>
    )
  }
  if (/^[A-Z]\d+$/.test(value)) {
    // An evidence-style ref that is not in this response's sources: never expanded or guessed at.
    return <span className={styles.plainRef}>{value} <span className={styles.muted}>(not in the evidence shown)</span></span>
  }
  return <span className={styles.ruleRef} title="Deterministic rule">rule {value}</span>
}
