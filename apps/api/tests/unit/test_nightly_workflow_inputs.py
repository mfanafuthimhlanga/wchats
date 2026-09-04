"""Every nightly job supplies every setting that has no default (#147).

The nightly's eval job starts uvicorn outside pytest, so nothing fills a setting the
workflow leaves unset, and `Settings` raises at import. The E2E job reads repository
secrets, and a secret the repository does not hold arrives as an empty string. This
file pins what the workflow asks for. Whether the repository holds a secret is not a
fact of the file; `.dev/reference/260902-credential-locations.md` records that.

The required set is derived from `Settings.model_fields[...].is_required()` at test
time, for the reason `test_env_example_covers_required_settings.py` gives. A hand list
drifts the first time somebody adds a field.

A name counts as supplied when it is a key in the job's `env:` block or a step writes
it to `$GITHUB_ENV` with `echo "NAME=..."`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from app.core.config import Settings

#: `apps/api/tests/unit/` sits four levels below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
NIGHTLY = REPO_ROOT / ".github" / "workflows" / "nightly.yml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

GITHUB_ENV_WRITE = re.compile(r'echo "([A-Z][A-Z0-9_]*)=')


def required_settings() -> set[str]:
    return {name for name, field in Settings.model_fields.items() if field.is_required()}


def workflow(path: Path = NIGHTLY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def step(path: Path, job_id: str, name: str) -> dict:
    steps = [s for s in workflow(path)["jobs"][job_id]["steps"] if s.get("name") == name]
    assert len(steps) == 1, f"{path.name} job {job_id!r} has {len(steps)} steps named {name!r}"
    return steps[0]


def names_supplied(job: dict) -> set[str]:
    """Keys in the job's env block plus names its steps write to $GITHUB_ENV."""
    supplied = set((job.get("env") or {}).keys())
    for step in job.get("steps") or []:
        supplied.update(GITHUB_ENV_WRITE.findall(step.get("run") or ""))
    return supplied


@pytest.mark.parametrize("job_id", ["e2e-neon", "eval-full"])
def test_each_job_supplies_every_required_setting(job_id: str) -> None:
    job = workflow()["jobs"][job_id]
    missing = required_settings() - names_supplied(job)
    assert not missing, (
        f"nightly.yml job {job_id!r} leaves these no-default settings unset, so the "
        f"API and the worker cannot boot there: {sorted(missing)}"
    )


def test_the_revoked_anthropic_key_is_not_read() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    assert "secrets.ANTHROPIC_API_KEY" not in text, (
        "the provider is OpenAI (ADR 0008) and the Anthropic key was revoked on "
        "2026-08-27; nightly.yml still reads it"
    )


SPEND_BEARING = {
    "NEON_API_KEY_TEST",  # provisions real Neon projects
    "VOYAGE_API_KEY",  # real embeddings
    "OPENAI_API_KEY",  # the Judges and the Agent turn
}

#: Read by the eval job for capture_responses.py. Neither carries spend, and neither
#: can name an agent in the job's own fresh database, which #147 records as the eval
#: job's open design gap. Named here so the test states them rather than blessing them.
EVAL_DEMO = {"EVAL_DEMO_AGENT_ID", "EVAL_DEMO_API_KEY"}


def test_secrets_are_read_for_spend_and_nothing_else() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    read = set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", text))
    assert SPEND_BEARING <= read, (
        f"nightly.yml stopped reading a spend-bearing secret: {sorted(SPEND_BEARING - read)}"
    )
    assert read <= SPEND_BEARING | EVAL_DEMO, (
        f"nightly.yml reads secrets that a fresh job could generate or literal "
        f"instead: {sorted(read - SPEND_BEARING - EVAL_DEMO)}"
    )


def test_the_readiness_wait_fails_its_own_step() -> None:
    """A loop that runs out must fail where the cause is visible (FM-013)."""
    job = workflow()["jobs"]["eval-full"]
    waits = [s for s in job["steps"] if str(s.get("name", "")).startswith("Wait for")]
    assert len(waits) == 1, "the eval-full job has exactly one readiness wait step"
    assert "exit 1" in waits[0]["run"], (
        "the readiness loop runs out without failing the step, so the next step "
        "fails on a symptom while the boot error scrolls past"
    )


#: The environment that holds NEON_API_KEY_TEST, OPENAI_API_KEY and VOYAGE_API_KEY.
#: Spaces and a slash are part of the name.
STAGING_ENVIRONMENT = "wchats / staging"


@pytest.mark.parametrize("job_id", ["e2e-neon", "eval-full"])
def test_each_job_declares_the_environment_that_holds_its_secrets(job_id: str) -> None:
    """GitHub hands an environment's secrets only to a job that names it (#147).

    Both jobs read three secrets that exist only on `wchats / staging`, and a job
    without this key receives an empty string for each one. That is what the E2E
    run reported as Neon "not authenticated" on every scheduled run from at least
    2026-08-29 to 2026-09-02.
    """
    job = workflow()["jobs"][job_id]
    assert job.get("environment") == STAGING_ENVIRONMENT, (
        f"nightly.yml job {job_id!r} declares environment {job.get('environment')!r}, "
        f"so the {sorted(SPEND_BEARING)} secrets arrive empty"
    )


#: The step that decides whether this job has anything to drive.
RESOLVE_STEP = "Resolve the eval target"

#: The three names, and the environment variable each one arrives as.
TARGET_NAMES = [
    ("AGENT_BASE_URL", "STAGING_AGENT_BASE_URL"),
    ("AGENT_ID", "EVAL_DEMO_AGENT_ID"),
    ("API_KEY", "EVAL_DEMO_API_KEY"),
]

PRESENT = {
    "AGENT_BASE_URL": "https://staging.example.test",
    "AGENT_ID": "agent-0000",
    "API_KEY": "vrd_key_0000",
}


class ResolveRun(NamedTuple):
    """What the resolve step's script did: its exit, its log, and what it published."""

    returncode: int
    stdout: str
    stderr: str
    #: Names the script appended to the `$GITHUB_ENV` file, parsed. Every later
    #: step in the job reads these, so this is the value that actually travels.
    published: dict[str, str]


def run_resolve(**overrides: str) -> ResolveRun:
    """Execute the resolve step's OWN script, under the shell GitHub gives it.

    Reading the YAML for a substring says the file mentions `exit 1`. Running the
    script says what it does with a value, which is what all three defects here
    were about: an empty variable that exits 0, a whitespace variable that reads
    as present, and a value validated stripped but handed onward raw.
    ubuntu-latest runs a `run:` block as `bash --noprofile --norc -eo pipefail`,
    so that is the shell used here, and `$GITHUB_ENV` points at a real file so the
    script's writes to it can be read back the way the runner reads them.
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH, so the step's own script cannot be executed here")
    with tempfile.TemporaryDirectory() as directory:
        github_env = Path(directory) / "github_env"
        github_env.write_text("", encoding="utf-8")
        env = {**os.environ, **PRESENT, **overrides, "GITHUB_ENV": str(github_env)}
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c",
             step(NIGHTLY, "eval-full", RESOLVE_STEP)["run"]],
            env=env,
            capture_output=True,
            text=True,
        )
        written = github_env.read_text(encoding="utf-8")
    published = dict(
        line.split("=", 1) for line in written.splitlines() if "=" in line
    )
    return ResolveRun(result.returncode, result.stdout, result.stderr, published)


def test_the_eval_job_drives_staging() -> None:
    """The eval job's own database can never hold the agent its secret names (#147).

    `capture_responses.py` needs a provisioned, ingested agent. The Postgres
    service beside this job is migrated at job start and holds no tenant and no
    agent, so `EVAL_DEMO_AGENT_ID` names nothing there whatever the secret holds.
    """
    job = workflow()["jobs"]["eval-full"]
    assert job["env"]["AGENT_BASE_URL"] == "${{ vars.STAGING_AGENT_BASE_URL }}", (
        "the eval job points AGENT_BASE_URL back at its own container, which holds "
        "no provisioned agent"
    )
    assert not [s for s in job["steps"] if "uvicorn" in str(s.get("run", ""))], (
        "the eval job starts an API of its own again; it drives staging, and a "
        "local server here only hides which target the capture actually reached"
    )


def test_a_resolvable_target_lets_the_job_run() -> None:
    """All three present is the only case that continues."""
    result = run_resolve()
    assert result.returncode == 0, result.stdout + result.stderr
    assert PRESENT["AGENT_BASE_URL"] in result.stdout


@pytest.mark.parametrize("variable, published_name", TARGET_NAMES)
def test_a_missing_name_fails_the_job_and_says_which(variable: str, published_name: str) -> None:
    """A job that cannot drive anything reports red, never a green check.

    WHAT WOULD HAVE GONE WRONG WITHOUT IT, and the tense is deliberate. A draft
    of this step earlier in the same branch published `ready=0`, exited 0 and let
    every downstream step skip, and GitHub reports a skipped step as a success,
    so the job conclusion would have been `success` over zero evals. Review
    caught it before it merged, so no run ever behaved that way: `main` carries
    no resolve step at all, and the last eight scheduled runs each concluded
    `failure`, the most recent at "Wait for API to be ready". None of
    `STAGING_AGENT_BASE_URL`, `EVAL_DEMO_AGENT_ID` or `EVAL_DEMO_API_KEY` exists
    on this repository or on the `wchats / staging` environment, so it would have
    been every scheduled run from that merge onward. It is the same false reading
    `scripts/assert_tests_ran.py` forbids one step down: a gate over zero
    observations is unknown, never pass.
    """
    result = run_resolve(**{variable: ""})

    assert result.returncode == 1, (
        f"{variable} is unset and the step exited {result.returncode}. Every "
        f"downstream step then skips and the job reports success over zero evals."
        f"\n{result.stdout}{result.stderr}"
    )
    assert published_name in result.stdout, (
        f"the step failed without naming {published_name}, so the log does not say "
        f"which of the three to go and set:\n{result.stdout}"
    )
    for _, other in TARGET_NAMES:
        if other != published_name:
            assert other not in result.stdout, (
                f"the step names {other} as missing when only {published_name} is"
            )


@pytest.mark.parametrize("variable, published_name", TARGET_NAMES)
def test_a_blank_name_counts_as_missing(variable: str, published_name: str) -> None:
    """`[ -z "$VAR" ]` is false for "   ", and blank is not a target.

    A whitespace-only variable read as present in the draft of this step, which
    published `ready=1` and printed `Driving    `. The wait step would then have
    curled `   /health` for sixty seconds and failed on the symptom, which is the
    failure shape this step exists to prevent.
    """
    result = run_resolve(**{variable: "   "})

    assert result.returncode == 1, (
        f"{variable} holds whitespace and the step accepted it as a target:"
        f"\n{result.stdout}{result.stderr}"
    )
    assert published_name in result.stdout
    assert not result.published, (
        f"the step rejected {variable} and still published {sorted(result.published)} "
        f"to $GITHUB_ENV, which later steps would read"
    )


#: The step whose curl is where an untrimmed target shows up as a symptom.
WAIT_STEP = "Wait for the eval target to be ready"


@pytest.mark.parametrize("variable", [name for name, _ in TARGET_NAMES])
def test_the_validated_value_is_the_one_later_steps_read(variable: str) -> None:
    """The step trims a value to validate it, so it must hand the trimmed one on.

    WHAT WENT WRONG WITHOUT IT. `tr -d '[:space:]'` ran only inside the emptiness
    test and the raw variable travelled onward untouched. A secret pasted with a
    trailing newline is the ordinary way that happens: the step exits 0, prints
    `Driving   https://...  `, and the wait step then curls
    `"  https://...  /health"`, where curl exits 3 on a malformed URL sixty
    seconds later. Dying there on the symptom is exactly what this step exists to
    prevent, so validating a stripped value and publishing a raw one keeps the
    whole defect while looking fixed.
    """
    padded = f"  {PRESENT[variable]}\n"
    result = run_resolve(**{variable: padded})

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.published.get(variable) == PRESENT[variable], (
        f"the step validated a stripped {variable} and published "
        f"{result.published.get(variable)!r} to $GITHUB_ENV, so every later step "
        f"reads the untrimmed value the check was never applied to"
    )
    assert f"Driving {PRESENT['AGENT_BASE_URL']}" in result.stdout, (
        f"the log line still carries the untrimmed value:\n{result.stdout}"
    )
    # And the step that reads it. The publish is only worth anything because the
    # wait step interpolates this name into the URL it curls.
    assert "${AGENT_BASE_URL}/health" in step(NIGHTLY, "eval-full", WAIT_STEP)["run"]


#: Steps of `eval-full` that need no staging target. They are the only signal a
#: scheduled nightly still produces while STAGING_AGENT_BASE_URL does not exist,
#: so every one of them must run before the resolve step can fail the job.
UNGATED_SIGNAL = [
    "Install Chromium headless shell for the widget",
    "Build widget (required for D7)",
    "Run control DB migration",
]


def test_the_resolve_step_gates_only_the_steps_that_need_a_target() -> None:
    """It sits immediately before the wait, and NOT at the top of the job.

    Failing at position two is the obvious optimisation and it costs the job its
    only independent signal. The widget build runs
    `scripts/check-rendered-notice.mjs` and the size check, and the migration runs
    `alembic upgrade head` against pgvector; neither needs a staging target, and
    with the resolve step exiting 1 ahead of them a widget size regression or a
    broken migration stops being visible anywhere until #57 lands. The three
    steps below the resolve step are the only ones that cannot run without a
    target, so that is where it belongs.
    """
    names = [str(s.get("name")) for s in workflow()["jobs"]["eval-full"]["steps"]]
    resolve = names.index(RESOLVE_STEP)

    lost = [name for name in UNGATED_SIGNAL if names.index(name) > resolve]
    assert not lost, (
        f"the resolve step now runs before {lost}, so a nightly with no staging "
        f"target stops exercising them and the job produces no signal at all"
    )
    assert names[resolve + 1] == WAIT_STEP, (
        f"the resolve step is followed by {names[resolve + 1]!r}, so it no longer "
        f"sits immediately before the steps it gates"
    )


def test_no_step_gates_itself_on_whether_a_target_was_found() -> None:
    """The skip mechanism is gone, not disabled. A future edit may not restore it.

    An `if:` gate reading the resolve step is how the green-over-nothing shape was
    built: the step exits 0, the gates evaluate false, and GitHub reports every
    skipped step as a success. Banning `if:` outright is the wrong pin, because
    `if: always()` on a teardown is legitimate and the `e2e-neon` job in this same
    file already carries one. What may not come back is a condition that reads
    whether a target was found, whether from `steps.<id>.outputs` or from a name
    the resolve step writes to `$GITHUB_ENV`.
    """
    job = workflow()["jobs"]["eval-full"]
    gated = {
        str(s.get("name")): str(s["if"])
        for s in job["steps"]
        if s.get("if") and ("steps." in str(s["if"]) or "env." in str(s["if"]))
    }
    assert not gated, (
        f"these eval steps skip themselves instead of the job failing: {gated}"
    )
    resolve = step(NIGHTLY, "eval-full", RESOLVE_STEP)
    assert "$GITHUB_OUTPUT" not in resolve["run"], (
        "the resolve step publishes an output again, and the only thing an output "
        "here has ever been used for is skipping the steps that do the work"
    )
    assert "id" not in resolve, (
        "the resolve step carries an id again, and `steps.<id>` is the context a "
        "skip gate needs to name it"
    )


#: Every step in either workflow that runs the eval harness. Both are named
#: after checks they claim to perform, which is what makes an empty run there a
#: false reading rather than a quiet one.
EVAL_STEPS = [
    (CI, "eval-deterministic", "Run deterministic evals (D3, D5, D6, D7, G-06)"),
    (NIGHTLY, "eval-full", "Run full eval suite (LLM-judged D1/D2/D3/D4/D8 + deterministic)"),
]


@pytest.mark.parametrize("path, job_id, name", EVAL_STEPS, ids=lambda v: getattr(v, "name", v))
def test_an_eval_step_that_asserted_nothing_cannot_report_a_pass(
    path: Path, job_id: str, name: str
) -> None:
    """A gate over zero observations is unknown, never pass (#102).

    Run 33150722736 reported a green check over "1 skipped, 1 deselected in
    0.08s". `run_evals.py` is right to skip an unmeasured dimension; the workflow
    then turned that skip back into a pass one layer up. Two flags and one
    assertion close it: `-rs` so the log states which guard fired, and
    `scripts/assert_tests_ran.py` over the JUnit report so a run with nothing
    passed exits 1.
    """
    run = step(path, job_id, name)["run"]
    assert " -rs" in run, "a skip whose reason never prints leaves the next question unanswerable"
    assert "--junitxml=" in run, "nothing writes the report the assertion reads"
    assert "scripts/assert_tests_ran.py" in run, (
        "the step reports pytest's exit code alone, and pytest exits 0 on a run "
        "where every test skipped"
    )
