import type { ReactNode } from 'react'
import styles from './EmptyState.module.css'
import { Icon, type IconName } from './Icon'

/** An honest empty or no-results state: what is missing, and what the user can do. */
export function EmptyState({ icon = 'info', title, children, action, compact = false }: {
  icon?: IconName
  title: string
  children?: ReactNode
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <div className={`${styles.empty} ${compact ? styles.compact : ''}`}>
      <span className={styles.icon}><Icon name={icon} size={compact ? 16 : 20} /></span>
      <div>
        <p className={styles.title}>{title}</p>
        {children ? <div className={styles.body}>{children}</div> : null}
        {action ? <div className={styles.action}>{action}</div> : null}
      </div>
    </div>
  )
}
