import { useEffect, useRef } from 'react'
import { EVIDENCE_KIND, SIGNAL_NATURE } from '../lib/evidence'
import { formatDate, formatDateTime, formatMoney, humanize } from '../lib/format'
import { useInspector, type InspectorItem, type ResolvedRecord } from '../lib/inspector'
import { EvidenceMark } from './EvidenceMark'
import styles from './Inspector.module.css'

function Missing() {
  return <span className={styles.missing}>not recorded</span>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.field}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function RecordLink({ type, id }: { type: string; id: string }) {
  const { resolve, open } = useInspector()
  const record = resolve(type, id)
  if (!record) {
    return (
      <span className={styles.mono}>
        {type} {id}
      </span>
    )
  }
  return (
    <button type="button" className={styles.link} onClick={() => open({ kind: 'record', record, key: `${type}:${id}` })}>
      {type} {id}
    </button>
  )
}

function RecordView({ record }: { record: ResolvedRecord }) {
  switch (record.type) {
    case 'deal': {
      const d = record.data
      return (
        <dl className={styles.fields}>
          <Field label="Title">{d.title}</Field>
          <Field label="Stage / status">{humanize(d.stage)} · {humanize(d.status)}</Field>
          <Field label="Value">{formatMoney(d.value_minor, d.currency) ?? <Missing />}</Field>
          <Field label="Expected close">{formatDate(d.expected_close_date) ?? <Missing />}</Field>
          <Field label="Owner">{d.owner_name ?? <Missing />}</Field>
        </dl>
      )
    }
    case 'interaction': {
      const i = record.data
      return (
        <dl className={styles.fields}>
          <Field label="When">{formatDateTime(i.occurred_at)}</Field>
          <Field label="Channel">{i.channel}</Field>
          <Field label="Title">{i.title ?? <Missing />}</Field>
          <Field label="Rep note (written by our sales rep)">
            <p className={styles.note}>{i.notes}</p>
          </Field>
        </dl>
      )
    }
    case 'commitment': {
      const c = record.data
      return (
        <dl className={styles.fields}>
          <Field label="Commitment">{c.description}</Field>
          <Field label="Owner">{c.owner_party === 'us' ? 'Us' : 'Customer'}{c.owner_name ? ` · ${c.owner_name}` : ''}</Field>
          <Field label="Due">{formatDate(c.due_date) ?? <Missing />}</Field>
          <Field label="Status">{c.status}</Field>
          {c.source_interaction_id ? (
            <Field label="Source">
              <RecordLink type="interaction" id={c.source_interaction_id} />
            </Field>
          ) : null}
        </dl>
      )
    }
    case 'stakeholder': {
      const s = record.data
      return (
        <dl className={styles.fields}>
          <Field label="Name">{s.name}</Field>
          <Field label="Role">{s.role ?? <Missing />}</Field>
          <Field label="Influence">{s.influence}</Field>
          <Field label="Recorded priorities">{s.priorities.length ? s.priorities.join(', ') : <Missing />}</Field>
        </dl>
      )
    }
  }
}

function Body({ item }: { item: InspectorItem }) {
  const { resolve } = useInspector()
  if (item.kind === 'record') {
    return (
      <>
        <h2 className={styles.title}>{humanize(item.record.type)}</h2>
        <p className={styles.sub}>
          <span className={styles.mono}>{item.record.data.id}</span>
        </p>
        <RecordView record={item.record} />
      </>
    )
  }
  if (item.kind === 'signal') {
    const s = item.signal
    return (
      <>
        <p>
          <EvidenceMark meta={SIGNAL_NATURE[s.nature]} detail={`${s.severity} severity`} />
        </p>
        <h2 className={styles.title}>{s.title}</h2>
        <p className={styles.text}>{s.explanation}</p>
        <dl className={styles.fields}>
          <Field label="Rule">
            <span className={styles.mono}>{s.rule.id} · v{s.rule.version}</span>
            <p className={styles.small}>{s.rule.description}</p>
          </Field>
          {Object.keys(s.rule.thresholds).length ? (
            <Field label="Thresholds">
              <FactList facts={s.rule.thresholds} />
            </Field>
          ) : null}
          {Object.keys(s.facts).length ? (
            <Field label="Compared values">
              <FactList facts={s.facts} />
            </Field>
          ) : null}
          <Field label="Source records">
            <ul className={styles.list}>
              {s.sources.map((r) => (
                <li key={`${r.type}:${r.id}`}>
                  <RecordLink type={r.type} id={r.id} />
                </li>
              ))}
            </ul>
          </Field>
        </dl>
      </>
    )
  }
  const src = item.source
  const record = resolve(src.source_type === 'memory' ? 'interaction' : src.source_type, src.source_id)
  return (
    <>
      <p>
        <EvidenceMark meta={EVIDENCE_KIND[src.kind]} />
      </p>
      <h2 className={styles.title}>Evidence {src.ref}</h2>
      {item.note ? <p className={styles.sub}>{item.note}</p> : null}
      <p className={styles.sub}>{item.note ? 'Exact evidence text:' : 'Exactly the text the model was given:'}</p>
      <p className={styles.excerpt}>{src.excerpt}</p>
      <dl className={styles.fields}>
        <Field label="Source">
          <span className={styles.mono}>
            {src.source_type}
            {src.source_id ? ` ${src.source_id}` : ''}
          </span>
        </Field>
        <Field label="Occurred">{formatDateTime(src.occurred_at) ?? <Missing />}</Field>
        {src.memory_ref ? (
          <Field label="Hindsight memory">
            <span className={styles.mono}>
              {src.memory_ref.memory_id} in {src.memory_ref.bank_id}
            </span>
            <p className={styles.small}>Extracted from the linked interaction's rep note.</p>
          </Field>
        ) : null}
        {src.related_records.length ? (
          <Field label="Records behind this signal">
            <ul className={styles.list}>
              {src.related_records.map((r) => (
                <li key={`${r.type}:${r.id}`}>
                  <RecordLink type={r.type} id={r.id} />
                </li>
              ))}
            </ul>
          </Field>
        ) : null}
      </dl>
      {record ? (
        <div className={styles.record}>
          <h3 className={styles.recordTitle}>Source record ({humanize(record.type)}, as currently stored)</h3>
          <RecordView record={record} />
        </div>
      ) : null}
    </>
  )
}

function FactList({ facts }: { facts: Record<string, string | number | boolean | null> }) {
  return (
    <ul className={styles.list}>
      {Object.entries(facts).map(([k, v]) => (
        <li key={k}>
          <span className={styles.factKey}>{humanize(k)}</span> <span className={styles.mono}>{v === null ? '—' : String(v)}</span>
        </li>
      ))}
    </ul>
  )
}

/** The inspector pane (docs/ui-ux-direction.md §5.1): citations open here, never elsewhere. */
export function Inspector() {
  const { item, close } = useInspector()
  const headingRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (item) headingRef.current?.focus()
  }, [item])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  if (!item) {
    return (
      <aside className={styles.pane} aria-label="Inspector">
        <p className={styles.empty}>Select a citation, finding or record to inspect its source here.</p>
      </aside>
    )
  }
  return (
    <aside className={`${styles.pane} ${styles.open}`} aria-label="Inspector">
      <div className={styles.bar}>
        <span className={styles.barLabel}>Inspector</span>
        <button type="button" className={styles.close} onClick={close}>
          Close <span aria-hidden="true">(Esc)</span>
        </button>
      </div>
      <div ref={headingRef} tabIndex={-1} className={styles.content}>
        <Body item={item} />
      </div>
    </aside>
  )
}
