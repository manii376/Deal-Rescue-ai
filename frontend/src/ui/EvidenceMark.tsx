import type { MarkMeta } from '../lib/evidence'
import styles from './EvidenceMark.module.css'

/** Label + glyph for an evidence kind. The text label always carries the meaning. */
export function EvidenceMark({ meta, detail }: { meta: MarkMeta; detail?: string }) {
  return (
    <span className={`${styles.mark} ${styles[meta.style]}`} title={meta.description} data-kind={meta.style}>
      <span aria-hidden="true" className={styles.glyph}>
        {meta.glyph}
      </span>
      {meta.label}
      {detail ? <span className={styles.detail}> · {detail}</span> : null}
    </span>
  )
}

/** Container with the 3px left evidence rule used for claims and findings. */
export function EvidenceBlock({ meta, children, as = 'div' }: { meta: MarkMeta; children: React.ReactNode; as?: 'div' | 'li' }) {
  const Tag = as
  return (
    <Tag className={`${styles.block} ${styles[`block_${meta.style}`]}`} data-kind={meta.style}>
      {children}
    </Tag>
  )
}
