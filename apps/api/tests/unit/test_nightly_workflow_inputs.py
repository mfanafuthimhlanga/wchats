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

import re
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings

#: `apps/api/tests/unit/` sits four levels below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
NIGHTLY = REPO_ROOT / ".github" / "workflows" / "nightly.yml"

GITHUB_ENV_WRITE = re.compile(r'echo "([A-Z][A-Z0-9_]*)=')


def required_settings() -> set[str]:
    return {name for name, field in Settings.model_fields.items() if field.is_required()}


def workflow() -> dict:
    return yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))


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


TARGET_GATE = "steps.target.outputs.ready == '1'"


def test_the_eval_job_drives_staging_and_skips_when_staging_is_unset() -> None:
    """The eval job's own database can never hold the agent its secret names (#147).

    `capture_responses.py` needs a provisioned, ingested agent. The Postgres
    service beside this job is migrated at job start and holds no tenant and no
    agent, so `EVAL_DEMO_AGENT_ID` names nothing there whatever the secret holds.
    The target is staging, and absent the staging variable the job says so and
    reports nothing rather than failing inside the capture script.
    """
    job = workflow()["jobs"]["eval-full"]
    assert job["env"]["AGENT_BASE_URL"] == "${{ vars.STAGING_AGENT_BASE_URL }}", (
        "the eval job points AGENT_BASE_URL back at its own container, which holds "
        "no provisioned agent"
    )

    steps = job["steps"]
    resolve = [s for s in steps if s.get("id") == "target"]
    assert len(resolve) == 1, "the eval-full job has exactly one target-resolution step"
    run = resolve[0]["run"]
    assert "ready=0" in run and "ready=1" in run, (
        "the resolution step publishes no ready output, so nothing downstream can skip"
    )
    assert "SKIPPING" in run, "a skip that prints no reason is indistinguishable from a pass"

    gated = {str(s.get("name")) for s in steps if s.get("if") == TARGET_GATE}
    needs_an_agent = {
        "Capture eval responses",
        "Run full eval suite (LLM-judged D1/D2/D3/D4/D8 + deterministic)",
        "Wait for the eval target to be ready",
    }
    assert needs_an_agent <= gated, (
        f"these steps drive an agent that may not exist and are not gated on "
        f"{TARGET_GATE}: {sorted(needs_an_agent - gated)}"
    )

    assert not [s for s in steps if "uvicorn" in str(s.get("run", ""))], (
        "the eval job starts an API of its own again; it drives staging, and a "
        "local server here only hides which target the capture actually reached"
    )
