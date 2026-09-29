import styles from './StatusNotice.module.css'
import { Icon } from './Icon'

export type NoticeTone = 'info' | 'warning' | 'error'

const TAG: Record<NoticeTone, string> = { info: 'Status', warning: 'Notice', error: 'Error' }

/** A ruled notice written into the page (never a disappearing toast). */
export function StatusNotice({ tone, title, children, action, live = false }: {
  tone: NoticeTone
  title: string
  children?: React.ReactNode
  action?: React.ReactNode
  live?: boolean
}) {
  return (
    <div className={`${styles.notice} ${styles[tone]}`} role={tone === 'error' ? 'alert' : 'status'}
      aria-live={live ? 'polite' : undefined}>
      <span className={styles.icon}><Icon name={tone === 'info' ? 'info' : 'alert'} size={16} /></span>
      <div className={styles.content}>
        <p className={styles.title}>
          <span className={styles.tag}>{TAG[tone]}</span>
          {title}
        </p>
        {children ? <div className={styles.body}>{children}</div> : null}
        {action ? <div className={styles.action}>{action}</div> : null}
      </div>
    </div>
  )
}

export function Loading({ label }: { label: string }) {
  return (
    <div className={styles.loading} role="status" aria-live="polite">
      <span className={styles.line} aria-hidden="true" />
      <span className={styles.line} aria-hidden="true" />
      <span className={styles.line} aria-hidden="true" />
      <p>{label}</p>
    </div>
  )
}
