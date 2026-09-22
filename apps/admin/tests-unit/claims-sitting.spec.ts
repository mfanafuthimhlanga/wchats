import { expect, test } from '@playwright/test'
import {
  keyOf,
  resumeIndex,
  scenarioProgress,
  sittingBody,
  storedAnswers,
  type FlaggedScenario,
} from '../app/agents/[id]/eval/[runId]/claims/sitting'

// claims-sitting.spec.ts is the browserless proof for the review page's state:
// which flags carry an answer, where a sitting resumes, and what one POST sends.

const scenarios: FlaggedScenario[] = [
  {
    scenario_id: 's1',
    question: 'q1',
    response: 'r1',
    retrieved_contexts: [],
    claims: [
      { position: 0, statement: 'a', review: true },
      { position: 2, statement: 'c', review: null },
    ],
  },
  {
    scenario_id: 's2',
    question: 'q2',
    response: 'r2',
    retrieved_contexts: [],
    claims: [{ position: 1, statement: 'b', review: false }],
  },
]

test('storedAnswers keys only the claims the server already holds an answer for', () => {
  const a = storedAnswers(scenarios)
  expect([...a.entries()]).toEqual([
    [keyOf('s1', 0), true],
    [keyOf('s2', 1), false],
  ])
})

test('scenarioProgress counts answers and carries the single answer of a one-claim screen', () => {
  const a = storedAnswers(scenarios)
  expect(scenarioProgress(scenarios[0], a)).toEqual({ answered: 1, total: 2, only: null })
  expect(scenarioProgress(scenarios[1], a)).toEqual({ answered: 1, total: 1, only: false })
})

test('resumeIndex is the first screen with an unanswered claim, else the first screen', () => {
  const a = storedAnswers(scenarios)
  expect(resumeIndex(scenarios, a)).toBe(0)
  a.set(keyOf('s1', 2), false)
  expect(resumeIndex(scenarios, a)).toBe(0)
  // the arrangement: s2 is done and s1 is not, so a reader that returns the
  // last done screen or the count of done screens gets 1, never 0
  expect(resumeIndex([scenarios[1], scenarios[0]], storedAnswers(scenarios))).toBe(1)
})

test('sittingBody sends only pending keys, with the statement the judge wrote', () => {
  const a = storedAnswers(scenarios)
  a.set(keyOf('s1', 2), false)
  const body = sittingBody(scenarios, new Set([keyOf('s1', 2), keyOf('s2', 1), keyOf('s9', 0)]), a)
  expect(body).toEqual({
    answers: [
      { scenario_id: 's1', position: 2, statement: 'c', supported: false },
      { scenario_id: 's2', position: 1, statement: 'b', supported: false },
    ],
  })
})
