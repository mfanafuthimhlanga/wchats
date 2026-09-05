// evalSeries.ts builds the eval chart's series out of what each run actually
// measured (#119).
//
// `GET /api/v1/agents/{id}/eval-runs` stores a Measurement per dataset per
// metric and no pooled mean. Section 11 of
// `.dev/reference/260818-llm-eval-fundamentals.md` forbids the pooled rate: the
// golden set is fixed and paired across runs, the exploratory sample rotates,
// and one mean over both moves whenever the draw moves while looking like a
// quality change. So the route reports a run-level `metrics` block only when
// exactly one dataset scored a row, names it in `metrics_dataset`, and puts the
// two measurements under `datasets.golden` and `datasets.exploratory`.
//
// `aggregate_scores` is the numeric compatibility projection beside those, in
// which an unmeasured metric reads 0.0. The chart used to plot it, so a tenant
// who designated a golden set would have seen every run sitting on 0.00, the
// fabricated total-quality collapse the route's own docstring warns about.
//
// Nothing here reads `aggregate_scores`. A metric with no observations is
// `null` all the way to the polyline, where `seriesSegments` breaks the line
// rather than joining across it. A gap says "not measured"; a point at zero
// says "measured, and terrible", and only one of those is true.

export const EVAL_DATASETS = ['golden', 'exploratory'] as const
export type DatasetKey = (typeof EVAL_DATASETS)[number]

// The words on screen, because "golden" and "exploratory" are the API's keys and
// the person reading this page runs a shop. The golden set is the curated one
// that runs in full every night; the exploratory half is a rotating sample.
export const DATASET_LABELS: Record<DatasetKey, string> = {
  golden: 'golden set',
  exploratory: 'exploratory sample',
}

// Channel order is the chart's own, and it is also the colour order: --ch-1 is
// the brightest because faithfulness matters most. Colour carries the channel;
// the dataset is carried by the stroke pattern and by the series label, so a
// reader never has to tell two greys apart to know which half of the suite a
// line belongs to.
export const EVAL_CHANNELS = [
  { key: 'faithfulness', label: 'Faithfulness' },
  { key: 'answer_relevancy', label: 'Answer relevancy' },
  { key: 'context_recall', label: 'Context recall' },
  { key: 'context_precision', label: 'Context precision' },
] as const

export type ChannelKey = (typeof EVAL_CHANNELS)[number]['key']

/** One metric as the API reports it. `value` is null exactly when `measured` is false. */
export interface Measurement {
  value: number | null
  measured: boolean
  observations: number
}

export type ChannelMetrics = Record<ChannelKey, Measurement>

/** One dataset's half of a run: its three counts and its four measurements. */
export interface DatasetOutcome {
  scenario_count: number | null
  valid_scenario_count: number | null
  scored_scenario_count: number | null
  metrics: ChannelMetrics
}

/**
 * The run's per-dataset block. `available` is false exactly when the run has no
 * record. That covers a tenant DB predating tenant migration 0022, a run that died before
 * the write, or a stored payload that broke a construction rule on the way out.
 * All three mean the run measured nothing that can be read.
 */
export type DatasetBlock = { available: boolean } & Partial<
  Record<DatasetKey, DatasetOutcome>
>

export interface EvalRun {
  id: string
  started_at: string
  finished_at: string | null
  status: 'running' | 'complete' | 'failed'
  scenario_count: number | null
  /** The honest run-level reading. Every metric is unmeasured when two datasets scored. */
  metrics?: ChannelMetrics
  /** Which dataset `metrics` was lifted from, and null when no single one was. */
  metrics_dataset?: DatasetKey | null
  /**
   * NUMERIC COMPATIBILITY PROJECTION. An unmeasured metric reads 0.0 here.
   * Declared so a caller can see the field exists and is not read; nothing in
   * this module touches it.
   */
  aggregate_scores?: Record<ChannelKey, number>
  datasets?: DatasetBlock
}

/** One drawable line: a channel measured on one dataset, across the runs. */
export interface EvalSeries {
  /** Stable React key and test handle, `${channel}:${dataset}`. */
  key: string
  channel: ChannelKey
  channelLabel: string
  dataset: DatasetKey
  /** The dataset in the words the page uses, e.g. "golden set". */
  datasetLabel: string
  /** The whole series named once, e.g. "Faithfulness on the golden set". */
  label: string
  /** Index into the --ch-1..4 channel colours. Colour follows the channel. */
  colorIndex: number
  /** One entry per run, oldest first. Null is a gap, never a floor. */
  values: (number | null)[]
  /** The value on the most recent run, or null when that run did not measure it. */
  latest: number | null
  measuredCount: number
}

/** A contiguous run of measured points, carrying each point's run index. */
export interface SeriesPoint {
  index: number
  value: number
}

/**
 * One metric's value on one run's one dataset, or null when nothing measured it.
 *
 * Reads the per-dataset block when the run has a record, and otherwise falls
 * back to the run-level `metrics`, which only ever belongs to the dataset
 * `metrics_dataset` names. Both paths refuse an unmeasured Measurement, so a
 * `{value: null, measured: false}` never becomes a number here.
 */
export function readMeasurement(
  run: EvalRun,
  dataset: DatasetKey,
  channel: ChannelKey,
): number | null {
  const block = run.datasets
  if (block && block.available) {
    return valueOf(block[dataset]?.metrics?.[channel])
  }
  if (run.metrics_dataset === dataset) {
    return valueOf(run.metrics?.[channel])
  }
  return null
}

function valueOf(reading: Measurement | undefined): number | null {
  if (!reading || !reading.measured || typeof reading.value !== 'number') return null
  return reading.value
}

/**
 * Every series the runs actually hold, channel-major so a legend pairs the two
 * halves of one metric.
 *
 * A series with no measurement on any run is dropped rather than drawn flat: an
 * ordinary tenant designates no golden rows, so the golden half of every channel
 * is absent and the chart shows the same four lines it always did. The moment a
 * tenant curates a golden set, four more appear.
 *
 * @param runs Chronological, oldest first, the order the chart plots.
 */
export function buildEvalSeries(runs: EvalRun[]): EvalSeries[] {
  const series: EvalSeries[] = []
  EVAL_CHANNELS.forEach((channel, colorIndex) => {
    for (const dataset of EVAL_DATASETS) {
      const values = runs.map((run) => readMeasurement(run, dataset, channel.key))
      const measuredCount = values.filter((v) => v !== null).length
      if (measuredCount === 0) continue
      series.push({
        key: `${channel.key}:${dataset}`,
        channel: channel.key,
        channelLabel: channel.label,
        dataset,
        datasetLabel: DATASET_LABELS[dataset],
        // No comma inside the name. describeSeries lists these, and a name
        // carrying its own comma made every boundary in that sentence
        // ambiguous to a listener.
        label: `${channel.label} on the ${DATASET_LABELS[dataset]}`,
        colorIndex,
        values,
        latest: values.length > 0 ? values[values.length - 1] : null,
        measuredCount,
      })
    }
  })
  return series
}

/**
 * The measured stretches of a series, each one a polyline's worth of points.
 *
 * A single-point segment is still a segment; the caller draws it as a dot,
 * because a polyline of one point paints nothing and an unmeasured neighbour is
 * not a licence to join it to the next measured run.
 */
export function seriesSegments(values: (number | null)[]): SeriesPoint[][] {
  const segments: SeriesPoint[][] = []
  let current: SeriesPoint[] = []
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length > 0) segments.push(current)
      current = []
      return
    }
    current.push({ index, value })
  })
  if (current.length > 0) segments.push(current)
  return segments
}

/**
 * The lowest and highest measured value across every series, or nulls when
 * nothing was measured.
 *
 * Over `aggregate_scores` this used to include the 0.0 projections, which
 * flattened the whole y-axis onto the floor as soon as one metric went
 * unmeasured. Gaps are skipped here, so the scale describes the readings.
 */
export function seriesDomain(series: EvalSeries[]): {
  min: number | null
  max: number | null
} {
  const values = series.flatMap((s) => s.values.filter((v): v is number => v !== null))
  if (values.length === 0) return { min: null, max: null }
  return { min: Math.min(...values), max: Math.max(...values) }
}

/** The distinct datasets these series cover, in EVAL_DATASETS order. */
export function datasetsCovered(series: EvalSeries[]): DatasetKey[] {
  return EVAL_DATASETS.filter((d) => series.some((s) => s.dataset === d))
}

/** The chart's headline, the one sentence that labels the whole picture. */
export function describeChart(series: EvalSeries[], runCount: number): string {
  if (runCount === 0) return 'No eval runs yet.'
  if (series.length === 0) {
    return `${runCount} run${runCount === 1 ? '' : 's'}, none of which recorded a measurement.`
  }
  const datasets = datasetsCovered(series).map((d) => DATASET_LABELS[d])
  return (
    `Channel telemetry over ${runCount} run${runCount === 1 ? '' : 's'}: ` +
    `${series.length} series across the ${datasets.join(' and ')}.`
  )
}

/**
 * One sentence per series, said in the same words as the pins.
 *
 * These are read as a list rather than joined into the chart's label. Eight of
 * them in one `aria-label` is a block a listener cannot re-read or interrupt,
 * and "unmeasured" is spelled out wherever a number does not exist, so a screen
 * reader is told the same thing the dash on screen tells a sighted reader.
 */
export function describeSeries(series: EvalSeries[], runCount: number): string[] {
  return series.map((s) => {
    // A gap is invisible to a listener, so every series says how many runs went
    // unmeasured. Without it a broken line reads as an unbroken one. When the
    // latest run is itself the unmeasured one, the clause below has already
    // said so and only the earlier gaps are left to count.
    const stated = s.latest === null ? 1 : 0
    const missing = runCount - s.measuredCount - stated
    const gaps =
      missing > 0
        ? `, and ${missing} earlier run${missing === 1 ? '' : 's'} did not measure it`
        : ''
    if (s.latest === null) return `${s.label} was not measured on the latest run${gaps}.`
    const first = s.values.find((v): v is number => v !== null)
    return runCount > 1 && first !== undefined && s.measuredCount > 1
      ? `${s.label} moves from ${first.toFixed(2)} to ${s.latest.toFixed(2)}${gaps}.`
      : `${s.label} at ${s.latest.toFixed(2)}${gaps}.`
  })
}
