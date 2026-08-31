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


def test_the_beat_never_scales():
    payload = tomllib.loads((_ROOT / "railway.beat.toml").read_text(encoding="utf-8"))
    assert payload["deploy"]["numReplicas"] == 1, (
        "two beats enqueue every schedule twice"
    )
