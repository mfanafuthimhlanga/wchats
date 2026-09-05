// currentRun.ts is the eval page's one answer to "which run is the current one"
// (#177).
//
// There used to be two. The chart filtered `status !== 'running'`, because a
// run in flight has no record yet and every metric on it reads unmeasured, so
// including it turned every pin into a dash the moment the owner pressed Run
// evals. The judge, the pass-rate line and the "last run" stamp still read
// `eval_runs[0]`, and the results query still fetched that run's scenarios. So
// for the length of a run the page said two different things: the chart and the
// ledger showed the previous run, the stamp showed a run that had not finished,
// and the scenario table emptied out to "No scenario results" over a run that
// had plenty.
//
// The decision, settled here and read everywhere:
//
//   1. THE CURRENT RUN IS THE NEWEST RUN THAT IS NOT RUNNING. Chart, judge,
//      pass-rate line, stamp, ledger and the results fetch all read `settled`.
//      A failed run is settled: it really did measure nothing, and the gap it
//      leaves on the chart says so.
//   2. A RUN IN FLIGHT IS ITS OWN STATE, NOT AN ABSENCE. `running` carries it,
//      and the page says "a run is going now" beside the settled run's stamp
//      rather than showing the in-flight run's start time as the last run or
//      blanking the page for its duration.
//
// The API returns `eval_runs` newest first (evals.py list_eval_runs), and every
// function here depends on that order.

import type { EvalRun } from './evalSeries'

export interface CurrentRun {
  /** The newest run that has finished. Every reader on the page reads this one. */
  settled: EvalRun | null
  /** The run still in flight, when there is one. Never the subject of a reading. */
  running: EvalRun | null
  /** Every finished run, oldest first: the order the chart plots. */
  chronological: EvalRun[]
  /** How many runs the API returned, in flight ones included. */
  runCount: number
}

/** Split the API's newest-first list into the run every reader reads and the one in flight. */
export function readCurrentRun(runs: EvalRun[] | undefined): CurrentRun {
  const all = runs ?? []
  const finished = all.filter((run) => run.status !== 'running')
  return {
    settled: finished[0] ?? null,
    running: all.find((run) => run.status === 'running') ?? null,
    chronological: [...finished].reverse(),
    runCount: all.length,
  }
}

/** Compact mono timestamp, "2026-07-13 09:14" (UTC), matching eval.html's `.stamp`. */
export function formatStamp(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
  )
}

/**
 * What the "last run" stamp says.
 *
 * The in-flight run's start time was rendered here as "last run", which is a
 * claim the run has not earned: it has not produced a reading, and the chart
 * beside the stamp was already ignoring it. It now announces itself as what it
 * is, and the settled run keeps the stamp.
 */
export function stampLabel(current: CurrentRun): string {
  const settled = current.settled ? `last run ${formatStamp(current.settled.started_at)}` : ''
  if (current.running === null) return settled || 'no run yet'
  const going = `a run is going now, started ${formatStamp(current.running.started_at)}`
  return settled ? `${going} · ${settled}` : going
}

/**
 * The judge's sentence, about the settled run and never about the run in flight.
 *
 * A run in flight has no scenario results, so this used to return the empty
 * string and the judge went blank for the length of the run. Blank reads as "no
 * verdict exists", which is false whenever an earlier run reached one. The
 * sentence stays, and `stampLabel` says a newer run is going.
 */
export function buildVerdict(
  current: CurrentRun,
  scenarios: { passed: boolean }[],
): string {
  if (current.settled === null) {
    return current.running !== null
      ? 'The first eval run is going now. Nothing has been measured yet.'
      : ''
  }
  if (scenarios.length === 0) return ''
  const total = scenarios.length
  const failed = scenarios.filter((s) => !s.passed)
  const passedCount = total - failed.length
  if (failed.length === 0) {
    return `All ${total} scenario${total === 1 ? '' : 's'} held on this run. The gate stays open.`
  }
  return (
    `${total} scenario${total === 1 ? '' : 's'} ran on this run. ${passedCount} held, ` +
    `${failed.length} failed. Review the ledger below before the gate can close.`
  )
}
