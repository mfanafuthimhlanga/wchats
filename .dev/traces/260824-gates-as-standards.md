# Gates as standards (#38)

Ticket #38, decision #8 on map #4. Two commits on `chore/gates-as-standards`, stacked on
`chore/m0-gate-followups` because `scripts/gates.py` and the import-linter config exist only
there, so m0 merges first.

- `4b54137` the per-function baseline and the source-assertion ban
- `c9a7757` review fixes; the gate meets its own CCN 15 on its own flags

## What changed

- `apps/api/scripts/gates.py`: `LIZARD_FLAGS` becomes `-C 15 -L 60 -a 11 --warnings_only`.
  `LIZARD_BASELINE` pins all 131 functions over that standard at their measured
  `(ccn, length)`. A new `static` step scans every `.py` under `tests/` for three patterns
  (`getsource`, `ast.parse`, `read_text` of an app path) against
  `SOURCE_ASSERTION_BASELINE`, 138 sites in 46 files. Both baselines fail three ways:
  unpinned, grown, stale. Entries may be deleted, never added.
- `apps/api/tests/unit/test_gates.py`: 28 tests. Snapshots all three baselines (ruff,
  lizard, source) with subset semantics, so a deletion passes untouched and an addition
  goes red. Pins the flag list by whole-list equality. Drives the comparators, the lizard
  warning parser, the format-change guard and the site matcher with table cases.

## Decisions made in execution

- `-a 11` stays in the flags. The #8 resolution retires the three size floors by name and
  says nothing about the params floor; removing it would loosen a live guard. It reported
  nothing on its own in this measurement (all 131 warnings exceed CCN or length anyway).
- Stale covers shrunk-below-pin, not only gone. A pinned function whose numbers improve
  reds the gate until the pin is lowered, the same contract as `RUFF_BASELINE`'s
  under-count case.
- A duplicate `(file, function)` key in one lizard run is its own failure. Merging
  duplicates would let a new offender hide under an already-pinned name. Zero duplicates
  exist today.
- Line comments do not count as sites. Counting strips `#` comments first, so prose naming
  a banned pattern cannot red the gate. Re-measured after the change: still 138/46.
- A key reported grown is excluded from stale, so a mixed regression (ccn up, length down)
  reports the growth alone and the shrunk dimension stales on a later run.

## Sharp edges a future test author hits

- A fake `app/` directory under `tmp_path` counts as an app read. The marker is textual.
  Name throwaway trees something other than `app` or the gate reds the file.
- Name binding is file scope. A variable bound to an app path anywhere in a test file
  marks every `read_text` through that name in the file.
- Params are not pinned. A pinned function's parameter count can grow past 11 unseen while
  its ccn and length hold, because the pin is `(ccn, length)` by decision #8.
- The scan reads `tests/` only. `scripts/gates.py` holds itself to CCN 15 by hand
  (`lizard scripts/gates.py -C 15 -L 60` reports only `run_ruff`, CCN 20, pre-existing).
- The matcher is text, not import analysis. It blocks the honest patterns; review still
  owns the exotic ones.

## Mutation proof, observed at c9a7757

Protocol per mutation: mutate, run, capture, `git restore` from HEAD, re-run green.
`git status --short` empty after every restore. Full outputs in this file's commit diff
context are trimmed to the deciding line.

- M1 new 65-line CCN 33 function in `app/services/alert_service.py`:
  `complexity: 1 function(s) over the standard and not in the baseline.` exit 1. Green after restore.
- M2 `get_current_tenant` grown in place:
  `complexity: 1 pinned function(s) grew past the baseline:` pinned ccn 19 length 120, found ccn 21 length 124. Green after restore.
- M3 phantom baseline entry `("app/does/not/exist.py", "ghost")`:
  `complexity: 1 baseline entry(ies) are stale.` with `found gone`. Green after restore.
- M4 new test file reading `app/main.py`:
  `source assertions: 1 test file(s) read app source and nothing pins them.` Green after delete.
- M5 flags loosened to `-C 35`: test red
  `At index 1 diff: '35' != '15'` on the whole-list pin. The gate itself reds with exactly
  one stale entry, not in bulk: only one pinned function has length under 60, every other
  entry stays warned through `-L 60`. The flags pin, not the baseline, is what catches a
  loosened `-C`. Green after restore, 28 passed.
- M6 phantom entry against the test:
  `AssertionError: ('app/does/not/exist.py', 'ghost') was added to LIZARD_BASELINE`,
  1 failed 27 passed. Green after restore.
- M7 second module-level `get_current_tenant` appended to `app/api/deps.py`:
  `complexity: 1 (file, function) key(s) came back twice from one lizard run.` Ruff's F811
  does not fire there (the original name is used before the append), so the complexity
  step is the only thing that catches it. Green after restore.

Falsification of the test fixtures themselves: deleting the unpinned block from either
comparator fails exactly the unpinned case (`1 failed, 27 passed`), restored green.

## Timing, observed

`static gates passed in 6.6s.` at c9a7757 (ruff, import contracts, complexity, source
assertions). The 20s ceiling in the acceptance criteria holds with 13s to spare.

## Collision found by the full tier

`gates.py full` at c9a7757: 2 failed, 2638 passed, 13 skipped in 572.07s. Both failures
in `TestR2ImportBoundary` (test_label_provenance.py): its detector flags any string
constant naming `label_service` or `record_human_label`, and the baseline entry
`("app/services/label_service.py", "record_human_label"): (6, 120)` sits in gates.py and
in the test snapshot as data.

Fix: `_writer_hits` excuses only the string-constant arm for exactly
`scripts/gates.py` and `tests/unit/test_gates.py`; imports, names and attributes stay
watched. Mutation proof observed: `import app.services.label_service` appended to
gates.py reds R2 naming the import; a from-import appended to test_gates.py reds the
tests-side pin with both hits named; restored from HEAD, `TestR2ImportBoundary`
18 passed.

Operative fact for later baselines: any guard that scans the tree for a module name as a
string will collide with a baseline that pins that module's functions. The excuse
pattern above is the resolution; a blanket file skip blinds the import arm and a split
string is undone by the next re-measure.

After the fix, observed at 4c3fac0: `full gates passed in 605.2s.`, exit 0,
2640 unit tests green.
