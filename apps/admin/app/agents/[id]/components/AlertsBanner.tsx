"use client"
import { useCallback, useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import Chip from "../../../components/gotham/Chip"
import { useGate } from "../../../components/gotham/GateProvider"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Alert {
  id: string
  alert_type: string
  severity: "warning" | "critical" | string
  message: string
  triggered_at: string
  resolved_at: string | null
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string): string {
  const diffSeconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diffSeconds < 60) return "just now"
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`
  return `${Math.floor(diffSeconds / 86400)}d ago`
}

const ALERT_TYPE_LABELS: Record<string, string> = {
  eval_regression: "Eval regression",
  red_team_critical: "Critical red-team finding",
}

function formatAlertType(alertType: string): string {
  if (ALERT_TYPE_LABELS[alertType]) return ALERT_TYPE_LABELS[alertType]
  // Replace underscores with spaces, then sentence-case (capitalise first letter only)
  const spaced = alertType.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

// ---------------------------------------------------------------------------
// AlertsBanner (UI-SPEC S7.1 flagged ambiguity, resolved: rendered at the top
// of the operations room, S6.4 / OQ2). Restyled to Gotham chip/banner tokens
// — the data source (GET .../alerts) and the resolve wiring (POST
// .../alerts/{id}/resolve) are byte-for-byte unchanged from the dusk build.
//
// Gate fold (OQ2): an unresolved `red_team_critical` alert is a real signal
// that the console must be shut, on top of whatever the checklist/red-team
// data on the page itself already says — so this component reads `useGate`
// and calls `setGate('blocked')` directly the moment one is present. It
// never calls `setGate('open')` itself (only the page's own combined
// checklist + red-team + alert derivation owns the "reopen" transition —
// see agents/[id]/page.tsx) so this component can only ever add a hold, not
// clear one it didn't independently know the full picture of.
//
// `onAlertsChange` additionally lifts the current alert list to the parent
// so the page can fold `eval_regression` into a Judgement-region chip and
// factor `red_team_critical` into its own combined gate computation — the
// fetch/resolve calls that produce that list are untouched.
// ---------------------------------------------------------------------------

export function AlertsBanner({
  agentId,
  onAlertsChange,
}: {
  agentId: string
  onAlertsChange?: (alerts: Alert[]) => void
}) {
  const { getToken } = useAuth()
  const { setGate } = useGate()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ""
  const [alerts, setAlerts] = useState<Alert[]>([])

  const fetchAlerts = useCallback(async () => {
    const token = await getToken()
    if (!token) return
    try {
      const res = await fetch(`${apiBase}/api/v1/agents/${agentId}/alerts`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) setAlerts(await res.json())
    } catch {
      // Silent failure — retain previous state until next poll
    }
  }, [agentId, getToken, apiBase])

  useEffect(() => {
    fetchAlerts()
    const id = setInterval(fetchAlerts, 30_000)
    return () => clearInterval(id)
  }, [fetchAlerts])

  useEffect(() => {
    onAlertsChange?.(alerts)
  }, [alerts, onAlertsChange])

  useEffect(() => {
    const hasUnresolvedCritical = alerts.some(
      (a) => a.alert_type === "red_team_critical" && !a.resolved_at
    )
    if (hasUnresolvedCritical) setGate("blocked")
  }, [alerts, setGate])

  if (alerts.length === 0) return null

  const handleResolve = async (alertId: string) => {
    // Optimistic update — remove immediately from local state
    setAlerts((prev) => prev.filter((a) => a.id !== alertId))
    // POST to resolve endpoint — silent failure (next poll restores if needed)
    const token = await getToken()
    if (!token) return
    try {
      await fetch(`${apiBase}/api/v1/agents/${agentId}/alerts/${alertId}/resolve`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      })
    } catch {
      // No error handling — next poll restores the alert if POST failed
    }
  }

  return (
    <div style={{ marginBottom: "22px", display: "flex", flexDirection: "column", gap: "8px" }}>
      {alerts.map((alert) => {
        const isCritical = alert.severity === "critical"

        return (
          <div
            key={alert.id}
            role="alert"
            style={{
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              gap: "16px",
              padding: "12px 16px",
              borderRadius: "var(--r-panel)",
              background: isCritical ? "var(--seal-dim)" : "var(--surface)",
              border: isCritical
                ? "1px solid color-mix(in oklch, var(--seal) 32%, transparent)"
                : "1px solid var(--hairline)",
            }}
          >
            {/* Left side: verdict chip + label + message + triggered_at */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                <Chip verdict={isCritical ? "seal" : "mute"}>{alert.severity}</Chip>
                <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--ink)" }}>
                  {formatAlertType(alert.alert_type)}
                </span>
              </div>

              <p
                style={{
                  fontSize: "14px",
                  fontWeight: 400,
                  color: "var(--ink-2)",
                  margin: "6px 0 3px 0",
                  lineHeight: "1.5",
                }}
              >
                {alert.message}
              </p>

              <p
                className="mono"
                style={{
                  fontSize: "11px",
                  color: "var(--ink-3)",
                  margin: 0,
                }}
              >
                {timeAgo(alert.triggered_at)}
              </p>
            </div>

            {/* Right side: Resolve button */}
            <button
              onClick={() => handleResolve(alert.id)}
              style={{
                flexShrink: 0,
                background: "none",
                border: "none",
                padding: 0,
                fontSize: "12px",
                color: "var(--ink-3)",
                textDecoration: "underline",
                cursor: "pointer",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--ink)"
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--ink-3)"
              }}
            >
              Resolve
            </button>
          </div>
        )
      })}
    </div>
  )
}
