# What a self-review missed, measured

**2026-08-18.** The same branch was reviewed three times: once by the agent that wrote it, then by
two independent agents on narrow scopes. This records the difference, because the repo's rule
("a separate agent, or it did not happen") has until now been asserted rather than measured.

## The count

| Pass | Scope | BLOCK | Surviving mutants | False claims found |
|---|---|---|---|---|
| Self-review, same turn as the work | the whole branch | 2 | 0 probed | 1 |
| Independent, `corpus.py` + `rates.py` | 4 files | 3 | **11 of 22** | 8 |
| Independent, `8.2a` + `8.2b` | 12 files | 2 | **3 of 14** | 11 |

**The self-review probed no mutants at all.** It read the code it had just written, found two real
defects, and reported. Both independent passes started by mutating, and that is where almost
everything came from.

## The four findings a self-review structurally could not reach

Each of these is invisible to whoever wrote the code, because each one is the author's own
assumption looking back at them.

**1. A guard bypassed by one indirection.** The red-team temperature check was
`"temperature" not in inspect.getsource(_build_probe_fn)`. The reviewer moved `{"temperature": 0}`
to a module-level constant and splatted it: fully deterministic on the wire, invisible to the scan.
The author cannot find this because the author already believes the check works.

**2. A fixture that cannot express the shape the product asks for.** `human_scores.csv` gained an
optional `human_score`, and the test fixture derived the verdict FROM the score, so verdict and
score were always both present or both absent. **No test could construct the row the harness's own
printed guidance asks the owner to produce**, and a TypeError sat in the CLI on exactly that shape.
The author wrote both halves and never noticed they were the same half.

**3. A claim whose counterfactual was never run.** `scenario_rates`' docstring said the zero-run
guard prevented "a rate of 1.0 over nothing". The reviewer deleted the guard and watched: it is
`0/0` and raises. Nobody checks the counterfactual of their own sentence.

**4. A measurement of the "before" state that was never taken.** `8.2a` said every LLM call had been
sampling at the provider default. The reviewer imported `ragas` and read `InstructorModelArgs`: it
defaults to `temperature=0.01, top_p=0.1`, and 0.01 was measured on the wire. Two of the nine sites
had never been at the default. The author had run `grep -rn "temperature"`, found nothing in this
repo, and stopped.

## The pattern under all four

**A self-review re-reads the artifact. An independent review runs an experiment against it.** Every
finding above came from executing something, not from reading: mutate and observe, construct the
input, delete the guard, import the dependency.

That also explains the one thing the self-review DID catch that the independents did not: it noticed
the harness reported "All checked dimensions PASSED" over an empty corpus, because it had just
written the summary table and could see the contradiction between two lines of output. Reading
finds contradictions in what is written down. Running finds everything else.

## What this changes about how to run one

- **Brief the reviewer to mutate, not to read.** Both independent passes were told to mutate,
  observe red, restore, observe green, and both spent most of their effort there.
- **Narrow scope beats broad.** The first attempt at a single whole-branch review died three times
  on API errors after long runs. Two narrow reviews finished. Four files is a workable scope.
- **Make the deliverable a file, appended as findings arrive.** Three earlier attempts died and left
  nothing. The two that produced anything were told to write to disk before starting.
- **Expect the reviewer to find the review artifacts too.** The worst BLOCK of the day was in the
  mutation-proof note added to make proofs reproducible: it restored with a repo-rooted path from
  `apps/api`, so following it verbatim left the mutant on disk and the reviewer stacked two
  mutations before noticing.

## The cost

Roughly 350k subagent tokens and about an hour of wall clock across the two reviews, against a
branch of about 2,000 lines. It found 5 BLOCKs. One of them broke the workflow the owner had already
been handed.
