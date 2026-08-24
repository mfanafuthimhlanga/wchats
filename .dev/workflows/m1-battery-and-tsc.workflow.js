export const meta = {
  name: 'm1-battery-and-tsc',
  description: 'Fix the tsc gate deadlock (7.9), adopt the Martin battery with measured floors (7.8)',
  phases: [
    { title: 'Fix tsc', detail: 'reduced-motion.spec.ts TS2353', model: 'opus' },
    { title: 'Verify fix' },
    { title: 'Add tools', detail: 'ruff/lizard/import-linter/mutmut via uv', model: 'opus' },
    { title: 'Baselines', detail: 'measure floors, wire gates', model: 'opus' },
    { title: 'Verify battery' },
  ],
}

const REPO = 'C:\\Users\\Bantu\\mzansi-agentive\\wchats'

phase('Fix tsc')
const fix = await agent(
  `Repo: ${REPO}. You are fixing exactly ONE TypeScript error. File: apps/admin/tests/reduced-motion.spec.ts around line 18. Error: TS2353: Object literal may only specify known properties, and 'reducedMotion' does not exist in type 'Fixtures<{}, {}, PlaywrightTestArgs & PlaywrightTestOptions, PlaywrightWorkerArgs & PlaywrightWorkerOptions>'. This is the ONLY tsc error in apps/admin, long carried as a known exception; your job is to make tsc report ZERO errors with a legitimate fix.
Rules: Read the file first and understand what the test intends (Playwright reduced-motion emulation). The likely correct fix uses Playwright's supported API (e.g. test.use({ contextOptions: { reducedMotion: 'reduce' } }) or a properly typed test.extend) - decide from the actual code. FORBIDDEN: @ts-ignore, @ts-expect-error, casts to any, changing tsconfig, excluding the file, editing any other file. Only apps/admin/tests/reduced-motion.spec.ts may change.
Do NOT run Playwright (needs a dev server; not your gate). After editing, run in PowerShell: cd ${REPO}\\apps\\admin; npx tsc --noEmit  - and capture the FULL observed output. Do not commit anything.
Return: (1) the exact diff you made, (2) the verbatim tsc output, (3) one sentence on whether the test still exercises the same behaviour it did, and if not, what changed.`,
  { label: 'fix:tsc-7.9', phase: 'Fix tsc', model: 'opus' }
)

phase('Verify fix')
const fixVerdict = await agent(
  `Repo: ${REPO}. An implementer claims to have fixed the single tsc error in apps/admin (tests/reduced-motion.spec.ts, TS2353) legitimately. Their report: <report>${fix}</report>
Verify independently - take nothing from the report on trust: (1) run in PowerShell: cd ${REPO}\\apps\\admin; npx tsc --noEmit  and capture the verbatim output; (2) run: git -C ${REPO} diff -- apps/admin/tests/reduced-motion.spec.ts  and read the actual change; (3) read the whole file after the change. Report EVERYTHING questionable, not only high-severity: suppression in any form (ts-ignore, expect-error, any, unknown-casts), semantics changed (does the test still assert what its filename and describe blocks claim - reduced-motion behaviour?), scope creep beyond the one file (check git -C ${REPO} status --porcelain), dead code left behind. Do not fix anything yourself.
Return: verdict GOOD or BAD, the verbatim tsc output you observed, and the list of findings with evidence.`,
  { label: 'verify:tsc-fix', phase: 'Verify fix' }
)

phase('Add tools')
const deps = await agent(
  `Repo: ${REPO}. Working dir for everything: ${REPO}\\apps\\api (uv-managed Python project, existing .venv). Add FOUR dev dependencies for a test-quality battery, then prove they run.
1. Edit pyproject.toml [project.optional-dependencies] dev list only: add pinned "ruff" (pin the exact current version uv resolves), and "lizard", "import-linter", "mutmut" (>= pins fine). Touch nothing else in the file.
2. Run EXACTLY: uv sync --extra dev --extra pipeline
   BOTH extras are mandatory: --extra dev alone UNINSTALLS docling (a ~3 GB reinstall mistake, documented in CLAUDE.md). This command may take minutes.
3. Prove each tool runs from the project venv, capturing verbatim output: .venv\\Scripts\\ruff.exe --version ; .venv\\Scripts\\python.exe -m lizard --version ; .venv\\Scripts\\lint-imports.exe --help (first lines) ; .venv\\Scripts\\mutmut.exe --version (or python -m equivalents if the exe name differs - report what actually worked).
4. Confirm docling survived: .venv\\Scripts\\python.exe -c "import docling; print(docling.__version__)"
Only pyproject.toml and uv.lock may change (git status --porcelain to confirm). Do NOT run the test suite. Do not commit.
Return: the pyproject diff, each verbatim version output, the docling check output, and git status --porcelain.`,
  { label: 'deps:battery-tools', phase: 'Add tools', model: 'opus' }
)

phase('Baselines')
const baselines = await agent(
  `Repo: ${REPO}, working dir ${REPO}\\apps\\api. Tools ruff/lizard/import-linter/mutmut are installed in .venv (installer's report: <report>${deps}</report>). Adopt them MEASURE-FIRST: every threshold you write must be a number you observed, never an aspiration. A gate introduced at a value the repo already fails is forbidden - floors pass today by construction.
1. MEASURE with lizard over app\\: run .venv\\Scripts\\python.exe -m lizard app -C 10 and -C 15; record: count of functions with CCN>10, CCN>15, the single worst CCN and its function, count of functions >60 lines and the worst. Also measure module sizes: count .py files under app\\ over 200 and over 400 lines (a one-line PowerShell or python snippet). Record all numbers verbatim.
2. MEASURE ruff: .venv\\Scripts\\ruff.exe check app  (pyproject already has [tool.ruff] config). Record the verbatim summary. If violations exist, do NOT fix code and do NOT loosen config in ways that mask new code - record the count.
3. import-linter: check what contract types this installed version supports (lint-imports --help / its docs module). If a cycle/acyclicity check is available, configure [tool.importlinter] in pyproject with root_package = "app" and that contract; run it; record verbatim. If only layers-style contracts exist, derive the layering FROM the observed import direction (e.g. app.api imports app.services imports app.worker?) and only write a contract that PASSES as measured; if no honest passing contract exists, write the config with root_package and NO contract plus a comment naming why, and record that as the observation.
4. mutmut: add [tool.mutmut] with paths_to_mutate = "app/" and the test command; run only .venv\\Scripts\\mutmut.exe --version. NEVER start a mutation run - it is differential-only on this 4 GB box.
5. WIRE: check whether make exists (make --version). Regardless of the answer, create scripts\\gates.py (stdlib only, no deps) with two modes: "python scripts/gates.py fast" = ruff check app + lint-imports (if a contract exists) + pytest tests/unit -q --collect-only; "python scripts/gates.py full" = fast plus lizard with the thresholds set AT your measured floors (invocation flags that pass today) + pytest tests/unit -q. Each step prints its command, streams output, stops at first nonzero exit. If apps/api/Makefile already exists, READ it first and report what is in it - do not overwrite existing targets; add gates/gates-fast targets delegating to scripts/gates.py only if make actually runs on this box.
6. TIME the fast mode: run .venv\\Scripts\\python.exe scripts\\gates.py fast once and record wall time and verbatim tail. It must stay well under 150s; if it does not, report the time and which step dominates - do not silently trim.
Allowed changes: pyproject.toml ([tool.importlinter], [tool.mutmut] only), scripts\\gates.py, optionally Makefile additions. git status --porcelain at the end. Do not commit.
Return: every measured number with the command that produced it, the configs you wrote, the fast-mode wall time and tail, and git status.`,
  { label: 'baselines:floors', phase: 'Baselines', model: 'opus' }
)

phase('Verify battery')
const batteryVerdict = await agent(
  `Repo: ${REPO}, working dir ${REPO}\\apps\\api. A junior adopted a test-quality battery measure-first. Claims: <report>${baselines}</report>
Verify adversarially - re-measure, take nothing on trust, and report everything you find, not only high severity:
1. Re-run their fast mode verbatim (.venv\\Scripts\\python.exe scripts\\gates.py fast), capture output and wall time.
2. Spot re-measure TWO of their numbers with your own commands (e.g. worst CCN via lizard, ruff violation count) and compare to their claims.
3. VACUITY PROBE with strict restore hygiene: create ${REPO}\\apps\\api\\app\\_probe_gate_vacuity.py containing one function with obvious CCN far above their lizard floor AND a blatant ruff violation (e.g. unused import). Run the full mode (or the lizard and ruff steps directly): it MUST go red - a battery that stays green over that file is decorative, and that finding outranks everything else. Then DELETE the probe file, re-run the failing step to observe green again, and show git status --porcelain is clean. The probe never gets committed.
4. Check the import-linter contract is not vacuous: does it actually match modules (their config vs app\\ layout)? A contract over zero modules passes forever.
5. Check scripts/gates.py stops on first failure (read the code) and that no step invokes a mutation run or the network.
Return: verdict SOUND or list of defects with evidence, the verbatim probe red-then-green output, both re-measured numbers vs claimed, and final git status.`,
  { label: 'verify:battery', phase: 'Verify battery' }
)

return {
  fix, fixVerdict: fixVerdict,
  deps: typeof deps === 'string' ? deps.slice(0, 4000) : deps,
  baselines: typeof baselines === 'string' ? baselines.slice(0, 6000) : baselines,
  batteryVerdict,
}