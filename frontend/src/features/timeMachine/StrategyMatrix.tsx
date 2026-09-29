import { useState } from 'react'
import type { EvidenceSource, StrategyCatalogue, StrategyChoice } from '../../api/types'
import { EVIDENCE_KIND, HYPOTHETICAL_COMMITMENT, SCENARIO } from '../../lib/evidence'
import { formatDate } from '../../lib/format'
import { Badge } from '../../ui/Badge'
import { Button } from '../../ui/Button'
import { EvidenceBlock, EvidenceMark } from '../../ui/EvidenceMark'
import { RefChip } from './RefChip'
import styles from './TimeMachine.module.css'

function Refs({ values, sources, note }: { values: string[]; sources: Map<string, EvidenceSource>; note: string }) {
  return (
    <span className={styles.chips}>
      {values.map((v) => <RefChip key={v} value={v} sources={sources} knownNote={note} />)}
    </span>
  )
}

/** Choose the requirement anchor from the backend's candidates only. Nothing is pre-selected. */
function AnchorPicker({ choice, sources, applied, onApply, busy }: {
  choice: StrategyChoice
  sources: Map<string, EvidenceSource>
  applied: string | null
  onApply: (ref: string | null) => void
  busy: boolean
}) {
  const [pending, setPending] = useState<string | null>(applied)
  const name = `tm-anchor-${choice.template.id}`
  return (
    <fieldset className={styles.anchor}>
      <legend>Requirement anchor (you choose)</legend>
      <p className={styles.muted}>Pick the recorded item that states the requirement. It is never detected automatically.</p>
      <ul className={styles.anchorList}>
        {choice.anchor_candidates.map((ref) => {
          const src = sources.get(ref)
          return (
            <li key={ref}>
              <label className={styles.anchorOption}>
                <input type="radio" name={name} value={ref} checked={pending === ref} onChange={() => setPending(ref)} />
                <span>
                  <span className={styles.anchorHead}>
                    <strong className={styles.mono}>{ref}</strong>
                    {src ? <EvidenceMark meta={EVIDENCE_KIND[src.kind]} detail={formatDate(src.occurred_at) ?? 'no event date'} /> : null}
                  </span>
                  {src ? <span className={styles.anchorExcerpt}>{src.excerpt.length > 140 ? `${src.excerpt.slice(0, 139)}…` : src.excerpt}</span> : null}
                </span>
              </label>
            </li>
          )
        })}
      </ul>
      <div className={styles.anchorActions}>
        <Button size="sm" variant="primary" disabled={!pending || pending === applied || busy} onClick={() => onApply(pending)}>
          Show evidence map
        </Button>
        {applied ? <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setPending(null); onApply(null) }}>Clear anchor</Button> : null}
      </div>
    </fieldset>
  )
}

function Cell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={styles.cell}>
      <h4 className={styles.cellTitle}>{title}</h4>
      {children}
    </div>
  )
}

function StrategyRow({ index, choice, sources, note, anchor, onAnchor, busy, assessment }: {
  index: number
  choice: StrategyChoice
  sources: Map<string, EvidenceSource>
  note: string
  anchor: string | null
  onAnchor: (ref: string | null) => void
  busy: boolean
  assessment?: React.ReactNode
}) {
  const t = choice.template
  const map = choice.evidence_map
  const none = <p className={styles.muted}>None recorded.</p>
  return (
    <li className={`${styles.strategy} ${t.offered ? '' : styles.strategyOff}`} aria-labelledby={`tm-s-${t.id}`}>
      <div className={styles.strategyHead}>
        <span className={styles.strategyNum} aria-hidden="true">{index + 1}</span>
        <h3 id={`tm-s-${t.id}`} className={styles.strategyTitle}>{t.label}</h3>
        {t.offered ? <Badge tone="ok" glyph="✓">Offered</Badge> : <Badge tone="neutral" glyph="–">Not offered</Badge>}
      </div>
      <EvidenceBlock meta={SCENARIO}>
        <p className={styles.markRow}><EvidenceMark meta={SCENARIO} /></p>
        <p className={styles.scenario}>{t.description}</p>
      </EvidenceBlock>

      <div className={styles.cells}>
        <Cell title={t.offered ? 'Why it is offered' : 'Why it is not offered'}>
          <ul className={styles.reasons}>{choice.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          {t.offered_because.length ? (
            <p className={styles.basedOn}>Based on: <Refs values={t.offered_because} sources={sources} note={note} /></p>
          ) : null}
        </Cell>
        <Cell title="Evidence known then">
          {map ? (map.related.length ? <Refs values={map.related} sources={sources} note={note} /> : none)
            : <p className={styles.muted}>{!t.offered ? 'Not applicable.' : t.needs_anchor ? 'Choose an anchor to build the map.' : 'Not available.'}</p>}
        </Cell>
        <Cell title="Evidence gaps">
          {map ? (map.gaps.length ? <ul className={styles.reasons}>{map.gaps.map((g) => <li key={g}>{g}</li>)}</ul> : none)
            : <p className={styles.muted}>—</p>}
        </Cell>
        <Cell title="Commitments it would create">
          {map && map.implied_commitments.length ? (
            <ul className={styles.stack}>
              {map.implied_commitments.map((c) => (
                <EvidenceBlock key={c.description} meta={HYPOTHETICAL_COMMITMENT} as="li">
                  <p className={styles.markRow}><EvidenceMark meta={HYPOTHETICAL_COMMITMENT} detail={c.owner_party === 'us' ? 'owner: us' : 'owner: customer'} /></p>
                  <p>{c.description}</p>
                </EvidenceBlock>
              ))}
            </ul>
          ) : <p className={styles.muted}>—</p>}
        </Cell>
      </div>

      {t.needs_anchor && t.offered && choice.anchor_candidates.length ? (
        <AnchorPicker key={anchor ?? 'none'} choice={choice} sources={sources} applied={anchor} onApply={onAnchor} busy={busy} />
      ) : null}
      {assessment}
    </li>
  )
}

/** The three fixed strategies in backend order. Eligibility and maps come from the backend only. */
export function StrategyMatrix({ catalogue, branchAt, anchor, onAnchor, busy, renderAssessment }: {
  catalogue: StrategyCatalogue
  branchAt: string
  anchor: string | null
  onAnchor: (ref: string | null) => void
  busy: boolean
  /** TM5: the AI assessment slot for a strategy (the matrix itself stays deterministic). */
  renderAssessment?: (choice: StrategyChoice) => React.ReactNode
}) {
  const sources = new Map(catalogue.sources.map((s) => [s.ref, s]))
  const note = `Evidence known at ${formatDate(branchAt)}.`
  return (
    <ol className={styles.strategies}>
      {catalogue.strategies.map((c, i) => (
        <StrategyRow key={c.template.id} index={i} choice={c} sources={sources} note={note} anchor={anchor}
          onAnchor={onAnchor} busy={busy} assessment={renderAssessment?.(c)} />
      ))}
    </ol>
  )
}
