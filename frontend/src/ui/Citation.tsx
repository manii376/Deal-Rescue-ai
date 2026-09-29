import styles from './Citation.module.css'

/** A citation chip. Opens the cited source in the inspector; it never navigates away. */
export function Citation({ refId, description, onOpen, pressed }: {
  refId: string
  description: string
  onOpen: () => void
  pressed?: boolean
}) {
  return (
    <button type="button" className={styles.chip} onClick={onOpen} aria-pressed={pressed}
      aria-label={`Citation ${refId}: ${description}`} title={description}>
      {refId}
    </button>
  )
}
