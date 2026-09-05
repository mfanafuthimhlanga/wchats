import { expect, test } from '@playwright/test'
import {
  buildVerdict,
  formatStamp,
  readCurrentRun,
  stampLabel,
} from '../app/agents/[id]/eval/currentRun'
import type { EvalRun } from '../app/agents/[id]/eval/evalSeries'

// current-run.spec.ts is the browserless proof for currentRun.ts (#177).
//
// The bug this pins: the eval page had two answers to "which run is the current
// one". `chronologicalRuns` filtered `status !== 'running'` for the chart, and
// `latestRun` was still `eval_runs[0]`, so for the length of a run the judge,
// the pass-rate line and the "last run" stamp read the in-flight run while the
// chart and the ledger did not. Nothing tested the filter, so PR #174's claim
// that a running run is kept out of the chart rested on reading the code.
//
// The rule under test: one current run, `settled`, is the newest run that is
// not running, and every reader takes it. A run in flight is `running`, an
// explicit state rather than an absence.

const RUN_A_START = '2026-07-13T09:14:00Z'
const RUN_B_START = '2026-07-14T11:02:00Z'
const IN_FLIGHT_START = '2026-07-15T06:30:00Z'

function run(id: string, status: EvalRun['status'], started_at: string): EvalRun {
  return {
    id,
    started_at,
    finished_at: status === 'running' ? null : started_at,
    status,
    scenario_count: status === 'running' ? null : 6,
  }
}

// The API returns eval_runs newest first (evals.py list_eval_runs).
const SETTLED_ONLY: EvalRun[] = [
  run('b', 'complete', RUN_B_START),
  run('a', 'complete', RUN_A_START),
]
const WITH_RUN_IN_FLIGHT: EvalRun[] = [
  run('c', 'running', IN_FLIGHT_START),
  ...SETTLED_ONLY,
]

test.describe('readCurrentRun', () => {
  test('the current run is the newest one that is not running', () => {
    const current = readCurrentRun(WITH_RUN_IN_FLIGHT)

    expect(current.settled?.id).toBe('b')
    expect(current.running?.id).toBe('c')
  })

  test('the in-flight run is kept out of the chart, and this is the filter', () => {
    // The claim PR #174 made and nothing tested. A running run has no record,
    // so every metric on it reads unmeasured and plotting it turns every pin
    // into a dash for the length of the run.
    const current = readCurrentRun(WITH_RUN_IN_FLIGHT)

    expect(current.chronological.map((r) => r.id)).toEqual(['a', 'b'])
    expect(current.chronological.some((r) => r.status === 'running')).toBe(false)
  })

  test('the chart plots oldest first', () => {
    expect(readCurrentRun(SETTLED_ONLY).chronological.map((r) => r.id)).toEqual(['a', 'b'])
  })

  test('a failed run is settled and stays on the chart', () => {
    // It really did measure nothing, and the gap it leaves says so. Dropping it
    // would join the line across a run that failed.
    const runs = [run('f', 'failed', RUN_B_START), run('a', 'complete', RUN_A_START)]
    const current = readCurrentRun(runs)

    expect(current.settled?.id).toBe('f')
    expect(current.running).toBeNull()
    expect(current.chronological.map((r) => r.id)).toEqual(['a', 'f'])
  })

  test('a first run in flight leaves no settled run, and says so with null', () => {
    const current = readCurrentRun([run('c', 'running', IN_FLIGHT_START)])

    expect(current.settled).toBeNull()
    expect(current.running?.id).toBe('c')
    expect(current.chronological).toEqual([])
    expect(current.runCount).toBe(1)
  })

  test('no runs at all is every field empty', () => {
    for (const input of [undefined, []]) {
      const current = readCurrentRun(input)
      expect(current.settled).toBeNull()
      expect(current.running).toBeNull()
      expect(current.chronological).toEqual([])
      expect(current.runCount).toBe(0)
    }
  })

  test('runCount counts the run in flight, so the page knows a run exists', () => {
    expect(readCurrentRun(WITH_RUN_IN_FLIGHT).runCount).toBe(3)
    expect(readCurrentRun(SETTLED_ONLY).runCount).toBe(2)
  })

  test('the input array is not mutated', () => {
    const input = [...WITH_RUN_IN_FLIGHT]
    readCurrentRun(input)

    expect(input.map((r) => r.id)).toEqual(['c', 'b', 'a'])
  })
})

test.describe('stampLabel', () => {
  test('with no run in flight the stamp is the settled run', () => {
    expect(stampLabel(readCurrentRun(SETTLED_ONLY))).toBe(
      `last run ${formatStamp(RUN_B_START)}`,
    )
  })

  test('a run in flight announces itself and does not become "last run"', () => {
    // The defect, said in one assertion: `eval_runs[0].started_at` rendered
    // under "last run" for a run that had not finished, beside a chart that was
    // already ignoring it.
    const label = stampLabel(readCurrentRun(WITH_RUN_IN_FLIGHT))

    expect(label).toContain('a run is going now')
    expect(label).toContain(`last run ${formatStamp(RUN_B_START)}`)
    expect(label).not.toBe(`last run ${formatStamp(IN_FLIGHT_START)}`)
  })

  test('a first run in flight says a run is going and names no last run', () => {
    const label = stampLabel(readCurrentRun([run('c', 'running', IN_FLIGHT_START)]))

    expect(label).toContain('a run is going now')
    expect(label).not.toContain('last run')
  })

  test('no runs at all says so rather than rendering an empty stamp', () => {
    expect(stampLabel(readCurrentRun([]))).toBe('no run yet')
  })
})

test.describe('buildVerdict', () => {
  const held = [{ passed: true }, { passed: true }]
  const mixed = [{ passed: true }, { passed: false }, { passed: true }]

  test('reads the settled run even while a newer run is going', () => {
    // It used to go blank for the length of the run, because the results query
    // fetched the in-flight run and got nothing back. Blank reads as "no
    // verdict exists", which is false whenever an earlier run reached one.
    const verdict = buildVerdict(readCurrentRun(WITH_RUN_IN_FLIGHT), held)

    expect(verdict).toBe('All 2 scenarios held on this run. The gate stays open.')
  })

  test('names the failures when there are some', () => {
    const verdict = buildVerdict(readCurrentRun(SETTLED_ONLY), mixed)

    expect(verdict).toContain('3 scenarios ran on this run')
    expect(verdict).toContain('2 held, 1 failed')
  })

  test('a first run in flight says what is happening rather than nothing', () => {
    const verdict = buildVerdict(readCurrentRun([run('c', 'running', IN_FLIGHT_START)]), [])

    expect(verdict).toBe('The first eval run is going now. Nothing has been measured yet.')
  })

  test('no runs and no scenarios is the empty sentence, and the judge is hidden', () => {
    expect(buildVerdict(readCurrentRun([]), [])).toBe('')
  })

  test('a settled run whose results have not loaded yet says nothing', () => {
    expect(buildVerdict(readCurrentRun(SETTLED_ONLY), [])).toBe('')
  })
})

test.describe('formatStamp', () => {
  test('renders UTC to the minute', () => {
    expect(formatStamp('2026-07-13T09:14:33Z')).toBe('2026-07-13 09:14')
  })

  test('pads every field', () => {
    expect(formatStamp('2026-01-02T03:04:00Z')).toBe('2026-01-02 03:04')
  })
})
