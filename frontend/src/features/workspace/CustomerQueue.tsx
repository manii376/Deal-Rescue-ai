import { useMemo, useState } from 'react'
import type { CustomerSummary, Page } from '../../api/types'
import { displayName, formatDate, initials } from '../../lib/format'
import { routeHref } from '../../lib/route'
import type { LoadState } from '../../lib/useApi'
import { Badge, SyntheticBadge } from '../../ui/Badge'
import { Button } from '../../ui/Button'
import { EmptyState } from '../../ui/EmptyState'
import { Icon } from '../../ui/Icon'
import { PageHeader } from '../../ui/PageHeader'
import { SearchField } from '../../ui/SearchField'
import { Loading, StatusNotice } from '../../ui/StatusNotice'
import styles from './CustomerQueue.module.css'

type SourceFilter = 'all' | 'synthetic' | 'other'

const FILTERS: { id: SourceFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'synthetic', label: 'Synthetic demo' },
  { id: 'other', label: 'Other records' },
]

/**
 * Workspace home: the customer work queue. Uses only CustomerSummary fields (the directory endpoint has no
 * deal counts or notes), so nothing here is invented.
 */
export function CustomerQueue({ state, reload }: { state: LoadState<Page<CustomerSummary>>; reload: () => void }) {
  const [query, setQuery] = useState('')
  const [source, setSource] = useState<SourceFilter>('all')

  const all = useMemo(() => (state.status === 'ok' ? state.data.items : []), [state])
  const counts = useMemo(() => ({
    all: all.length,
    synthetic: all.filter((c) => c.is_synthetic).length,
    other: all.filter((c) => !c.is_synthetic).length,
  }), [all])
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return all.filter((c) => (source === 'all' || (source === 'synthetic') === c.is_synthetic)
      && (!q || c.name.toLowerCase().includes(q) || (c.industry ?? '').toLowerCase().includes(q)))
  }, [all, query, source])

  return (
    <div className={styles.page}>
      <PageHeader eyebrow="Workspace" title="Customers"
        description="Choose a customer to review its deals in order of deterministic attention, then open a deal for its signals, recorded history and an evidence-grounded briefing." />

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <SearchField label="Search customers by name or industry" placeholder="Search by name or industry"
            value={query} onChange={setQuery} />
        </div>
        <div className={styles.segmented} role="group" aria-label="Filter by data source">
          {FILTERS.map((f) => (
            <button key={f.id} type="button" className={styles.segment} aria-pressed={source === f.id}
              onClick={() => setSource(f.id)}>
              {f.label}
              <span className={styles.segmentCount}>{counts[f.id]}</span>
            </button>
          ))}
        </div>
      </div>

      {state.status === 'loading' || state.status === 'idle' ? <Loading label="Loading customers…" /> : null}
      {state.status === 'error' ? (
        <StatusNotice tone="error" title="Customers could not be loaded"
          action={<Button size="sm" icon="refresh" onClick={reload}>Try again</Button>}>
          {state.error.message}
        </StatusNotice>
      ) : null}
      {state.status === 'ok' && all.length === 0 ? (
        <EmptyState icon="users" title="No customers recorded yet">
          Customers appear here once they are recorded through the API.
        </EmptyState>
      ) : null}
      {state.status === 'ok' && all.length > 0 && visible.length === 0 ? (
        <EmptyState icon="search" title="No customers match these filters"
          action={<Button size="sm" onClick={() => { setQuery(''); setSource('all') }}>Clear filters</Button>}>
          {query ? <>Nothing matches “{query}”</> : 'Nothing matches'}
          {source !== 'all' ? ` in “${FILTERS.find((f) => f.id === source)?.label}”` : ''}.
        </EmptyState>
      ) : null}

      {visible.length > 0 ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="visually-hidden">Customers ({visible.length} shown)</caption>
            <thead>
              <tr>
                <th scope="col">Customer</th>
                <th scope="col">Data source</th>
                <th scope="col">Added</th>
                <th scope="col">Last updated</th>
                <th scope="col"><span className="visually-hidden">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((c) => (
                <tr key={c.id} className={styles.row}>
                  <th scope="row">
                    <a className={styles.customer} href={routeHref({ customerId: c.id, dealId: null })}>
                      <span className={styles.avatar} aria-hidden="true">{initials(c.name)}</span>
                      <span className={styles.nameBlock}>
                        <span className={styles.name}>{displayName(c.name, c.is_synthetic)}</span>
                        <span className={styles.industry}>{c.industry ?? 'Industry not recorded'}</span>
                      </span>
                    </a>
                  </th>
                  <td data-label="Data source">
                    {c.is_synthetic ? <SyntheticBadge /> : <Badge tone="neutral">Recorded</Badge>}
                  </td>
                  <td data-label="Added" className={styles.data}>{formatDate(c.created_at)}</td>
                  <td data-label="Last updated" className={styles.data}>{formatDate(c.updated_at)}</td>
                  <td className={styles.go}>
                    <a className={styles.open} href={routeHref({ customerId: c.id, dealId: null })}
                      aria-label={`Open workspace for ${c.name}`} tabIndex={-1}>
                      Open <Icon name="chevronRight" size={14} />
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className={styles.foot}>
            {visible.length} of {state.status === 'ok' ? state.data.total : all.length} customers shown
            {state.status === 'ok' && state.data.total > all.length ? ` (first ${all.length} loaded)` : ''}.
          </p>
        </div>
      ) : null}
    </div>
  )
}
