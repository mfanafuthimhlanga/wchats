"""Every nightly job supplies every setting that has no default (#147).

On 2026-09-02 the nightly workflow had failed on every scheduled run since at
least 2026-08-29, for two reasons that nothing in the repo could catch. The
E2E job read seven repository secrets the repository did not hold, so
`NEON_API_KEY` reached the job empty and Neon answered `not authenticated`.
The eval job never set `PLATFORM_CREDENTIAL_KEY`, `JWT_SECRET` or
`CLERK_WEBHOOK_SIGNING_SECRET` at all, and because it starts uvicorn outside
pytest nothing filled them, so `Settings` raised at import, the readiness loop
ran out without failing its step, and "Capture eval responses" failed against
a server that never started.

Why the required set is derived, never listed
---------------------------------------------
The same reason `test_env_example_covers_required_settings.py` gives: a hand
list drifts the first time somebody adds a no-default field. The set comes
from `Settings.model_fields[...].is_required()` at test time.

What counts as supplied
-----------------------
A key in the job's `env:` block, or a name a step writes to `$GITHUB_ENV` with
`echo "NAME=..."`. A secret reference counts as supplied here, because whether
the repository holds the secret is not a fact of the file; the reader of the
credential-locations note (`.dev/reference/260902-credential-locations.md`)
checks that. What this test pins is that the workflow asks for everything the
process needs, and only reaches for a secret where real spend is involved.
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


def test_only_spend_bearing_names_come_from_secrets() -> None:
    """Every secret the workflow reads is one an owner must set for a reason."""
    text = NIGHTLY.read_text(encoding="utf-8")
    read = set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", text))
    allowed = {
        "NEON_API_KEY_TEST",  # provisions real Neon projects
        "VOYAGE_API_KEY",  # real embeddings
        "OPENAI_API_KEY",  # the Judges and the Agent turn
        "EVAL_DEMO_AGENT_ID",  # the agent the capture script drives
        "EVAL_DEMO_API_KEY",  # its tenant key
    }
    assert read <= allowed, (
        f"nightly.yml reads secrets that a fresh job could generate or literal "
        f"instead: {sorted(read - allowed)}"
    )


def test_the_readiness_wait_fails_its_own_step() -> None:
    """A loop that runs out must fail where the cause is visible (FM-013)."""
    job = workflow()["jobs"]["eval-full"]
    waits = [s for s in job["steps"] if str(s.get("name", "")).startswith("Wait for API")]
    assert len(waits) == 1, "the eval-full job has exactly one readiness wait step"
    assert "exit 1" in waits[0]["run"], (
        "the readiness loop runs out without failing the step, so the next step "
        "fails on a symptom while the boot error scrolls past"
    )
