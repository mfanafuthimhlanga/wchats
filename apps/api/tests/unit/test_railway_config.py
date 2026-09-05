"""The four Railway service files parse, and each names its own job.

PR #122's body claimed this test; the ticket-18 review found it absent. The
tomls are the only place the queue-to-service mapping lives, so a typo in a
startCommand would otherwise be caught by nothing before a deploy.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

_SERVICES = {
    "railway.api.toml": ("Dockerfile", "uvicorn"),
    "railway.worker-runtime.toml": ("Dockerfile", "--queues=runtime"),
    "railway.worker-pipeline.toml": ("Dockerfile.pipeline", "--queues=pipeline"),
    "railway.beat.toml": ("Dockerfile", "beat"),
}


@pytest.mark.parametrize("name", sorted(_SERVICES))
def test_the_service_file_parses_and_names_its_job(name):
    payload = tomllib.loads((_ROOT / name).read_text(encoding="utf-8"))
    dockerfile, start_fragment = _SERVICES[name]
    assert payload["build"]["builder"] == "DOCKERFILE"
    assert payload["build"]["dockerfilePath"] == dockerfile
    assert start_fragment in payload["deploy"]["startCommand"], (
        f"{name} must start {start_fragment!r}: {payload['deploy']['startCommand']!r}"
    )


def test_the_two_workers_split_the_two_queues():
    """Every queue is consumed by exactly one service; a swap or a typo here
    is an outage the deploy log would not explain."""
    commands = {
        name: tomllib.loads((_ROOT / name).read_text(encoding="utf-8"))["deploy"][
            "startCommand"
        ]
        for name in _SERVICES
    }
    assert "--queues=runtime" not in commands["railway.worker-pipeline.toml"]
    assert "--queues=pipeline" not in commands["railway.worker-runtime.toml"]


def test_the_api_service_migrates_both_databases_before_it_serves():
    """The pre-deploy step is where a release meets its schema.

    It ran the tenant walk alone, so a merge carrying a control migration
    shipped code against a control schema nothing had upgraded: staging was at
    0020 on 2026-09-04 with `main` at 0022. `predeploy.py` is the entry point
    that runs the control migration first and the fleet second.
    """
    payload = tomllib.loads((_ROOT / "railway.api.toml").read_text(encoding="utf-8"))
    command = payload["deploy"]["preDeployCommand"]
    assert "scripts/predeploy.py" in command, (
        f"the api service's preDeployCommand must run the ordered pair, not one "
        f"half of it: {command!r}"
    )
    assert (_ROOT / "scripts" / "predeploy.py").exists(), (
        "the preDeployCommand names a script that is not in the image"
    )


def test_only_the_api_service_migrates():
    """Four services sharing one pre-deploy command would race each other for
    the same Alembic lock on every database, once per release."""
    for name in sorted(_SERVICES):
        payload = tomllib.loads((_ROOT / name).read_text(encoding="utf-8"))
        has_step = "preDeployCommand" in payload["deploy"]
        assert has_step == (name == "railway.api.toml"), (
            f"{name} {'carries' if has_step else 'is missing'} a "
            f"preDeployCommand; exactly one service may run the migrations"
        )


def test_the_beat_never_scales():
    payload = tomllib.loads((_ROOT / "railway.beat.toml").read_text(encoding="utf-8"))
    assert payload["deploy"]["numReplicas"] == 1, (
        "two beats enqueue every schedule twice"
    )
