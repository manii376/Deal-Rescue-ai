import { createContext, useContext } from 'react'
import type { Commitment, Deal, EvidenceSource, Interaction, Signal, Stakeholder } from '../api/types'

/** A record already loaded for the current deal, so the inspector can show it in full. */
export type ResolvedRecord =
  | { type: 'deal'; data: Deal }
  | { type: 'interaction'; data: Interaction }
  | { type: 'commitment'; data: Commitment }
  | { type: 'stakeholder'; data: Stakeholder }

export type RecordResolver = (type: string, id: string | null) => ResolvedRecord | undefined

export type InspectorItem =
  /** note: context shown above the excerpt (e.g. "known at <date>"); without it the briefing wording is used. */
  | { kind: 'evidence'; source: EvidenceSource; key: string; note?: string }
  | { kind: 'signal'; signal: Signal; key: string }
  | { kind: 'record'; record: ResolvedRecord; key: string }

export interface InspectorApi {
  item: InspectorItem | null
  open: (item: InspectorItem) => void
  close: () => void
  resolve: RecordResolver
}

export const InspectorContext = createContext<InspectorApi>({
  item: null,
  open: () => undefined,
  close: () => undefined,
  resolve: () => undefined,
})

export function useInspector(): InspectorApi {
  return useContext(InspectorContext)
}
