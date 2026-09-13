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

_WIZARD = _ROOT / "scripts" / "railway_staging_wizard.sh"

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


def _service_fields_body() -> str:
    """The wizard's `service_fields` function, source text only."""
    text = _WIZARD.read_text(encoding="utf-8")
    body = text[text.index("service_fields() {") :]
    return body[: body.index("\n}\n")]


def test_the_wizard_dictates_every_field_the_tomls_carry():
    """`#139`. Railway closed Config-as-code to services created after
    2026-08-28, so a service may be unable to read its toml at all and the
    operator types the fields in instead. A field the wizard does not name is a
    field that never reaches Railway, and the deploy log does not explain it.
    """
    body = _service_fields_body()
    keys: set[str] = set()
    for name in sorted(_SERVICES):
        payload = tomllib.loads((_ROOT / name).read_text(encoding="utf-8"))
        keys |= set(payload["build"]) | set(payload["deploy"])

    missing = sorted(key for key in keys if f'"$file" {key}' not in body)
    assert not missing, (
        f"the tomls carry {missing!r} and the wizard's manual path never names "
        f"them, so an operator following it leaves those fields unset"
    )


@pytest.mark.parametrize("name", sorted(_SERVICES))
def test_every_service_stage_offers_the_manual_path(name):
    """One helper per service, given that service's file. A stage that still
    told the operator only to set a Config-as-code path would strand a service
    Railway refuses to opt in."""
    text = _WIZARD.read_text(encoding="utf-8")
    assert f"service_config {name} " in text, (
        f"the wizard has no stage running service_config for {name}"
    )


def test_the_beat_never_scales():
    payload = tomllib.loads((_ROOT / "railway.beat.toml").read_text(encoding="utf-8"))
    assert payload["deploy"]["numReplicas"] == 1, (
        "two beats enqueue every schedule twice"
    )


_IDLE_FLAGS = ("--without-gossip", "--without-mingle", "--without-heartbeat")


@pytest.mark.parametrize("name", ["railway.worker-runtime.toml", "railway.worker-pipeline.toml"])
def test_an_idle_worker_sends_no_heartbeat_gossip_or_mingle(name):
    """#237: the 2 s heartbeat was 30 of the 87 Redis commands an idle worker sent
    a minute, and Upstash bills per command. The two workers sit on separate
    queues and never need to see each other, so gossip and mingle buy nothing."""
    command = tomllib.loads((_ROOT / name).read_text(encoding="utf-8"))["deploy"]["startCommand"]
    missing = [flag for flag in _IDLE_FLAGS if flag not in command]
    assert missing == [], f"{name} start command lacks {missing}"


def test_beat_keeps_its_start_command_unflagged():
    command = tomllib.loads((_ROOT / "railway.beat.toml").read_text(encoding="utf-8"))["deploy"]["startCommand"]
    assert not any(flag in command for flag in _IDLE_FLAGS), "beat is not a worker; the flags are unknown to it"


def test_an_idle_worker_polls_every_ten_seconds_not_every_second():
    """The other 57 of the 87. A blocking BRPOP still returns the moment a task
    lands, so this bounds reissues on an idle queue, not pickup latency."""
    from app.worker.celery_app import BROKER_POLLING_INTERVAL_S, celery_app

    assert BROKER_POLLING_INTERVAL_S == 10
    assert type(BROKER_POLLING_INTERVAL_S) is int, (
        "a float reaches BRPOP as '10.0' and a pre-6 Redis kills the worker on its first poll"
    )
    assert celery_app.conf.broker_transport_options["polling_interval"] == BROKER_POLLING_INTERVAL_S
