"""Filesystem anchors for subprocesses spawned by the integration fixtures.

Deliberately a separate module rather than a helper inside ``conftest.py``:
importing ``tests.integration.conftest`` from anywhere else would execute that
module's environment mutations (``CELERY_TASK_ALWAYS_EAGER``, ``CONTROL_DB_URL``,
``CONTROL_DB_SYNC_URL``) as an import side effect and leak them into whichever
suite did the importing. This repo has already been bitten once by exactly that
class of cross-module import contamination.

Nothing here touches the environment, the filesystem, or the network at import
time, so it is safe to import from a unit test.
"""

from pathlib import Path

# This file lives at <api_root>/tests/integration/_paths.py, so the API package
# root is three levels up. Derived from __file__, never from os.getcwd(), so it
# is correct no matter which directory pytest was invoked from.
_API_ROOT = Path(__file__).resolve().parents[2]


def api_root() -> Path:
    """Absolute path to the ``apps/api`` directory containing the ``app`` package.

    This is the working directory a ``celery -A app.worker.celery_app`` subprocess
    needs in order to import the app package.
    """
    return _API_ROOT
