# Five ways a check was green while the thing it guards was broken

One session, 2026-08-18, working on `7.29`. Every one of these passed a check that existed
specifically to catch it. The fifth is the one to read first: it is the only one where the check
was not merely uninformative but arithmetically incapable of returning its own PASS.

They are collected here because the shapes recur and each has a cheap test that separates a real
pass from a green one.

## 1. A guard no production shape can reach

`_published_context` in `agent.py` skips a retrieve whose payload could not be decoded, and skips
entries belonging to another tool. Two tests covered those skips. Both passed with the guards
deleted.

`_attach_retrieve_capture` only ever attaches chunks to a `retrieve` entry, and writes `[]` for a
payload it could not decode. No shape it emits can reach either guard, so the tests were describing
a state the code cannot produce.

**Separator:** delete the guard and run the test. If it stays green, the test is not covering the
guard, whatever its name says.

**What to do about it.** Not always deletion. A guard can be worth keeping against a future change
to its upstream while being unreachable today. Then the test has to build the hostile shape by hand
and say so at the test, so a later reader is not misled into thinking production emits it.

## 2. A mutation that mutated the wrong function

The proof for the tool-name guard reported GREEN twice. The second time was not a tautology: the
anchor line

```
if tc.get("tool_name") != "retrieve" or "result" not in tc:
```

appears in two functions in `agent.py`, and `text.replace(old, new, 1)` took the first, which is
`_judge_retrieved_context` at line 522. The guard under proof was never touched.

**A mutation proof that mutates the wrong function is indistinguishable from a tautology.** Both
print "guard not proven".

**Separator:** assert the anchor is unique before mutating.

```python
if text.count(OLD) != 1:
    sys.exit(f"anchor is not unique: {text.count(OLD)} occurrences")
```

Prefer an anchor that includes a line only the target function has, such as its first statement.

## 3. An assertion pinned to a state the project has left

`test_the_shipped_tree_is_honest_about_being_unready` asserted `readiness()["blocking"]` was
non-empty. True while `responses/` had never been captured. The E2E-6 capture on 2026-08-17 filled
it, every machine-fixable input became present, and `blocking` correctly emptied. The test went red
and stayed red for a day.

Nothing noticed, because the gate that runs at the end of every session was whole-suite
`--collect-only`, which imports every module and asserts nothing about behaviour. The full battery
had last run the day before.

**Separator:** when a test asserts a container is non-empty, ask what fills it and whether the
project is moving towards filling it. If it is, assert on what the report SAYS rather than on which
list happens to carry it.

## 4. A corpus check that cannot see a well-formed worthless answer

The E2E-6 corpus was accepted as clean on three checks: no empty responses, none under 120
characters, none containing provider-error text. Four of the twenty responses were the PII
firewall's deflection, a fluent sentence of ordinary length that answers nothing. It passed all
three, and was found by a person reading the files.

`compute_correlation.py --check` now names deflected responses and excludes them from the scorable
count. Nothing yet catches the other members of the family: a refusal or an escalation where the
scenario expected an answer (`7.31`).

**Separator:** a corpus check that only looks for malformed output cannot see a well-formed wrong
one. List the specific well-formed failures the system can produce, and check for those by value.

## 5. A rubric whose PASS branch cannot be reached by the data it is given

The strongest of the five, because it produces a confident number that is about nothing.

`grounding_fidelity`'s rubric, in full:

> PASS: Every factual claim in the agent response is traceable to a retrieved chunk **provided in
> the tool_calls log**. FAIL: ... OR the retrieve tool was not called before a factual response.

Every entry in the calibration corpus carries `"result": {}`, because the capture drives the widget
SSE and SSE does not carry tool results. **No chunk is ever provided.** So PASS is unreachable, FAIL
is certain, and the verdict is decided by the capture format rather than by the answer. Every tool
call was also named `""`, which forecloses the rubric's second FAIL condition the same way.

Nothing was broken in the ordinary sense. The judge ran, returned well-formed verdicts, and a
correlation could have been computed against them. It would have been a real number measuring
nothing, and it would have been quoted afterwards.

**Separator:** for each verdict a rubric can return, ask what the input would have to contain for
that branch to fire, then check the data actually contains it. A branch no input can reach is a
constant wearing a rubric.

**Cheaper still:** count the distinct verdicts a dimension produces across a corpus. One distinct
value over many rows is either a very consistent system or an unreachable branch, and it costs
nothing to find out which.

## The common shape

Every one of the five is a check whose *inputs* moved out from under it, or never matched it: the
capture stopped producing the shape, the file stopped being empty, the corpus started containing
fluent refusals, the anchor stopped being unique, the tool results were never carried at all. None
of them is a check that was written carelessly.

So the question that finds this class is not "is this assertion correct?" but **"what would have to
change for this check to become vacuous, and has it?"**
