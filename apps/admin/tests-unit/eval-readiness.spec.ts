import { expect, test } from '@playwright/test'
import {
  readEvalReadiness,
  type EvalSummary,
} from '../app/agents/[id]/deploy/evalReadiness'
import { EVAL_CHANNELS, type ChannelKey, type Measurement } from '../app/agents/[id]/eval/evalSeries'

// eval-readiness.spec.ts is the browserless proof for evalReadiness.ts (#175).
//
// The bug this pins: the deploy gate's "Evals pass rate" row averaged
// `eval_summary.pass_rates` across metrics and read `failing_scenarios ?? 0`.
// `pass_rates` is null by design on a run whose two datasets both scored, and
// `failing_scenarios` is null wherever the collector had no record to count
// from, so a run that measured nothing rendered as zero failing with a Pass
// chip beside it.
//
// Two rules govern the assertions below, the same two eval-series.spec.ts uses.
//   1. A fixture that carries a run-level `pass_rates` block gives it numbers
//      that contradict its per-dataset measurements, so a reader that reaches
//      for the projection produces a visibly wrong number rather than an
//      accidentally right one.
//   2. An unmeasured count is compared against `null`, never against a number.
//      `toBeNull()` and `toBe(0)` are both satisfied by a falsy value in a loose
//      comparison; the strict null is the whole claim.

const CHANNEL_KEYS: ChannelKey[] = EVAL_CHANNELS.map((c) => c.key)

const UNMEASURED: Measurement = { value: null, measured: false, observations: 0 }

function metricsAt(value: number | null): Record<ChannelKey, Measurement> {
  const reading: Measurement =
    value === null ? UNMEASURED : { value, measured: true, observations: 6 }
  return Object.fromEntries(CHANNEL_KEYS.map((k) => [k, reading])) as Record<
    ChannelKey,
    Measurement
  >
}

/** A summary whose per-dataset block is available, with the given halves. */
function summaryWith(
  halves: Partial<Record<'golden' | 'exploratory', number | null>>,
  extra: Partial<EvalSummary> = {},
): EvalSummary {
  const datasets: Record<string, unknown> = { available: true }
  for (const [name, value] of Object.entries(halves)) {
    datasets[name] = { metrics: metricsAt(value ?? null) }
  }
  return {
    eval_signal: 'measured',
    failing_scenarios: 0,
    unmeasured_scenarios: 0,
    datasets: datasets as EvalSummary['datasets'],
    ...extra,
  }
}

test.describe('a run whose two datasets both scored', () => {
  // The shape #175 is about. `pass_rates` is null, so the old average was
  // computed over nothing and rendered as "not yet run" beside a run that
  // measured eight numbers.
  const twoDatasets = summaryWith(
    { golden: 0.94, exploratory: 0.62 },
    { pass_rates: null, pass_rates_dataset: null, failing_scenarios: 3, unmeasured_scenarios: 0 },
  )

  test('reads both halves and names each one', () => {
    const readiness = readEvalReadiness(twoDatasets)

    expect(readiness.readings.map((r) => r.dataset)).toEqual(['golden', 'exploratory'])
    expect(readiness.readings[0].average).toBeCloseTo(0.94, 10)
    expect(readiness.readings[1].average).toBeCloseTo(0.62, 10)
  })

  test('never pools the two into one number', () => {
    const readiness = readEvalReadiness(twoDatasets)

    // 0.78 is the mean of 0.94 and 0.62. It must appear nowhere: the golden set
    // is fixed and the exploratory sample rotates, so a pooled mean moves with
    // the draw while looking like a quality change.
    expect(readiness.value).not.toContain('0.78')
    expect(readiness.value).toContain('0.94')
    expect(readiness.value).toContain('0.62')
    expect(readiness.value).toContain('golden set')
    expect(readiness.value).toContain('exploratory sample')
  })

  test('quotes the metric count each average came off', () => {
    const readiness = readEvalReadiness(twoDatasets)

    expect(readiness.readings[0].metricCount).toBe(CHANNEL_KEYS.length)
    expect(readiness.value).toContain(`over ${CHANNEL_KEYS.length} metrics`)
  })

  test('the failing count the judge reached still decides the chip', () => {
    const readiness = readEvalReadiness(twoDatasets)

    expect(readiness.failingScenarios).toBe(3)
    expect(readiness.verdict).toBe('fail')
    expect(readiness.chipLabel).toBe('Fail')
  })
})

test.describe('a null the collector wrote is never a zero', () => {
  test('a null failing count reads as unmeasured, not as nothing failed', () => {
    const readiness = readEvalReadiness(
      summaryWith({ exploratory: 0.9 }, { failing_scenarios: null, unmeasured_scenarios: null }),
    )

    // The `?? 0` this replaces made this exact payload render "0.90 avg" with a
    // Pass chip. The run does not say how many failed.
    expect(readiness.failingScenarios).toBeNull()
    expect(readiness.verdict).not.toBe('pass')
    expect(readiness.verdict).toBe('mute')
    expect(readiness.chipLabel).toBe('Unmeasured')
    expect(readiness.value).toContain('failing scenarios unmeasured')
  })

  test('nought failing beside undecided scenarios is not a clean run', () => {
    const readiness = readEvalReadiness(
      summaryWith({ exploratory: 0.9 }, { failing_scenarios: 0, unmeasured_scenarios: 40 }),
    )

    expect(readiness.verdict).toBe('mute')
    expect(readiness.chipLabel).toBe('Partly measured')
    expect(readiness.value).toContain('40 undecided')
  })

  test('a run that decided every scenario and failed none passes', () => {
    const readiness = readEvalReadiness(
      summaryWith({ exploratory: 0.9 }, { failing_scenarios: 0, unmeasured_scenarios: 0 }),
    )

    expect(readiness.verdict).toBe('pass')
    expect(readiness.chipLabel).toBe('Pass')
    expect(readiness.value).toBe('0.90 over 4 metrics on the exploratory sample')
  })
})

test.describe('a dataset that scored nothing is absent, not a floor', () => {
  test('an unmeasured half contributes no reading and no zero', () => {
    const readiness = readEvalReadiness(summaryWith({ golden: null, exploratory: 0.81 }))

    expect(readiness.readings.map((r) => r.dataset)).toEqual(['exploratory'])
    expect(readiness.value).not.toContain('0.00')
    expect(readiness.value).not.toContain('golden set')
  })

  test('a measured signal that scored no metric anywhere says so', () => {
    const readiness = readEvalReadiness(summaryWith({ golden: null, exploratory: null }))

    expect(readiness.readings).toEqual([])
    expect(readiness.verdict).toBe('mute')
    expect(readiness.chipLabel).toBe('No data')
    expect(readiness.value).toBe('ran, scored no metric')
  })
})

test.describe('the run-level projection is read only where it is attributed', () => {
  test('a one-dataset run with no per-dataset block names its dataset', () => {
    // A report persisted before `datasets` travelled. `pass_rates_dataset` is
    // the only thing that says whose number this is.
    const readiness = readEvalReadiness({
      eval_signal: 'measured',
      pass_rates: { faithfulness: 0.7, answer_relevancy: 0.9 },
      pass_rates_dataset: 'golden',
      failing_scenarios: 0,
      unmeasured_scenarios: 0,
    })

    expect(readiness.readings).toHaveLength(1)
    expect(readiness.readings[0].dataset).toBe('golden')
    expect(readiness.readings[0].metricCount).toBe(2)
    expect(readiness.value).toContain('0.80 over 2 metrics on the golden set')
  })

  test('pass_rates with no dataset name is not quoted at all', () => {
    // The unattributed number is the one a reader assigns to the wrong half of
    // the run. There is no correct label for it, so there is no cell for it.
    const readiness = readEvalReadiness({
      eval_signal: 'measured',
      pass_rates: { faithfulness: 0.99 },
      pass_rates_dataset: null,
    })

    expect(readiness.readings).toEqual([])
    expect(readiness.value).not.toContain('0.99')
  })

  test('the per-dataset block wins over a contradicting projection', () => {
    const readiness = readEvalReadiness(
      summaryWith(
        { exploratory: 0.42 },
        { pass_rates: { faithfulness: 0.99 }, pass_rates_dataset: 'golden' },
      ),
    )

    expect(readiness.readings.map((r) => r.dataset)).toEqual(['exploratory'])
    expect(readiness.value).toContain('0.42')
    expect(readiness.value).not.toContain('0.99')
  })
})

test.describe('every absence state says which absence it is', () => {
  const cases: [string, string][] = [
    ['no_runs', 'not yet run'],
    ['no_record', 'ran, recorded no measurement'],
    ['no_valid_scores', 'ran, scored no metric'],
    ['agent_not_invoked', 'scored something other than this agent'],
    ['run_failed', 'the last run failed'],
    ['did_not_finish', 'the last run was still going'],
    ['unavailable', 'could not be read'],
  ]

  for (const [signal, copy] of cases) {
    test(`${signal} reads as "${copy}"`, () => {
      // Every absence state suppresses its numbers at the collector, so the
      // payload carries the signal and nulls. A single "not yet run" for all
      // seven told an owner whose run scored the reference answers that no run
      // had happened.
      const readiness = readEvalReadiness({
        eval_signal: signal,
        pass_rates: null,
        pass_rates_dataset: null,
        failing_scenarios: null,
        unmeasured_scenarios: null,
        datasets: { available: false },
      })

      expect(readiness.value).toBe(copy)
      expect(readiness.verdict).toBe('mute')
      expect(readiness.chipLabel).toBe('No data')
    })
  }

  test('no eval_summary at all reads as not yet run', () => {
    const readiness = readEvalReadiness(undefined)

    expect(readiness.value).toBe('not yet run')
    expect(readiness.readings).toEqual([])
    expect(readiness.failingScenarios).toBeNull()
    expect(readiness.verdict).toBe('mute')
  })
})
