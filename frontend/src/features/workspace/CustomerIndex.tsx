import { useMemo, useState } from 'react'
import type { CustomerSummary, Page } from '../../api/types'
import { displayName, initials } from '../../lib/format'
import { routeHref } from '../../lib/route'
import type { LoadState } from '../../lib/useApi'
import { SearchField } from '../../ui/SearchField'
import styles from './CustomerIndex.module.css'

/** Customer list in the navigation rail (summaries only; no notes, per the security follow-up). */
export function CustomerIndex({ state, reload, selectedId }: {
  state: LoadState<Page<CustomerSummary>>
  reload: () => void
  selectedId: string | null
}) {
  const [query, setQuery] = useState('')

  const customers = useMemo(() => {
    if (state.status !== 'ok') return []
    const q = query.trim().toLowerCase()
    return q ? state.data.items.filter((c) => c.name.toLowerCase().includes(q)) : state.data.items
  }, [state, query])

  return (
    <nav className={styles.index} aria-label="Customers">
      <div className={styles.headingRow}>
        <h2 className={styles.heading}>Customers</h2>
        {state.status === 'ok' ? <span className={styles.count}>{state.data.total}</span> : null}
      </div>
      <SearchField tone="rail" label="Filter customers by name" placeholder="Filter by name" value={query}
        onChange={setQuery} />
      {state.status === 'loading' ? <p className={styles.note} role="status">Loading customers…</p> : null}
      {state.status === 'error' ? (
        <div className={styles.error} role="alert">
          <p>Customers could not be loaded. {state.error.message}</p>
          <button type="button" className={styles.retry} onClick={reload}>Try again</button>
        </div>
      ) : null}
      {state.status === 'ok' && state.data.items.length === 0 ? (
        <p className={styles.note}>No customers recorded yet.</p>
      ) : null}
      {state.status === 'ok' && state.data.items.length > 0 && customers.length === 0 ? (
        <p className={styles.note}>No customer matches “{query}”.</p>
      ) : null}
      <ul className={styles.list}>
        {customers.map((c) => (
          <li key={c.id}>
            <a className={styles.item} href={routeHref({ customerId: c.id, dealId: null })}
              aria-current={c.id === selectedId ? 'page' : undefined}>
              <span className={styles.avatar} aria-hidden="true">{initials(c.name)}</span>
              <span className={styles.text}>
                <span className={styles.name}>{displayName(c.name, c.is_synthetic)}</span>
                <span className={styles.meta}>
                  {c.industry ?? 'Industry not recorded'}
                  {c.is_synthetic ? ' · synthetic' : ''}
                </span>
              </span>
            </a>
          </li>
        ))}
      </ul>
      {state.status === 'ok' && state.data.total > state.data.items.length ? (
        <p className={styles.note}>Showing the first {state.data.items.length} of {state.data.total} customers.</p>
      ) : null}
    </nav>
  )
}
