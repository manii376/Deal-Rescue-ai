import { useEffect, useState } from 'react'

/** Where the user is. Encoded in the URL hash so back/forward and shared links work. */
export interface Route {
  customerId: string | null
  dealId: string | null
  /** Sub-view of a deal. Absent = the deal page. */
  view?: 'time-machine'
}

const SAFE_ID = /^[A-Za-z0-9_-]{1,64}$/

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  const customerId = parts[0] === 'c' && parts[1] && SAFE_ID.test(parts[1]) ? parts[1] : null
  const dealId = customerId && parts[2] === 'd' && parts[3] && SAFE_ID.test(parts[3]) ? parts[3] : null
  return dealId && parts[4] === 'time-machine' ? { customerId, dealId, view: 'time-machine' } : { customerId, dealId }
}

export function routeHref(route: Route): string {
  if (!route.customerId) return '#/'
  if (!route.dealId) return `#/c/${route.customerId}`
  return `#/c/${route.customerId}/d/${route.dealId}` + (route.view === 'time-machine' ? '/time-machine' : '')
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
