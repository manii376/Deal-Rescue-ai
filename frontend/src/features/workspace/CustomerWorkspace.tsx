import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { DealAttentionSummary, Signal } from '../../api/types'
import { SIGNAL_NATURE } from '../../lib/evidence'
import { displayName, formatDate, humanize } from '../../lib/format'
import { routeHref } from '../../lib/route'
import { useApi } from '../../lib/useApi'
import { Badge, SeverityBadge, SyntheticBadge } from '../../ui/Badge'
import { EmptyState } from '../../ui/EmptyState'
import { EvidenceBlock, EvidenceMark } from '../../ui/EvidenceMark'
import { Icon } from '../../ui/Icon'
import { PageHeader } from '../../ui/PageHeader'
import { Section } from '../../ui/Section'
import { Loading, StatusNotice } from '../../ui/StatusNotice'
import styles from './CustomerWorkspace.module.css'

interface DealSignals {
  findings: Signal[]
  gaps: Signal[] // missing information and data quality: not evidence of a problem
}

function groupByDeal(signals: Signal[]): { byDeal: Map<string, DealSignals>; customerLevel: Signal[] } {
  const byDeal = new Map<string, DealSignals>()
  const customerLevel: Signal[] = []
  for (const s of signals) {
    if (s.deal_id === null) {
      customerLevel.push(s)
      continue
    }
    const entry = byDeal.get(s.deal_id) ?? { findings: [], gaps: [] }
    ;(s.nature === 'finding' ? entry.findings : entry.gaps).push(s)
    byDeal.set(s.deal_id, entry)
  }
  return { byDeal, customerLevel }
}

function TypeList({ signals, empty }: { signals: Signal[]; empty: string }) {
  if (!signals.length) return <span className={styles.none}>{empty}</span>
  return (
    <span className={styles.types}>
      <span className={styles.count}>{signals.length}</span>
      {[...new Set(signals.map((s) => humanize(s.type)))].join(', ')}
    </span>
  )
}

function DealRow({ deal, customerId, synthetic, signals }: {
  deal: DealAttentionSummary
  customerId: string
  synthetic: boolean
  signals?: DealSignals
}) {
  const href = routeHref({ customerId, dealId: deal.deal_id })
  return (
    <tr className={styles.row}>
      <th scope="row">
        <a className={styles.dealLink} href={href}>{displayName(deal.title, synthetic)}</a>
        <span className={styles.stage}>
          {humanize(deal.stage)} <span aria-hidden="true">·</span> {humanize(deal.status)}
        </span>
      </th>
      <td data-label="Highest severity"><SeverityBadge severity={deal.highest_severity} /></td>
      <td data-label="Findings">
        {signals ? <TypeList signals={signals.findings} empty="None" /> : <span className={styles.none}>…</span>}
      </td>
      <td data-label="Missing information">
        {signals ? <TypeList signals={signals.gaps} empty="None" /> : <span className={styles.none}>…</span>}
      </td>
      <td data-label="Last activity" className={styles.data}>
        {formatDate(deal.last_meaningful_activity_at) ?? <span className={styles.none}>None recorded</span>}
      </td>
      <td className={styles.go}>
        <a href={href} className={styles.open} tabIndex={-1} aria-label={`Open deal ${deal.title}`}>
          <Icon name="chevronRight" size={16} />
        </a>
      </td>
    </tr>
  )
}

/** One customer's workspace: deals ordered by the backend's deterministic attention ranking. */
export function CustomerWorkspace({ customerId }: { customerId: string }) {
  // One reference time for both requests, so the attention list and the signal breakdown agree.
  const [asOf] = useState(() => new Date().toISOString())
  const customer = useApi(`customer:${customerId}`, (signal) => api.customers.get(customerId, signal))
  const deals = useApi(`attention:${customerId}:${asOf}`,
    (signal) => api.intelligence.deals(customerId, { limit: 200, as_of: asOf }, signal))
  const signals = useApi(`signals:${customerId}:${asOf}`,
    (signal) => api.intelligence.signals(customerId, { limit: 200, as_of: asOf }, signal))

  const grouped = useMemo(
    () => (signals.state.status === 'ok' ? groupByDeal(signals.state.data.items) : null),
    [signals.state],
  )

  if (customer.state.status === 'loading' || customer.state.status === 'idle') return <Loading label="Loading customer…" />
  if (customer.state.status === 'error') {
    return (
      <StatusNotice tone="error" title={customer.state.error.status === 404 ? 'Customer not found' : 'Customer could not be loaded'}>
        {customer.state.error.message}
      </StatusNotice>
    )
  }
  const c = customer.state.data
  const items = deals.state.status === 'ok' ? deals.state.data.items : null
  const pending = <span className={styles.none}>…</span>
  const signalsPartial = signals.state.status === 'ok' && signals.state.data.total > signals.state.data.items.length

  return (
    <article>
      <PageHeader
        eyebrow="Customer workspace"
        badges={c.is_synthetic ? <SyntheticBadge /> : null}
        title={displayName(c.name, c.is_synthetic)}
        description={c.industry ?? 'Industry not recorded'}
        facts={[
          { label: 'Deals', value: items ? items.length : pending, mono: true },
          { label: 'Open deals', value: items ? items.filter((d) => d.status === 'open').length : pending, mono: true },
          { label: 'Deals with findings', mono: true,
            value: grouped && items ? items.filter((d) => grouped.byDeal.get(d.deal_id)?.findings.length).length : pending },
          { label: 'Missing-info items', mono: true,
            value: signals.state.status === 'ok'
              ? signals.state.data.items.filter((s) => s.nature !== 'finding').length : pending },
          { label: 'Evaluated', mono: true, value: deals.state.status === 'ok' ? formatDate(deals.state.data.as_of) : pending },
        ]}
      />

      <Section id="deals" title="Deals by attention"
        meta="Ordered by the deterministic signal rules, highest severity first. Findings are negative facts from recorded data; missing information means a record or field is absent. No scores or predictions.">
        {deals.state.status === 'loading' ? <Loading label="Evaluating deals…" /> : null}
        {deals.state.status === 'error' ? (
          <StatusNotice tone="error" title="Deal signals could not be loaded">{deals.state.error.message}</StatusNotice>
        ) : null}
        {signals.state.status === 'error' ? (
          <StatusNotice tone="warning" title="Signal breakdown unavailable">
            Findings and missing information could not be split per deal: {signals.state.error.message}
          </StatusNotice>
        ) : null}
        {items && items.length === 0 ? (
          <EmptyState icon="briefcase" title="No deals are recorded for this customer">
            Deals, their signals and briefings appear here once a deal is recorded.
          </EmptyState>
        ) : null}
        {items && items.length > 0 ? (
          <div className={styles.tableWrap}>
            <table className={styles.ledger}>
              <caption className="visually-hidden">Deals of {c.name}, most attention needed first</caption>
              <thead>
                <tr>
                  <th scope="col">Deal</th>
                  <th scope="col">Highest severity</th>
                  <th scope="col">Findings</th>
                  <th scope="col">Missing information</th>
                  <th scope="col">Last meaningful activity</th>
                  <th scope="col"><span className="visually-hidden">Open</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((d) => (
                  <DealRow key={d.deal_id} deal={d} customerId={customerId} synthetic={c.is_synthetic}
                    signals={grouped?.byDeal.get(d.deal_id) ?? (grouped ? { findings: [], gaps: [] } : undefined)} />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {deals.state.status === 'ok' ? (
          <p className={styles.small}>
            Evaluated as of {formatDate(deals.state.data.as_of)} ({deals.state.data.timezone}), rules {deals.state.data.rules_version}.
            {signalsPartial ? ' Signal breakdown shows the first 200 signals only.' : ''}
          </p>
        ) : null}
      </Section>

      {grouped && grouped.customerLevel.length ? (
        <Section id="customer-signals" title="Customer-level items"
          meta="Rules that apply to the customer as a whole rather than one deal.">
          <ul className={styles.stack}>
            {grouped.customerLevel.map((s) => (
              <EvidenceBlock key={s.id} meta={SIGNAL_NATURE[s.nature]} as="li">
                <p className={styles.markLine}>
                  <EvidenceMark meta={SIGNAL_NATURE[s.nature]} />
                  <Badge tone="quiet">{s.severity} severity</Badge>
                </p>
                <p className={styles.itemTitle}>{s.title}</p>
                <p className={styles.explain}>{s.explanation}</p>
              </EvidenceBlock>
            ))}
          </ul>
        </Section>
      ) : null}
    </article>
  )
}
