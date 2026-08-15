"""Structural gates for apps/api. Standard library only, no dependencies.

    python scripts/gates.py fast    ruff + import contracts + test collection
    python scripts/gates.py full    the above, plus lizard and the unit suite

Every threshold in this file is a number measured against the tree on 2026-08-15,
not a target. Each one sits exactly on the worst value the repo already contains,
so the gates pass today and block the next thing that is worse. The measurements
and the commands that produced them are recorded beside each step.

Tightening a threshold is a deliberate act: move it down, watch the gate go red,
fix the code, move it down again. Never raise one to make a red gate green.
"""

import os
import subprocess
import sys
import time

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tool(name):
    """Resolve a console script from the local venv, falling back to PATH."""
    for sub, suffix in (("Scripts", ".exe"), ("bin", "")):
        path = os.path.join(API_DIR, ".venv", sub, name + suffix)
        if os.path.exists(path):
            return path
    return name


PYTHON = tool("python") if os.path.exists(tool("python")) else sys.executable

# ruff check app exits 1 today on two pre-existing I001s, so it cannot be the gate
# as-is. Those two are pinned by COUNT: (file, rule) -> how many times that rule may
# fire in that file. The gate fails three ways.
#
#   - a (file, rule) pair that is not on this list appears
#   - a pinned pair fires MORE times than its count, e.g. a second I001 in
#     agent_tools.py, which is a new violation wearing an already-pinned name
#   - a pinned pair fires FEWER times, or stops firing: the line is stale, so lower
#     it or delete it and the gate holds the tree to the smaller number
#
# The counts are what make the second case visible. Pinning bare pairs would let any
# number of same-rule violations in a pinned file collapse onto the one pair and pass.
#
#   .venv/Scripts/ruff.exe check app --output-format=concise   (2026-08-15)
#     app/services/agent_tools.py:33:1: I001 Import block is un-sorted or un-formatted
#     app/worker/tasks/pipeline/chunk.py:45:1: I001 Import block is un-sorted or un-formatted
#     Found 2 errors. [*] 2 fixable with the `--fix` option.
RUFF_BASELINE = {
    ("app/services/agent_tools.py", "I001"): 1,
    ("app/worker/tasks/pipeline/chunk.py", "I001"): 1,
}

# Measured with `python -m lizard app --csv` over 567 functions, 20193 nloc.
# Each flag sits ON the worst observed value, and each was observed to go red one
# step tighter:
#
#   -C 35        worst CCN 35    retrieve_tool            app/services/agent_tools.py
#                                (-C 34 -> exit 1)        59 functions exceed CCN 10, 30 exceed 15
#   -L 804       worst length 804 _execute_transactional_tool
#                                (-L 803 -> exit 1)       app/services/transactional/tools.py
#   -a 11        worst params 11 _write_turn_metrics      app/worker/tasks/runtime/agent.py
#                                (-a 10 -> exit 1)
#   -Tnloc=545   worst nloc 545  _execute_transactional_tool
#                                (nloc=544 -> exit 1)     lizard's default is 1000000, i.e. never
LIZARD_FLAGS = ["-C", "35", "-L", "804", "-a", "11", "-Tnloc=545", "--warnings_only"]


def run(command):
    """Print the command, stream its output, return its exit code."""
    print("\n$ " + " ".join(command), flush=True)
    return subprocess.call(command, cwd=API_DIR)


def run_ruff():
    """Fail on any ruff violation beyond the counts pinned in RUFF_BASELINE."""
    command = [tool("ruff"), "check", "app", "--output-format=concise"]
    print("\n$ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=API_DIR, capture_output=True, text=True)
    output = result.stdout + result.stderr
    print(output, end="", flush=True)

    found = {}
    parsed = 0
    for line in output.splitlines():
        parts = line.split(":", 3)
        if len(parts) == 4 and parts[1].isdigit():
            key = (parts[0].replace("\\", "/"), parts[3].split()[0])
            found[key] = found.get(key, 0) + 1
            parsed += 1

    # A violation this parser fails to read would be a violation this gate lets
    # through. Ruff's own count is the check on the parser: if they disagree, fail
    # rather than pass on an incomplete reading.
    for line in output.splitlines():
        # "Found 2 errors." — absent when ruff finds nothing.
        if line.startswith("Found ") and line.split()[1].isdigit():
            reported = int(line.split()[1])
            if reported != parsed:
                print("\nruff: parsed %d violation(s), ruff reported %d." % (parsed, reported))
                print("The output format changed. Fix the parser in scripts/gates.py.")
                return 1

    unpinned = sorted(key for key in found if key not in RUFF_BASELINE)
    if unpinned:
        print("\nruff: %d violation(s) outside the pinned baseline:" % len(unpinned))
        for key in unpinned:
            print("  %s  %s  x%d" % (key[1], key[0], found[key]))
        return 1

    over = sorted(key for key in RUFF_BASELINE if found.get(key, 0) > RUFF_BASELINE[key])
    if over:
        print("\nruff: %d pinned violation(s) fired more often than the baseline allows:" % len(over))
        for key in over:
            print("  %s  %s  pinned %d, found %d" % (key[1], key[0], RUFF_BASELINE[key], found.get(key, 0)))
        return 1

    under = sorted(key for key in RUFF_BASELINE if found.get(key, 0) < RUFF_BASELINE[key])
    if under:
        print("\nruff: %d baseline line(s) are stale. Lower the count, or delete the" % len(under))
        print("line, in RUFF_BASELINE in scripts/gates.py so it cannot come back:")
        for key in under:
            print("  %s  %s  pinned %d, found %d" % (key[1], key[0], RUFF_BASELINE[key], found.get(key, 0)))
        return 1

    print("ruff: clean against the %d pinned baseline violation(s)." % sum(RUFF_BASELINE.values()))
    return 0


def steps(mode):
    """Ordered (label, callable) pairs for the requested mode."""
    fast = [
        ("ruff", run_ruff),
        # lint-imports must be the console script. `python -m importlinter.cli`
        # exits 0 without checking anything, which would make this a silent pass.
        ("import contracts", lambda: run([tool("lint-imports")])),
        ("test collection", lambda: run([PYTHON, "-m", "pytest", "tests/unit", "-q", "--collect-only"])),
    ]
    if mode == "fast":
        return fast
    return fast + [
        ("complexity", lambda: run([PYTHON, "-m", "lizard", "app"] + LIZARD_FLAGS)),
        ("unit tests", lambda: run([PYTHON, "-m", "pytest", "tests/unit", "-q"])),
    ]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("fast", "full"):
        print(__doc__)
        return 2

    started = time.time()
    for index, (label, step) in enumerate(steps(mode), start=1):
        print("\n" + "=" * 78)
        print("[%d] %s" % (index, label))
        print("=" * 78)
        code = step()
        if code != 0:
            print("\nFAILED at step %d (%s) after %.1fs, exit %d." % (index, label, time.time() - started, code))
            return code

    print("\n" + "=" * 78)
    print("%s gates passed in %.1fs." % (mode, time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
