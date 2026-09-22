// sitting.ts is the review's state on the page: which flagged claims have an
// answer, which answers are still to be sent, and what the POST body of one
// sitting is. Pure, so the browserless suite proves it.

export interface FlaggedClaim {
  position: number
  statement: string
  /** The stored answer, true for "yes, it is in my documents", or null. */
  review: boolean | null
}

export interface FlaggedScenario {
  scenario_id: string
  question: string
  response: string
  retrieved_contexts: string[]
  claims: FlaggedClaim[]
}

export interface FlaggedClaimsResponse {
  scenarios: FlaggedScenario[]
  flagged: number
  answered: number
  reviews_available: boolean
  dropped_scenarios: number
}

export interface ClaimAnswer {
  scenario_id: string
  position: number
  statement: string
  supported: boolean
}

/** One key per flagged claim. The separator cannot appear in a scenario id. */
export function keyOf(scenarioId: string, position: number): string {
  return `${scenarioId}\u0000${position}`
}

/** The answers the server already holds, keyed. */
export function storedAnswers(scenarios: readonly FlaggedScenario[]): Map<string, boolean> {
  const out = new Map<string, boolean>()
  for (const s of scenarios) {
    for (const c of s.claims) if (c.review !== null) out.set(keyOf(s.scenario_id, c.position), c.review)
  }
  return out
}

export interface ScenarioProgress {
  answered: number
  total: number
  /** The single answer when the scenario has one claim, else null. */
  only: boolean | null
}

export function scenarioProgress(scenario: FlaggedScenario, answers: ReadonlyMap<string, boolean>): ScenarioProgress {
  const total = scenario.claims.length
  let answered = 0
  for (const c of scenario.claims) if (answers.has(keyOf(scenario.scenario_id, c.position))) answered++
  const only = total === 1 ? (answers.get(keyOf(scenario.scenario_id, scenario.claims[0].position)) ?? null) : null
  return { answered, total, only }
}

export const isDone = (p: ScenarioProgress) => p.total > 0 && p.answered === p.total

/** The first screen with a claim still unanswered, else the first screen. */
export function resumeIndex(scenarios: readonly FlaggedScenario[], answers: ReadonlyMap<string, boolean>): number {
  const i = scenarios.findIndex((s) => !isDone(scenarioProgress(s, answers)))
  return i === -1 ? 0 : i
}

/**
 * The answers of one sitting: every pending key that still has an answer,
 * with the statement the judge wrote, which the server checks against its own.
 */
export function sittingBody(
  scenarios: readonly FlaggedScenario[],
  pending: ReadonlySet<string>,
  answers: ReadonlyMap<string, boolean>,
): { answers: ClaimAnswer[] } {
  const out: ClaimAnswer[] = []
  for (const s of scenarios) {
    for (const c of s.claims) {
      const k = keyOf(s.scenario_id, c.position)
      if (!pending.has(k)) continue
      const supported = answers.get(k)
      if (supported === undefined) continue
      out.push({ scenario_id: s.scenario_id, position: c.position, statement: c.statement, supported })
    }
  }
  return { answers: out }
}
