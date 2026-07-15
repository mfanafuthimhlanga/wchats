import type { ReactNode } from 'react'

/**
 * Thin passthrough for every /agents/[id]/** operations sub-route.
 *
 * The dusk build rendered a step-provisioning sidebar component here; the
 * Gotham IA drops that sidebar from the operations sub-routes entirely (the
 * console Rail, mounted one level up in agents/layout.tsx, is the only
 * navigation chrome an authenticated operations-room page gets). That
 * sidebar's step-state logic still lives on `/agents/new` only (UI-SPEC
 * S6.3) — it is not deleted, just no longer imported here.
 *
 * The per-agent gate state + gatebar are owned by the operations-room page
 * itself (UI-SPEC S6.4), not this layout — this layout has no data
 * dependency at all, which is why it can be a plain server component.
 */
export default function AgentDetailLayout({ children }: { children: ReactNode }) {
  return <div className="tint">{children}</div>
}
