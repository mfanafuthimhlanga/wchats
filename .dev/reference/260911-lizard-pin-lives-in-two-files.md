# The complexity pin lives in two files, and `length` counts your comments

Two facts about `scripts/gates.py`'s complexity step that each cost a full 13 minute gate
run to discover. Both bite when you add a comment to a function that is already pinned.

## A pin is written twice and both copies must move

`LIZARD_BASELINE` in `apps/api/scripts/gates.py` is the pin. `apps/api/tests/unit/test_gates.py`
holds a second copy of the same dict and asserts the two are equal.

Lower a pin in `gates.py` alone and `scripts/gates.py static` passes. The failure arrives
at step 9 of `full`, after the whole unit suite has run:

```
__________________ test_lizard_baseline_equals_the_snapshot ___________________
E  Left contains one more item: "('app/worker/tasks/runtime/eval.py', 'run_eval_suite')
   is (24, 455) in LIZARD_BASELINE and (24, 462) in the snapshot"

1 failed, 5051 passed, 14 skipped in 797.77s (0:13:17)
FAILED at step 9 (unit tests) after 909.6s, exit 1.
```

Observed 2026-09-11. Edit both files in the same change, then run
`pytest tests/unit/test_gates.py` before spending 15 minutes on `full`.

## `length` includes comments and docstrings

The standard is `-C 15 -L 60`: CCN 15, and **60 lines**, not 60 NLOC. Lizard reports both
numbers and the gate reads `length`, so a docstring paragraph costs exactly as much as the
code it explains.

Adding eleven lines of comment to `run_eval_suite` moved it from its pin at 462 to 473 and
failed the gate. Nothing about the behaviour changed.

The consequence worth planning around: **in a pinned or near-ceiling function, prose is a
budgeted resource.** Three functions touched on 2026-09-11 measured at or over their
ceiling before a single line was added:

| function | measured | ceiling |
|---|---|---|
| `EvalResult.from_payload` | 60 | 60 |
| `eval_service.build_eval_result` | 59 | 60 |
| `run_eval_suite` | 462 | pinned at 462 |

None could take one line. The fix that works is extraction, which gives the reasoning a
helper docstring to live in where it costs nothing, and usually lowers the pin as a side
effect: `run_eval_suite` came out at 455.

## Check the ceiling before writing the comment

```bash
cd apps/api
.venv/Scripts/python.exe -m lizard app/services/eval_service.py -C 15 -L 60 -a 11 --warnings_only
```

A function absent from that output is under the standard and has room up to 60. A function
present in it must be in `LIZARD_BASELINE`, and the gate fails three ways: unpinned and
over, pinned and grown, or pinned and now smaller than its pin. The third is not a pass.
Lower the number in both files.
