import type { Signal } from '../../api/types'
import { SIGNAL_NATURE } from '../../lib/evidence'
import { formatDate, humanize } from '../../lib/format'
import { useInspector } from '../../lib/inspector'
import { EvidenceBlock, EvidenceMark } from '../../ui/EvidenceMark'
import styles from './Deal.module.css'

function dateFacts(signal: Signal): string[] {
  return Object.entries(signal.facts)
    .filter(([k, v]) => typeof v === 'string' && (k.endsWith('_at') || k.includes('date')))
    .map(([k, v]) => `${humanize(k)}: ${formatDate(v as string)}`)
}

function SignalRow({ signal }: { signal: Signal }) {
  const { open, item } = useInspector()
  const key = `signal:${signal.id}`
  const dates = dateFacts(signal)
  return (
    <EvidenceBlock meta={SIGNAL_NATURE[signal.nature]} as="li">
      <p className={styles.markLine}>
        <EvidenceMark meta={SIGNAL_NATURE[signal.nature]} detail={`${signal.severity} severity`} />
      </p>
      <p className={styles.claimText}>{signal.title}</p>
      <p className={styles.explain}>{signal.explanation}</p>
      <p className={styles.rowMeta}>
        Rule <span className={styles.mono}>{signal.rule.id}</span>
        {dates.length ? <> · {dates.join(' · ')}</> : null}
        {' · '}
        <button type="button" className={styles.inlineButton} aria-pressed={item?.key === key}
          onClick={() => open({ kind: 'signal', signal, key })}>
          Inspect rule and {signal.sources.length} source record{signal.sources.length === 1 ? '' : 's'}
        </button>
      </p>
    </EvidenceBlock>
  )
}

/** Deterministic M4 findings. Findings and missing-information/data-quality items are listed apart. */
export function SignalLedger({ signals, customerSignals }: { signals: Signal[]; customerSignals: Signal[] }) {
  const all = [...signals, ...customerSignals]
  const findings = all.filter((s) => s.nature === 'finding')
  const gaps = all.filter((s) => s.nature !== 'finding')
  if (all.length === 0) {
    return <p className={styles.empty}>No findings: none of the deterministic rules fired for this deal.</p>
  }
  return (
    <>
      <h3 className={styles.subhead}>Findings ({findings.length})</h3>
      {findings.length ? (
        <ul className={styles.stack}>{findings.map((s) => <SignalRow key={s.id} signal={s} />)}</ul>
      ) : (
        <p className={styles.empty}>No negative findings.</p>
      )}
      <h3 className={styles.subhead}>Missing information &amp; data quality ({gaps.length})</h3>
      {gaps.length ? (
        <ul className={styles.stack}>{gaps.map((s) => <SignalRow key={s.id} signal={s} />)}</ul>
      ) : (
        <p className={styles.empty}>Nothing missing according to the rules.</p>
      )}
    </>
  )
}
