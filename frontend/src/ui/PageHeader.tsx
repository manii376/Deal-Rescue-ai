import type { ReactNode } from 'react'
import styles from './PageHeader.module.css'

export interface Fact {
  label: string
  value: ReactNode
  mono?: boolean
}

/** Page title block: eyebrow, serif title, description, badges, actions and a ruled row of facts. */
export function PageHeader({ eyebrow, title, description, badges, actions, facts }: {
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  badges?: ReactNode
  actions?: ReactNode
  facts?: Fact[]
}) {
  return (
    <header className={styles.header}>
      <div className={styles.top}>
        <div className={styles.titles}>
          {eyebrow || badges ? (
            <div className={styles.eyebrowRow}>
              {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
              {badges}
            </div>
          ) : null}
          <h1 className={styles.title}>{title}</h1>
          {description ? <p className={styles.description}>{description}</p> : null}
        </div>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
      {facts && facts.length ? <FactRow facts={facts} /> : null}
    </header>
  )
}

/** A ruled metadata row (not KPI cards): label above value. */
export function FactRow({ facts }: { facts: Fact[] }) {
  return (
    <dl className={styles.facts}>
      {facts.map((f) => (
        <div key={f.label} className={styles.fact}>
          <dt>{f.label}</dt>
          <dd className={f.mono ? styles.mono : undefined}>{f.value}</dd>
        </div>
      ))}
    </dl>
  )
}
