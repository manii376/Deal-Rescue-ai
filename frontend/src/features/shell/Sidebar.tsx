import { useEffect, useRef } from 'react'
import type { Capabilities, CustomerSummary, Page } from '../../api/types'
import { routeHref } from '../../lib/route'
import type { LoadState } from '../../lib/useApi'
import { Icon, type IconName } from '../../ui/Icon'
import { CustomerIndex } from '../workspace/CustomerIndex'
import styles from './Sidebar.module.css'

type Health = 'ready' | 'off' | 'down' | 'unknown'

function StatusItem({ icon, label, value, health, detail }: {
  icon: IconName
  label: string
  value: string
  health: Health
  detail?: string | null
}) {
  return (
    <li className={styles.statusItem} title={detail ?? undefined}>
      <Icon name={icon} size={14} className={styles.statusIcon} />
      <span className={styles.statusLabel}>{label}</span>
      <span className={`${styles.statusValue} ${styles[health]}`}>
        <span className={styles.dot} aria-hidden="true" />
        {value}
      </span>
    </li>
  )
}

/** Live service status from GET /api/system/capabilities. Text carries the meaning; the dot only repeats it. */
function SystemStatus({ state }: { state: LoadState<Capabilities> }) {
  if (state.status === 'error') {
    return (
      <ul className={styles.status} aria-label="System status">
        <StatusItem icon="database" label="Backend" value="Unreachable" health="down" detail={state.error.message} />
      </ul>
    )
  }
  if (state.status !== 'ok') {
    return <p className={styles.statusPending} role="status">Checking services…</p>
  }
  const { database, memory, ai } = state.data
  const memoryValue = memory.backend === 'disabled' ? 'Off' : memory.available ? 'Ready' : 'Unavailable'
  const aiValue = ai.provider === 'none' ? 'Not configured' : ai.available ? (ai.model ?? 'Ready') : 'Unavailable'
  return (
    <ul className={styles.status} aria-label="System status">
      <StatusItem icon="database" label="Records" value={database.available ? 'SQLite ready' : 'Unavailable'}
        health={database.available ? 'ready' : 'down'} />
      <StatusItem icon="layers" label="Memory" value={memoryValue} detail={memory.reason}
        health={memory.backend === 'disabled' ? 'off' : memory.available ? 'ready' : 'down'} />
      <StatusItem icon="cpu" label="Local AI" value={aiValue} detail={ai.reason}
        health={ai.provider === 'none' ? 'off' : ai.available ? 'ready' : 'down'} />
    </ul>
  )
}

/** The dark navigation rail: product identity, the one real destination (customers) and service status. */
export function Sidebar({ customers, reloadCustomers, capabilities, selectedId, open, onClose }: {
  customers: LoadState<Page<CustomerSummary>>
  reloadCustomers: () => void
  capabilities: LoadState<Capabilities>
  selectedId: string | null
  open: boolean
  onClose: () => void
}) {
  const closeRef = useRef<HTMLButtonElement>(null)
  // Mobile drawer: move focus into it when it opens.
  useEffect(() => {
    if (open) closeRef.current?.focus()
  }, [open])
  return (
    <div className={`${styles.rail} ${open ? styles.open : ''}`} id="app-navigation">
      <div className={styles.brandRow}>
        <a className={styles.brand} href={routeHref({ customerId: null, dealId: null })}>
          <span className={styles.mark} aria-hidden="true">DR</span>
          <span className={styles.brandText}>
            <span className={styles.brandName}>Deal Rescue</span>
            <span className={styles.brandSub}>Evidence-first sales intelligence</span>
          </span>
        </a>
        <button ref={closeRef} type="button" className={styles.close} onClick={onClose} aria-label="Close navigation">
          <Icon name="close" size={18} />
        </button>
      </div>

      <nav aria-label="Primary" className={styles.primary}>
        <a className={styles.navItem} href={routeHref({ customerId: null, dealId: null })}
          aria-current={selectedId ? undefined : 'page'}>
          <Icon name="users" size={16} />
          All customers
        </a>
      </nav>

      <div className={styles.scroll}>
        <CustomerIndex state={customers} reload={reloadCustomers} selectedId={selectedId} />
      </div>

      <div className={styles.footer}>
        <p className={styles.footerHeading}>System</p>
        <SystemStatus state={capabilities} />
      </div>
    </div>
  )
}
