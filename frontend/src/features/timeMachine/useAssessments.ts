// Request state for TM5 AI strategy assessments. Rendering lives in AssessmentPanel.tsx.
//
// Rules: one request at a time (each is a local model call); never started automatically; every result is tagged
// with the exact selection it was made for (assessmentKey) and is only shown while that selection is current;
// superseded requests are aborted and their late responses ignored; nothing is persisted.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../../api/client'
import type { StrategyTemplateId, TimeMachineAssessment, TimeMachineAssessRequest } from '../../api/types'
import { sameInstant } from './branchTime'

export type AssessEntry =
  | { key: string; status: 'loading' }
  | { key: string; status: 'ok'; data: TimeMachineAssessment }
  | { key: string; status: 'error'; error: ApiError }

/** Everything an assessment depends on. Instants are normalised so "…Z" and "….000Z" compare equal. */
export function assessmentKey(customerId: string, dealId: string, body: TimeMachineAssessRequest): string {
  return JSON.stringify([customerId, dealId, body.strategy_id, Date.parse(body.branch_at),
    body.as_of ? Date.parse(body.as_of) : null, body.anchor_ref ?? null, Boolean(body.include_memory)])
}

function matchesRequest(data: TimeMachineAssessment, customerId: string, dealId: string,
  body: TimeMachineAssessRequest): boolean {
  return data.customer_id === customerId && data.deal_id === dealId && data.strategy.id === body.strategy_id
    && sameInstant(data.branch_at, body.branch_at)
}

export function useAssessments(customerId: string, dealId: string) {
  const [entries, setEntries] = useState<Partial<Record<StrategyTemplateId, AssessEntry>>>({})
  const controllerRef = useRef<AbortController | null>(null)
  const inFlightRef = useRef<StrategyTemplateId | null>(null)

  // Leaving the page (or switching customer/deal, which remounts it) aborts any request in flight.
  useEffect(() => () => controllerRef.current?.abort(), [])

  /** Clear results (all, or one strategy's) and abort the request in flight if it is affected. */
  const clear = useCallback((strategyId?: StrategyTemplateId) => {
    if (!strategyId || inFlightRef.current === strategyId) {
      controllerRef.current?.abort()
      controllerRef.current = null
      inFlightRef.current = null
    }
    setEntries((prev) => {
      if (!strategyId) return Object.keys(prev).length ? {} : prev
      if (!(strategyId in prev)) return prev
      const next = { ...prev }
      delete next[strategyId]
      return next
    })
  }, [])

  const run = useCallback((body: TimeMachineAssessRequest) => {
    if (inFlightRef.current) return // one model call at a time; the UI also disables the buttons
    const key = assessmentKey(customerId, dealId, body)
    const controller = new AbortController()
    controllerRef.current = controller
    inFlightRef.current = body.strategy_id
    setEntries((prev) => ({ ...prev, [body.strategy_id]: { key, status: 'loading' } }))
    const settle = (entry: AssessEntry) => {
      if (controller.signal.aborted || controllerRef.current !== controller) return // superseded: ignore
      controllerRef.current = null
      inFlightRef.current = null
      setEntries((prev) => (prev[body.strategy_id]?.key === key ? { ...prev, [body.strategy_id]: entry } : prev))
    }
    api.ai.timeMachineAssess(customerId, dealId, body, controller.signal)
      .then((data) => settle(matchesRequest(data, customerId, dealId, body)
        ? { key, status: 'ok', data }
        : { key, status: 'error', error: new ApiError(0, 'scope_mismatch', 'The response did not match this selection', null) }))
      .catch((err: unknown) => settle({
        key, status: 'error',
        error: err instanceof ApiError ? err : new ApiError(0, 'client_error', err instanceof Error ? err.message : String(err), null),
      }))
  }, [customerId, dealId])

  const busy = Object.values(entries).some((e) => e?.status === 'loading')
  /** The entry for this strategy only if it was made for exactly this selection. */
  const current = (strategyId: StrategyTemplateId, key: string): AssessEntry | undefined =>
    entries[strategyId]?.key === key ? entries[strategyId] : undefined

  return { run, clear, current, busy }
}
