"""Pytest plugin that makes `docling` unimportable, whatever the machine has installed.

The mirror of _docling_present_plugin. Together they let
tests/unit/test_ingestion_chain_docling_gate.py observe BOTH directions of the
importorskip gate on tests/integration/test_ingestion_chain.py without depending on
what happens to be installed.

Why not just rely on docling being absent here: because that makes the absent-direction
assertion silently unobservable the day someone installs the `pipeline` extra — the
guard would keep passing while checking nothing, which is the failure mode this repo
keeps finding. Blocking the import explicitly keeps the assertion true on every machine.

Loaded with `-p tests.unit._docling_absent_plugin`. Deliberately NOT a conftest: it
must apply only to the subprocess that opts in.
"""

import sys

_BLOCKED_ROOTS = ("docling", "docling_core")


def _is_blocked(fullname: str) -> bool:
    return any(
        fullname == root or fullname.startswith(root + ".") for root in _BLOCKED_ROOTS
    )


class _DoclingBlocker:
    """A meta-path finder that refuses docling, ahead of every real finder."""

    def find_spec(self, fullname, path=None, target=None):
        if _is_blocked(fullname):
            raise ModuleNotFoundError(
                f"No module named {fullname!r} (blocked by _docling_absent_plugin)",
                name=fullname,
            )
        return None


# Purge anything already imported, or the blocker would never be consulted.
for _name in [m for m in sys.modules if _is_blocked(m)]:
    del sys.modules[_name]

sys.meta_path.insert(0, _DoclingBlocker())
