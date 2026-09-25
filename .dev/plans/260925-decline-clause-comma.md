# A decline followed by a comma and a clause is scored on its words (#319)

Branch `fix/decline-clause-comma` off `main` at `a0a5105`. `is_decline` in
`app/domain/grounding.py` grounds a whole-sentence decline; `_SECOND_CLAUSE_RE` withdraws that
for `;`, ` but `, ` yet `, `it does`, `it is`. A comma and `and` is not in the list, so "The
documentation does not specify the port, and the Fastify server listens on 8080." is grounded
as a decline and 8080 is never checked. A bare `,\s*(?:and|or)\s` rule was tried in #298 and
reverted because it failed two real declines whose comma is a list comma.

## The rule

A comma before `and` or `or` joins a clause when what follows carries a subject, an auxiliary
or a named verb, and one more word. The rule prefers a missed clause, which is the behaviour
before this change, to a decline wrongly withdrawn, which scores a true sentence on words the
documents cannot carry.

```
subject      (the|this|that|these|those|a|an|its|our|my|their|your), up to two capitalised
             names, then one to three plain words | a capitalised word + up to two plain words
plain word   lowercase, and no determiner, pronoun or relative pronoun: "the teams that are on
             call", "the keys the service requires", "the settings Fastify expects" are list
             items carrying their own clause, not clauses
finite verb  is|are|was|were|has|have|had|does|do|did|will|would|can|cannot|could|should|
             must|may|might|shall, or one of sixty named verbs (listens, expects,
             requires, serves, exposes, ...). Named rather than guessed from an s ending: a
             list item after a determiner ("the deployment steps for staging") ends in s as
             often as a verb does
then         a space and one more word
```

`_CLAUSE_COMMA_RE` in `grounding.py` is case-sensitive so the capitalised branch can tell a
name from a list word. `is_decline` adds it as a third condition. Twins: `CLAUSE_COMMA_RE`
in `claims/reading.ts` and in `claims_template.html` (both copies, byte-identical), using the
aids' `W` class and `B_AFTER` in place of `\w` and `\b`.

## Sentences the rule must decide

| sentence | decision |
|---|---|
| the port, and the Fastify server listens on 8080 | clause |
| without network access, and the normal configuration still expects Anthropic credentials | clause |
| a retry count, or the tests would say so | clause |
| the port, and Fastify listens on 8080 | clause |
| who approves an answer, how provenance is stored, or how cache entries are invalidated | list |
| a measured performance, bundle-size, or maintenance comparison | list |
| the build, the tests, or the deployment steps | list |
| React, Vue, or Svelte as options | list |
| the build, the tests, or the deployment steps for staging | list |
| the build, or the deployment process for staging | list |
| the fixtures, or the tests themselves in detail | list |
| React, Vue, or Svelte plugins for this | list |
| the timeout, or the 3 retries per minute | list |
| the owner, or the teams that are on call | list |
| the port, or the host which is used in staging | list |
| the queue, or the files it writes to | list |
| the owner, or the keys the service requires for signing | list |
| the port, or the settings Fastify expects in production | list |
| the hosts, or the ports each service listens on | list |
| the refunds, or the webhooks Stripe sends on failure | list |

## Pins

- `tests/unit/test_grounding.py`: the sixteen list sentences join the decline parametrize, the
  four clause sentences join the second-clause parametrize; the benchmark pin moves 83 to 84.
  `GROUNDING_RULE_VERSION` is `grounding-v3`, so rows scored before and after never share a
  calibration population.
- `claims-reading.spec.ts`: the same twenty through `isDecline`.
- `fixtures-gate-rules.json`: a tenth sentence, rule `clause comma`, tint `fail`, reason
  "passage 3 carries 29% of its words; number 8080 appears in no passage", read by the
  console spec, the bench spec in Chromium and `test_claims_benchmark.py` against `ground()`.
- Mutation: the third condition removed from `is_decline`, red observed, restored, green.
- `ground_rows.py`: recall 9 of 10, passes 10 of 30, flagged 84 of 260.

## Out of scope

A clause the rule misses stays a decline, as before this change: a pronoun subject ("and it
listens on 8080"), "there is", a past-tense verb, a verb outside the named list, a subject of
four or more words, a comma alone or `, so`. None appears in the benchmark. A named verb used
as a noun after a determiner ("the build, or the test runs for staging") is read as a clause
and the decline scored on its words; the benchmark holds no such sentence.
