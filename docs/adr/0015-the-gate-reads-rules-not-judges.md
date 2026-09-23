# 0015: Faithfulness is scored by a rule, and the deploy gate reads no judge

Status: accepted. Decided with the owner 2026-09-23. Supersedes the instrument half of
ADR 0013 (faithfulness by the ragas judge) and the resolver of ADR 0010. Related: #270,
#290, ADR 0012, ADR 0014.

## What was decided

`faithfulness` keeps its column, its `EVAL_FAITHFULNESS_THRESHOLD` of 0.80 and its place as
the one metric a deploy gates on. The instrument behind it is `app/domain/grounding.py`: the
answer is split into sentences, each sentence is scored by the share of its content words
that the best retrieved passage carries, stemmed to a four-letter floor, ties to the denser
passage, and every number in the sentence must appear somewhere in the retrieved text. A
sentence at or above `CARRIED_FLOOR` (0.4) with no missing number is grounded. A sentence
that is nothing but a decline about the documents ("the documentation does not specify the
port") asserts nothing the documents could carry and is grounded with its reason saying so;
a negated claim about the system, a doing verb or a second clause is scored on its words. The score is
the grounded share, the claims column carries one row per sentence with a reason a person
can read, and `eval_results.judge_identity` names the rule (`rule:grounding`, `grounding-v1`).

The eval task scores nothing else. The other four `METRIC_KEYS` still get their unscored row,
because an unmeasured dimension is a row that says so. The question resolver is not called.
The rejudge task scores faithfulness alone. A run whose every gated identity is a rule reads
as calibrated by construction, status `rule`, because its calibration is the test that pins
its numbers and not a labeller's sheet.

## Why

A model-generated label never gates a deploy (ADR 0012, CLAUDE.md). The faithfulness Judge's
recall on planted sentences was 10 of 10, its precision never settled across six calibration
passes, and the first real run after #291 flagged 289 claims over 46 answers, most of them the
agent speculating on long opinion questions. Every attempt to make that number trustworthy
cost the owner a labelling sitting and moved nothing. A rule flags by a threshold anyone can
read, is measured on the same planted benchmark with no model call in about a second, and
when it flags wrongly the fix is a number in one file.

The rule is the overlap the claims bench and the console's reading aid use to light a passage,
promoted. The aids tint bone at the same 0.4 the gate reads; the console aid on `main` has
neither stemming nor the density tie-break (#295 carries them) and neither aid has the number
rule or the decline rule (#298).

## Measured, `tests/evals/calibration/benchmark/ground_rows.py`, 2026-09-23

Each planted sentence is scored on its own against its answer's retrieved text. The first
measurement scored it inside the answer, and on one row a neighbouring fragment on the same
line earned the flag; the adversary pass caught it and the table below is the corrected one.

| variant | planted flagged | real answers passing 0.80 | sentences flagged |
|---|---|---|---|
| best passage, floor 0.4 | 9 of 10 | 8 of 30 | 103 of 260 |
| best passage, floor 0.5 | 9 of 10 | 6 of 30 | 127 of 260 |
| best passage, floor 0.6 | 10 of 10 | 1 of 30 | 167 of 260 |

The plant that escapes below 0.6 shares half its words with a passage ("It ships as a pip
package and checks every install against the OSV database", `4b0b3432`). Floor 0.4 is the
rule: the same recall as 0.5 with fewer real sentences flagged, and 0.6 would fail 29 of 30
real answers. Recall is therefore 9 of 10 planted sentences, interval [0.55, 1.00], against
the judge's 10 of 10. `tests/unit/test_grounding.py` pins the first row.

The benchmark stores each answer's retrieved text as one joined string, so a benchmark passage
can span two chunks where a production passage never does; the numbers above are for the
benchmark's split.

## The cost, stated

The rule is stricter than the judge. On the benchmark's 30 real answers, 22 fail the 0.80
gate under it where the judge passed 17. A paraphrase with no shared words fails; a summary
drawing on two passages scores against one. The golden rule blocks a deploy on one failed
golden scenario, so until the agent quotes and cites, deploys block. That is the pressure the
owner chose, and the speculation the judge found is now a red scenario in the suite instead
of 38 questions for him.

What the rule misses, from the benchmark rows: a wrong number that appears elsewhere in the
retrieved text for another reason (R80 for a fee of R30, `b28d106f`) passes; a negation flip
("there is a subscription" for "there is no subscription") passes on shared words; a digit
inside an identifier (BM25, Shell3D) joins the number set; an inference dressed as a decline
("that means the repository cannot provide a monthly count") is scored on its words and can
pass on them. The number rule catches a figure the retrieved text never states anywhere, and
nothing subtler. The decline rule's precision is unmeasured: the sentences it grounds on the
benchmark are unlabelled.

## What follows

- The judge modules (`judge_llm`, `relevance_judge`, `question_resolution`,
  `faithfulness_metric`), the ragas plumbing and the four unscored columns are deleted in
  #296 with a migration note. Until then they are on disk and not called.
- `classify_severity` in the red team is a model call whose `critical` the gate reads. It
  moves to a table keyed by vector and verdict tag in #297.
- The reading aids take the number rule and the decline rule (#298), so what the owner
  sees lit is what the gate scored.
