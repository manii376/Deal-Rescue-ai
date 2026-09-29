import styles from './Section.module.css'

/** A document section: serif title, optional meta line, content. No card frame. */
export function Section({ id, title, meta, actions, children }: {
  id: string
  title: string
  meta?: React.ReactNode
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className={styles.section} aria-labelledby={`${id}-title`}>
      <header className={styles.header}>
        <div>
          <h2 id={`${id}-title`} className={styles.title}>
            {title}
          </h2>
          {meta ? <p className={styles.meta}>{meta}</p> : null}
        </div>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </header>
      {children}
    </section>
  )
}
