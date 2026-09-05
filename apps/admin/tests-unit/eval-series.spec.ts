import { expect, test } from '@playwright/test'
import {
  EVAL_CHANNELS,
  EVAL_DATASETS,
  buildEvalSeries,
  datasetsCovered,
  describeChart,
  describeSeries,
  readMeasurement,
  seriesDomain,
  seriesSegments,
  type ChannelKey,
  type DatasetKey,
  type EvalRun,
  type Measurement,
} from '../app/agents/[id]/eval/evalSeries'

// eval-series.spec.ts is the browserless proof for evalSeries.ts (#119).
//
// The bug this pins: `GET /agents/{id}/eval-runs` stores a Measurement per
// dataset per metric and no pooled mean, so a tenant that has designated a
// golden set gets `metrics_dataset: null` and four run-level metrics that read
// unmeasured. `aggregate_scores` projects an unmeasured metric to 0.0 for
// clients that type the field `number`, and the chart used to plot that
// projection, so every run sat at 0.00, a fabricated total-quality collapse.
//
// Two rules govern the assertions below.
//   1. Every fixture carries an `aggregate_scores` block whose numbers
//      contradict the per-dataset measurements. A builder that reaches for the
//      projection therefore produces a visibly wrong number rather than an
//      accidentally right one, so these assertions discriminate.
//   2. An unmeasured point is compared against `null`, never against a
//      number. `toBeNull()` and `toBe(0)` are both satisfied by a falsy value
//      in a loose comparison; the strict null is the whole claim.

const CHANNEL_KEYS = EVAL_CHANNELS.map((c) => c.key)

function measured(value: number, observations = 4): Measurement {
  return { value, measured: true, observations }
}

const UNMEASURED: Measurement = { value: null, measured: false, observations: 0 }

function metricsAt(value: number | null): Record<ChannelKey, Measurement> {
  const reading = value === null ? UNMEASURED : measured(value)
  return Object.fromEntries(CHANNEL_KEYS.map((k) => [k, reading])) as Record<
    ChannelKey,
    Measurement
  >
}

function outcome(value: number | null) {
  return {
    scenario_count: 6,
    valid_scenario_count: 6,
    scored_scenario_count: value === null ? 0 : 6,
    metrics: metricsAt(value),
  }
}

// A run whose two datasets scored. `metrics_dataset` is null and the run-level
// metrics read unmeasured, exactly as apps/api/app/api/v1/evals.py builds it.
// `aggregate_scores` therefore reads 0.0 on all four, the lie the chart used
// to plot.
function twoDatasetRun(
  id: string,
  startedAt: string,
  golden: number | null,
  exploratory: number | null,
): EvalRun {
  return {
    id,
    started_at: startedAt,
    finished_at: startedAt,
    status: 'complete',
    scenario_count: 12,
    metrics: metricsAt(null),
    metrics_dataset: null,
    aggregate_scores: {
      faithfulness: 0,
      answer_relevancy: 0,
      context_precision: 0,
      context_recall: 0,
    },
    datasets: {
      available: true,
      golden: outcome(golden),
      exploratory: outcome(exploratory),
    },
  }
}

// A run on today's ordinary tenant: nobody designated a golden set, the
// exploratory half scored everything, so the run-level reading exists and names
// its dataset. No `datasets` block at all, the older response shape.
function singleDatasetRun(
  id: string,
  startedAt: string,
  dataset: DatasetKey,
  value: number,
): EvalRun {
  return {
    id,
    started_at: startedAt,
    finished_at: startedAt,
    status: 'complete',
    scenario_count: 6,
    metrics: metricsAt(value),
    metrics_dataset: dataset,
    aggregate_scores: {
      faithfulness: value,
      answer_relevancy: value,
      context_precision: value,
      context_recall: value,
    },
  }
}

test.describe('buildEvalSeries', () => {
  test('a two-dataset run yields one series per dataset per channel', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', 0.93, 0.86),
    ])

    expect(series).toHaveLength(EVAL_CHANNELS.length * EVAL_DATASETS.length)

    const faithfulness = series.filter((s) => s.channel === 'faithfulness')
    expect(faithfulness.map((s) => s.dataset)).toEqual(['golden', 'exploratory'])
    expect(faithfulness.map((s) => s.datasetLabel)).toEqual([
      'golden set',
      'exploratory sample',
    ])
    expect(faithfulness.map((s) => s.label)).toEqual([
      'Faithfulness on the golden set',
      'Faithfulness on the exploratory sample',
    ])
    // No comma inside a series name. describeSeries returns one sentence per
    // series and a name carrying its own comma made every boundary ambiguous.
    for (const s of series) expect(s.label).not.toContain(',')
    expect(faithfulness[0].values).toEqual([0.94, 0.93])
    expect(faithfulness[1].values).toEqual([0.88, 0.86])
  })

  test('an unmeasured point is a null, never a zero', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
      twoDatasetRun('r3', '2026-08-03T00:00:00Z', 0.92, 0.87),
    ])

    const golden = series.find(
      (s) => s.channel === 'faithfulness' && s.dataset === 'golden',
    )!
    expect(golden.values).toEqual([0.94, null, 0.92])
    expect(golden.values[1]).toBeNull()
    expect(golden.values).not.toContain(0)
    expect(golden.measuredCount).toBe(2)
  })

  test('the last run being unmeasured leaves latest null rather than zero', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
    ])

    const golden = series.find(
      (s) => s.channel === 'faithfulness' && s.dataset === 'golden',
    )!
    expect(golden.latest).toBeNull()

    const exploratory = series.find(
      (s) => s.channel === 'faithfulness' && s.dataset === 'exploratory',
    )!
    expect(exploratory.latest).toBe(0.86)
  })

  test('a dataset that never scored produces no series at all', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', null, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
    ])

    expect(series.map((s) => s.dataset)).toEqual(
      EVAL_CHANNELS.map(() => 'exploratory'),
    )
    expect(series).toHaveLength(EVAL_CHANNELS.length)
  })

  test('a run with no dataset block falls back to the run-level reading and its named dataset', () => {
    const series = buildEvalSeries([
      singleDatasetRun('r1', '2026-08-01T00:00:00Z', 'exploratory', 0.91),
      singleDatasetRun('r2', '2026-08-02T00:00:00Z', 'exploratory', 0.9),
    ])

    expect(series).toHaveLength(EVAL_CHANNELS.length)
    expect(series[0].dataset).toBe('exploratory')
    expect(series[0].values).toEqual([0.91, 0.9])
  })

  test('a run with no record contributes a gap, not a floor', () => {
    const recordless: EvalRun = {
      id: 'r2',
      started_at: '2026-08-02T00:00:00Z',
      finished_at: '2026-08-02T00:00:00Z',
      status: 'complete',
      scenario_count: null,
      metrics: metricsAt(null),
      metrics_dataset: null,
      aggregate_scores: {
        faithfulness: 0,
        answer_relevancy: 0,
        context_precision: 0,
        context_recall: 0,
      },
      datasets: { available: false },
    }
    const series = buildEvalSeries([
      singleDatasetRun('r1', '2026-08-01T00:00:00Z', 'exploratory', 0.91),
      recordless,
      singleDatasetRun('r3', '2026-08-03T00:00:00Z', 'exploratory', 0.93),
    ])

    expect(series[0].values).toEqual([0.91, null, 0.93])
  })
})

test.describe('seriesSegments', () => {
  test('a null splits the line into two drawable runs of points', () => {
    const segments = seriesSegments([0.94, null, 0.92, 0.91])
    expect(segments).toEqual([
      [{ index: 0, value: 0.94 }],
      [
        { index: 2, value: 0.92 },
        { index: 3, value: 0.91 },
      ],
    ])
  })

  test('an unbroken series is one segment', () => {
    expect(seriesSegments([0.9, 0.91])).toEqual([
      [
        { index: 0, value: 0.9 },
        { index: 1, value: 0.91 },
      ],
    ])
  })

  test('all-null yields no segments, so nothing is drawn at the floor', () => {
    expect(seriesSegments([null, null])).toEqual([])
  })
})

test.describe('seriesDomain', () => {
  test('a gap does not drag the floor to zero', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
    ])
    const { min, max } = seriesDomain(series)
    expect(min).toBeGreaterThan(0.5)
    expect(min).toBeCloseTo(0.86, 5)
    expect(max).toBeCloseTo(0.94, 5)
  })

  test('no runs at all leave the domain null so the caller picks its own', () => {
    expect(seriesDomain([])).toEqual({ min: null, max: null })
  })
})

test.describe('datasetsCovered and describeSeries', () => {
  test('the covered datasets come back in EVAL_DATASETS order', () => {
    const both = buildEvalSeries([twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88)])
    expect(datasetsCovered(both)).toEqual(['golden', 'exploratory'])

    const one = buildEvalSeries([
      singleDatasetRun('r1', '2026-08-01T00:00:00Z', 'exploratory', 0.91),
    ])
    expect(datasetsCovered(one)).toEqual(['exploratory'])
  })

  test('the headline names both datasets in the words the page uses', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
    ])

    expect(describeChart(series, 2)).toBe(
      'Channel telemetry over 2 runs: 8 series across the golden set and exploratory sample.',
    )
  })

  test('one sentence per series, and a gap never reads as 0.00', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
    ])
    const sentences = describeSeries(series, 2)

    expect(sentences).toHaveLength(series.length)
    // The latest run is the only unmeasured one, and the clause already said
    // so, so nothing counts it a second time.
    expect(sentences[0]).toBe(
      'Faithfulness on the golden set was not measured on the latest run.',
    )
    expect(sentences[1]).toBe(
      'Faithfulness on the exploratory sample moves from 0.88 to 0.86.',
    )
    for (const s of sentences) expect(s).not.toContain('0.00')
  })

  test('a gap in the middle of a series is spoken, because a listener cannot see it', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
      twoDatasetRun('r3', '2026-08-03T00:00:00Z', 0.92, 0.87),
    ])
    const sentences = describeSeries(series, 3)

    expect(sentences[0]).toBe(
      'Faithfulness on the golden set moves from 0.94 to 0.92, and 1 earlier run did not measure it.',
    )
    expect(sentences[1]).toBe(
      'Faithfulness on the exploratory sample moves from 0.88 to 0.87.',
    )
  })

  test('the latest run being the only gap is counted once, not twice', () => {
    const series = buildEvalSeries([
      twoDatasetRun('r1', '2026-08-01T00:00:00Z', 0.94, 0.88),
      twoDatasetRun('r2', '2026-08-02T00:00:00Z', null, 0.86),
      twoDatasetRun('r3', '2026-08-03T00:00:00Z', null, 0.87),
    ])
    const sentences = describeSeries(series, 3)

    expect(sentences[0]).toBe(
      'Faithfulness on the golden set was not measured on the latest run, and 1 earlier run did not measure it.',
    )
  })

  test('no runs says so rather than describing an empty chart', () => {
    expect(describeChart([], 0)).toBe('No eval runs yet.')
    expect(describeSeries([], 0)).toEqual([])
  })

  test('runs that measured nothing say that, and never read as a score of zero', () => {
    expect(describeChart([], 3)).toBe('3 runs, none of which recorded a measurement.')
  })
})

// readMeasurement is the single rule both paths above go through. Named here so
// the export is exercised directly, not only through buildEvalSeries.
test('readMeasurement refuses an unmeasured Measurement on either path', () => {
  const twoDataset = twoDatasetRun('r1', '2026-08-01T00:00:00Z', null, 0.88)
  expect(readMeasurement(twoDataset, 'golden', 'faithfulness')).toBeNull()
  expect(readMeasurement(twoDataset, 'exploratory', 'faithfulness')).toBe(0.88)

  const single = singleDatasetRun('r1', '2026-08-01T00:00:00Z', 'exploratory', 0.91)
  expect(readMeasurement(single, 'golden', 'faithfulness')).toBeNull()
  expect(readMeasurement(single, 'exploratory', 'faithfulness')).toBe(0.91)
})
