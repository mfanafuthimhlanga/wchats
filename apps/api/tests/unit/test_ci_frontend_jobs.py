"""CI runs every front-end gate the two workspaces define (#181).

Until this file existed `ci.yml` had five jobs and all five were Python, so a
green tick on a branch that changed only TypeScript and CSS meant the backend
suite had passed. PR #174 was exactly that branch: it carried two BLOCKs a
rendering review found by hand, and every check on it was green.

THE GATE LIST IS DERIVED, NOT WRITTEN DOWN HERE. It comes from each workspace's
`package.json` at test time, for the reason `test_nightly_workflow_inputs.py`
gives about required settings: a hand list drifts the first time somebody adds a
script. A script named `check*`, plus `test:unit`, is a gate and has to be run by
its job. `test:e2e` is the one exclusion and it is named below with its reason.

The file is parsed as YAML rather than grepped, so a job that stops existing, a
step that loses its `run`, or a `working-directory` that moves is a failure here
rather than a green run over nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

#: `apps/api/tests/unit/` sits four levels below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: workspace directory -> the job in ci.yml that gates it.
WORKSPACES = {"apps/admin": "admin", "apps/widget": "widget"}

#: The one gate CI deliberately does not run. 135 tests at about 36 minutes with
#: seven known networkidle timeouts (CLAUDE.md, .dev/PRODUCTION-READINESS.md
#: 3.8) is its own decision. Naming it here means a future edit that adds it to
#: ci.yml has to come past this constant rather than past nobody.
EXCLUDED_SCRIPTS = {"test:e2e"}

#: `npm install` / `npm ci` as its own word. "pnpm install" ends in "npm install".
NPM_INSTALL = re.compile(r"(?<![\w-])npm\s+(install|ci)")


def workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def job(job_id: str) -> dict:
    jobs = workflow()["jobs"]
    assert job_id in jobs, (
        f"ci.yml has no {job_id!r} job, so nothing in CI runs that workspace's gates. "
        f"Jobs present: {sorted(jobs)}"
    )
    return jobs[job_id]


def run_text(job_id: str) -> str:
    """Every `run:` block in the job, joined. What the job actually executes."""
    return "\n".join(step.get("run") or "" for step in job(job_id)["steps"])


def scripts(workspace: str) -> dict[str, str]:
    package = json.loads((REPO_ROOT / workspace / "package.json").read_text(encoding="utf-8"))
    return package.get("scripts") or {}


def gate_scripts(workspace: str) -> set[str]:
    """The scripts that are gates: anything named `check*`, plus `test:unit`."""
    named = set(scripts(workspace))
    gates = {name for name in named if name.startswith("check")} | (named & {"test:unit"})
    return gates - EXCLUDED_SCRIPTS


@pytest.mark.parametrize("workspace, job_id", sorted(WORKSPACES.items()))
def test_every_gate_script_is_run_by_its_job(workspace: str, job_id: str) -> None:
    """A `check:` script the repo defines and CI does not run gates nothing."""
    gates = gate_scripts(workspace)
    assert gates, f"{workspace}/package.json defines no gate scripts, which cannot be right"

    text = run_text(job_id)
    # Either `pnpm run <name>` or the script's own command line, because
    # check-size.mjs is invoked directly and postbuild chains three of them.
    missing = sorted(
        name
        for name in gates
        if name not in text and scripts(workspace)[name] not in text
    )
    assert not missing, (
        f"ci.yml job {job_id!r} does not run these {workspace} gates, so a branch "
        f"that breaks one of them still gets a green tick: {missing}"
    )


@pytest.mark.parametrize("workspace, job_id", sorted(WORKSPACES.items()))
def test_each_job_works_in_its_own_workspace(workspace: str, job_id: str) -> None:
    """The scripts only exist there, so a job running at the repo root runs nothing."""
    directory = ((job(job_id).get("defaults") or {}).get("run") or {}).get(
        "working-directory"
    )
    assert directory == workspace, (
        f"ci.yml job {job_id!r} defaults to working-directory {directory!r} rather "
        f"than {workspace!r}"
    )


@pytest.mark.parametrize("job_id", sorted(WORKSPACES.values()))
def test_each_job_installs_from_the_committed_lockfile(job_id: str) -> None:
    """pnpm, and the lockfile, so CI resolves what a developer resolved.

    npm in a pnpm workspace writes a package-lock.json nobody maintains, and an
    unfrozen install silently upgrades past a pinned version, which is how a
    front end passes in CI and fails on the machine that built it.
    """
    text = run_text(job_id)
    assert "pnpm install --frozen-lockfile" in text, (
        f"ci.yml job {job_id!r} does not install with `pnpm install --frozen-lockfile`"
    )
    # The lookbehind is load-bearing: "pnpm install" contains "npm install", and
    # a bare substring test failed this assertion on the correct workflow.
    npm = NPM_INSTALL.search(text)
    assert npm is None, (
        f"ci.yml job {job_id!r} reaches for npm in a pnpm workspace: {npm.group(0)!r}"
    )


#: Every gate that opens a browser, and the job that has to install one for it.
BROWSER_GATES = [("admin", "check:chart-render"), ("widget", "check:rendered-notice")]


@pytest.mark.parametrize("job_id, gate", BROWSER_GATES)
def test_a_job_running_a_browser_gate_installs_a_browser(job_id: str, gate: str) -> None:
    """ubuntu-latest ships no Playwright browser.

    Without this step the gate dies on a missing executable, which reads as a
    broken workflow rather than as the measurement it was supposed to take.
    """
    text = run_text(job_id)
    assert gate in text, f"ci.yml job {job_id!r} no longer runs {gate}"
    assert "install --with-deps chromium-headless-shell" in text, (
        f"ci.yml job {job_id!r} runs {gate}, which opens Chromium, and installs no browser"
    )


@pytest.mark.parametrize("job_id", sorted(WORKSPACES.values()))
def test_each_job_runs_on_node_22(job_id: str) -> None:
    """pnpm 11 refuses to start on Node 20, and vite 8 asks for >=22.12.0."""
    versions = [
        str((step.get("with") or {}).get("node-version"))
        for step in job(job_id)["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-node")
    ]
    assert versions == ["22"], (
        f"ci.yml job {job_id!r} sets up Node {versions} rather than exactly one 22"
    )


def test_the_admin_job_type_checks() -> None:
    """`tsc --noEmit` is a gate and is not a package.json script, so it is named here."""
    assert "tsc --noEmit" in run_text("admin"), (
        "ci.yml's admin job does not type-check, so a TypeScript error reaches main"
    )


def test_the_end_to_end_suite_is_not_run_here() -> None:
    """The exclusion is deliberate and stays deliberate.

    Adding `test:e2e` to CI is a decision about 36 minutes of runtime and seven
    known networkidle timeouts, not a tidy-up. It has to be made on purpose,
    which means past this test.
    """
    text = "\n".join(run_text(job_id) for job_id in WORKSPACES.values())
    assert "test:e2e" not in text and "playwright test\n" not in text, (
        "ci.yml runs the end-to-end suite, which is a 36-minute job with seven "
        "known timeouts. If that is the intention, change this test and say why"
    )


def test_the_widget_job_gates_the_embed_copies_before_anything_writes() -> None:
    """check:embed-sync must precede any step that runs sync-embed in write mode.

    `pnpm run build`'s postbuild ends in `sync-embed.mjs` without `--check`, so a
    job that builds that way rewrites the committed copies in its own checkout
    and every later comparison passes over what the job itself just wrote. The
    job runs `vite build` and then the gates, in that order.
    """
    steps = job("widget")["steps"]
    names = [str(s.get("name")) for s in steps]
    runs = [s.get("run") or "" for s in steps]

    for index, command in enumerate(runs):
        assert "run build" not in command, (
            f"ci.yml's widget job step {names[index]!r} runs the build script, whose "
            "postbuild writes the embed copies. Run `vite build` and the gates "
            "separately, or the embed-sync gate compares the job against itself"
        )

    sync = next(i for i, command in enumerate(runs) if "check:embed-sync" in command)
    build = next(i for i, command in enumerate(runs) if "vite build" in command)
    assert build < sync, (
        "check:embed-sync compares the committed copies against dist/, so the "
        "build has to have happened first"
    )
