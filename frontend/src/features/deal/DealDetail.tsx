import { useEffect, useMemo } from 'react'
import { api } from '../../api/client'
import type { AIStatus, Commitment, Deal, Interaction, Stakeholder } from '../../api/types'
import { displayName, formatDate, formatDateTime, formatMoney, humanize } from '../../lib/format'
import { useInspector, type RecordResolver, type ResolvedRecord } from '../../lib/inspector'
import { useApi } from '../../lib/useApi'
import { routeHref } from '../../lib/route'
import { SyntheticBadge } from '../../ui/Badge'
import { buttonClass } from '../../ui/buttonClass'
import { Icon } from '../../ui/Icon'
import { PageHeader } from '../../ui/PageHeader'
import { Section } from '../../ui/Section'
import { Loading, StatusNotice } from '../../ui/StatusNotice'
import { BriefingPanel } from './BriefingPanel'
import styles from './Deal.module.css'
import { SignalLedger } from './SignalLedger'

function Missing({ what }: { what: string }) {
  return <span className={styles.missing}>{what} not recorded</span>
}

function RecordButton({ record, label }: { record: ResolvedRecord; label: string }) {
  const { open, item } = useInspector()
  const key = `${record.type}:${record.data.id}`
  return (
    <button type="button" className={styles.inlineButton} aria-pressed={item?.key === key}
      onClick={() => open({ kind: 'record', record, key })}>
      {label}
    </button>
  )
}

function CommitmentsLedger({ items }: { items: Commitment[] }) {
  if (!items.length) return <p className={styles.empty}>No commitments recorded for this deal.</p>
  return (
    <table className={styles.ledger}>
      <caption className="visually-hidden">Commitments for this deal</caption>
      <thead>
        <tr><th scope="col">Commitment</th><th scope="col">Owner</th><th scope="col">Due</th><th scope="col">Status</th><th scope="col"><span className="visually-hidden">Inspect</span></th></tr>
      </thead>
      <tbody>
        {items.map((c) => (
          <tr key={c.id}>
            <th scope="row">{c.description}</th>
            <td>{c.owner_party === 'us' ? 'Us' : 'Customer'}{c.owner_name ? ` · ${c.owner_name}` : ''}</td>
            <td className={styles.mono}>{formatDate(c.due_date) ?? <Missing what="Due date" />}</td>
            <td>{c.status}</td>
            <td><RecordButton record={{ type: 'commitment', data: c }} label="Inspect" /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function InteractionTimeline({ items }: { items: Interaction[] }) {
  if (!items.length) return <p className={styles.empty}>No interactions recorded for this deal.</p>
  return (
    <ol className={styles.timeline}>
      {items.map((i) => (
        <li key={i.id} className={styles.timelineItem}>
          <p className={styles.timelineDate}>{formatDateTime(i.occurred_at)}</p>
          <div>
            <p className={styles.markLine}>
              <span className={styles.repLabel}>Rep note · {i.channel}</span>
              {i.memory ? <span className={styles.small}> · memory {i.memory.status}</span> : null}
            </p>
            {i.title ? <p className={styles.claimText}>{i.title}</p> : null}
            <p className={styles.explain}>{i.notes}</p>
            <p className={styles.rowMeta}><RecordButton record={{ type: 'interaction', data: i }} label="Inspect record" /></p>
          </div>
        </li>
      ))}
    </ol>
  )
}

function StakeholderList({ items }: { items: Stakeholder[] }) {
  if (!items.length) return <p className={styles.empty}>No stakeholders recorded for this customer.</p>
  return (
    <ul className={styles.plainList}>
      {items.map((s) => (
        <li key={s.id}>
          <RecordButton record={{ type: 'stakeholder', data: s }} label={s.name} />
          {' — '}{s.role ?? <Missing what="Role" />} · influence {s.influence} · priorities:{' '}
          {s.priorities.length ? s.priorities.join(', ') : <Missing what="Priorities" />}
        </li>
      ))}
    </ul>
  )
}

function CaseHeader({ deal, customerName, synthetic }: { deal: Deal; customerName: string; synthetic: boolean }) {
  const timeMachine = routeHref({ customerId: deal.customer_id, dealId: deal.id, view: 'time-machine' })
  const value = formatMoney(deal.value_minor, deal.currency)
  return (
    <PageHeader
      eyebrow={`Deal · ${customerName}`}
      badges={synthetic ? <SyntheticBadge /> : null}
      title={displayName(deal.title, synthetic)}
      actions={<a className={buttonClass('secondary')} href={timeMachine}><Icon name="clock" /> Open Time Machine</a>}
      facts={[
        { label: 'Stage', value: <span className={styles.cap}>{humanize(deal.stage)}</span> },
        { label: 'Status', value: <span className={styles.cap}>{humanize(deal.status)}</span> },
        { label: 'Value', value: value ?? <Missing what="Value" />, mono: value !== null },
        { label: 'Expected close', value: formatDate(deal.expected_close_date) ?? <Missing what="Close date" />,
          mono: deal.expected_close_date !== null },
        { label: 'Owner', value: deal.owner_name ?? <Missing what="Owner" /> },
      ]}
    />
  )
}

const SECTIONS = [
  { id: 'briefing', label: 'Briefing' },
  { id: 'signals', label: 'Signals' },
  { id: 'commitments', label: 'Commitments' },
  { id: 'interactions', label: 'Interactions' },
  { id: 'stakeholders', label: 'Stakeholders' },
]

/** In-page jump links. Buttons, not #anchors, because the URL hash holds the route. */
function SectionNav() {
  const jump = (id: string) => {
    const heading = document.getElementById(`${id}-title`)
    if (!heading) return
    heading.setAttribute('tabindex', '-1')
    heading.focus({ preventScroll: true })
    heading.closest('section')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  return (
    <nav className={styles.sectionNav} aria-label="On this page">
      {SECTIONS.map((s) => (
        <button key={s.id} type="button" className={styles.sectionLink} onClick={() => jump(s.id)}>{s.label}</button>
      ))}
    </nav>
  )
}

/** One deal as a case file: header, briefing, deterministic signals, then the recorded history. */
export function DealDetail({ customerId, dealId, ai, onResolver }: {
  customerId: string
  dealId: string
  ai: AIStatus | null
  onResolver: (resolver: RecordResolver | null) => void
}) {
  const scope = `${customerId}/${dealId}`
  const customer = useApi(`customer:${customerId}`, (s) => api.customers.get(customerId, s))
  const deal = useApi(`deal:${scope}`, (s) => api.deals.get(customerId, dealId, s))
  const intel = useApi(`intel:${scope}`, (s) => api.intelligence.deal(customerId, dealId, undefined, s))
  const commitments = useApi(`commitments:${scope}`, (s) => api.commitments.list(customerId, { deal_id: dealId, limit: 200 }, s))
  const interactions = useApi(`interactions:${scope}`, (s) => api.interactions.list(customerId, { deal_id: dealId, limit: 200 }, s))
  const stakeholders = useApi(`stakeholders:${customerId}`, (s) => api.stakeholders.list(customerId, { limit: 200 }, s))

  const resolver = useMemo<RecordResolver>(() => {
    const index = new Map<string, ResolvedRecord>()
    if (deal.state.status === 'ok') index.set(`deal:${deal.state.data.id}`, { type: 'deal', data: deal.state.data })
    if (commitments.state.status === 'ok') commitments.state.data.items.forEach((c) => index.set(`commitment:${c.id}`, { type: 'commitment', data: c }))
    if (interactions.state.status === 'ok') interactions.state.data.items.forEach((i) => index.set(`interaction:${i.id}`, { type: 'interaction', data: i }))
    if (stakeholders.state.status === 'ok') stakeholders.state.data.items.forEach((s) => index.set(`stakeholder:${s.id}`, { type: 'stakeholder', data: s }))
    return (type, id) => (id ? index.get(`${type}:${id}`) : undefined)
  }, [deal.state, commitments.state, interactions.state, stakeholders.state])

  useEffect(() => {
    onResolver(resolver)
  }, [resolver, onResolver])
  useEffect(() => () => onResolver(null), [onResolver])

  if (deal.state.status === 'loading' || deal.state.status === 'idle') return <Loading label="Loading deal…" />
  if (deal.state.status === 'error') {
    return (
      <StatusNotice tone="error" title={deal.state.error.status === 404 ? 'Deal not found for this customer' : 'Deal could not be loaded'}>
        {deal.state.error.message}
      </StatusNotice>
    )
  }
  const synthetic = customer.state.status === 'ok' && customer.state.data.is_synthetic
  const customerName = customer.state.status === 'ok' ? displayName(customer.state.data.name, synthetic) : 'Customer'

  return (
    <article>
      <CaseHeader deal={deal.state.data} customerName={customerName} synthetic={synthetic} />
      <SectionNav />

      <Section id="briefing" title="Briefing" meta="Generated on request from this deal's recorded evidence. Inferences are unconfirmed.">
        <BriefingPanel customerId={customerId} dealId={dealId} ai={ai} />
      </Section>

      <Section id="signals" title="Signals" meta="Deterministic rules over recorded data. No scores or predictions.">
        {intel.state.status === 'loading' ? <Loading label="Evaluating rules…" /> : null}
        {intel.state.status === 'error' ? <StatusNotice tone="error" title="Signals could not be loaded">{intel.state.error.message}</StatusNotice> : null}
        {intel.state.status === 'ok' ? (
          <>
            <p className={styles.small}>
              Last meaningful activity: {formatDateTime(intel.state.data.activity.last_meaningful_activity_at) ?? 'none recorded'}
              {intel.state.data.activity.stall_threshold_days !== null ? ` · stall threshold ${intel.state.data.activity.stall_threshold_days} days` : ''}
              {' · '}evaluated {formatDateTime(intel.state.data.as_of)} ({intel.state.data.timezone})
            </p>
            <SignalLedger signals={intel.state.data.signals} customerSignals={intel.state.data.customer_signals} />
          </>
        ) : null}
      </Section>

      <Section id="commitments" title="Commitments">
        {commitments.state.status === 'loading' ? <Loading label="Loading commitments…" /> : null}
        {commitments.state.status === 'error' ? <StatusNotice tone="error" title="Commitments could not be loaded">{commitments.state.error.message}</StatusNotice> : null}
        {commitments.state.status === 'ok' ? <CommitmentsLedger items={commitments.state.data.items} /> : null}
      </Section>

      <Section id="interactions" title="Interactions" meta="Notes are written by our sales rep; they are not the customer's own words.">
        {interactions.state.status === 'loading' ? <Loading label="Loading interactions…" /> : null}
        {interactions.state.status === 'error' ? <StatusNotice tone="error" title="Interactions could not be loaded">{interactions.state.error.message}</StatusNotice> : null}
        {interactions.state.status === 'ok' ? <InteractionTimeline items={interactions.state.data.items} /> : null}
      </Section>

      <Section id="stakeholders" title="Stakeholders" meta="Recorded for the customer (not per deal).">
        {stakeholders.state.status === 'loading' ? <Loading label="Loading stakeholders…" /> : null}
        {stakeholders.state.status === 'error' ? <StatusNotice tone="error" title="Stakeholders could not be loaded">{stakeholders.state.error.message}</StatusNotice> : null}
        {stakeholders.state.status === 'ok' ? <StakeholderList items={stakeholders.state.data.items} /> : null}
      </Section>
    </article>
  )
}
