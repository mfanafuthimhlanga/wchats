# A decline followed by a comma and a clause is scored on its words (#319)

Branch `fix/decline-clause-comma` off `main` at `a0a5105`. `is_decline` in
`app/domain/grounding.py` grounds a whole-sentence decline; `_SECOND_CLAUSE_RE` withdraws that
for `;`, ` but `, ` yet `, `it does`, `it is`. A comma and `and` is not in the list, so "The
documentation does not specify the port, and the Fastify server listens on 8080." is grounded
as a decline and 8080 is never checked. A bare `,\s*(?:and|or)\s` rule was tried in #298 and
reverted: it failed two real declines whose comma is a list comma.

## The rule

A comma before `and` or `or` joins a clause when what follows carries its own subject and a
finite verb with a word after it; a list item carries neither.

```
subject      (the|this|that|these|those|a|an|its|our|my|their|your) + one to three words
             | a capitalised word + up to two words
finite verb  is|are|was|were|has|have|had|does|do|did|will|would|can|cannot|could|should|
             must|may|might|shall | a word of three letters or more ending in s, not in a
             small stop list (this, its, thus, plus, across, always, perhaps, less, unless,
             various, previous, serious, obvious)
then         a space and one more word, so a plural ending a list ("the deployment steps.")
             is not a verb
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
| who approves an answer, how provenance is stored, or how cache entries are invalidated | list |
| a measured performance, bundle-size, or maintenance comparison | list |
| the build, the tests, or the deployment steps | list |
| React, Vue, or Svelte as options | list |

## Pins

- `tests/unit/test_grounding.py`: the four list sentences join the decline parametrize, the
  three clause sentences join the second-clause parametrize; the benchmark pin moves 83 to 84.
- `claims-reading.spec.ts`: the same seven through `isDecline`.
- `fixtures-gate-rules.json`: a tenth sentence, rule `clause comma`, tint `fail`, reason
  "passage 3 carries 29% of its words; number 8080 appears in no passage", read by the
  console spec, the bench spec in Chromium and `test_claims_benchmark.py` against `ground()`.
- Mutation: the third condition removed from `is_decline`, red observed, restored, green.
- `ground_rows.py`: recall 9 of 10, passes 10 of 30, flagged 84 of 260.

## Out of scope

A clause joined by a comma alone ("...the port, the server listens on 8080") or by `, so`
and `, which`; neither appears in the benchmark. Three or more items with a clause as the last.
