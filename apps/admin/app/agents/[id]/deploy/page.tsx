'use client'
import { use, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../../components/gotham/Btn'
import Chip from '../../../components/gotham/Chip'
import Ledger, { LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import Zone from '../../../components/gotham/Zone'
import EmptyState from '../../../components/gotham/EmptyState'
import { useGate } from '../../../components/gotham/GateProvider'

/**
 * Deploy — `/agents/[id]/deploy` (UI-SPEC S6.8, UI2-06, ported from
 * prototypes/gotham/deploy.html). Two-column `.bench`: the gate + embed +
 * appearance on the left, the sticky customer widget preview on the right
 * (hidden <=1100px).
 *
 * PRESERVED VERBATIM (non-regression, UI-SPEC S9): the real
 * `POST/GET /checklist-runs`, `POST .../acknowledge`,
 * `POST /approve-deployment` and `GET/POST /widget-config` endpoints the
 * prior dusk build already consumed. The embed snippet's CDN/API base env
 * vars and iframe query-string shape are unchanged.
 *
 * MUST-FIX 4 (UI-SPEC S6.8 / S10 anti-pattern S10.8): deploy.html's `.rig`
 * "Test the gate" simulate/clear buttons are prototype-only instrumentation
 * that fakes a critical red-team finding on a REAL agent — they are dropped
 * entirely. `data-gate` here is written exactly once, from the real
 * `recommendation` + `deployment_blocked` signals (mirrors the gatebar
 * pattern established in `agents/[id]/page.tsx`), never from a page-local
 * toggle.
 *
 * Bug fix (Rule 1, found during this port): the prior dusk page polled
 * `GET /checklist-runs/{checklist_run_id}` using the id the trigger POST
 * returns — but that id is the CELERY TASK id
 * (`apps/api/app/api/v1/deployment.py` returns `task.id`), not the
 * `checklist_runs.id` row the Celery task creates for itself
 * (`apps/api/app/worker/tasks/runtime/deployment.py:125`, a fresh
 * `gen_random_uuid()`). Polling by that id 404s forever. This port instead
 * polls the LIST endpoint (`GET /checklist-runs`, already ordered
 * newest-first) while its first row's `status === 'running'` — the same
 * row the trigger POST just inserted, so this is strictly correct and
 * avoids the mismatched id.
 *
 * Widget-exception palette (UI-SPEC S4): the sticky preview hardcodes a
 * light theme (`--widget-accent: #C79A3C`, scoped to `.preview`/`.stage`
 * only, never `:root`) and must NOT repaint on `data-gate="blocked"` — see
 * `PAGE_CSS` below, none of its rules reference `--live`/`--seal`/`--ch-*`.
 *
 * Appearance tiles: UI-SPEC S6.8's prototype copy ("Floating button / Side
 * panel / Inline") uses placeholder radio values (`float`/`panel`/`inline`)
 * that do not match the real `widget_config.appearance` enum
 * (`floating-button` | `floating-mini-modal` | `slide-out-panel` —
 * `apps/api/app/schemas/agent.py` `WidgetConfigUpdate`). This port keeps
 * the REAL three enum values + the dusk page's existing labels/hints
 * (non-regression) and re-skins only the tile visuals to the Gotham
 * `.tile`/`Zone` pattern.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentDetail {
  id: string
  name: string
  status: string
  is_deployed?: boolean
  soul_role?: string | null
  soul_voice?: string | null
}

interface ChecklistWarning {
  warning_id: string
  category: string
  message: string
  severity_level: string
}

// BLR-01 — the fifth M8 signal: a configured ceiling (authorization) and an
// observed maximum (history) as four separately-named cents fields, plus the
// resolved warn thresholds. Every cents field is `number | null` and no
// reader may ever coalesce null to 0 (UI-SPEC D3/D4).
interface BlastRadiusSignal {
  configured_max_single_action_cents: number | null
  configured_max_hourly_aggregate_cents: number | null
  observed_max_single_action_cents: number | null
  observed_max_hourly_aggregate_cents: number | null
  observed_window_days: number
  warn_threshold_single_cents: number | null
  warn_threshold_hourly_cents: number | null
  enabled_skill_count: number
}

interface DeploymentReport {
  summary?: string
  eval_summary?: {
    pass_rates?: Record<string, number>
    failing_scenarios?: number
  }
  red_team_summary?: {
    deployment_blocked?: boolean
    critical_count?: number
    high_count?: number
  }
  corpus_stats?: {
    document_count?: number
    chunk_count?: number
  }
  blast_radius?: BlastRadiusSignal
}

interface ChecklistRun {
  id: string
  agent_id: string
  status: 'running' | 'complete' | 'failed' | string
  recommendation: 'ship' | 'ship_with_warnings' | 'block' | null
  report: DeploymentReport | null
  warnings: ChecklistWarning[]
  warning_acknowledgments: Record<string, string>
  all_warnings_acknowledged: boolean
  approved_at: string | null
  created_at: string
  // BLR-02 (plan 18-07): the envelope hash the run acknowledged, whether it
  // has drifted from the live envelope, and when an owner acknowledged it.
  envelope_hash: string | null
  envelope_acknowledged_at: string | null
  envelope_drift: boolean
}

interface WidgetConfig {
  appearance: 'floating-button' | 'floating-mini-modal' | 'slide-out-panel'
  launcher_shape: 'circle' | 'square'
  colors: Record<string, string>
  typography: {
    font_family: string
    font_custom_url: string | null
    border_radius_preset: string
  }
}

// CAP-03 — actor_mode is an ordinal, not a free string: "off" < any sampled
// rate < "always-on" (UI-SPEC D2). The domain is byte-matched to the server's
// ck_capability_envelopes_actor_mode CHECK constraint.
type ActorMode = 'off' | 'always-on' | `sample_at_rate_${number}`

interface CapabilityEnvelope {
  skill: string
  enabled: boolean
  rate_limit: string | null
  constraints: { max_amount_cents?: number | null; [key: string]: unknown }
  requires_confirmation: boolean
  requires_identity_verification: boolean
  actor_mode: ActorMode
  updated_at: string | null
  // platform_default/mutating are read-only helper fields plan 18-08's GET
  // attaches to every entry — mutating is the ONLY thing this page ever
  // filters the six capability Zones on (never a skill-name list or a slice).
  platform_default: {
    enabled: boolean
    rate_limit: string | null
    constraints: { max_amount_cents?: number | null }
    requires_confirmation: boolean
    requires_identity_verification: boolean
    actor_mode: ActorMode
    mutating: boolean
  }
  mutating: boolean
}

interface CapabilityEnvelopeList {
  envelopes: CapabilityEnvelope[]
}

// ---------------------------------------------------------------------------
// Module-level constants
// ---------------------------------------------------------------------------

const DEFAULT_CONFIG: WidgetConfig = {
  appearance: 'floating-button',
  launcher_shape: 'circle',
  colors: {
    widget_bg: '#FDF9F5',
    header_bg: '#7B1C3A',
    header_text: '#FFFFFF',
    agent_bubble_bg: '#FDF9F5',
    agent_bubble_text: '#4A2030',
    user_bubble_bg: '#7B1C3A',
    user_bubble_text: '#FFFFFF',
    send_button: '#7B1C3A',
    input_bg: '#F7F0EA',
  },
  typography: {
    font_family: 'Inter',
    font_custom_url: null,
    border_radius_preset: 'rounded',
  },
}

const WIDGET_CDN_BASE = process.env.NEXT_PUBLIC_WCHATS_WIDGET_CDN || 'https://widget.wchats.app'
const WIDGET_API_BASE = process.env.NEXT_PUBLIC_WCHATS_API_BASE || ''

function EMBED_SNIPPET(id: string): string {
  return (
    '<script src="' + WIDGET_CDN_BASE + '/widget.js"' +
    ' data-agent="' + id + '"' +
    ' data-api="' + WIDGET_API_BASE + '"' +
    ' async></script>'
  )
}

// The three real appearance modes (WidgetConfigUpdate.appearance, non-regression).
const APPEARANCE_OPTIONS: {
  key: WidgetConfig['appearance']
  label: string
  hint: string
  Icon: () => React.JSX.Element
}[] = [
  {
    key: 'floating-button',
    label: 'Floating button',
    hint: 'A circular launcher fixed to the corner of your page',
    Icon: FloatIcon,
  },
  {
    key: 'floating-mini-modal',
    label: 'Floating mini-modal',
    hint: 'A compact card near the launcher, expands on click',
    Icon: ModalIcon,
  },
  {
    key: 'slide-out-panel',
    label: 'Slide-out panel',
    hint: 'Full-height panel slides in from the right page edge',
    Icon: PanelIcon,
  },
]

const PREVIEW_CAPTIONS: Record<WidgetConfig['appearance'], string> = {
  'floating-button': 'The customer sees a floating bubble in the bottom corner of your site.',
  'floating-mini-modal': 'A compact card opens near the launcher and expands into the full conversation.',
  'slide-out-panel': 'The widget slides in from the right edge and holds the full height of the screen.',
}

// Human labels for every PLATFORM_CAPABILITY_DEFAULTS key (capability_service.py).
// A label map only — membership in the six-Zone capability panel is decided
// exclusively by the `mutating` flag on each envelope, never by this list.
const SKILL_LABELS: Record<string, string> = {
  place_order: 'Place order',
  cancel_order: 'Cancel order',
  issue_refund: 'Issue refund',
  update_subscription: 'Update subscription',
  book_slot: 'Book slot',
  update_customer_record: 'Update customer record',
  confirm_action: 'Confirm action',
}

// CAP-03/D1: the only three windows the rate-limit unit select ever offers,
// narrowest first — mirrors enforcement.py's _UNIT_TO_SECS domain exactly so
// the UI's tightness comparison can never diverge from the server's.
const RATE_UNITS = ['minute', 'hour', 'day'] as const
type RateUnit = (typeof RATE_UNITS)[number]
const RATE_UNIT_SECS: Record<RateUnit, number> = { minute: 60, hour: 3600, day: 86400 }

// CAP-03/D2: fixed tightness order, Off first — this order alone (plus the
// recessed-vs-live treatment) teaches the metaphor without a legend. The Off
// tile is filtered out at render time for every mutating skill.
const ACTOR_MODE_ORDER: { tier: 0 | 1 | 2; key: 'off' | 'sampled' | 'always-on'; label: string }[] = [
  { tier: 0, key: 'off', label: 'Off' },
  { tier: 1, key: 'sampled', label: 'Sampled' },
  { tier: 2, key: 'always-on', label: 'Always-on' },
]

type CapabilityEnvelopePatch = Partial<{
  enabled: boolean
  rate_limit: string
  constraints: { max_amount_cents: number | null }
  requires_confirmation: boolean
  requires_identity_verification: boolean
  actor_mode: ActorMode
}>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toISOString().slice(0, 16).replace('T', ' ')
}

function avgPassRate(rates: Record<string, number> | undefined): number | null {
  if (!rates) return null
  const values = Object.values(rates)
  if (values.length === 0) return null
  return values.reduce((a, b) => a + b, 0) / values.length
}

function buildConsequence(run: ChecklistRun | null): string {
  if (!run) return 'Run the checklist to see whether this agent is ready for a customer.'
  if (run.status === 'running') return "Checking your agent's readiness…"
  if (run.status === 'failed') return 'The last checklist run failed. Try again below.'
  if (run.recommendation === 'block') {
    return run.report?.summary || 'The gate is shut. This agent cannot reach a customer.'
  }
  if (run.recommendation === 'ship_with_warnings') {
    return run.report?.summary || 'Ready to deploy with warnings. Acknowledge each one before approving.'
  }
  return run.report?.summary || 'Every signal holds. Approving puts this agent in front of every customer.'
}

// BLR-01/D3: a configured ceiling and an observed maximum are never merged.
// formatCents/centsOrNotTracked are the only two readers of a blast-radius
// cents figure on this page — neither ever coalesces null to 0 (D4).
function formatCents(cents: number): string {
  const rand = cents / 100
  const [intPart, decPart] = rand.toFixed(2).split('.')
  const withThousands = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `R${withThousands}.${decPart}`
}

function centsOrNotTracked(cents: number | null, windowDays: number): { text: string; tracked: boolean } {
  if (cents === null) {
    return {
      text: `Not tracked yet - no transactions in the last ${windowDays} days.`,
      tracked: false,
    }
  }
  return { text: formatCents(cents), tracked: true }
}

// CAP-03/D1: mirrors enforcement.py's `_parse_rate_limit` exactly — a rate
// comparison computed differently from the server would offer values the
// PATCH route then rejects, exactly the failure mode D1 exists to eliminate.
function parseRateLimit(rateStr: string | null | undefined): { calls: number; windowSecs: number } | null {
  if (!rateStr) return null
  const parts = rateStr.trim().split('/')
  if (parts.length !== 2) return null
  const calls = Number(parts[0])
  if (!Number.isInteger(calls)) return null
  const unit = parts[1].toLowerCase() as RateUnit
  const windowSecs = RATE_UNIT_SECS[unit]
  if (windowSecs === undefined) return null
  return { calls, windowSecs }
}

// The largest integer call count expressible in `unit` without loosening
// `parsed`. Deliberately integer arithmetic on the ORIGINAL calls/window pair
// rather than a round-trip through a per-second float: `5/hour` computed as
// `Math.floor((5 / 3600) * 3600)` can land on 4 in IEEE-754, which would set
// the number input's `max` BELOW its own current value.
function maxCallsForUnit(
  parsed: { calls: number; windowSecs: number } | null,
  unit: RateUnit,
): number | undefined {
  if (parsed === null) return undefined
  return Math.floor((parsed.calls * RATE_UNIT_SECS[unit]) / parsed.windowSecs)
}

// Mirrors capability_service.py's `parse_actor_mode` ordinal pair exactly:
// off < any sampled rate < always-on, and within the sampled tier a higher N
// is tighter.
function actorModeTier(mode: string | null | undefined): { tier: 0 | 1 | 2; n: number } | null {
  if (!mode) return null
  if (mode === 'off') return { tier: 0, n: 0 }
  if (mode === 'always-on') return { tier: 2, n: 0 }
  const match = /^sample_at_rate_([1-9][0-9]?|100)$/.exec(mode)
  if (match) return { tier: 1, n: Number(match[1]) }
  return null
}

function isActorModeReachable(candidateTier: number, currentTier: number): boolean {
  return candidateTier >= currentTier
}

// ---------------------------------------------------------------------------
// Appearance tile icons — bespoke 30x30 stroke SVGs, ported from
// prototypes/gotham/deploy.html's own page-local `.tile svg` markup (not in
// the shared icons.tsx set — that file only carries rail + cross-page
// utility glyphs; deploy.html defines these three page-locally too).
// ---------------------------------------------------------------------------

function FloatIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.4" y="2.4" width="25.2" height="25.2" rx="2.6" />
      <circle cx="21.6" cy="21.6" r="3.6" fill="currentColor" fillOpacity={0.18} />
    </svg>
  )
}

function PanelIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.4" y="2.4" width="25.2" height="25.2" rx="2.6" />
      <path d="M19.4 2.4v25.2" />
      <path d="M22.2 9.6h2.6" />
      <path d="M22.2 13.2h2.6" />
    </svg>
  )
}

function ModalIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.4" y="2.4" width="25.2" height="25.2" rx="2.6" />
      <rect x="7" y="10.4" width="16" height="9.2" rx="1.6" fill="currentColor" fillOpacity={0.18} />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function AppearanceTile({
  option,
  selected,
  onSelect,
}: {
  option: (typeof APPEARANCE_OPTIONS)[number]
  selected: boolean
  onSelect: () => void
}) {
  const Icon = option.Icon
  return (
    <Zone as="label" live={selected} className="tile">
      <input type="radio" name="appearance" value={option.key} checked={selected} onChange={onSelect} />
      <Icon />
      <span className="name">{option.label}</span>
      <span className="note">{option.hint}</span>
    </Zone>
  )
}

// BLR-01 — D3's two-line-per-figure rule: a configured ceiling and an
// observed maximum are always two labelled lines, never merged into one
// number. D4 splits the two absence cases: a missing observation is quiet
// muted text (D4.1), an unconfigured ceiling is a verdict chip (D4.2).
function BlastRadiusBlock({ blastRadius }: { blastRadius: BlastRadiusSignal | undefined }) {
  if (!blastRadius || blastRadius.enabled_skill_count === 0) {
    return (
      <div className="blast-block">
        <p className="blast-note">
          No transactional skill is enabled for this agent. There is no blast radius to report.
        </p>
      </div>
    )
  }

  const {
    configured_max_single_action_cents: configuredSingle,
    configured_max_hourly_aggregate_cents: configuredHourly,
    observed_max_single_action_cents: observedSingle,
    observed_max_hourly_aggregate_cents: observedHourly,
    observed_window_days: windowDays,
    warn_threshold_single_cents: warnSingle,
    warn_threshold_hourly_cents: warnHourly,
    enabled_skill_count: enabledSkillCount,
  } = blastRadius

  const singleObserved = centsOrNotTracked(observedSingle, windowDays)
  const hourlyObserved = centsOrNotTracked(observedHourly, windowDays)

  return (
    <div className="blast-block">
      <div className="blast-line">
        <span className="blast-label">Max single action</span>
        <span className="num">{configuredSingle !== null ? `${formatCents(configuredSingle)} ceiling` : 'No ceiling'}</span>
        {configuredSingle === null && enabledSkillCount > 0 ? (
          <Chip verdict="fail">No ceiling</Chip>
        ) : configuredSingle === null ? null : warnSingle === null ? (
          // No threshold was resolved for this tenant, so nothing was measured
          // against. "Within threshold" in --pass here would assert that a
          // ceiling cleared a bar that does not exist. Same treatment the four
          // signal rows above give a missing input.
          <Chip verdict="mute">No threshold set</Chip>
        ) : configuredSingle > warnSingle ? (
          <Chip verdict="fail">Exceeds threshold</Chip>
        ) : (
          <Chip verdict="pass">Within threshold</Chip>
        )}
      </div>
      <div className="blast-line">
        <span className="blast-label vh">Max single action, observed</span>
        {singleObserved.tracked ? (
          <span className="num">{singleObserved.text} observed · {windowDays}d</span>
        ) : (
          <span className="num blast-note">{singleObserved.text}</span>
        )}
      </div>

      <div className="blast-line">
        <span className="blast-label">Max hourly aggregate</span>
        <span className="num">{configuredHourly !== null ? `${formatCents(configuredHourly)} ceiling` : 'No ceiling'}</span>
        {configuredHourly === null && enabledSkillCount > 0 ? (
          <Chip verdict="fail">No ceiling</Chip>
        ) : configuredHourly === null ? null : warnHourly === null ? (
          <Chip verdict="mute">No threshold set</Chip>
        ) : configuredHourly > warnHourly ? (
          <Chip verdict="fail">Exceeds threshold</Chip>
        ) : (
          <Chip verdict="pass">Within threshold</Chip>
        )}
      </div>
      <div className="blast-line">
        <span className="blast-label vh">Max hourly aggregate, observed</span>
        {hourlyObserved.tracked ? (
          <span className="num">{hourlyObserved.text} observed · {windowDays}d</span>
        ) : (
          <span className="num blast-note">{hourlyObserved.text}</span>
        )}
      </div>
    </div>
  )
}

// BLR-02 — D5: the object of attestation is the human-legible table the
// envelope hash is computed over, with the checkbox bound directly beneath
// it, never the hex string itself. D6: "Confirmation required" / "Verification
// required" are plain `.help`-weight text, never a Chip — a chip on this
// surface would dilute exactly the verdict signal the owner needs at the
// moment they accept financial risk.
function EnvelopeAcknowledgement({
  latestRun,
  capabilityEnvelopes,
  envelopesLoaded,
  envelopesFailed,
  envelopeAcknowledged,
  onToggleAcknowledged,
}: {
  latestRun: ChecklistRun
  capabilityEnvelopes: CapabilityEnvelope[]
  envelopesLoaded: boolean
  envelopesFailed: boolean
  envelopeAcknowledged: boolean
  onToggleAcknowledged: (checked: boolean) => void
}) {
  const drifted = latestRun.envelope_drift === true
  const enabledEnvelopes = capabilityEnvelopes.filter((env) => env.enabled)
  const hash = latestRun.envelope_hash
  const fingerprint = hash ? `${hash.slice(0, 8)}…${hash.slice(-4)}` : null
  // The signature is only collectable once the configuration being attested to
  // is actually on screen. An in-flight or failed capability GET leaves
  // `capabilityEnvelopes` empty, which would render this table as "no skill is
  // enabled" and still take the owner's tick: a false claim about a
  // money-moving configuration, collected at the moment of attestation. A run
  // with no `envelope_hash` has nothing to fingerprint, so it is not
  // attestable either.
  const attestable = envelopesLoaded && hash !== null

  return (
    <Zone className="ack-zone" aria-labelledby="ack-label">
      <div className="section-head">
        <h3 className="label" id="ack-label">Capability envelope</h3>
        {drifted && <Chip verdict="fail">Changed since approval</Chip>}
      </div>

      {drifted ? (
        <>
          <p className="voice">Capability limits changed since this checklist ran.</p>
          <p className="help">
            Re-run the checklist to review and acknowledge the new configuration before deploying.
          </p>
        </>
      ) : !attestable ? (
        <>
          <p className="voice">
            {envelopesFailed
              ? 'The capability limits could not be loaded.'
              : !envelopesLoaded
                ? 'Reading the capability limits…'
                : 'This checklist run carries no configuration fingerprint.'}
          </p>
          <p className="help">
            {envelopesFailed
              ? 'Reload the page. Approve stays disabled until these limits are on screen.'
              : !envelopesLoaded
                ? 'They appear here, with the acknowledgement, once they load.'
                : 'Re-run the checklist to record the configuration this deploy would approve.'}
          </p>
        </>
      ) : (
        <>
          <Ledger
            caption="The capability limits this checklist's envelope hash covers, one row per enabled skill."
            className="ack-table"
          >
            <thead>
              <tr>
                <LedgerColHead>Skill</LedgerColHead>
                <LedgerColHead numeric>Rate limit</LedgerColHead>
                <LedgerColHead numeric>Max amount</LedgerColHead>
                <LedgerColHead>Confirmation</LedgerColHead>
                <LedgerColHead>Verification</LedgerColHead>
                <LedgerColHead>Actor mode</LedgerColHead>
              </tr>
            </thead>
            <tbody>
              {enabledEnvelopes.length === 0 ? (
                <tr>
                  <td colSpan={6} className="blast-note">No skill is enabled yet.</td>
                </tr>
              ) : (
                enabledEnvelopes.map((env) => (
                  <tr key={env.skill}>
                    <LedgerRowHead>{SKILL_LABELS[env.skill] ?? env.skill}</LedgerRowHead>
                    <td className="num">{env.rate_limit ?? 'not set'}</td>
                    <td className="num">
                      {env.constraints.max_amount_cents != null
                        ? formatCents(env.constraints.max_amount_cents)
                        : 'not set'}
                    </td>
                    <td>{env.requires_confirmation && <span className="help">Confirmation required</span>}</td>
                    <td>{env.requires_identity_verification && <span className="help">Verification required</span>}</td>
                    <td>{env.actor_mode}</td>
                  </tr>
                ))
              )}
            </tbody>
          </Ledger>
          {/* Reached only when `attestable`, so the fingerprint is always a
              real hash here — there is no "config unavailable" caption sitting
              above a tickable checkbox. */}
          <p className="ack-fingerprint mono">config {fingerprint}</p>
          <label className="ack-checkbox">
            <input
              type="checkbox"
              checked={envelopeAcknowledged}
              onChange={(e) => onToggleAcknowledged(e.target.checked)}
            />
            <span>I&apos;ve reviewed these limits and approve deploying with them</span>
          </label>
        </>
      )}
    </Zone>
  )
}

// CAP-03/D2 — the actor-mode segmented control, built from the AppearanceTile
// shape. Positions to the left of the current selection (looser) render
// recessed and unreachable; the Off tile is physically absent whenever the
// skill is mutating (tested on `envelope.mutating`, never a skill-name list).
function ActorModeTiles({
  envelope,
  isSaving,
  onSave,
}: {
  envelope: CapabilityEnvelope
  isSaving: boolean
  onSave: (skill: string, patch: CapabilityEnvelopePatch) => void
}) {
  const currentTier = actorModeTier(envelope.actor_mode)
  const currentTierNum = currentTier?.tier ?? 2
  const [sampledN, setSampledN] = useState<number>(
    currentTier?.tier === 1 ? currentTier.n : Math.max(currentTier?.n ?? 1, 1),
  )

  const tiles = ACTOR_MODE_ORDER.filter((t) => !(t.tier === 0 && envelope.mutating))

  const handleSelect = (tier: 0 | 1 | 2) => {
    if (!isActorModeReachable(tier, currentTierNum)) return
    if (tier === 0) {
      onSave(envelope.skill, { actor_mode: 'off' })
    } else if (tier === 2) {
      onSave(envelope.skill, { actor_mode: 'always-on' })
    } else {
      const n = Math.max(sampledN, currentTier?.tier === 1 ? currentTier.n : 1)
      onSave(envelope.skill, { actor_mode: `sample_at_rate_${n}` as ActorMode })
    }
  }

  return (
    <div className="field cap-row">
      <label>Actor mode</label>
      <div className="tiles cap-actor-tiles">
        {tiles.map((t) => {
          const selected = currentTierNum === t.tier
          const reachable = isActorModeReachable(t.tier, currentTierNum)
          return (
            <Zone
              key={t.key}
              as="label"
              live={selected}
              className={reachable ? 'tile' : 'tile tile-recessed'}
            >
              <input
                type="radio"
                name={`actor-mode-${envelope.skill}`}
                checked={selected}
                disabled={!reachable || isSaving}
                onChange={() => handleSelect(t.tier)}
              />
              <span className="name">{t.label}</span>
            </Zone>
          )
        })}
      </div>
      {currentTier?.tier === 1 && (
        <div className="cap-sampled-stepper">
          <label htmlFor={`${envelope.skill}-sample-n`}>Sample rate (per 100 calls)</label>
          <input
            id={`${envelope.skill}-sample-n`}
            type="number"
            min={currentTier.n}
            max={100}
            value={sampledN}
            disabled={isSaving}
            onChange={(e) => setSampledN(Math.max(Number(e.target.value) || currentTier.n, currentTier.n))}
            onBlur={() => {
              if (sampledN !== currentTier.n) {
                onSave(envelope.skill, { actor_mode: `sample_at_rate_${sampledN}` as ActorMode })
              }
            }}
          />
        </div>
      )}
      <p className="help cap-caption">
        {currentTierNum === 2
          ? 'Currently: Always-on. Nothing stricter exists for this skill.'
          : currentTierNum === 1
            ? `Currently: Sampled at ${currentTier?.n ?? 1} in 100.`
            : 'Currently: Off. Turning this on adds Actor review before every call.'}
      </p>
    </div>
  )
}

// CAP-03/D1 — every control here is built so the looser direction is not a
// reachable input state: a capped number input, a filtered unit list, a
// one-way toggle that becomes a locked chip, and the actor-mode tiles above.
function CapabilityZone({
  envelope,
  fieldErrors,
  isSaving,
  onSave,
}: {
  envelope: CapabilityEnvelope
  fieldErrors: Record<string, string>
  isSaving: boolean
  onSave: (skill: string, patch: CapabilityEnvelopePatch) => void
}) {
  const enabledLocked = envelope.enabled === false && envelope.platform_default.enabled === false
  const currentMaxCents = envelope.constraints.max_amount_cents ?? null
  const parsedRate = parseRateLimit(envelope.rate_limit)
  const currentUnit: RateUnit =
    (RATE_UNITS.find((u) => RATE_UNIT_SECS[u] === parsedRate?.windowSecs) as RateUnit | undefined) ?? 'hour'
  const currentSecs = parsedRate?.windowSecs ?? RATE_UNIT_SECS.day

  const maxForUnit = (u: RateUnit): number | undefined => maxCallsForUnit(parsedRate, u)

  // A window is only offered when it is at-or-narrower than the current one
  // AND the tightest rate it can express is a reachable integer. Filtering on
  // window length alone offers `minute` for the factory-default `5/hour`,
  // where the only expressible value is `0/minute`: the server accepts it
  // (`_parse_rate_limit` has no `> 0` guard, and `validate_tighten_only` reads
  // 0 as tighter than everything), after which every nonzero rate is a loosen
  // and the skill is capped at zero calls permanently.
  const allowedUnits = RATE_UNITS.filter(
    (u) => RATE_UNIT_SECS[u] <= currentSecs && (maxForUnit(u) ?? 1) >= 1,
  )

  const [unit, setUnit] = useState<RateUnit>(currentUnit)
  const [rateInput, setRateInput] = useState<string>(parsedRate ? String(parsedRate.calls) : '')
  const [maxAmountInput, setMaxAmountInput] = useState<string>(
    currentMaxCents !== null ? String(currentMaxCents / 100) : '',
  )

  const handleRateNumberChange = (raw: string) => {
    const cap = maxForUnit(unit)
    const num = Number(raw)
    if (cap !== undefined && Number.isFinite(num) && num > cap) {
      setRateInput(String(cap))
    } else {
      setRateInput(raw)
    }
  }

  const commitRate = () => {
    const num = Number(rateInput)
    if (!Number.isFinite(num) || num <= 0) return
    const nextRate = `${num}/${unit}`
    if (nextRate !== envelope.rate_limit) onSave(envelope.skill, { rate_limit: nextRate })
  }

  const handleUnitChange = (newUnit: RateUnit) => {
    const cap = maxForUnit(newUnit)
    const currentNum = Number(rateInput)
    const nextNum = cap !== undefined ? Math.min(Number.isFinite(currentNum) ? currentNum : cap, cap) : currentNum
    // Hard floor, independent of the option filter above: no code path in this
    // component may emit a non-positive rate, because a zero written here
    // cannot be raised again from this screen.
    if (!Number.isFinite(nextNum) || nextNum < 1) return
    setUnit(newUnit)
    setRateInput(String(nextNum))
    const nextRate = `${nextNum}/${newUnit}`
    if (nextRate !== envelope.rate_limit) onSave(envelope.skill, { rate_limit: nextRate })
  }

  const handleMaxAmountChange = (raw: string) => {
    if (currentMaxCents === null) {
      setMaxAmountInput(raw)
      return
    }
    const capRand = currentMaxCents / 100
    const num = Number(raw)
    if (Number.isFinite(num) && num > capRand) {
      setMaxAmountInput(String(capRand))
    } else {
      setMaxAmountInput(raw)
    }
  }

  const commitMaxAmount = () => {
    const num = Number(maxAmountInput)
    if (!Number.isFinite(num) || num < 0) return
    const nextCents = Math.round(num * 100)
    if (nextCents !== currentMaxCents) {
      onSave(envelope.skill, { constraints: { ...envelope.constraints, max_amount_cents: nextCents } })
    }
  }

  return (
    <Zone className="cap-zone" aria-labelledby={`cap-${envelope.skill}-label`}>
      <h3 className="label" id={`cap-${envelope.skill}-label`}>{SKILL_LABELS[envelope.skill] ?? envelope.skill}</h3>

      <div className="field cap-row">
        <label htmlFor={`${envelope.skill}-enabled`}>Enabled</label>
        <input
          id={`${envelope.skill}-enabled`}
          type="checkbox"
          checked={envelope.enabled}
          disabled={enabledLocked || isSaving}
          onChange={(e) => onSave(envelope.skill, { enabled: e.target.checked })}
        />
        <p className="help cap-caption">
          {enabledLocked
            ? 'Cannot re-enable - the platform default is off for this skill.'
            : envelope.enabled
              ? 'Enabled.'
              : 'Disabled.'}
        </p>
        {fieldErrors[`${envelope.skill}.enabled`] && (
          <p className="help cap-error">{fieldErrors[`${envelope.skill}.enabled`]}</p>
        )}
      </div>

      <div className="field cap-row">
        <label htmlFor={`${envelope.skill}-rate-n`}>Rate limit</label>
        <div className="cap-rate-inputs">
          <input
            id={`${envelope.skill}-rate-n`}
            type="number"
            min={1}
            max={maxForUnit(unit)}
            value={rateInput}
            disabled={isSaving}
            onChange={(e) => handleRateNumberChange(e.target.value)}
            onBlur={commitRate}
          />
          {allowedUnits.length === 1 ? (
            // One reachable window means there is no choice to present. A
            // select holding a single option reads as an offer that isn't one.
            <span className="cap-unit-fixed">per {allowedUnits[0]}</span>
          ) : (
            <select value={unit} disabled={isSaving} onChange={(e) => handleUnitChange(e.target.value as RateUnit)}>
              {allowedUnits.map((u) => (
                <option key={u} value={u}>{u}</option>
              ))}
            </select>
          )}
        </div>
        <p className="help cap-caption">
          {parsedRate !== null ? `Currently ${parsedRate.calls} per ${currentUnit}.` : 'No rate limit set.'}
        </p>
        {fieldErrors[`${envelope.skill}.rate_limit`] && (
          <p className="help cap-error">{fieldErrors[`${envelope.skill}.rate_limit`]}</p>
        )}
      </div>

      <div className="field cap-row">
        <label htmlFor={`${envelope.skill}-max-amount`}>Max amount</label>
        <input
          id={`${envelope.skill}-max-amount`}
          type="number"
          min={0}
          step="0.01"
          max={currentMaxCents !== null ? currentMaxCents / 100 : undefined}
          value={maxAmountInput}
          placeholder="No ceiling set"
          disabled={isSaving}
          onChange={(e) => handleMaxAmountChange(e.target.value)}
          onBlur={commitMaxAmount}
        />
        <p className="help cap-caption">
          {currentMaxCents !== null ? `Currently ${formatCents(currentMaxCents)}.` : 'No ceiling set.'}
        </p>
        {fieldErrors[`${envelope.skill}.constraints`] && (
          <p className="help cap-error">{fieldErrors[`${envelope.skill}.constraints`]}</p>
        )}
      </div>

      <div className="field cap-row">
        <label htmlFor={`${envelope.skill}-confirmation`}>Confirmation</label>
        {envelope.requires_confirmation ? (
          <Chip verdict="live">On</Chip>
        ) : (
          <input
            id={`${envelope.skill}-confirmation`}
            type="checkbox"
            checked={false}
            disabled={isSaving}
            onChange={(e) => {
              if (e.target.checked) onSave(envelope.skill, { requires_confirmation: true })
            }}
          />
        )}
        <p className="help cap-caption">
          {envelope.requires_confirmation
            ? 'Confirmation is on - it cannot be turned off from here.'
            : 'Off. Turning this on requires the customer to confirm before this action runs.'}
        </p>
      </div>

      <div className="field cap-row">
        <label htmlFor={`${envelope.skill}-verification`}>Verification</label>
        {envelope.requires_identity_verification ? (
          <Chip verdict="live">On</Chip>
        ) : (
          <input
            id={`${envelope.skill}-verification`}
            type="checkbox"
            checked={false}
            disabled={isSaving}
            onChange={(e) => {
              if (e.target.checked) onSave(envelope.skill, { requires_identity_verification: true })
            }}
          />
        )}
        <p className="help cap-caption">
          {envelope.requires_identity_verification
            ? 'Verification is on - it cannot be turned off from here.'
            : 'Off. Turning this on requires identity verification before this action runs.'}
        </p>
      </div>

      <ActorModeTiles envelope={envelope} isSaving={isSaving} onSave={onSave} />
    </Zone>
  )
}

// The customer's side of the glass — the one friendly, light-mode surface in
// the whole console (UI-SPEC S4 widget exception). Decorative: it renders
// the SELECTED mode's layout/caption, but never the operator's own dark
// tokens, and never repaints on `data-gate="blocked"`.
function WidgetPreview({ mode }: { mode: WidgetConfig['appearance'] }) {
  const stageMode = mode === 'slide-out-panel' ? 'panel' : mode === 'floating-mini-modal' ? 'modal' : 'float'
  return (
    <aside className="preview" aria-labelledby="preview-label">
      <span className="label" id="preview-label">Preview</span>
      <div className="stage" data-mode={stageMode}>
        <div className="widget" aria-hidden="true">
          <div className="w-head">
            <span className="w-avatar">W</span>
            <span>
              <span className="w-name">Your agent</span>
              <span className="w-state">Usually replies in a few seconds</span>
            </span>
          </div>
          <div className="w-body">
            <p className="w-msg w-agent">Hi — ask me anything about the business.</p>
            <p className="w-msg w-user">What are your opening hours?</p>
          </div>
          <div className="w-input">
            <span>Ask a question</span>
            <span className="w-send">Send</span>
          </div>
        </div>
        {stageMode === 'float' && (
          <div className="launcher" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
              <path d="M17 9.6a6.6 6.6 0 0 1-7.1 6.6L4 17.5l1.4-4.6A6.6 6.6 0 1 1 17 9.6z" />
            </svg>
          </div>
        )}
      </div>
      <p className="caption">{PREVIEW_CAPTIONS[mode]}</p>
    </aside>
  )
}

// ---------------------------------------------------------------------------
// DeployPage
// ---------------------------------------------------------------------------

export default function DeployPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const queryClient = useQueryClient()
  const { setGate } = useGate()

  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied'>('idle')
  const [widgetConfig, setWidgetConfig] = useState<WidgetConfig>(DEFAULT_CONFIG)
  // BLR-02/D5: acknowledging one configuration must never carry over to a
  // different one — reset on latestRun.id/envelope_hash change below.
  const [envelopeAcknowledged, setEnvelopeAcknowledged] = useState(false)
  // CAP-03/D1 server-side backstop: keyed "<skill>.<field>", set from a
  // rejected PATCH and cleared the moment that field changes again.
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  // ---- Agent (name, is_deployed, soul fields for the "Soul" gate row) ----
  const agentQuery = useQuery({
    queryKey: ['agent', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated. Please sign in.')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<AgentDetail>
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 15_000,
  })
  const agent = agentQuery.data ?? null

  // ---- Checklist runs (list, newest-first) — PRESERVED, GET checklist-runs.
  // Also the polling mechanism: refetches every 3s while the newest row is
  // still 'running' (see Rule-1 bug-fix note in the file header). ---------
  const checklistListQuery = useQuery({
    queryKey: ['checklist-runs', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/checklist-runs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      return (data.runs ?? []) as ChecklistRun[]
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 10_000,
    refetchInterval: (query) => (query.state.data?.[0]?.status === 'running' ? 3000 : false),
  })
  const latestRun = checklistListQuery.data?.[0] ?? null

  // BLR-02/D5: an acknowledgement of one configuration must never carry over
  // to a different one — reset whenever the run or its envelope hash changes.
  useEffect(() => {
    setEnvelopeAcknowledged(false)
  }, [latestRun?.id, latestRun?.envelope_hash])

  // ---- Capability envelopes (CAP-03) — GET added here (plan 18-10 Task 1),
  // consumed by both the acknowledgement summary table and (plan 18-10 Task
  // 2) the six capability Zones. PATCH mutation added in Task 2. -----------
  const capabilityEnvelopesQuery = useQuery({
    queryKey: ['capability-envelopes', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/capability-envelopes`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as CapabilityEnvelopeList
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 15_000,
  })
  const capabilityEnvelopes = capabilityEnvelopesQuery.data?.envelopes ?? []
  // CAP-03: the mutating flag is the ONLY thing that decides which entries
  // render a capability Zone — never a slice, a length check, or a
  // hard-coded skill-name list, so a future seventh mutating skill appears
  // here with no second edit.
  const mutatingCapabilityEnvelopes = useMemo(
    () => capabilityEnvelopes.filter((env) => env.mutating === true),
    [capabilityEnvelopes],
  )

  const saveCapabilityEnvelope = useMutation({
    mutationFn: async ({ skill, patch }: { skill: string; patch: CapabilityEnvelopePatch }) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/capability-envelopes/${skill}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        const detail = (body as { detail?: string }).detail ?? `HTTP ${res.status}`
        throw Object.assign(new Error(detail), { skill, patch })
      }
      return res.json() as Promise<CapabilityEnvelope>
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['capability-envelopes', id] })
      // A capability PATCH changes the live envelope, so the checklist run's
      // `envelope_hash` / `envelope_drift` are now stale. Without refetching
      // the run, the reset effect below never fires (it keys on the run's id
      // and hash, neither of which changes while the run is not refetched):
      // the tick survives, the caption keeps printing the OLD fingerprint over
      // the NEW figures, `envelope_drift` stays false, and Approve stays
      // enabled on a configuration nobody attested to. Dropping the tick here
      // as well means the acknowledgement is never carried across an edit even
      // for the moment before the refetch lands.
      queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] })
      setEnvelopeAcknowledged(false)
    },
    onError: (err: unknown) => {
      // D1's server-side backstop: a rejection lands inline under the
      // specific field it targeted — never a toast, never a page alert.
      const withContext = err as Error & { skill?: string; patch?: CapabilityEnvelopePatch }
      const skill = withContext.skill
      const field = withContext.patch ? Object.keys(withContext.patch)[0] : undefined
      if (skill && field) {
        setFieldErrors((prev) => ({ ...prev, [`${skill}.${field}`]: withContext.message }))
      }
    },
  })

  const handleSaveCapability = (skill: string, patch: CapabilityEnvelopePatch) => {
    setFieldErrors((prev) => {
      const next = { ...prev }
      for (const field of Object.keys(patch)) delete next[`${skill}.${field}`]
      return next
    })
    saveCapabilityEnvelope.mutate({ skill, patch })
  }

  // ---- Widget config — PRESERVED, GET/POST widget-config. ---------------
  const widgetConfigQuery = useQuery({
    queryKey: ['widget-config', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<Partial<WidgetConfig>>
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  useEffect(() => {
    const data = widgetConfigQuery.data
    if (data && Object.keys(data).length > 0) {
      setWidgetConfig((prev) => ({
        ...prev,
        ...data,
        colors: { ...prev.colors, ...(data.colors ?? {}) },
        typography: { ...prev.typography, ...(data.typography ?? {}) },
      }))
    }
  }, [widgetConfigQuery.data])

  const saveWidgetConfig = useMutation({
    mutationFn: async (next: WidgetConfig) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      // Always POSTs the FULL merged config — WidgetConfigUpdate.colors/typography
      // default-fill server-side, so a partial body would silently reset any
      // previously-saved colors/typography (Rule 1 data-loss guard).
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
  })

  const handleSelectAppearance = (appearance: WidgetConfig['appearance']) => {
    const next = { ...widgetConfig, appearance }
    setWidgetConfig(next)
    saveWidgetConfig.mutate(next)
  }

  // ---- Checklist actions — PRESERVED endpoints. --------------------------
  const triggerChecklist = useMutation({
    mutationFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/checklist-runs`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.status !== 202) throw new Error(`Failed to start checklist run (HTTP ${res.status}).`)
      return res.json()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] }),
  })

  const acknowledgeWarning = useMutation({
    mutationFn: async (warningId: string) => {
      if (!latestRun) throw new Error('No checklist run to acknowledge against')
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(
        `${apiBase}/api/v1/agents/${id}/checklist-runs/${latestRun.id}/acknowledge`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ warning_ids: [warningId] }),
        },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] }),
  })

  const approveDeployment = useMutation({
    mutationFn: async () => {
      if (!latestRun) throw new Error('No checklist run to approve')
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/approve-deployment`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ checklist_run_id: latestRun.id }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { detail?: string }).detail ?? `Approval failed (HTTP ${res.status})`)
      }
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent', id] })
      queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] })
    },
  })

  // ---- Embed copy — Clipboard API with an execCommand fallback for
  // insecure/file:// contexts (UI-SPEC S6.8). --------------------------
  const handleCopyEmbed = () => {
    const text = EMBED_SNIPPET(id)
    const flip = () => {
      setCopyStatus('copied')
      setTimeout(() => setCopyStatus('idle'), 1500)
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(flip, () => fallbackCopy(text, flip))
    } else {
      fallbackCopy(text, flip)
    }
  }
  const fallbackCopy = (text: string, onDone: () => void) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      onDone()
    } catch {
      /* Select-and-copy failed; the snippet text is still visible to copy by hand. */
    }
    document.body.removeChild(ta)
  }

  // ---- The gate — derived entirely from the real latest checklist run
  // (mirrors the gatebar pattern in agents/[id]/page.tsx). No page-local
  // toggle ever exists here (must-fix 4). --------------------------------
  const report = latestRun?.status === 'complete' ? latestRun.report : null
  const evalAvg = avgPassRate(report?.eval_summary?.pass_rates)
  const failingScenarios = report?.eval_summary?.failing_scenarios ?? 0
  const criticalFindings = report?.red_team_summary?.critical_count ?? 0
  const highFindings = report?.red_team_summary?.high_count ?? 0
  const redTeamBlockedSignal = report?.red_team_summary?.deployment_blocked === true
  const docCount = report?.corpus_stats?.document_count ?? 0
  const chunkCount = report?.corpus_stats?.chunk_count ?? 0
  const soulConfigured = !!(agent?.soul_role && agent?.soul_voice)

  const checklistBlocked = latestRun?.status === 'complete' && latestRun.recommendation === 'block'
  const gateBlocked = checklistBlocked || redTeamBlockedSignal

  useEffect(() => {
    setGate(gateBlocked ? 'blocked' : 'open')
  }, [gateBlocked, setGate])

  // BLR-02/D5: envelope acknowledgement and drift are folded into
  // isApprovable alongside the pre-existing recommendation/warnings checks —
  // baseApprovable is kept separate so the inline reason caption below can
  // tell "blocked by the checklist" apart from "blocked by the envelope".
  const baseApprovable =
    !!latestRun &&
    latestRun.status === 'complete' &&
    (latestRun.recommendation === 'ship' ||
      (latestRun.recommendation === 'ship_with_warnings' && latestRun.all_warnings_acknowledged))
  // `capabilityEnvelopesQuery.isSuccess` is a precondition of approval, not a
  // cosmetic detail: without it, a failed capability GET renders an empty
  // attestation table and Approve would still light up behind a tick the owner
  // gave to a configuration that was never on screen.
  const isApprovable =
    baseApprovable &&
    latestRun?.envelope_drift === false &&
    envelopeAcknowledged === true &&
    capabilityEnvelopesQuery.isSuccess

  const loadError = useMemo(() => {
    const errs = [
      agentQuery.error,
      checklistListQuery.error,
      widgetConfigQuery.error,
      capabilityEnvelopesQuery.error,
    ]
    const first = errs.find((e): e is Error => e instanceof Error)
    return first?.message ?? null
  }, [
    agentQuery.error,
    checklistListQuery.error,
    widgetConfigQuery.error,
    capabilityEnvelopesQuery.error,
  ])

  const actionError = useMemo(() => {
    const errs = [triggerChecklist.error, acknowledgeWarning.error, approveDeployment.error]
    const first = errs.find((e): e is Error => e instanceof Error)
    return first?.message ?? null
  }, [triggerChecklist.error, acknowledgeWarning.error, approveDeployment.error])

  const consequence = buildConsequence(latestRun)
  const gateStamp = latestRun && latestRun.status === 'complete' ? latestRun.created_at : null

  return (
    <div className="page">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />

      <header className="page-head">
        <div className="row">
          <div>
            <h1>Deploy</h1>
            <p className="sub">
              Four signals stand between this agent and a paying customer. The gate opens only
              while all four hold, and it shuts itself the moment one does not.
            </p>
          </div>
        </div>
      </header>

      {loadError && (
        <div role="alert" className="alert">{loadError}</div>
      )}
      {actionError && (
        <div role="alert" className="alert">{actionError}</div>
      )}

      <div className="bench">
        <div>
          {/* ═══ THE GATE ══════════════════════════════════════════════ */}
          <section aria-labelledby="gate-label">
            <div className="section-head">
              <h2 className="label" id="gate-label">The gate</h2>
              <div className="gate-chips">
                {latestRun?.envelope_drift === true && <Chip verdict="fail">Changed since approval</Chip>}
                <Chip verdict={gateBlocked ? 'seal' : 'pass'}>{gateBlocked ? 'Shut' : 'Open'}</Chip>
              </div>
            </div>

            {!latestRun && (
              <div className="gate-idle">
                <p className="voice">Run the checklist to see whether this agent is ready for a customer.</p>
                <Btn onClick={() => triggerChecklist.mutate()} disabled={triggerChecklist.isPending || agent?.status !== 'ready'}>
                  {triggerChecklist.isPending ? 'Starting…' : 'Run pre-deployment checklist'}
                </Btn>
                {agent && agent.status !== 'ready' && (
                  <p className="foot-note">The agent must be ready before the checklist can run.</p>
                )}
              </div>
            )}

            {latestRun?.status === 'running' && (
              <div className="gate-idle">
                <span className="spinner" aria-hidden="true" />
                <p className="voice">Checking your agent&apos;s readiness…</p>
                <p className="foot-note" role="status" aria-live="polite">
                  This usually takes 1–2 minutes. Results appear here automatically.
                </p>
              </div>
            )}

            {latestRun?.status === 'failed' && (
              <div className="gate-idle">
                <p className="voice">The last checklist run failed.</p>
                <Btn onClick={() => triggerChecklist.mutate()} disabled={triggerChecklist.isPending}>
                  {triggerChecklist.isPending ? 'Starting…' : 'Retry checklist'}
                </Btn>
              </div>
            )}

            {latestRun?.status === 'complete' && (
              <>
                <Ledger caption="The four signals a deployment gate checks before an agent can reach a customer.">
                  <thead>
                    <tr>
                      <LedgerColHead className="sig">Signal</LedgerColHead>
                      <LedgerColHead>Value</LedgerColHead>
                      <LedgerColHead>Verdict</LedgerColHead>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <LedgerRowHead className="sig">
                        Evals pass rate
                        <span className="why">Ragas-scored scenarios, judged for faithfulness &amp; relevancy</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {evalAvg !== null
                          ? `${evalAvg.toFixed(2)} avg${failingScenarios > 0 ? ` · ${failingScenarios} failing` : ''}`
                          : 'not yet run'}
                      </td>
                      <td>
                        <Chip verdict={evalAvg === null ? 'mute' : failingScenarios > 0 ? 'fail' : 'pass'}>
                          {evalAvg === null ? 'No data' : failingScenarios > 0 ? 'Fail' : 'Pass'}
                        </Chip>
                      </td>
                    </tr>
                    <tr>
                      <LedgerRowHead className="sig">
                        Red team
                        <span className="why">Adversarial probes for jailbreaks &amp; data leakage</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {report?.red_team_summary
                          ? `${criticalFindings} critical · ${highFindings} high`
                          : 'not yet run'}
                      </td>
                      <td>
                        <Chip verdict={!report?.red_team_summary ? 'mute' : redTeamBlockedSignal ? 'fail' : 'pass'}>
                          {!report?.red_team_summary ? 'No data' : redTeamBlockedSignal ? 'Fail' : 'Pass'}
                        </Chip>
                      </td>
                    </tr>
                    <tr>
                      <LedgerRowHead className="sig">
                        Knowledge base
                        <span className="why">Everything the agent is allowed to cite</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {report?.corpus_stats
                          ? `${docCount} document${docCount === 1 ? '' : 's'} · ${chunkCount} chunk${chunkCount === 1 ? '' : 's'}`
                          : 'not yet run'}
                      </td>
                      <td>
                        <Chip verdict={!report?.corpus_stats ? 'mute' : docCount > 0 ? 'pass' : 'fail'}>
                          {!report?.corpus_stats ? 'No data' : docCount > 0 ? 'Pass' : 'Fail'}
                        </Chip>
                      </td>
                    </tr>
                    <tr>
                      <LedgerRowHead className="sig">
                        Soul
                        <span className="why">Tone, refusals and escalation rules</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {agentQuery.isPending ? 'checking…' : soulConfigured ? 'role & voice set' : 'not yet configured'}
                      </td>
                      <td>
                        <Chip verdict={agentQuery.isPending ? 'mute' : soulConfigured ? 'pass' : 'fail'}>
                          {agentQuery.isPending ? 'No data' : soulConfigured ? 'Pass' : 'Fail'}
                        </Chip>
                      </td>
                    </tr>
                  </tbody>
                </Ledger>

                <BlastRadiusBlock blastRadius={report?.blast_radius} />

                {latestRun.recommendation === 'ship_with_warnings' && latestRun.warnings.length > 0 && (
                  <div className="warnings">
                    <p className="label">Acknowledge each warning to proceed</p>
                    {latestRun.warnings.map((w) => (
                      <label key={w.warning_id} className="warning-row">
                        <input
                          type="checkbox"
                          checked={!!latestRun.warning_acknowledgments[w.warning_id]}
                          disabled={acknowledgeWarning.isPending || !!latestRun.warning_acknowledgments[w.warning_id]}
                          onChange={(e) => {
                            if (e.target.checked) acknowledgeWarning.mutate(w.warning_id)
                          }}
                        />
                        <Chip verdict="fail" className="warning-cat">{w.category.replace(/_/g, ' ')}</Chip>
                        <span>{w.message}</span>
                      </label>
                    ))}
                  </div>
                )}

                <EnvelopeAcknowledgement
                  latestRun={latestRun}
                  capabilityEnvelopes={capabilityEnvelopes}
                  envelopesLoaded={capabilityEnvelopesQuery.isSuccess}
                  envelopesFailed={capabilityEnvelopesQuery.isError}
                  envelopeAcknowledged={envelopeAcknowledged}
                  onToggleAcknowledged={setEnvelopeAcknowledged}
                />

                <div className="verdict-bar">
                  <Btn
                    disabled={!isApprovable || approveDeployment.isPending}
                    aria-describedby="consequence"
                    onClick={() => approveDeployment.mutate()}
                  >
                    {approveDeployment.isPending
                      ? 'Approving…'
                      : agent?.is_deployed
                        ? 'Re-approve deploy'
                        : 'Approve deploy'}
                  </Btn>
                  {agent?.is_deployed && <Chip verdict="pass" dot>Live</Chip>}
                  <span className="mono stamp">agent {id}</span>
                </div>
                {baseApprovable && !isApprovable && (
                  <p className="help">
                    {latestRun.envelope_drift
                      ? 'Re-run the checklist to acknowledge the new configuration.'
                      : 'Tick the acknowledgement above to enable Approve.'}
                  </p>
                )}
                <p className="voice consequence" id="consequence">{consequence}</p>
                <p className="vh" role="status" aria-live="polite">
                  {gateBlocked
                    ? 'The gate is shut. A blocking finding is open and no new build reaches a customer.'
                    : 'The gate is open. Approving puts this agent in front of every customer.'}
                </p>

                <div className="rig">
                  <Btn variant="ghost" onClick={() => triggerChecklist.mutate()} disabled={triggerChecklist.isPending}>
                    {triggerChecklist.isPending ? 'Starting…' : 'Run checklist again'}
                  </Btn>
                  {gateStamp && <span className="mono stamp">last verified {formatDateTime(gateStamp)}</span>}
                </div>
              </>
            )}
          </section>

          {/* ═══ EMBED ══════════════════════════════════════════════════ */}
          <section className="section">
            <div className="section-head">
              <h2 className="label" id="embed-label">Embed</h2>
              <span className="mono stamp">one tag, before the closing body</span>
            </div>
            <div className="embed-row">
              <code className="well" id="snippet">{EMBED_SNIPPET(id)}</code>
              <Btn variant="ghost" onClick={handleCopyEmbed} aria-describedby="embed-label">
                {copyStatus === 'copied' ? 'Copied' : 'Copy'}
              </Btn>
            </div>
            <p className="help">
              The widget loads on its own, after your page has painted. It adds nothing to your
              first render.
            </p>
          </section>

          {/* ═══ APPEARANCE ═════════════════════════════════════════════ */}
          <section className="section">
            <div className="section-head">
              <h2 className="label" id="appearance-label">Appearance</h2>
              {saveWidgetConfig.isPending && <span className="mono stamp">saving…</span>}
              {saveWidgetConfig.isSuccess && !saveWidgetConfig.isPending && <span className="mono stamp">saved</span>}
            </div>
            <fieldset className="tiles-fieldset">
              <legend className="vh">Where the widget sits on your page</legend>
              <div className="tiles">
                {APPEARANCE_OPTIONS.map((opt) => (
                  <AppearanceTile
                    key={opt.key}
                    option={opt}
                    selected={widgetConfig.appearance === opt.key}
                    onSelect={() => handleSelectAppearance(opt.key)}
                  />
                ))}
              </div>
            </fieldset>
          </section>

          {/* ═══ CAPABILITIES AND LIMITS ════════════════════════════════ */}
          <section className="section">
            <div className="section-head">
              <h2 className="label" id="capabilities-label">Capabilities and limits</h2>
              {saveCapabilityEnvelope.isPending && <span className="mono stamp">saving…</span>}
              {saveCapabilityEnvelope.isSuccess && !saveCapabilityEnvelope.isPending && (
                <span className="mono stamp">saved</span>
              )}
            </div>
            {mutatingCapabilityEnvelopes.length === 0 ? (
              <EmptyState
                heading="No capabilities configured yet."
                body="This agent cannot take any action on a customer's behalf until you enable a skill below."
              />
            ) : (
              <div className="cap-grid">
                {mutatingCapabilityEnvelopes.map((env) => (
                  <CapabilityZone
                    key={`${env.skill}:${env.updated_at ?? 'unsaved'}`}
                    envelope={env}
                    fieldErrors={fieldErrors}
                    isSaving={saveCapabilityEnvelope.isPending}
                    onSave={handleSaveCapability}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        <WidgetPreview mode={widgetConfig.appearance} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page-scoped CSS — ported from prototypes/gotham/deploy.html's own <style>
// block (`.bench`/`.gate`/`.rig`/`.embed-row`/`.tiles`/`.preview`/`.widget`
// family), same static dangerouslySetInnerHTML pattern used by eval/agent
// page.tsx. `--widget-accent` is scoped to `.preview`/`.stage` only — never
// `:root` — so it can never leak into console chrome and never resolves off
// `--live`/`--seal` (UI-SPEC S4: the gate must never repaint what the
// customer sees).
// ---------------------------------------------------------------------------
const PAGE_CSS = `
  .alert {
    padding: 12px 16px; margin-bottom: 20px;
    background: var(--fail-dim);
    border: 1px solid color-mix(in oklch, var(--fail) 32%, transparent);
    border-radius: var(--r-panel);
    font-size: 14px; color: var(--fail);
  }

  .bench { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 44px; align-items: start; }
  @media (max-width: 1100px) { .bench { grid-template-columns: minmax(0, 1fr); } .preview { display: none; } }

  .gate-idle {
    border: 2px dashed var(--hairline-strong);
    border-radius: var(--r-panel);
    padding: 40px 32px;
    text-align: center;
    display: flex; flex-direction: column; align-items: center; gap: 14px;
  }
  .gate-idle .voice { font-size: 15px; max-width: 48ch; }

  .spinner {
    display: inline-block; width: 26px; height: 26px;
    border: 3px solid var(--hairline); border-top-color: var(--live);
    border-radius: 50%; animation: gate-spin 1s linear infinite;
  }
  @keyframes gate-spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 1ms; } }

  .ledger th.sig, .ledger td.sig { width: 42%; font-weight: 500; }
  .ledger td .why { display: block; margin-top: 2px; font-size: 12px; color: var(--ink-3); font-weight: 400; }
  .ledger td.val { color: var(--ink-2); }

  /* ── blast radius (BLR-01, D3/D4): two labelled lines per figure, never
       merged, never a coalesced zero. ──────────────────────────────────── */
  .blast-block { display: flex; flex-direction: column; gap: 14px; margin-top: 20px; }
  .blast-line { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .blast-label {
    font-family: var(--mono); font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.16em; color: var(--ink-3);
    min-width: 168px; flex: none;
  }
  .blast-note { color: var(--ink-3); font-size: 13.5px; }

  /* ── envelope acknowledgement (BLR-02, D5/D6): the table the hash covers,
       the checkbox bound directly beneath it, and the drift state. ───────── */
  .gate-chips { display: flex; align-items: center; gap: 12px; }
  .ack-zone { margin-top: 20px; }
  .ack-table { margin-bottom: 14px; }
  .ack-fingerprint { color: var(--ink-3); font-size: 12px; margin-bottom: 14px; }
  .ack-checkbox {
    display: flex; align-items: flex-start; gap: 12px; cursor: pointer;
    font-family: var(--sans); text-transform: none; letter-spacing: normal; color: var(--ink);
  }
  .ack-checkbox input { width: 16px; height: 16px; flex: none; margin-top: 2px; accent-color: var(--live); }
  .ack-checkbox span { font-size: 13.5px; flex: 1; }

  /* ── capabilities and limits (CAP-03, D1/D2): six independent Zones, each
       control's looser direction physically unreachable. ─────────────────── */
  .cap-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; margin-top: 16px; }
  @media (max-width: 900px) { .cap-grid { grid-template-columns: 1fr; } }
  .cap-row { margin-bottom: 16px; }
  .cap-row:last-child { margin-bottom: 0; }
  .cap-caption { margin-top: 6px; }
  .cap-error { margin-top: 6px; color: var(--fail); }
  .cap-row input[type="checkbox"]:disabled,
  .cap-row input[type="number"]:disabled,
  .cap-row select:disabled {
    background: var(--surface-2); color: var(--ink-3); cursor: not-allowed;
  }
  .cap-rate-inputs { display: flex; gap: 12px; }
  .cap-rate-inputs input { flex: 2; }
  .cap-rate-inputs select { flex: 1; }
  .cap-unit-fixed { flex: 1; display: flex; align-items: center; font-size: 13.5px; color: var(--ink-2); }
  .cap-actor-tiles { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 7px; }
  .tile-recessed { cursor: not-allowed; }
  .tile-recessed .name { color: var(--ink-3); }
  .tile-recessed:hover { border-color: var(--hairline); }
  .cap-sampled-stepper { margin-top: 12px; }

  .warnings { margin-top: 20px; }
  .warning-row {
    display: flex; align-items: flex-start; gap: 12px;
    padding: 10px 0; border-bottom: 1px solid var(--hairline-soft);
    text-transform: none; font-family: var(--sans); letter-spacing: normal;
    color: var(--ink); cursor: pointer;
  }
  .warning-row input { width: 16px; height: 16px; flex: none; margin-top: 2px; accent-color: var(--live); }
  .warning-cat { flex: none; }
  .warning-row span:last-child { font-size: 13.5px; flex: 1; }

  .stamp { color: var(--ink-3); font-size: 12px; }

  .verdict-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 24px; }
  .consequence { margin-top: 16px; font-size: 16px; max-width: 62ch; }

  .rig { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 26px; padding-top: 20px; border-top: 1px solid var(--hairline-soft); }

  .foot-note { font-size: 11.5px; color: var(--ink-3); }

  .embed-row { display: flex; align-items: flex-start; gap: 12px; }
  .embed-row .well { flex: 1; min-width: 0; white-space: pre-wrap; word-break: break-all; }
  .embed-row .btn { flex: none; }
  @media (max-width: 640px) { .embed-row { flex-direction: column; } .embed-row .btn { width: 100%; justify-content: center; } }

  .tiles-fieldset { border: 0; padding: 0; margin: 0; }
  .tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  @media (max-width: 700px) { .tiles { grid-template-columns: 1fr; } }

  .tile {
    position: relative; padding: 16px; cursor: pointer;
    font-family: var(--sans); text-transform: none; letter-spacing: 0; color: var(--ink);
  }
  .tile:hover { border-color: var(--hairline-strong); }
  .tile:has(input:focus-visible) { outline: 2px solid var(--live); outline-offset: 2px; }
  .tile input { position: absolute; width: 1px; height: 1px; opacity: 0; clip-path: inset(50%); margin: 0; padding: 0; }
  .tile svg { display: block; color: var(--ink-3); margin-bottom: 12px; }
  .tile[data-live="true"] svg { color: var(--live); }
  .tile .name { display: block; font-size: 13.5px; font-weight: 600; }
  .tile .note { display: block; margin-top: 3px; font-size: 12px; color: var(--ink-3); }

  /* ── the widget preview: the widget-exception light palette (UI-SPEC S4).
       --widget-accent is scoped here only — it must never leak into the
       console and never tracks --live/--seal/data-gate. ─────────────────── */
  .preview { position: sticky; top: 34px; --widget-accent: #C79A3C; }
  .preview .label { display: block; margin-bottom: 12px; }
  .stage {
    background: var(--well); border: 1px solid var(--hairline-soft); border-radius: var(--r-panel);
    padding: 18px; display: flex; flex-direction: column; align-items: flex-end; gap: 12px;
  }
  /* 20-15 fix (axe color-contrast, real defect): prototypes/gotham/deploy.html
     ports .widget's background as rgba(255,255,255,0.7) over the dark
     .stage (--well) backdrop, compositing to roughly #B5B5B6. Against that,
     the prototype's own secondary-text greys (#7C8687/#9AA3A3) topped out at
     3.74:1/2.58:1 even at fully OPAQUE white -- never legible at any
     opacity, not a porting regression -- and the locked --widget-accent
     (#C79A3C) used as bare text measured 1.26:1, effectively invisible. Since
     darkening those foreground colors alone couldn't reach 4.5:1 against the
     original 0.7-alpha backdrop without abandoning their "light secondary
     text" / "brand accent" character entirely, the backdrop opacity is
     raised to 0.94 (still visibly translucent, not solid) so the darkened
     foregrounds below clear 4.5:1 with comfortable margin while staying
     recognizably grey / amber-gold rather than near-black/near-brown. */
  .widget {
    width: 100%;
    background: rgba(255, 255, 255, 0.94);
    border-radius: 12px; overflow: hidden;
    font-family: var(--sans); color: #12181A;
  }
  .w-head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid #ECEAE4; }
  .w-avatar {
    width: 30px; height: 30px; flex: none; border-radius: 50%;
    background: var(--widget-accent); color: #0A1416;
    display: grid; place-items: center; font-family: var(--display); font-size: 13px; font-weight: 600;
  }
  .w-name { display: block; font-size: 13px; font-weight: 600; line-height: 1.3; }
  .w-state { display: block; font-size: 11px; color: #5F6669; }
  .w-body { padding: 14px; display: flex; flex-direction: column; gap: 9px; }
  .w-msg { max-width: 88%; padding: 9px 12px; font-size: 12.5px; line-height: 1.5; border-radius: 12px; }
  .w-agent { align-self: flex-start; background: #F3F1EB; border-bottom-left-radius: 4px; }
  .w-user { align-self: flex-end; background: #12181A; color: #F6F5F1; border-bottom-right-radius: 4px; }
  .w-input {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    margin: 0 14px 14px; padding: 9px 12px; border: 1px solid #E4E1D9; border-radius: 100px;
    font-size: 12.5px; color: #5F6669;
  }
  .w-send { color: #7A5A16; font-weight: 600; }
  .launcher {
    width: 46px; height: 46px; flex: none; border-radius: 50%;
    background: var(--widget-accent); color: #0A1416; display: grid; place-items: center;
  }

  .stage[data-mode="panel"] .widget { border-radius: 12px 0 0 12px; }
  .stage[data-mode="panel"] .w-body { min-height: 190px; }
  .stage[data-mode="modal"] .w-body { min-height: 120px; }

  .caption { margin-top: 12px; font-size: 12px; color: var(--ink-3); }
`
