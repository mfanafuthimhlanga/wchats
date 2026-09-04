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
from pathlib import Path

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


def run_resolve(**overrides: str) -> subprocess.CompletedProcess:
    """Execute the resolve step's OWN script, under the shell GitHub gives it.

    Reading the YAML for a substring says the file mentions `exit 1`. Running the
    script says what it does with a value, which is what both defects here were
    about: an empty variable that exits 0, and a whitespace variable that reads as
    present. ubuntu-latest runs a `run:` block as
    `bash --noprofile --norc -eo pipefail`, so that is the shell used here.
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH, so the step's own script cannot be executed here")
    env = {**os.environ, **PRESENT, **overrides}
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c",
         step(NIGHTLY, "eval-full", RESOLVE_STEP)["run"]],
        env=env,
        capture_output=True,
        text=True,
    )


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

    WHAT WENT WRONG WITHOUT IT. The step published `ready=0`, exited 0, and every
    downstream step skipped, so the job conclusion was `success` over zero evals.
    None of `STAGING_AGENT_BASE_URL`, `EVAL_DEMO_AGENT_ID` or `EVAL_DEMO_API_KEY`
    exists on this repository or on the `wchats / staging` environment, so that
    was every scheduled run. It is the same false reading
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

    A whitespace-only variable used to publish `ready=1` and print `Driving    `.
    The wait step then curled `   /health` for sixty seconds and failed on the
    symptom, which is the failure shape this step exists to prevent.
    """
    result = run_resolve(**{variable: "   "})

    assert result.returncode == 1, (
        f"{variable} holds whitespace and the step accepted it as a target:"
        f"\n{result.stdout}{result.stderr}"
    )
    assert published_name in result.stdout


def test_the_resolve_step_runs_before_the_toolchains() -> None:
    """Nothing is installed before the job knows it has something to drive.

    This step used to sit twelfth of sixteen. Python, pnpm, Node 22, a Chromium
    headless shell, two `pnpm install` runs, a widget build and a control-DB
    migration all ran first, and then the job discovered there was no target.
    """
    names = [str(s.get("name")) for s in workflow()["jobs"]["eval-full"]["steps"]]
    assert names[0] == "Checkout"
    assert names[1] == RESOLVE_STEP, (
        f"the eval job does {names[1:names.index(RESOLVE_STEP)]} before it checks "
        f"whether it has a target at all"
    )


def test_no_step_skips_itself_on_a_readiness_flag() -> None:
    """The skip mechanism is gone, not disabled. A future edit may not restore it.

    An `if:` gate on a resolve-step output is how the green-over-nothing run was
    built: the step exits 0, the gates evaluate false, and GitHub reports every
    skipped step as a success.
    """
    job = workflow()["jobs"]["eval-full"]
    gated = {str(s.get("name")): s["if"] for s in job["steps"] if s.get("if")}
    assert not gated, (
        f"these eval steps skip themselves instead of the job failing: {gated}"
    )
    resolve = step(NIGHTLY, "eval-full", RESOLVE_STEP)
    assert "$GITHUB_OUTPUT" not in resolve["run"], (
        "the resolve step publishes an output again, and the only thing an output "
        "here has ever been used for is skipping the steps that do the work"
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
