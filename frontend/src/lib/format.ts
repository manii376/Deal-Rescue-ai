// Formatting helpers. Components never format numbers or dates inline.

const DATE = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
const DATETIME = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
  timeZoneName: 'short',
})

/** "USD 42,000" from minor units; null when either part is missing (never invent a value). */
export function formatMoney(valueMinor: number | null, currency: string | null): string | null {
  if (valueMinor === null || currency === null) return null
  const major = valueMinor / 100
  const digits = Number.isInteger(major) ? 0 : 2
  return `${currency} ${major.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: 2 })}`
}

/** "12 Sep 2026" for an ISO date (YYYY-MM-DD) or date-time. */
export function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null
  const value = iso.length === 10 ? new Date(`${iso}T00:00:00Z`) : new Date(iso)
  return Number.isNaN(value.getTime()) ? iso : DATE.format(value)
}

export function formatDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  const value = new Date(iso)
  return Number.isNaN(value.getTime()) ? iso : DATETIME.format(value)
}

export function humanize(value: string): string {
  return value.replaceAll('_', ' ')
}

export function shortId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 12)}…` : id
}

/** Two letters from a name for a monogram, ignoring bracketed prefixes such as "[SYNTHETIC DEMO]". */
export function initials(name: string): string {
  const words = name.replace(/\[[^\]]*\]/g, '').trim().split(/\s+/).filter(Boolean)
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '?').slice(0, 2)).toUpperCase()
}

/**
 * Display form of a synthetic record's name: without its "[SYNTHETIC DEMO]" style prefix. Only used when the
 * record is flagged synthetic, and callers always show an explicit synthetic label next to it.
 */
export function displayName(name: string, isSynthetic: boolean): string {
  if (!isSynthetic) return name
  return name.replace(/^\s*\[[^\]]*\]\s*/, '') || name
}
