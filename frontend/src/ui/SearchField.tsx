import styles from './SearchField.module.css'
import { Icon } from './Icon'

/** Labelled search input with a leading icon. `tone="rail"` styles it for the dark navigation rail. */
export function SearchField({ label, value, onChange, placeholder, tone = 'light', hideLabel = true }: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  tone?: 'light' | 'rail'
  hideLabel?: boolean
}) {
  return (
    <label className={`${styles.field} ${styles[tone]}`}>
      <span className={hideLabel ? 'visually-hidden' : styles.label}>{label}</span>
      <span className={styles.control}>
        <Icon name="search" size={15} className={styles.icon} />
        <input className={styles.input} type="search" value={value} placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)} autoComplete="off" spellCheck={false} />
      </span>
    </label>
  )
}
