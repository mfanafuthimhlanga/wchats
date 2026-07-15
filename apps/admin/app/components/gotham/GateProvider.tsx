'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'

export type GateState = 'open' | 'blocked'

interface GateContextValue {
  gate: GateState
  setGate: (gate: GateState) => void
}

const GateContext = createContext<GateContextValue | null>(null)

/**
 * GateProvider is the SOLE writer of `document.documentElement.dataset.gate`.
 *
 * The Gotham token system keys an entire recolour of the console — every
 * `--live`-derived value, every hairline — off this one root attribute
 * (globals.css `:root[data-gate="blocked"]`). No other component may set
 * `data-gate` directly (Pitfall 5): doing so risks a "stuck red" state that
 * leaks across client-side navigations once a route that set it unmounts.
 *
 * GateProvider resets the attribute back to `'open'` on unmount so a blocked
 * gate from one screen never bleeds into the next.
 */
export function GateProvider({ children }: { children: ReactNode }) {
  const [gate, setGateState] = useState<GateState>('open')
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    document.documentElement.dataset.gate = gate

    return () => {
      mountedRef.current = false
    }
  }, [gate])

  useEffect(() => {
    // Reset on unmount only — never on every gate change — so the gate can
    // still flip freely while GateProvider is mounted.
    return () => {
      document.documentElement.dataset.gate = 'open'
    }
  }, [])

  const setGate = useCallback((next: GateState) => {
    if (!mountedRef.current) return
    setGateState(next)
  }, [])

  return (
    <GateContext.Provider value={{ gate, setGate }}>
      {children}
    </GateContext.Provider>
  )
}

export function useGate(): GateContextValue {
  const ctx = useContext(GateContext)
  if (!ctx) {
    throw new Error('useGate must be used within a GateProvider')
  }
  return ctx
}
