import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client'
import styles from './App.module.css'
import { DealDetail } from './features/deal/DealDetail'
import { Sidebar } from './features/shell/Sidebar'
import { CustomerQueue } from './features/workspace/CustomerQueue'
import { TimeMachine } from './features/timeMachine/TimeMachine'
import { CustomerWorkspace } from './features/workspace/CustomerWorkspace'
import { displayName } from './lib/format'
import { InspectorContext, type InspectorApi, type InspectorItem, type RecordResolver } from './lib/inspector'
import { routeHref, useRoute } from './lib/route'
import { useApi } from './lib/useApi'
import { Icon } from './ui/Icon'
import { Inspector } from './ui/Inspector'

function Breadcrumb({ customerId, dealId, timeMachine, customerName }: {
  customerId: string | null
  dealId: string | null
  timeMachine: boolean
  customerName: string | null
}) {
  return (
    <nav aria-label="Breadcrumb" className={styles.crumbs}>
      <ol>
        <li>
          <a href={routeHref({ customerId: null, dealId: null })} aria-current={customerId ? undefined : 'page'}>
            Customers
          </a>
        </li>
        {customerId ? (
          <li>
            <a href={routeHref({ customerId, dealId: null })} aria-current={dealId ? undefined : 'page'}>
              {customerName ?? 'Customer'}
            </a>
          </li>
        ) : null}
        {customerId && dealId && !timeMachine ? <li><span aria-current="page">Deal</span></li> : null}
        {customerId && dealId && timeMachine ? (
          <>
            <li><a href={routeHref({ customerId, dealId })}>Deal</a></li>
            <li><span aria-current="page">Time Machine</span></li>
          </>
        ) : null}
      </ol>
    </nav>
  )
}

export default function App() {
  const route = useRoute()
  const capabilities = useApi('capabilities', (signal) => api.capabilities(signal))
  const customers = useApi('customers', (signal) => api.customers.list({ limit: 200 }, signal))
  const customer = useApi(route.customerId ? `customer:${route.customerId}` : null, (signal) =>
    api.customers.get(route.customerId as string, signal),
  )

  const [item, setItem] = useState<InspectorItem | null>(null)
  const [navOpen, setNavOpen] = useState(false)
  const resolverRef = useRef<RecordResolver | null>(null)
  const [resolverVersion, setResolverVersion] = useState(0)
  const onResolver = useCallback((resolver: RecordResolver | null) => {
    resolverRef.current = resolver
    setResolverVersion((v) => v + 1)
  }, [])

  // Anything open in the inspector or the mobile navigation belongs to the previous page once the route changes.
  const routeKey = routeHref(route)
  useEffect(() => {
    setItem(null)
    setNavOpen(false)
  }, [routeKey])

  useEffect(() => {
    if (!navOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setNavOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])

  const inspector = useMemo<InspectorApi>(
    () => ({
      item,
      open: setItem,
      close: () => setItem(null),
      resolve: (type, id) => resolverRef.current?.(type, id),
    }),
    // resolverVersion re-renders consumers when the deal's records finish loading.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [item, resolverVersion],
  )

  const ai = capabilities.state.status === 'ok' ? capabilities.state.data.ai : null
  const customerName = customer.state.status === 'ok'
    ? displayName(customer.state.data.name, customer.state.data.is_synthetic) : null
  const onDeal = Boolean(route.customerId && route.dealId)

  return (
    <InspectorContext.Provider value={inspector}>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className={styles.app} data-inspector={onDeal ? 'true' : 'false'}>
        <div className={styles.rail}>
          <Sidebar customers={customers.state} reloadCustomers={customers.reload} capabilities={capabilities.state}
            selectedId={route.customerId} open={navOpen} onClose={() => setNavOpen(false)} />
        </div>
        {navOpen ? <div className={styles.scrim} onClick={() => setNavOpen(false)} aria-hidden="true" /> : null}

        <div className={styles.content}>
          <header className={styles.top}>
            <button type="button" className={styles.menu} onClick={() => setNavOpen(true)}
              aria-expanded={navOpen} aria-controls="app-navigation" aria-label="Open navigation">
              <Icon name="menu" size={20} />
            </button>
            <a className={styles.miniBrand} href={routeHref({ customerId: null, dealId: null })} aria-label="Deal Rescue, all customers">
              <span aria-hidden="true">DR</span>
            </a>
            <Breadcrumb customerId={route.customerId} dealId={route.dealId} timeMachine={route.view === 'time-machine'}
              customerName={customerName} />
            <p className={styles.principle}>
              <Icon name="file" size={14} /> Recorded evidence first · no scores or predictions
            </p>
          </header>
          <main id="main" tabIndex={-1} className={styles.main}>
            <div className={styles.inner}>
              {route.customerId && route.dealId && route.view === 'time-machine' ? (
                <TimeMachine key={`${route.customerId}/${route.dealId}/tm`} customerId={route.customerId}
                  dealId={route.dealId} ai={ai} onResolver={onResolver} />
              ) : route.customerId && route.dealId ? (
                <DealDetail key={`${route.customerId}/${route.dealId}`} customerId={route.customerId} dealId={route.dealId}
                  ai={ai} onResolver={onResolver} />
              ) : route.customerId ? (
                <CustomerWorkspace key={route.customerId} customerId={route.customerId} />
              ) : (
                <CustomerQueue state={customers.state} reload={customers.reload} />
              )}
            </div>
          </main>
        </div>

        {onDeal ? (
          <div className={styles.inspector}>
            <Inspector />
          </div>
        ) : null}
      </div>
    </InspectorContext.Provider>
  )
}
