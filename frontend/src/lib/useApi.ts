import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../api/client'

export type LoadState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ok'; data: T }
  | { status: 'error'; error: ApiError }

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  return new ApiError(0, 'client_error', err instanceof Error ? err.message : 'Unknown error', null)
}

type Settled<T> = { tag: string } & ({ status: 'ok'; data: T } | { status: 'error'; error: ApiError })

/**
 * Load data for the current key. Changing the key aborts the previous request, and a
 * response that arrives for an older key is ignored, so a quick switch between customers or
 * deals can never show the previous record's data. A null key means "nothing to load".
 */
export function useApi<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>) {
  const [nonce, setNonce] = useState(0)
  const [settled, setSettled] = useState<Settled<T> | null>(null)
  const fetcherRef = useRef(fetcher)
  useLayoutEffect(() => {
    fetcherRef.current = fetcher
  })

  const tag = key === null ? null : `${nonce}|${key}`

  useEffect(() => {
    if (tag === null) return
    const controller = new AbortController()
    fetcherRef
      .current(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setSettled({ tag, status: 'ok', data })
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setSettled({ tag, status: 'error', error: toApiError(err) })
      })
    return () => controller.abort()
  }, [tag])

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  // A result only counts for the request that produced it; anything else is still loading.
  // Memoized so consumers can depend on `state` identity without re-running on every render.
  const state = useMemo<LoadState<T>>(() => {
    if (tag === null) return { status: 'idle' }
    if (!settled || settled.tag !== tag) return { status: 'loading' }
    return settled.status === 'ok' ? { status: 'ok', data: settled.data } : { status: 'error', error: settled.error }
  }, [tag, settled])
  return { state, reload }
}
