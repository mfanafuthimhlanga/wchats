"""Importing an integration module leaves the process-wide keys agreed (#101, #178).

WHAT WENT WRONG WITHOUT IT
    `test_provision_neon_stores_encrypted_connection_string` failed on
    `cryptography.fernet.InvalidToken` on every CI Integration run from the first
    one that reached it (33150052552) through four more on 2026-08-29, and passed
    locally every time it was run alone.

    The mechanism is collection order, not the encryption path:

    1.  `tests/conftest.py` puts a key in `os.environ` before the first `import
        app`, so `settings.NEON_ENCRYPTION_KEY` freezes on that value. Call it K1.
    2.  pytest then IMPORTS every module under `tests/integration/`, including the
        ones whose tests will skip. `test_usage_rollup_e2e.py` used to assign
        `os.environ["NEON_ENCRYPTION_KEY"]` a fresh random value at module scope.
        Call it K2. Its own process keeps K1, because `settings` is already built,
        so the module never noticed.
    3.  A test runs. `_spawn_pipeline_worker` builds the child env as
        `os.environ.copy()`, so the Celery worker starts with K2 and its own
        `settings` freezes there.
    4.  The worker encrypts the connection string with K2. The pytest process
        decrypts with K1. InvalidToken.

    The issue's own leading hypothesis, that the key does not survive
    `subprocess.Popen`, was measured and is dead: it survives. What differs
    between the two processes is WHEN each read the variable.

WHERE THE TWO HALVES LIVE NOW (#178)
    The SOURCE half is `scripts/gates.py`, as the `process-wide keys` step of
    `gates.py static`. It walks every module under `tests/integration/`
    recursively for a module-scope environment write by any spelling, exempting
    `conftest.py`, which pytest loads first and which is the one source these
    values come from. It replaced a column-0 regex that a probe file carrying
    five module-scope rebinds walked straight past. `tests/unit/test_gates.py`
    holds its cases.

    The BEHAVIOURAL half is here, and it is the half that has to survive
    collection order. It used to call `importlib.import_module`, which is a
    `sys.modules` cache hit when anything imported that module first. Under
    `pytest tests/integration/... tests/unit/test_integration_key_isolation.py`
    it passed with the unconditional assignment restored, which is the exact
    order the original defect came from. It runs the import in a FRESH
    SUBPROCESS now, so no cache in this process can answer for it, and the
    subprocess is also what the defect is about: a child that inherits this
    process's environment.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import settings

API_DIR = Path(__file__).resolve().parents[2]

#: Every setting whose value must be identical in the pytest process and in a
#: worker it spawns. Both are read once, at the first `import app` in each process.
PROCESS_WIDE_KEYS = (
    "NEON_ENCRYPTION_KEY",
    "PLATFORM_CREDENTIAL_KEY",
    "CONTROL_DB_URL",
    "CONTROL_DB_SYNC_URL",
)

#: The module the CI failure came from. It is the one that assigned the key, and
#: it is cheap to import: psycopg2 and pytest at module scope, nothing from app.
THE_MODULE_THAT_DID_IT = "test_usage_rollup_e2e"

#: What the child runs. It reports the environment BEFORE and AFTER the import,
#: so a rebind shows up as a changed value rather than as a missing one, and the
#: parent can name the key that moved.
_PROBE = """
import json, os, sys
sys.path.insert(0, {api_dir!r})
KEYS = {keys!r}
before = {{key: os.environ.get(key) for key in KEYS}}
import importlib
importlib.import_module("tests.integration." + {module!r})
after = {{key: os.environ.get(key) for key in KEYS}}
print(json.dumps({{"before": before, "after": after}}))
"""


def _import_in_a_fresh_process(module: str) -> dict:
    """Import one integration module in a child, and report what the environment did.

    The child inherits this process's environment, which is exactly the
    inheritance the defect rides on, and it holds no `sys.modules` entry for the
    module, so the import really runs.
    """
    source = _PROBE.format(api_dir=str(API_DIR), keys=PROCESS_WIDE_KEYS, module=module)
    finished = subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(API_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if finished.returncode != 0:
        pytest.fail(
            "the child could not import tests/integration/%s.py, so this test "
            "proved nothing:\n%s" % (module, finished.stderr[-2000:])
        )
    return json.loads(finished.stdout.strip().splitlines()[-1])


def test_importing_the_rollup_module_rebinds_no_process_wide_key() -> None:
    """The module the CI failure came from leaves every guarded key where it was.

    Run in a child so the result does not depend on whether this process has
    already imported it. The old spelling of this test read `sys.modules` and
    passed with the bug live in any run that collected tests/integration first.
    """
    seen = _import_in_a_fresh_process(THE_MODULE_THAT_DID_IT)

    changed = {
        key: (seen["before"][key], seen["after"][key])
        for key in PROCESS_WIDE_KEYS
        if seen["before"][key] != seen["after"][key]
    }
    assert not changed, (
        f"importing tests/integration/{THE_MODULE_THAT_DID_IT}.py changed {sorted(changed)}, "
        f"so a worker spawned after it encrypts with one value while this process "
        f"decrypts with another (#101): {changed}"
    )


def test_the_child_reports_the_same_keys_this_process_froze() -> None:
    """What the child inherits is what `settings` here was built from.

    The subprocess above is the same inheritance `_spawn_pipeline_worker` uses.
    A key this process holds only in `settings`, and not in the environment, is
    a key the worker never receives at all.
    """
    seen = _import_in_a_fresh_process(THE_MODULE_THAT_DID_IT)

    for key in PROCESS_WIDE_KEYS:
        expected = getattr(settings, key, None)
        if expected is None:
            continue
        assert seen["after"][key] == str(expected), (
            f"{key} is {seen['after'][key]!r} in a spawned child and {expected!r} in "
            f"this process's settings (#101)"
        )
