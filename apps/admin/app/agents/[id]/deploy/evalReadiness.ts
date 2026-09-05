// evalReadiness.ts reads the deploy gate's "Evals pass rate" row out of what the
// checklist run actually measured (#175).
//
// The deploy page used to average `eval_summary.pass_rates` across metrics and
// read `failing_scenarios ?? 0`. Both are wrong on the same run.
//
// `pass_rates` carries a run-level number only when exactly one dataset scored
// anything, and `pass_rates_dataset` names which one
// (`deployment_service._pass_rates`). A tenant with a designated golden set has
// two measurements and no honest way to pool them, so `pass_rates` is null by
// design and `avgPassRate(undefined)` returned null. That much was survivable.
// What was not: `failing_scenarios ?? 0` turned a null the collector wrote to
// mean "this run does not say" into the number zero, and zero failing beside a
// null average is the shape of a clean bill of health over no observations.
//
// Same fix shape as evalSeries.ts on #119. The per-dataset measurements are read
// where the record keeps them apart, each dataset's average is named with the
// dataset it belongs to, and unmeasured is said rather than shown as a number.
// The two halves are never added together: the golden set is fixed and runs in
// full, the exploratory sample rotates, and one mean over both moves whenever
// the draw moves while looking like a quality change.

import {
  DATASET_LABELS,
  EVAL_CHANNELS,
  EVAL_DATASETS,
  type ChannelKey,
  type DatasetKey,
  type Measurement,
} from '../eval/evalSeries'

export type { DatasetKey }

/**
 * `report.eval_summary` as `deployment_service._eval_summary` writes it.
 *
 * Every field is optional because a report persisted before a field existed does
 * not carry it, and every count is `number | null` because the collector writes
 * null rather than zero wherever the run did not say.
 */
export interface EvalSummary {
  /** 'measured', or one of the seven absence states. Absent on pre-#51 reports. */
  eval_signal?: string
  /**
   * The run-level projection, present only when exactly one dataset scored.
   * Never read without `pass_rates_dataset`: a rate nobody attributed is a rate
   * a reader attributes to the wrong half of the run.
   */
  pass_rates?: Record<string, number> | null
  pass_rates_dataset?: DatasetKey | null
  /** The judge's own count of scenarios it decided against. Null when the run does not say. */
  failing_scenarios?: number | null
  /** Scored scenarios the judge never reached a gated verdict on. */
  unmeasured_scenarios?: number | null
  datasets?: EvalDatasetBlock
}

export type EvalDatasetBlock = { available?: boolean } & Partial<
  Record<DatasetKey, { metrics?: Partial<Record<ChannelKey, Measurement>> }>
>

/** One dataset's headline, over the metrics that dataset actually scored. */
export interface DatasetReading {
  dataset: DatasetKey
  /** The words the console uses, e.g. "golden set". */
  datasetLabel: string
  /** The mean of this dataset's measured metrics. Metrics pool, datasets do not. */
  average: number
  /** How many of the four channels produced that mean. A denominator, always shown. */
  metricCount: number
}

export type EvalVerdict = 'pass' | 'fail' | 'mute'

export interface EvalReadiness {
  /** One entry per dataset that scored a metric, in EVAL_DATASETS order. Never pooled. */
  readings: DatasetReading[]
  failingScenarios: number | null
  unmeasuredScenarios: number | null
  /** The Value cell's text. */
  value: string
  verdict: EvalVerdict
  /** The Chip's label. */
  chipLabel: string
}

/**
 * What each absence state says in the Value cell.
 *
 * The seven are `deployment_service`'s own, and they are different events: a
 * tenant who has never run an eval and a tenant whose last run scored the
 * reference answers instead of the agent both used to render as "not yet run".
 */
const SIGNAL_COPY: Record<string, string> = {
  no_runs: 'not yet run',
  no_record: 'ran, recorded no measurement',
  no_valid_scores: 'ran, scored no metric',
  agent_not_invoked: 'scored something other than this agent',
  run_failed: 'the last run failed',
  did_not_finish: 'the last run was still going',
  unavailable: 'could not be read',
}

/** The mean of the metrics a dataset measured, or null when it measured none. */
function averageOfMeasured(
  metrics: Partial<Record<ChannelKey, Measurement>> | undefined,
): { average: number; metricCount: number } | null {
  const values: number[] = []
  for (const channel of EVAL_CHANNELS) {
    const reading = metrics?.[channel.key]
    // `measured` is the claim and `value` is the number. A reading that says
    // false carries null, and a reading that says true over nothing was refused
    // at construction, so both halves are tested rather than either alone.
    if (reading && reading.measured && typeof reading.value === 'number') {
      values.push(reading.value)
    }
  }
  if (values.length === 0) return null
  return {
    average: values.reduce((a, b) => a + b, 0) / values.length,
    metricCount: values.length,
  }
}

/**
 * The per-dataset headlines this run supports, oldest rule first.
 *
 * The per-dataset block is the source whenever the run has a record. The
 * run-level `pass_rates` is the fallback for a report written before `datasets`
 * travelled, and it is read only alongside `pass_rates_dataset`, which is the
 * only thing that says whose number it is.
 */
function readDatasetReadings(summary: EvalSummary | undefined): DatasetReading[] {
  const block = summary?.datasets
  if (block?.available) {
    const readings: DatasetReading[] = []
    for (const dataset of EVAL_DATASETS) {
      const reading = averageOfMeasured(block[dataset]?.metrics)
      if (reading === null) continue
      readings.push({ dataset, datasetLabel: DATASET_LABELS[dataset], ...reading })
    }
    return readings
  }

  const dataset = summary?.pass_rates_dataset
  const rates = summary?.pass_rates
  if (!dataset || !rates) return []
  const values = Object.values(rates).filter((v): v is number => typeof v === 'number')
  if (values.length === 0) return []
  return [
    {
      dataset,
      datasetLabel: DATASET_LABELS[dataset],
      average: values.reduce((a, b) => a + b, 0) / values.length,
      metricCount: values.length,
    },
  ]
}

/** The scenario-count clause, which says which of the three states this run is in. */
function scenarioClause(failing: number | null, unmeasured: number | null): string {
  if (failing === null) return ' · failing scenarios unmeasured'
  if (failing > 0) return ` · ${failing} failing`
  if (unmeasured !== null && unmeasured > 0) return ` · 0 failing, ${unmeasured} undecided`
  return ''
}

function countOrNull(value: number | null | undefined): number | null {
  return typeof value === 'number' ? value : null
}

/**
 * The whole "Evals pass rate" row, read off the run rather than projected.
 *
 * The verdict is a Pass only when a dataset scored, the run says how many
 * scenarios failed, none did, and none was left undecided. Nought failing out of
 * forty undecided scenarios is not nought failing
 * (`deployment_service._record_counts`), so that run reads as partly measured
 * rather than as a clean one.
 */
export function readEvalReadiness(summary: EvalSummary | undefined): EvalReadiness {
  const readings = readDatasetReadings(summary)
  const failingScenarios = countOrNull(summary?.failing_scenarios)
  const unmeasuredScenarios = countOrNull(summary?.unmeasured_scenarios)

  if (readings.length === 0) {
    const signal = summary?.eval_signal
    const copy =
      (signal !== undefined && SIGNAL_COPY[signal]) ||
      (signal === 'measured' ? 'ran, scored no metric' : 'not yet run')
    return {
      readings,
      failingScenarios,
      unmeasuredScenarios,
      value: copy,
      verdict: 'mute',
      chipLabel: 'No data',
    }
  }

  const value =
    readings
      .map(
        (r) =>
          `${r.average.toFixed(2)} over ${r.metricCount} metric${r.metricCount === 1 ? '' : 's'} ` +
          `on the ${r.datasetLabel}`,
      )
      .join('; ') + scenarioClause(failingScenarios, unmeasuredScenarios)

  if (failingScenarios === null) {
    return { readings, failingScenarios, unmeasuredScenarios, value, verdict: 'mute', chipLabel: 'Unmeasured' }
  }
  if (failingScenarios > 0) {
    return { readings, failingScenarios, unmeasuredScenarios, value, verdict: 'fail', chipLabel: 'Fail' }
  }
  if (unmeasuredScenarios !== null && unmeasuredScenarios > 0) {
    return {
      readings,
      failingScenarios,
      unmeasuredScenarios,
      value,
      verdict: 'mute',
      chipLabel: 'Partly measured',
    }
  }
  return { readings, failingScenarios, unmeasuredScenarios, value, verdict: 'pass', chipLabel: 'Pass' }
}
