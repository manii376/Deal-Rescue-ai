// Branch-time helpers. Pure functions; all times are ISO 8601 UTC strings and are compared as instants.

import type { DealTimeline, TimelineEvent } from '../../api/types'

const ms = (iso: string) => Date.parse(iso)

export function isAfter(a: string, b: string): boolean {
  return ms(a) > ms(b)
}

export function sameInstant(a: string, b: string): boolean {
  return ms(a) === ms(b)
}

/** Events that happened by NOW (scheduled due dates and computed checkpoints may lie after it). */
export function eventsUpToNow(timeline: DealTimeline): TimelineEvent[] {
  return timeline.events.filter((e) => !isAfter(e.at, timeline.as_of))
}

/**
 * Default branch point (plan §3): the latest recorded interaction by NOW; else the latest event by NOW; else NOW.
 * Always a date that exists in the records (or NOW), never an invented one.
 */
export function defaultBranch(timeline: DealTimeline): string {
  const past = eventsUpToNow(timeline)
  const interactions = past.filter((e) => e.kind === 'interaction')
  const pick = (interactions.length ? interactions : past).at(-1)
  return pick ? pick.at : timeline.as_of
}

/** Value for <input type="datetime-local"> interpreted as UTC ("YYYY-MM-DDTHH:MM"). */
export function toUtcInput(iso: string): string {
  return new Date(iso).toISOString().slice(0, 16)
}

/** Parse the UTC input back to ISO; null when empty or invalid. */
export function fromUtcInput(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null
  const iso = `${value}:00Z`
  return Number.isNaN(ms(iso)) ? null : new Date(iso).toISOString()
}

/** Split a "type:id" record reference (e.g. "stakeholder:stk_1") used when a record is not in the capped pack. */
export function parseRecordRef(value: string): { type: string; id: string } | null {
  const m = /^(interaction|stakeholder|commitment|deal|customer):([A-Za-z0-9_-]{1,64})$/.exec(value)
  return m ? { type: m[1], id: m[2] } : null
}
