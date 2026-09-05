import type { ChipVerdict } from '../../../components/gotham/Chip'

/**
 * opsFormat.ts — the pure render and derivation layer for the operations
 * room's six regions (23-03, Phase 23 Wave 0/1).
 *
 * Two facts motivate this module's existence:
 *
 *   1. The Phase 21 backends deliberately ship honest sentinels — a string
 *      like "not_tracked" instead of a fabricated 0.0 — whenever the
 *      underlying measurement has no rows yet. The console's job is to
 *      render that honestly: neither asserting data that is absent (a
 *      sentinel rendered as a number) nor hiding data that is present (a
 *      real zero rendered as an absence message, WIRE-02's exact defect).
 *
 *   2. Every decision in this file is a pure function — no React import, no
 *      JSX, no side effects — specifically so it can be proven by the spec
 *      beside it (tests-unit/ops-format.spec.ts) without a browser, a
 *      server, or a signed-in session. WIRE-01 through WIRE-04 shipped
 *      undetected through two phases that both passed their own
 *      verification; nothing in this repository could have caught them.
 *      This module, and the static gate beside it
 *      (scripts/check-ops-room-wiring.mjs), are what closes that gap.
 *
 * This module imports nothing from React and contains no JSX. The single
 * import above is a `import type` of the Chip primitive's closed verdict
 * union — erased entirely at compile time (tsconfig has
 * `isolatedModules: true`, which is why the `type` keyword is required),
 * so it carries no runtime dependency on React even though Chip.tsx itself
 * does. Re-using that exact type (rather than redeclaring it here) is what
 * makes "both verdict mappings return members of the chip primitive's
 * closed union" a compiler-enforced fact instead of a convention two files
 * could quietly drift apart on.
 */

export type { ChipVerdict }

// ---------------------------------------------------------------------------
// Sentinel constants — two, and only two, spellings exist anywhere in the
// Phase 21 backend surface this phase wires up. Declared as separate named
// constants (not a shared "NOT_TRACKED" alias) because the two spellings
// are never interchangeable — see the two predicates below.
// ---------------------------------------------------------------------------

/**
 * The underscore spelling. Returned by `metrics_service.py:60`
 * (`NOT_TRACKED = "not_tracked"`, governing every `GET /agents/{id}/metrics`
 * field) and by `staleness.py:65` (the identical literal, governing the
 * four `index_staleness` fields inside `GET /agents/{id}/retrieval-health`).
 * Both modules chose the same spelling independently — this constant is
 * shared between them because the STRING is identical, not because their
 * meanings are: metrics_service.py's sentinel means "zero turns in this
 * window"; staleness.py's means "the staleness scan itself failed". The
 * cell renderers below produce different copy for each case even though
 * both check this same constant.
 */
export const METRICS_SENTINEL = 'not_tracked' as const

/**
 * The spaced spelling. Returned by `retrieval_metrics_service.py:145`
 * (`_NOT_TRACKED = "not tracked yet"`), governing every `avg_*` field
 * inside `GET /agents/{id}/retrieval-health`. This is a genuinely
 * different literal from METRICS_SENTINEL — verified directly against both
 * source files (23-RESEARCH.md §2.2, independently re-confirmed this
 * session) — and the two are never unified into one check. A region that
 * string-matches only one spelling silently renders a fabricated `NaN`/`0`
 * for whichever field group uses the other.
 */
export const RETRIEVAL_SENTINEL = 'not tracked yet' as const

/**
 * Accepts METRICS_SENTINEL and rejects everything else, including
 * RETRIEVAL_SENTINEL, any number, null, undefined, and the empty string.
 * Narrows `value` to the sentinel's literal type, so a caller that has
 * passed this check cannot then hand the value to a numeric formatter
 * without the compiler objecting — the mechanical form of the rule that a
 * sentinel is never coerced into a number (23-UI-SPEC.md §7 rule 1).
 *
 * This predicate's body references ONLY METRICS_SENTINEL. It does not, and
 * must not, also match RETRIEVAL_SENTINEL — that unification is explicitly
 * rejected by 23-UI-SPEC.md's don't-hand-roll table: a shared "is this
 * string a not-tracked sentinel" helper would make it one keystroke to
 * render a genuine staleness-scan failure as an empty query window, or vice
 * versa, and the copy for those two cases is deliberately different.
 */
export function isMetricsSentinel(value: unknown): value is typeof METRICS_SENTINEL {
  return value === METRICS_SENTINEL
}

/**
 * Accepts RETRIEVAL_SENTINEL and rejects everything else, including
 * METRICS_SENTINEL, any number, null, undefined, and the empty string. The
 * mirror image of isMetricsSentinel — see that function's comment for why
 * these stay two independently named checks rather than one shared helper.
 * This predicate's body references ONLY RETRIEVAL_SENTINEL.
 */
export function isRetrievalSentinel(value: unknown): value is typeof RETRIEVAL_SENTINEL {
  return value === RETRIEVAL_SENTINEL
}

// ---------------------------------------------------------------------------
// Formatters — each takes a number and returns a string. None of them
// accepts a union type; that is what forces a sentinel check to happen
// first at every call site; a formatter that also accepted the sentinel
// type would let a caller skip the check by accident.
// ---------------------------------------------------------------------------

/** A percentage from a zero-to-one ratio, one decimal place — e.g. containment, escalation_rate. */
export function formatPercent(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`
}

/** A CSAT score out of five, one decimal place — `x.x / 5` per 23-UI-SPEC.md §4.1. */
export function formatCsatScore(score: number): string {
  return `${score.toFixed(1)} / 5`
}

/** A millisecond figure, rounded to the nearest whole millisecond — `{n} ms`. */
export function formatMilliseconds(ms: number): string {
  return `${Math.round(ms)} ms`
}

/**
 * A dollars figure, e.g. cost_per_session.
 *
 * Do NOT reuse `deploy/page.tsx`'s `formatCents()` (lines 482-497) for this
 * field. That formatter divides its input by 100 because ITS input is
 * cents. `cost_per_session` is dollars — it is `SUM(turn_metrics.cost_usd)
 * / COUNT(DISTINCT conversation_id)` (`metrics_service.py:31`, the
 * `cost_usd` column name is the tell: this codebase suffixes cent-valued
 * fields `_cents`, e.g. `max_amount_cents`, and dollar-valued ones `_usd`).
 * Running a dollars value through `formatCents` would render every cost a
 * hundredfold too small — a one-dollar session would read as one cent.
 * Writing the reason at this exact site is deliberate: a future reader
 * searching this file for a currency formatter will find `formatCents` in
 * the neighbouring deploy page first, and needs to be told not to use it
 * here before they wire it in.
 */
export function formatDollars(value: number): string {
  return `$${value.toFixed(2)}`
}

/** A plain integer — e.g. sample_size (Sessions), stale_count. */
export function formatInteger(n: number): string {
  return String(Math.round(n))
}

/** A fixed two-decimal score for the retrieval-health readings ledger (BM25/vector/RRF/rerank top scores, recall@k, nDCG@10, MRR, cited-chunk rank, compaction ratio, citation coverage, faithfulness). */
export function formatRetrievalScore(n: number): string {
  return n.toFixed(2)
}

// ---------------------------------------------------------------------------
// Cell renderers — three functions, one per copy case. Each takes the raw
// union value (a number, or the group's own sentinel) and returns the
// string to display. The three locked sentences are copied verbatim from
// 23-UI-SPEC.md §5.
// ---------------------------------------------------------------------------

/**
 * The Live region's case: a metric that may be a number or METRICS_SENTINEL,
 * plus the window (in days) the metrics endpoint reports on its own payload
 * (`metrics.py:59`'s `window_days` query param, echoed back in the
 * response body) so the no-data sentence can interpolate it.
 */
export function renderLiveMetricCell(
  value: number | typeof METRICS_SENTINEL,
  windowDays: number,
  format: (n: number) => string
): string {
  if (isMetricsSentinel(value)) {
    return `No data in the last ${windowDays} days.`
  }
  return format(value)
}

/**
 * The retrieval-health averages case: a value that may be a number or
 * RETRIEVAL_SENTINEL. No window interpolation — the retrieval-health
 * endpoint does not return a window in its body (verified against
 * `read_retrieval_health`'s return shape, `retrieval_metrics_service.py`).
 * Defaults to `formatRetrievalScore` since every `avg_*` field this
 * renderer serves is one of the twelve readings-ledger rows.
 */
export function renderRetrievalAverageCell(
  value: number | typeof RETRIEVAL_SENTINEL,
  format: (n: number) => string = formatRetrievalScore
): string {
  if (isRetrievalSentinel(value)) {
    return 'No queries in this window yet.'
  }
  return format(value)
}

/**
 * Faithfulness is the one reading whose average covers a SUBSET of the
 * window (issue #120). `read_retrieval_health` averages only rows whose
 * `context_source` names the current context shape, and counts the rest
 * separately, because a score taken against an older proxy and a score taken
 * against the retrieved chunks are two different measurements and averaging
 * them reports an instrument change as a quality change.
 *
 * So the shared sentinel sentence is wrong for this row. "No queries in this
 * window yet." is a claim about the window, and a window whose every query
 * was scored under an earlier instrument has queries in it. This renderer
 * says which of the two is true.
 */
export function renderFaithfulnessCell(
  value: number | typeof RETRIEVAL_SENTINEL,
  otherInstrumentCount: number
): string {
  if (!isRetrievalSentinel(value)) {
    return formatRetrievalScore(value)
  }
  if (otherInstrumentCount > 0) {
    return 'Not comparable yet.'
  }
  return 'No queries in this window yet.'
}

/**
 * The sentence beside the Faithfulness reading, saying what the average
 * covered and what it left out. Null when the window holds no faithfulness
 * score at all, because the reading's own cell already says so and a second
 * "0 and 0" line adds nothing.
 */
export function renderFaithfulnessCoverage(
  sampleCount: number,
  otherInstrumentCount: number
): string | null {
  if (sampleCount === 0 && otherInstrumentCount === 0) {
    return null
  }
  if (sampleCount === 0) {
    return (
      `The instrument changed. All ${otherInstrumentCount} scored in this window ` +
      'were scored under an earlier one, so there is nothing to compare them to yet.'
    )
  }
  if (otherInstrumentCount === 0) {
    return `${sampleCount} scored under the current instrument.`
  }
  return (
    `${sampleCount} scored under the current instrument, ` +
    `${otherInstrumentCount} under an earlier one.`
  )
}

/**
 * The staleness-failure case: a numeric `index_staleness` field (today,
 * `stale_count`) that may carry METRICS_SENTINEL when the scan itself
 * failed (`staleness.py:123-126,141-144` — a query exception, not "zero
 * documents"). This is a DIFFERENT cause from the Live region's no-data
 * case even though both check the same underscore literal, which is why
 * this renderer's sentinel sentence is a different sentence from
 * renderLiveMetricCell's, not a shared one.
 *
 * `drift_detected` (the other staleness field capable of carrying this
 * sentinel) is a boolean feeding a pass/fail Chip, not a formatted string —
 * it is not routed through this renderer, whose contract is "returns a
 * string to display". Its own sentinel check reuses the exported
 * `isMetricsSentinel` predicate directly at the call site instead.
 */
export function renderStalenessField(
  value: number | typeof METRICS_SENTINEL,
  format: (n: number) => string = formatInteger
): string {
  if (isMetricsSentinel(value)) {
    return 'Staleness scan unavailable.'
  }
  return format(value)
}

// ---------------------------------------------------------------------------
// Gate derivations — pure functions over a list of currently open findings.
// This is the pure half of WIRE-04's stale-verdict fix (23-UI-SPEC.md §3.3):
// today, `page.tsx:251-260,303` derive `severityCounts`/`redTeamBlocked`
// from `latestRedTeamRun.findings`, a per-run JSONB snapshot frozen at the
// moment a red-team run completed and never updated by `contain`. These
// four functions are the replacement, over the live `open_findings` array
// (`GET /agents/{id}/red-team/programme`, extended by 23-02 — verified
// against the real shipped shape of
// `redteam_programme_service.py::read_programme`, not merely the UI-SPEC's
// illustrative example, since 23-02 landed before this task was written).
// Putting them here, in wave 1, means the correctness of the fix is
// provable before the component that consumes it exists.
// ---------------------------------------------------------------------------

/** The shape of one row in `GET /agents/{id}/red-team/programme`'s `open_findings` array. */
export interface OpenFinding {
  id: string
  run_id: string | null
  strategy_id: string | null
  severity: 'low' | 'medium' | 'high' | 'critical'
  attack_vector: string | null
  probe_message: string | null
  agent_response: string | null
  turn_count: number | null
  created_at: string | null
  /** Recovered by the backend from the finding's own run's JSONB snapshot; null on a correlation miss. Never required — a finding with no description is still fully containable. */
  description: string | null
}

export interface SeverityCounts {
  critical: number
  high: number
  medium: number
  low: number
}

/** Counts each severity exactly once. Returns four zeros for an empty list — never a missing key. */
export function computeSeverityCounts(findings: OpenFinding[]): SeverityCounts {
  const counts: SeverityCounts = { critical: 0, high: 0, medium: 0, low: 0 }
  for (const finding of findings) {
    if (finding.severity in counts) {
      counts[finding.severity] += 1
    }
  }
  return counts
}

/**
 * True when at least one open finding is critical; false for an empty
 * list, a list with findings but none critical, and — the entire point of
 * this function existing — a list whose only critical entry has just been
 * removed (the moment `contain` resolves). This is what replaces
 * `page.tsx:303`'s `latestRedTeamRun?.deployment_blocked === true`, which
 * is a snapshot that `contain` never touches and which, once true, stays
 * true forever regardless of what the operator does afterward.
 */
export function isGateBlocked(findings: OpenFinding[]): boolean {
  return findings.some((finding) => finding.severity === 'critical')
}

/** The first critical finding in the list, or null if none is critical. Mirrors `page.tsx:259-260`'s existing shape, retargeted to the live list. */
export function firstCriticalFinding(findings: OpenFinding[]): OpenFinding | null {
  return findings.find((finding) => finding.severity === 'critical') ?? null
}

/**
 * The generic sentence already shipped and already approved at
 * `page.tsx:322` — reused verbatim rather than inventing new copy, per
 * OD-5 (23-01-PLAN.md § Open Decisions Resolved). Not exported: a caller
 * proving `gateMessage`'s output should compare it against a literal
 * copied from source, the same discipline the cell renderers' sentinel
 * sentences follow — comparing output to this module's own constant would
 * only prove the module agrees with itself.
 */
const GENERIC_GATE_MESSAGE =
  'A blocking signal is open. Nothing new reaches a customer until it clears.'

/**
 * The message the critical-finding banner shows. Uses the finding's
 * description when it is present AND non-empty; falls back to
 * GENERIC_GATE_MESSAGE for a null finding (no critical finding at all), a
 * finding with a null description, or a finding with an empty-string
 * description. Implements OD-5 exactly: Gap B's `open_findings` types
 * `description` as `string | null` because `red_team_findings` has no
 * description column and the backend's own JSONB correlation can miss
 * (`redteam_programme_service.py::_correlate_description`) — this function
 * never returns an empty string and never renders the word for a null.
 */
export function gateMessage(criticalFinding: OpenFinding | null): string {
  const description = criticalFinding?.description
  if (description) {
    return description
  }
  return GENERIC_GATE_MESSAGE
}

// ---------------------------------------------------------------------------
// Verdict mappings — both return members of Chip's closed ChipVerdict union
// (imported, not redeclared — see the module header). Neither introduces a
// raw color; Chip enforces that by construction (Chip.tsx:14-22, no
// color/background/raw-hex prop exists anywhere on the component).
// ---------------------------------------------------------------------------

/**
 * Every value bench_service.py's own filter allows through `verdict`
 * (`'fail' | 'ungrounded' | 'partial'` — `bench_service.py:153`) names a
 * real Gatekeeper/Auditor failure, since `GET .../traces` only ever
 * returns rows a judge already failed. All three map to the chip's `fail`
 * member. A value outside that known set maps to `mute` rather than a bare
 * `return 'fail'` constant — this module never asserts a failure verdict
 * it did not itself confirm, even for a value this bench pane does not yet
 * know about.
 */
const JUDGE_FAILURE_VERDICTS = new Set<string>(['fail', 'ungrounded', 'partial'])

export function judgeVerdictToChip(verdict: string): ChipVerdict {
  return JUDGE_FAILURE_VERDICTS.has(verdict) ? 'fail' : 'mute'
}

/**
 * A grade (`'filed' | 'held' | 'dismissed'`, or the pre-grading default
 * `'ungraded'`) is an operator's decision, not a machine's verdict —
 * rendering it as pass/fail would assert a judgement no machine made. This
 * is the same rule Phase 22 locked as T-22-ACT-17 (an approved confirmation
 * renders neutral, not green) and the reason this mapping is a constant
 * rather than a lookup table: there is no grade value, known or future,
 * that should ever produce `pass` or `fail` here.
 */
export function gradeToChip(_gradedStatus: string): ChipVerdict {
  return 'mute'
}

// ---------------------------------------------------------------------------
// Canary percentage — a version with no canary is genuinely routing zero
// per cent of turns; that is a fact, not a gap, so absent and zero render
// identically.
// ---------------------------------------------------------------------------

/** Renders a prompt version's canary percentage. Absent (null) and zero both render as `0%` — never an absence message. */
export function renderCanaryPercent(value: number | null): string {
  return `${value ?? 0}%`
}
