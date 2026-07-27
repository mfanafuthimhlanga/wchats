'use client'
import { use, useEffect, useMemo, useRef, useState } from 'react'
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

// CAP-03/D2: fixed tightness order, loosest first — this order alone (plus the
// recessed-vs-live treatment) teaches the metaphor without a legend.
//
// `off` is deliberately not a member. A Zone renders only for a mutating skill,
// every platform default seeds `actor_mode: "always-on"` (the tightest tier),
// and `validate_tighten_only` refuses every loosening — so Off is not a legal
// destination from any state this control can be in. It used to be listed here
// and filtered back out on `envelope.mutating`, which left an unreachable tile,
// an unreachable `handleSelect` branch, and an unreachable caption narrating
// what turning Actor review on would do (a B5 violation waiting for the state
// to become reachable). `actorModeTier`/`actorModeLabel` still carry tier 0,
// because the acknowledgement table has to be able to name an out-of-band
// `off` value the API returns.
const ACTOR_MODE_ORDER: { tier: 1 | 2; key: 'sampled' | 'always-on'; label: string }[] = [
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

// Human labels for the actor_mode ordinal, the same job SKILL_LABELS does for
// skill keys. `sample_at_rate_25` / `always-on` are machine tokens; printing one
// to a non-technical owner at the moment of financial attestation, while the
// capability Zone renders the same field as "Sampled", puts two vocabularies on
// one value and the machine one at the highest-stakes moment.
function actorModeLabel(mode: string): string {
  const tier = actorModeTier(mode)
  if (tier === null) return mode
  if (tier.tier === 0) return 'Off'
  if (tier.tier === 2) return 'Always-on'
  return `Sampled at ${tier.n} in 100`
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
  // This block is the signal the gate's approval now also turns on, and it
  // carried no heading at all, so the words "Blast radius" appeared nowhere in
  // the product. D3 allowed a fifth Ledger row OR a labelled sub-block; this is
  // the labelled sub-block, in the `.section-head` + `h2/h3.label` grammar every
  // other block on this page uses.
  const head = (
    <div className="section-head">
      <h3 className="label" id="blast-label">Blast radius</h3>
    </div>
  )

  // `<section>` rather than a bare `div` for the same reason the capability
  // Zones carry `as="section"`: `aria-labelledby` on a role-less element has
  // nothing to name and the name is discarded, so `#blast-label` was naming
  // nothing programmatically and M4's heading was decorative only.
  if (!blastRadius || blastRadius.enabled_skill_count === 0) {
    return (
      <section className="blast-block" aria-labelledby="blast-label">
        {head}
        <p className="blast-note">
          No transactional skill is enabled for this agent. There is no blast radius to report.
        </p>
      </section>
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
  } = blastRadius

  const singleObserved = centsOrNotTracked(observedSingle, windowDays)
  const hourlyObserved = centsOrNotTracked(observedHourly, windowDays)

  return (
    <section className="blast-block" aria-labelledby="blast-label">
      {head}
      <div className="blast-line">
        <span className="label blast-label">Max single action</span>
        {/* D4.2: when there is no ceiling the chip IS the claim. Printing the
            same words in --ink 12px to its left halved the chip's signal. */}
        {configuredSingle !== null && (
          <span className="num">{formatCents(configuredSingle)} ceiling</span>
        )}
        {/* The early return above already left on `enabled_skill_count === 0`,
            so the count is not re-tested here. Re-testing it made
            `enabledSkillCount > 0` always true and left a `null` middle branch
            that rendered no chip at all for an unconfigured ceiling. */}
        {configuredSingle === null ? (
          <Chip verdict="fail">No ceiling</Chip>
        ) : warnSingle === null ? (
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
      {/* The label is SR-only, so it is out of flow and the 168px label column
          collapses on this line: the figure landed at x=0, directly above the
          NEXT row's label, and an owner could not tell which ceiling an observed
          figure belonged to. The indent is restored on the line itself. */}
      <div className="blast-line blast-line--observed">
        <span className="vh">Max single action, observed</span>
        {singleObserved.tracked ? (
          <span className="num">{singleObserved.text} observed · {windowDays}d</span>
        ) : (
          // Not `.num`: this branch is a full prose sentence, and mono with
          // tabular figures is scoped to numbers, ids, timestamps and logs.
          <span className="blast-note">{singleObserved.text}</span>
        )}
      </div>

      <div className="blast-line">
        <span className="label blast-label">Max hourly aggregate</span>
        {configuredHourly !== null && (
          <span className="num">{formatCents(configuredHourly)} ceiling</span>
        )}
        {configuredHourly === null ? (
          <Chip verdict="fail">No ceiling</Chip>
        ) : warnHourly === null ? (
          <Chip verdict="mute">No threshold set</Chip>
        ) : configuredHourly > warnHourly ? (
          <Chip verdict="fail">Exceeds threshold</Chip>
        ) : (
          <Chip verdict="pass">Within threshold</Chip>
        )}
      </div>
      <div className="blast-line blast-line--observed">
        <span className="vh">Max hourly aggregate, observed</span>
        {hourlyObserved.tracked ? (
          <span className="num">{hourlyObserved.text} observed · {windowDays}d</span>
        ) : (
          <span className="blast-note">{hourlyObserved.text}</span>
        )}
      </div>
    </section>
  )
}

// BLR-02 — D5: the object of attestation is the human-legible table the
// envelope hash is computed over, with the checkbox bound directly beneath
// it, never the hex string itself. D6: the Confirmation and Verification cells
// are plain `.help`-weight text present only when true, never a Chip — a chip on
// this surface would dilute exactly the verdict signal the owner needs at the
// moment they accept financial risk. (The cell reads "Required": the column
// header already carries the word "Confirmation", see m4.)
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
    // `as="section"` for the same reason the capability Zones carry it: on a
    // role-less div, `aria-labelledby` has nothing to name and the name is
    // discarded.
    <Zone as="section" className="ack-zone" aria-labelledby="ack-label">
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
          {/* Six columns, one of which holds an unbreakable actor-mode value.
              The left bench column is ~600px in the 1101-1280 band (the sidebar
              holds 320px until 1100px), which is not one of the three widths
              the parity suite asserts, so this scrolls in its own container
              rather than pushing the page. `tabIndex` keeps the scroll
              reachable without a pointer — but that put an unnamed generic in
              the tab order immediately before the financial acknowledgement
              checkbox, so it carries a role and a name. The name is its own
              rather than `#ack-label` a second time: two regions both called
              "Capability envelope" are indistinguishable in a landmark list. */}
          <div
            className="ack-table-scroll"
            role="region"
            aria-label="Capability limits"
            tabIndex={0}
          >
          <Ledger
            caption="The capability limits this checklist's envelope hash covers, one row per skill. Rows for skills that are not enabled are shown recessed."
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
              {/* Every row the hash covers, not the enabled subset. The hash
                  SELECT carries no `WHERE enabled` and `HASHED_ENVELOPE_FIELDS`
                  includes `enabled` itself, so filtering here meant tightening a
                  disabled skill's ceiling changed the hash, raised the drift
                  chip, and then showed an identical table after a re-run: drift
                  with no visible cause, which is the desensitising false-drift
                  failure the field list was chosen to avoid. Rows for skills
                  that are not enabled are recessed rather than hidden. */}
              {capabilityEnvelopes.length === 0 ? (
                <tr>
                  <td colSpan={6} className="blast-note">No capability envelope exists for this agent.</td>
                </tr>
              ) : (
                capabilityEnvelopes.map((env) => (
                  <tr key={env.skill} className={env.enabled ? undefined : 'ack-row-off'}>
                    <LedgerRowHead>
                      {SKILL_LABELS[env.skill] ?? env.skill}
                      {!env.enabled && <span className="help ack-off-note">not enabled</span>}
                    </LedgerRowHead>
                    {/* D4.2 vocabulary, at the claim strength this surface can
                        carry: the same words the blast-radius chip uses, in
                        --ink-3 rather than a chip, so an absence is not mistaken
                        for a figure and the verdict red is not spent six times
                        inside an attestation table (D6). */}
                    <td className={env.rate_limit ? 'num' : 'num blast-note'}>
                      {env.rate_limit ?? 'No rate limit'}
                    </td>
                    <td className={env.constraints.max_amount_cents != null ? 'num' : 'num blast-note'}>
                      {env.constraints.max_amount_cents != null
                        ? formatCents(env.constraints.max_amount_cents)
                        : 'No ceiling'}
                    </td>
                    <td>{env.requires_confirmation && <span className="help">Required</span>}</td>
                    <td>{env.requires_identity_verification && <span className="help">Required</span>}</td>
                    <td>{actorModeLabel(env.actor_mode)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </Ledger>
          </div>
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
// recessed and unreachable; Off is not offered at all (see ACTOR_MODE_ORDER).
function ActorModeTiles({
  envelope,
  skillLabel,
  isSaving,
  onSave,
}: {
  envelope: CapabilityEnvelope
  skillLabel: string
  isSaving: boolean
  onSave: (skill: string, patch: CapabilityEnvelopePatch) => void
}) {
  const currentTier = actorModeTier(envelope.actor_mode)
  const currentTierNum = currentTier?.tier ?? 2
  const serverSampledN = currentTier?.tier === 1 ? currentTier.n : Math.max(currentTier?.n ?? 1, 1)
  const [sampledN, setSampledN] = useState<number>(serverSampledN)

  // The Zone is no longer remounted on every save (that dropped focus to
  // <body> on each change), so the local draft has to follow the
  // server-authoritative value when a refetch brings a new one in.
  useEffect(() => {
    setSampledN(serverSampledN)
  }, [serverSampledN])

  const handleSelect = (tier: 1 | 2) => {
    if (!isActorModeReachable(tier, currentTierNum)) return
    if (tier === 2) {
      onSave(envelope.skill, { actor_mode: 'always-on' })
    } else {
      const n = Math.max(sampledN, currentTier?.tier === 1 ? currentTier.n : 1)
      onSave(envelope.skill, { actor_mode: `sample_at_rate_${n}` as ActorMode })
    }
  }

  return (
    // A radio group needs a group name, or a screen reader announces
    // "Sampled, radio button, 1 of 2" with no field and no skill attached — on
    // six Zones whose labels are textually identical. `fieldset`/`legend` is
    // the same pattern the Appearance tiles two sections down already use.
    <fieldset className="field cap-row cap-fieldset">
      <legend className="label">Actor mode for {skillLabel}</legend>
      <div
        className="tiles cap-actor-tiles"
        // Column count follows the tier count. Hardcoding two columns would,
        // for any future third tier, wrap the tightest tile to the row below
        // and place it visually LEFT of a looser one — inverting the fixed
        // tightness ordering that is this control's only legend.
        style={{ gridTemplateColumns: `repeat(${ACTOR_MODE_ORDER.length}, minmax(0, 1fr))` }}
      >
        {ACTOR_MODE_ORDER.map((t) => {
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
            aria-label={`Sample rate (per 100 calls) for ${skillLabel}`}
            onChange={(e) => setSampledN(Math.max(Number(e.target.value) || currentTier.n, currentTier.n))}
            onBlur={() => {
              if (sampledN !== currentTier.n) {
                onSave(envelope.skill, { actor_mode: `sample_at_rate_${sampledN}` as ActorMode })
              }
            }}
          />
        </div>
      )}
      {/* B5: the present fact, never the mechanism. The label comes from
          `actorModeLabel`, the same helper the acknowledgement table uses, so
          every reachable value (and an out-of-band `off`) is named correctly
          here without a third branch that would have to assert something. */}
      <p className="help cap-caption">
        Currently: {actorModeLabel(envelope.actor_mode)}.
        {currentTierNum === 2 && ' Nothing stricter exists for this skill.'}
      </p>
    </fieldset>
  )
}

// CAP-03/D1 — every control here is built so the looser direction is not a
// reachable input state: a capped number input, a filtered unit list, a
// one-way toggle that becomes a locked chip, and the actor-mode tiles above.
function CapabilityZone({
  envelope,
  fieldErrors,
  isSaving,
  justSaved,
  onSave,
}: {
  envelope: CapabilityEnvelope
  fieldErrors: Record<string, string>
  isSaving: boolean
  justSaved: boolean
  onSave: (skill: string, patch: CapabilityEnvelopePatch) => void
}) {
  const skillLabel = SKILL_LABELS[envelope.skill] ?? envelope.skill
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

  // `''` is a real state, not a placeholder for `hour`: an envelope with no rate
  // limit yet holds no window, and a select pre-showing "hour" displays a value
  // the system does not hold while the caption says otherwise.
  const [unit, setUnit] = useState<RateUnit | ''>(parsedRate ? currentUnit : '')
  const [rateInput, setRateInput] = useState<string>(parsedRate ? String(parsedRate.calls) : '')
  const [maxAmountInput, setMaxAmountInput] = useState<string>(
    currentMaxCents !== null ? String(currentMaxCents / 100) : '',
  )
  // Neither numeric field writes on focus loss. Both stage the change here and
  // wait for an explicit confirmation naming the old and the new value.
  const [pendingRate, setPendingRate] = useState<{ calls: number; unit: RateUnit } | null>(null)
  const [pendingMaxCents, setPendingMaxCents] = useState<number | null>(null)
  const [rateNote, setRateNote] = useState<string | null>(null)
  const [maxNote, setMaxNote] = useState<string | null>(null)

  // This Zone used to be keyed on `${skill}:${updated_at}`, which remounted it
  // after every successful PATCH purely to reset these three drafts — and
  // destroyed focus in the process. The key is now the skill alone, so the
  // drafts follow the server-authoritative values explicitly. The dependency
  // is the SERVER value, so a refetch that returns what is already on screen
  // does not touch a draft mid-edit.
  useEffect(() => {
    // Recomputed inside the effect on purpose: `parsedRate` is a fresh object
    // every render, so listing it as a dependency would re-run this on every
    // keystroke and clobber the draft being typed.
    const parsed = parseRateLimit(envelope.rate_limit)
    setUnit(
      parsed
        ? ((RATE_UNITS.find((u) => RATE_UNIT_SECS[u] === parsed.windowSecs) as RateUnit | undefined) ?? 'hour')
        : '',
    )
    setRateInput(parsed ? String(parsed.calls) : '')
    setPendingRate(null)
    setRateNote(null)
  }, [envelope.rate_limit])

  useEffect(() => {
    setMaxAmountInput(currentMaxCents !== null ? String(currentMaxCents / 100) : '')
    setPendingMaxCents(null)
    setMaxNote(null)
  }, [currentMaxCents])

  const rateCap = unit === '' ? undefined : maxForUnit(unit)

  // The draft as an integer call count plus a chosen window, or null while it
  // is not yet a rate the server could hold.
  const draftRateCalls = Number(rateInput.trim())
  const draftRate =
    unit !== '' && rateInput.trim() !== '' && Number.isInteger(draftRateCalls)
      ? { calls: draftRateCalls, unit }
      : null
  const rateDirty = draftRate !== null && `${draftRate.calls}/${draftRate.unit}` !== envelope.rate_limit

  const draftMaxCents = (() => {
    const trimmed = maxAmountInput.trim()
    if (trimmed === '') return null
    const num = Number(trimmed)
    if (!Number.isFinite(num) || num < 0) return null
    return Math.round(num * 100)
  })()
  const maxDirty = draftMaxCents !== null && draftMaxCents !== currentMaxCents

  // No mid-typing clamp. Clamping on every keystroke meant a keystroke landed on
  // top of an already-clamped value and was re-clamped: against a R500 ceiling,
  // typing "600" passed through 6, then 500, then 5000, then 500 again. The
  // ceiling is checked once, at commit.
  const handleRateNumberChange = (raw: string) => {
    setRateInput(raw)
    setPendingRate(null)
    setRateNote(null)
  }

  const handleMaxAmountChange = (raw: string) => {
    setMaxAmountInput(raw)
    setPendingMaxCents(null)
    setMaxNote(null)
  }

  // A window change IS a discrete action, so the draft count is re-fitted to the
  // new window immediately and shown before anything is written. Nothing is
  // saved here: this used to PATCH on the select's own change event, which is
  // how one click could write `0/minute`.
  const handleUnitChange = (newUnit: RateUnit) => {
    const cap = maxCallsForUnit(parsedRate, newUnit)
    const typed = Number(rateInput.trim())
    const fitted = cap !== undefined && Number.isFinite(typed) ? Math.min(typed, cap) : typed
    setUnit(newUnit)
    if (Number.isFinite(fitted) && fitted >= 1) setRateInput(String(fitted))
    setPendingRate(null)
    setRateNote(null)
  }

  const requestRate = () => {
    if (draftRate === null) return
    // Hard floor, independent of the option filter above: no code path in this
    // component may stage a non-positive rate, because a zero written here
    // cannot be raised again from this screen.
    if (draftRate.calls < 1) {
      setRateInput(parsedRate ? String(parsedRate.calls) : '')
      setPendingRate(null)
      setRateNote('A rate limit has to allow at least one call.')
      return
    }
    if (rateCap !== undefined && draftRate.calls > rateCap) {
      setRateInput(parsedRate ? String(parsedRate.calls) : '')
      setUnit(parsedRate ? currentUnit : '')
      setPendingRate(null)
      setRateNote('That rate allows more calls than the current limit. Nothing was changed.')
      return
    }
    if (`${draftRate.calls}/${draftRate.unit}` === envelope.rate_limit) return
    setRateNote(null)
    setPendingRate({ calls: draftRate.calls, unit: draftRate.unit })
  }

  const confirmRate = () => {
    if (pendingRate === null) return
    const next = `${pendingRate.calls}/${pendingRate.unit}`
    setPendingRate(null)
    onSave(envelope.skill, { rate_limit: next })
  }

  const cancelRate = () => {
    setPendingRate(null)
    setUnit(parsedRate ? currentUnit : '')
    setRateInput(parsedRate ? String(parsedRate.calls) : '')
  }

  const requestMaxAmount = () => {
    if (draftMaxCents === null || draftMaxCents === currentMaxCents) return
    if (currentMaxCents !== null && draftMaxCents > currentMaxCents) {
      setMaxAmountInput(String(currentMaxCents / 100))
      setPendingMaxCents(null)
      setMaxNote('That amount is higher than the current ceiling. Nothing was changed.')
      return
    }
    setMaxNote(null)
    setPendingMaxCents(draftMaxCents)
  }

  const confirmMaxAmount = () => {
    if (pendingMaxCents === null) return
    const next = pendingMaxCents
    setPendingMaxCents(null)
    onSave(envelope.skill, { constraints: { ...envelope.constraints, max_amount_cents: next } })
  }

  const cancelMaxAmount = () => {
    setPendingMaxCents(null)
    setMaxAmountInput(currentMaxCents !== null ? String(currentMaxCents / 100) : '')
  }

  return (
    // `aria-labelledby` on a plain `div` with no role is inert, so the six
    // near-identical Zones had no group name at all: a screen reader read "Max
    // amount, spin button" six times with nothing separating `place_order` from
    // `issue_refund`, on a panel that sets irreversible money ceilings.
    // `as="section"` gives the name somewhere to land and makes each skill a
    // navigable region; every control below also carries the skill in its own
    // accessible name so it survives being read out of context.
    <Zone as="section" className="cap-zone" aria-labelledby={`cap-${envelope.skill}-label`}>
      <div className="section-head cap-head">
        <h3 className="label" id={`cap-${envelope.skill}-label`}>{skillLabel}</h3>
        {/* Per skill, not per section: a save in the sixth Zone used to report
            "saved" three rows away, and an auto-saved irreversible write gave a
            screen-reader user no confirmation at all. */}
        <span className="mono stamp cap-status" role="status">
          {isSaving ? 'saving…' : justSaved ? 'saved' : ''}
        </span>
      </div>

      <div className="field cap-row">
        <div className="cap-bool">
          <label htmlFor={`${envelope.skill}-enabled`}>Enabled</label>
          <input
            id={`${envelope.skill}-enabled`}
            type="checkbox"
            checked={envelope.enabled}
            disabled={enabledLocked || isSaving}
            aria-label={`Enabled for ${skillLabel}`}
            onChange={(e) => onSave(envelope.skill, { enabled: e.target.checked })}
          />
        </div>
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
            max={rateCap}
            value={rateInput}
            disabled={isSaving}
            aria-label={`Rate limit calls for ${skillLabel}`}
            onChange={(e) => handleRateNumberChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                requestRate()
              }
            }}
          />
          {allowedUnits.length === 1 ? (
            // One reachable window means there is no choice to present. A
            // select holding a single option reads as an offer that isn't one.
            <span className="cap-unit-fixed">per {allowedUnits[0]}</span>
          ) : (
            <select
              value={unit}
              disabled={isSaving}
              aria-label={`Rate limit window for ${skillLabel}`}
              onChange={(e) => handleUnitChange(e.target.value as RateUnit)}
            >
              {parsedRate === null && (
                <option value="" disabled>
                  window
                </option>
              )}
              {allowedUnits.map((u) => (
                <option key={u} value={u}>{u}</option>
              ))}
            </select>
          )}
        </div>
        <p className="help cap-caption">
          {parsedRate !== null ? `Currently ${parsedRate.calls} per ${currentUnit}.` : 'No rate limit set.'}
        </p>
        {rateDirty && pendingRate === null && (
          <div className="cap-commit">
            <Btn variant="ghost" disabled={isSaving} onClick={requestRate}>Set rate limit</Btn>
          </div>
        )}
        {pendingRate !== null && (
          <div className="cap-confirm">
            <p className="cap-confirm-q" role="status">
              {parsedRate !== null
                ? `Change the rate limit from ${parsedRate.calls} per ${currentUnit} to ${pendingRate.calls} per ${pendingRate.unit}?`
                : `Set the rate limit to ${pendingRate.calls} per ${pendingRate.unit}?`}
            </p>
            <div className="cap-confirm-actions">
              <Btn variant="ghost" disabled={isSaving} onClick={confirmRate}>
                Set {pendingRate.calls} per {pendingRate.unit}
              </Btn>
              <Btn variant="ghost" disabled={isSaving} onClick={cancelRate}>
                {parsedRate !== null ? `Keep ${parsedRate.calls} per ${currentUnit}` : 'Cancel'}
              </Btn>
            </div>
          </div>
        )}
        {rateNote && <p className="help cap-caption" role="status">{rateNote}</p>}
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
          aria-label={`Max amount in rand for ${skillLabel}`}
          onChange={(e) => handleMaxAmountChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              requestMaxAmount()
            }
          }}
        />
        <p className="help cap-caption">
          {currentMaxCents !== null ? `Currently ${formatCents(currentMaxCents)}.` : 'No ceiling set.'}
        </p>
        {maxDirty && pendingMaxCents === null && (
          <div className="cap-commit">
            <Btn variant="ghost" disabled={isSaving} onClick={requestMaxAmount}>Set max amount</Btn>
          </div>
        )}
        {pendingMaxCents !== null && (
          <div className="cap-confirm">
            <p className="cap-confirm-q" role="status">
              {currentMaxCents !== null
                ? `Change the max amount from ${formatCents(currentMaxCents)} to ${formatCents(pendingMaxCents)}?`
                : `Set the max amount to ${formatCents(pendingMaxCents)}?`}
            </p>
            <div className="cap-confirm-actions">
              <Btn variant="ghost" disabled={isSaving} onClick={confirmMaxAmount}>
                Set {formatCents(pendingMaxCents)}
              </Btn>
              <Btn variant="ghost" disabled={isSaving} onClick={cancelMaxAmount}>
                {currentMaxCents !== null ? `Keep ${formatCents(currentMaxCents)}` : 'Cancel'}
              </Btn>
            </div>
          </div>
        )}
        {maxNote && <p className="help cap-caption" role="status">{maxNote}</p>}
        {fieldErrors[`${envelope.skill}.constraints`] && (
          <p className="help cap-error">{fieldErrors[`${envelope.skill}.constraints`]}</p>
        )}
      </div>

      <div className="field cap-row">
        {/* In the locked state the input is not rendered at all, so the
            `htmlFor` here pointed at an id that does not exist in the document.
            The locked branch drops it; the label keeps the same typography as
            every other field label rather than forking a span style, and the
            chip reads immediately after it in reading order. */}
        {envelope.requires_confirmation ? (
          <div className="cap-bool">
            <label>Confirmation</label>
            <Chip verdict="live">On</Chip>
          </div>
        ) : (
          <div className="cap-bool">
            <label htmlFor={`${envelope.skill}-confirmation`}>Confirmation</label>
            <input
              id={`${envelope.skill}-confirmation`}
              type="checkbox"
              checked={false}
              disabled={isSaving}
              aria-label={`Confirmation required for ${skillLabel}`}
              onChange={(e) => {
                if (e.target.checked) onSave(envelope.skill, { requires_confirmation: true })
              }}
            />
          </div>
        )}
        <p className="help cap-caption">
          {envelope.requires_confirmation
            ? 'Confirmation is on - it cannot be turned off from here.'
            : 'Off. Turning this on requires the customer to confirm before this action runs.'}
        </p>
      </div>

      <div className="field cap-row">
        {envelope.requires_identity_verification ? (
          <div className="cap-bool">
            <label>Verification</label>
            <Chip verdict="live">On</Chip>
          </div>
        ) : (
          <div className="cap-bool">
            <label htmlFor={`${envelope.skill}-verification`}>Verification</label>
            <input
              id={`${envelope.skill}-verification`}
              type="checkbox"
              checked={false}
              disabled={isSaving}
              aria-label={`Identity verification required for ${skillLabel}`}
              onChange={(e) => {
                if (e.target.checked) onSave(envelope.skill, { requires_identity_verification: true })
              }}
            />
          </div>
        )}
        <p className="help cap-caption">
          {envelope.requires_identity_verification
            ? 'Verification is on - it cannot be turned off from here.'
            : 'Off. Turning this on requires identity verification before this action runs.'}
        </p>
      </div>

      <ActorModeTiles envelope={envelope} skillLabel={skillLabel} isSaving={isSaving} onSave={onSave} />
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
  // Per-skill save state. A single shared `mutation.isPending` disabled all six
  // Zones (~36 controls) while one field saved, and a single shared
  // `mutation.isSuccess` never reset — so "saved" stuck forever and reported
  // itself in whichever Zone happened to hold the section stamp.
  const [savingSkills, setSavingSkills] = useState<Record<string, true>>({})
  const [savedSkills, setSavedSkills] = useState<Record<string, true>>({})
  const savedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  useEffect(
    () => () => {
      for (const t of Object.values(savedTimers.current)) clearTimeout(t)
    },
    [],
  )

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
    onSuccess: (_data, { skill }) => {
      // "saved" is per skill and self-clearing, and lives in a `role="status"`
      // inside its own Zone so an auto-saved irreversible write is announced.
      setSavedSkills((prev) => ({ ...prev, [skill]: true }))
      clearTimeout(savedTimers.current[skill])
      savedTimers.current[skill] = setTimeout(() => {
        setSavedSkills((prev) => {
          const next = { ...prev }
          delete next[skill]
          return next
        })
        delete savedTimers.current[skill]
      }, 4000)

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
    onSettled: (_data, _err, { skill }) => {
      setSavingSkills((prev) => {
        const next = { ...prev }
        delete next[skill]
        return next
      })
    },
  })

  const handleSaveCapability = (skill: string, patch: CapabilityEnvelopePatch) => {
    setFieldErrors((prev) => {
      const next = { ...prev }
      for (const field of Object.keys(patch)) delete next[`${skill}.${field}`]
      return next
    })
    setSavingSkills((prev) => ({ ...prev, [skill]: true }))
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

  // Why Approve is unavailable once the checklist itself is clear. Scoped to
  // the envelope preconditions on purpose: the block / unacknowledged-warnings
  // cases already carry their own on-screen messaging, and repeating them here
  // would put two claims on screen for one fact.
  const envelopeBlockReason =
    !baseApprovable || isApprovable
      ? null
      : latestRun?.envelope_drift === true
        ? 'Re-run the checklist to acknowledge the new configuration.'
        : !capabilityEnvelopesQuery.isSuccess
          ? 'The capability limits are not on screen yet. Approve stays disabled until they load.'
          : latestRun?.envelope_hash === null
            ? 'This checklist run carries no configuration fingerprint. Re-run the checklist before approving.'
            : 'Tick the acknowledgement above to enable Approve.'
  const approveUnavailable = !isApprovable || approveDeployment.isPending

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
            {/* Count-free on purpose. This said "four" while approval already
                depended on six conditions, and the count has moved twice. */}
            <p className="sub">
              Every signal below stands between this agent and a paying customer. The gate opens
              only while all of them hold, and it shuts itself the moment one does not.
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
                <Ledger caption="The readiness signals a deployment gate checks before an agent can reach a customer.">
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
                  {/* `aria-disabled` + a no-op handler rather than `disabled`:
                      a `disabled` button leaves the tab order, so a keyboard
                      user never reaches it and never hears the reason it is
                      unavailable. `.is-disabled` carries the identical
                      `.btn[disabled]` treatment from globals.css. */}
                  <Btn
                    className={approveUnavailable ? 'is-disabled' : undefined}
                    aria-disabled={approveUnavailable || undefined}
                    aria-describedby={
                      envelopeBlockReason ? 'approve-reason consequence' : 'consequence'
                    }
                    onClick={() => {
                      if (!approveUnavailable) approveDeployment.mutate()
                    }}
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
                {envelopeBlockReason && (
                  <p className="help" id="approve-reason" role="status">
                    {envelopeBlockReason}
                  </p>
                )}
                <p className="voice consequence" id="consequence">{consequence}</p>
                <p className="vh" role="status" aria-live="polite">
                  {gateBlocked
                    ? 'The gate is shut. A blocking finding is open and no new build reaches a customer.'
                    : isApprovable
                      ? 'The gate is open. Approving puts this agent in front of every customer.'
                      : `The gate is open. This deploy is not approvable yet. ${
                          envelopeBlockReason ?? 'Every signal above must hold first.'
                        }`}
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
              {/* The saving/saved stamp moved into each Zone — see m9. A single
                  section-level stamp reported one skill's write against all six. */}
            </div>
            {mutatingCapabilityEnvelopes.length === 0 ? (
              // The old body ended "until you enable a skill below", but this
              // state renders INSTEAD of the grid, so there was nothing below.
              <EmptyState
                heading="No capabilities configured yet."
                body="This agent cannot take any action on a customer's behalf. No transactional skill has been provisioned for it."
              />
            ) : (
              <div className="cap-grid">
                {mutatingCapabilityEnvelopes.map((env) => (
                  <CapabilityZone
                    // Keyed on the skill alone. The old `${skill}:${updated_at}`
                    // key remounted the whole Zone after every save, which drops
                    // focus to <body>; the drafts now follow the server value
                    // through effects inside the component instead.
                    key={env.skill}
                    envelope={env}
                    fieldErrors={fieldErrors}
                    isSaving={savingSkills[env.skill] === true}
                    justSaved={savedSkills[env.skill] === true}
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
  .blast-block .section-head { margin-bottom: 0; }
  .blast-line { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  /* Layout only. This used to fork .label's typography with 0.16em tracking
     against .label's 0.2em, so blast labels did not match the section labels
     directly above them. */
  .blast-label { min-width: 168px; flex: none; }
  /* 168px label column + the 12px row gap: puts each observed figure under the
     ceiling it belongs to, since its own label is SR-only and out of flow. */
  .blast-line--observed { padding-left: 180px; }
  @media (max-width: 900px) { .blast-line--observed { padding-left: 0; } }
  .blast-note { color: var(--ink-3); font-size: 13.5px; }

  /* ── envelope acknowledgement (BLR-02, D5/D6): the table the hash covers,
       the checkbox bound directly beneath it, and the drift state. ───────── */
  .gate-chips { display: flex; align-items: center; gap: 12px; }
  .ack-zone { margin-top: 20px; }
  .ack-table-scroll { overflow-x: auto; margin-bottom: 14px; }
  /* A skill the hash covers but the agent has not enabled: present, so drift
     always has a visible cause, and recessed, so it never reads as live. */
  .ack-table tr.ack-row-off th, .ack-table tr.ack-row-off td { color: var(--ink-3); }
  .ack-off-note { display: block; margin-top: 2px; }
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
  .cap-head { margin-bottom: 18px; }
  .cap-fieldset { border: 0; padding: 0; margin: 0 0 16px; min-width: 0; }
  .cap-fieldset:last-child { margin-bottom: 0; }
  .cap-fieldset > legend { padding: 0; }
  .cap-row { margin-bottom: 16px; }
  .cap-row:last-child { margin-bottom: 0; }
  .cap-caption { margin-top: 6px; }
  .cap-error { margin-top: 6px; color: var(--fail); }

  /* globals.css sizes every input at width:100% with 9px 12px padding, which
     rendered these three checkboxes as full-Zone-width bordered boxes, and
     with no accent-color the checkmark painted in the UA accent (system blue
     on Windows Chrome) on a chroma-zero bench. Same reset .ack-checkbox and
     .warning-row already carry, plus the label inline with the control. */
  .cap-bool { display: flex; align-items: center; gap: 10px; }
  .cap-bool > label { margin-bottom: 0; }
  .cap-row input[type="checkbox"] {
    width: 16px; height: 16px; flex: none; padding: 0;
    accent-color: var(--live);
  }
  .cap-row input[type="checkbox"]:disabled,
  .cap-row input[type="number"]:disabled,
  .cap-row select:disabled {
    background: var(--surface-2); color: var(--ink-3); cursor: not-allowed;
  }
  .cap-rate-inputs { display: flex; gap: 12px; }
  .cap-rate-inputs input { flex: 2; }
  .cap-rate-inputs select { flex: 1; }
  .cap-unit-fixed { flex: 1; display: flex; align-items: center; font-size: 13.5px; color: var(--ink-2); }

  /* Neither money field commits on blur. The staged change is confirmed against
     the value it replaces, in the same "rule then decision" grammar .rig uses,
     rather than in a box nested inside the Zone. Both buttons stay ghost:
     UI-SPEC S4 reserves the --live fill for Approve, the acknowledgement
     checkbox and the selected actor-mode tile, and S4 forbids variant="seal"
     here because tightening a limit is not destructive. */
  .cap-commit { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
  .cap-commit .btn { flex: none; }
  .cap-confirm { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--hairline-soft); }
  .cap-confirm-q { font-size: 13px; line-height: 1.5; color: var(--ink); }
  .cap-confirm-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
  .cap-confirm-actions .btn { flex: none; }
  .cap-confirm-actions .btn:first-child { border-color: var(--hairline-strong); }
  /* No grid-template-columns here: the column count follows the tile count and
     is set inline by ActorModeTiles (see m12). */
  .cap-actor-tiles { display: grid; gap: 12px; margin-top: 7px; }
  /* Doubled class on purpose. At (0,1,0) and (0,2,0) these lost the cascade to
     .tile and .tile:hover declared 36 lines below, so a recessed tile still
     brightened on hover and still showed a pointer -- and recessed-vs-live is
     the only thing teaching tightness direction without a legend. */
  .tile.tile-recessed { cursor: not-allowed; }
  .tile.tile-recessed .name { color: var(--ink-3); }
  .tile.tile-recessed:hover { border-color: var(--hairline); }
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
  /* Approve uses aria-disabled rather than disabled so it stays reachable by
     Tab (see M11). Two consequences to absorb here. (1) .btn-primary:hover would
     otherwise still brighten a button announced as unavailable. (2) WCAG 1.4.3
     exempts text in an INACTIVE component, and .btn[disabled]'s --ink-3 on
     --surface-2 measures 4.12:1 on that exemption; an aria-disabled button is
     not inactive in the DOM sense, so its label is held to 4.5:1 and steps up to
     --ink-2 (5.9:1 on the same fill). Both existing chroma-zero tokens. */
  .verdict-bar .btn.is-disabled { color: var(--ink-2); }
  .verdict-bar .btn.is-disabled:hover { filter: none; }
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
