import type { ButtonHTMLAttributes, ReactNode } from 'react'
import styles from './Button.module.css'
import { buttonClass, type ButtonSize, type ButtonVariant } from './buttonClass'
import { Icon, type IconName } from './Icon'

/** Primary = ink-filled (one per section at most, §6.7); secondary = outlined; ghost = text-only. */
export function Button({ variant = 'secondary', size = 'md', icon, iconAfter, busy = false, className, children, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant
    size?: ButtonSize
    icon?: IconName
    iconAfter?: IconName
    busy?: boolean
    children: ReactNode
  }) {
  return (
    <button type="button" {...rest} className={buttonClass(variant, size, className)} aria-busy={busy || undefined}>
      {busy ? <span className={styles.spinner} aria-hidden="true" /> : icon ? <Icon name={icon} size={size === 'sm' ? 14 : 16} /> : null}
      <span>{children}</span>
      {iconAfter ? <Icon name={iconAfter} size={size === 'sm' ? 14 : 16} /> : null}
    </button>
  )
}
