"use client"
import { useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Alert {
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
  eval_regression: "Eval Regression",
  red_team_critical: "Critical Red Team Finding",
}

function formatAlertType(alertType: string): string {
  if (ALERT_TYPE_LABELS[alertType]) return ALERT_TYPE_LABELS[alertType]
  // Replace underscores with spaces and title-case each word
  return alertType
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// ---------------------------------------------------------------------------
// AlertsBanner
// ---------------------------------------------------------------------------

export function AlertsBanner({ agentId }: { agentId: string }) {
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ""
  const [alerts, setAlerts] = useState<Alert[]>([])

  const fetchAlerts = async () => {
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
  }

  useEffect(() => {
    fetchAlerts()
    const id = setInterval(fetchAlerts, 30_000)
    return () => clearInterval(id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId])

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
    <div style={{ marginBottom: "20px" }}>
      {alerts.map((alert) => {
        const isWarning = alert.severity === "warning"
        const isCritical = alert.severity === "critical"

        const rowBackground = isCritical
          ? "var(--red-bg)"
          : isWarning
          ? "var(--amber-bg)"
          : "var(--amber-bg)"

        const rowBorder = isCritical
          ? "1px solid rgba(185, 28, 28, 0.20)"
          : isWarning
          ? "1px solid rgba(146, 64, 14, 0.20)"
          : "1px solid rgba(146, 64, 14, 0.20)"

        const badgeClass = isCritical
          ? "bg-red-100 text-red-800"
          : "bg-amber-100 text-amber-800"

        return (
          <div
            key={alert.id}
            role="alert"
            className="flex items-start justify-between"
            style={{
              padding: "12px 16px",
              marginBottom: "8px",
              background: rowBackground,
              border: rowBorder,
              borderRadius: "var(--radius-xs)",
            }}
          >
            {/* Left side: badge + label + message + triggered_at */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {/* First line: severity badge + alert_type label */}
              <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                <span
                  className={`${badgeClass} uppercase rounded px-1.5 py-0.5`}
                  style={{ fontSize: "12px", fontWeight: 600, lineHeight: "1.25" }}
                >
                  {alert.severity}
                </span>
                <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-1)" }}>
                  {formatAlertType(alert.alert_type)}
                </span>
              </div>

              {/* Second line: message body */}
              <p
                style={{
                  fontSize: "14px",
                  fontWeight: 400,
                  color: "var(--text-3)",
                  margin: "4px 0 2px 0",
                  lineHeight: "1.5",
                }}
              >
                {alert.message}
              </p>

              {/* Third line: triggered_at relative time */}
              <p
                style={{
                  fontSize: "12px",
                  fontWeight: 400,
                  color: "var(--text-3)",
                  margin: 0,
                  lineHeight: "1.4",
                }}
              >
                {timeAgo(alert.triggered_at)}
              </p>
            </div>

            {/* Right side: Resolve button */}
            <button
              onClick={() => handleResolve(alert.id)}
              className="ml-4 shrink-0"
              style={{
                background: "none",
                border: "none",
                padding: 0,
                fontSize: "12px",
                color: "var(--text-3)",
                textDecoration: "underline",
                cursor: "pointer",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--text-1)"
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--text-3)"
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
