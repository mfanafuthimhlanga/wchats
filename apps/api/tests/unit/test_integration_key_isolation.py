"""No integration module may rebind NEON_ENCRYPTION_KEY after `settings` froze (#101).

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

WHAT THIS FILE ASSERTS
    The invariant that makes step 4 impossible: the value a subprocess would
    inherit is still the value this process decrypts with. One source check over
    every module, so the next unconditional assignment is caught where it is
    written, and one behavioural check that imports the module the CI failure came
    from and compares the two readers.

    The behavioural half imports one module rather than sweeping all thirty. The
    sweep cost 66 seconds, most of it importing app packages the source check
    reads without loading.
"""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path

from app.core.config import settings

INTEGRATION_DIR = Path(__file__).resolve().parents[1] / "integration"

#: Every setting whose value must be identical in the pytest process and in a
#: worker it spawns. Both are read once, at the first `import app` in each process.
PROCESS_WIDE_KEYS = ("NEON_ENCRYPTION_KEY", "PLATFORM_CREDENTIAL_KEY")

#: The module the CI failure came from. It is the one that assigned the key, and
#: it is cheap to import: psycopg2 and pytest at module scope, nothing from app.
THE_MODULE_THAT_DID_IT = "test_usage_rollup_e2e"


def integration_modules() -> list[Path]:
    return sorted(p for p in INTEGRATION_DIR.glob("*.py") if p.name != "__init__.py")


#: An assignment into os.environ that starts in column 0. Column 0 is module scope
#: in Python, so the pattern separates the two cases exactly:
#: `tests/integration/conftest.py` guards its own write behind
#: `if "NEON_ENCRYPTION_KEY" not in os.environ` and therefore indents it, and a
#: commented-out line starts with a hash. A text scan rather than an ast.parse,
#: because gates.py counts a parsed syntax tree in a test as a source assertion and
#: its baseline never gains entries.
UNCONDITIONAL_ENV_WRITE = re.compile(
    r"""^os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]\s*=""", re.MULTILINE
)


def module_scope_env_writes(source: Path) -> set[str]:
    """Names an unconditional module-level statement assigns into `os.environ`."""
    return set(UNCONDITIONAL_ENV_WRITE.findall(source.read_text(encoding="utf-8")))


def test_no_integration_module_rebinds_a_process_wide_key() -> None:
    offenders = {
        path.name: sorted(module_scope_env_writes(path) & set(PROCESS_WIDE_KEYS))
        for path in integration_modules()
    }
    offenders = {name: keys for name, keys in offenders.items() if keys}
    assert not offenders, (
        f"these modules assign a process-wide key at import time, so every worker "
        f"spawned after collection inherits a value this process already froze past: "
        f"{offenders}. Use os.environ.setdefault so one source decides the key (#101)."
    )


def test_importing_the_rollup_module_leaves_the_key_agreed() -> None:
    """Import it the way collection does, then compare the two readers.

    `settings` is what this process decrypts with. `os.environ` is what a Celery
    worker spawned by `_spawn_pipeline_worker` inherits. They agree or a decrypt
    across that boundary fails, and it fails as InvalidToken, which names neither
    process.
    """
    before = {key: os.environ.get(key) for key in PROCESS_WIDE_KEYS}
    try:
        importlib.import_module(f"tests.integration.{THE_MODULE_THAT_DID_IT}")
        for key in PROCESS_WIDE_KEYS:
            assert os.environ.get(key) == getattr(settings, key), (
                f"importing tests/integration/{THE_MODULE_THAT_DID_IT}.py changed "
                f"{key}, so a worker spawned after it encrypts with one value while "
                f"this process decrypts with another (#101)"
            )
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
