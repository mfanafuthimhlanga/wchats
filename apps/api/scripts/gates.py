"""Structural gates for apps/api. Standard library only, no dependencies.

    python scripts/gates.py static  ruff, import contracts, complexity, source
                                    assertions, log bounds, process-wide keys.
                                    None of them imports app code
    python scripts/gates.py mypy    the type baseline on its own, which is what CI runs
    python scripts/gates.py fast    static, plus mypy, plus whole-suite test collection
    python scripts/gates.py full    the above, plus the unit suite

`static` exists because of a measured ceiling, not a preference. The Stop hook
clamps its gate to 170s, and above that the harness kills the hook and the gate
disappears silently, which reads exactly like a gate that was never declared.
Test collection imports the whole app: docling pulls transformers and torch, and
it was measured at 142.5s on 2026-08-18 against 78.8s on 2026-08-15, growing with
the dependency tree rather than with the suite. It crossed the clamp and the hook
gate was killed mid-run. `static` runs the six steps that never import app code:

    ruff                1.8s   measured 2026-08-18
    import contracts    1.4s   measured 2026-08-18
    complexity          2.6s   measured 2026-08-24, lizard parses, it does not import
    source assertions   3.6s   measured 2026-08-24, one text pass over tests/
    log bounds          1.1s   measured 2026-09-05, one AST pass over app/
    process-wide keys   0.2s   measured 2026-09-05, one AST pass over tests/integration

That is the hook's gate now. mypy, collection and the suite belong to `fast` and
`full`, which are run deliberately and detached, not at the end of every session.
mypy builds a type graph over the whole tree, so it costs what an import costs.
Warm runs on this box on 2026-09-02 took between 26s and 80s, against 11s to 16s for
`static`, and a run with no .mypy_cache did not finish inside ten minutes. `mypy` is
its own mode so CI's Type-check job runs that one step and nothing else.

Three of the steps are baselines rather than ceilings. RUFF_BASELINE,
LIZARD_BASELINE and SOURCE_ASSERTION_BASELINE each hold what the tree contains
today, and each fails three ways: on something new, on something that grew, and on
an entry that has gone stale. Entries come out as the code improves. Entries never
go in. tests/unit/test_gates.py holds a snapshot of all three baselines and fails on
an addition.

mypy is the fourth, and it has none: it held a baseline while #92's 153 errors came
down and holds the tree to zero now that they are gone. A baseline is what a number
on its way down needs; a number that has arrived needs nothing.

Tightening a threshold is a deliberate act: move it down, watch the gate go red,
fix the code, move it down again. Never raise one to make a red gate green.
"""

import ast
import os
import re
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

# The gate reads `app tests`, the same two trees CI reads
# (`ruff check apps/api/app/ apps/api/tests/`). It read `app` alone until #95, so
# 23 violations sat under tests/ where the gate could not look and CI went red on
# them.
#
# The dict is EMPTY as of 2026-08-29, so the gate holds the tree to zero and reaches
# the same verdict as CI. It held one entry, an auto-fixable I001 in
# app/worker/tasks/pipeline/chunk.py that #43 put on main. The gate passed on it and
# CI's Lint job failed on it, so every pull request opened after #43 carried a red
# check that this gate reported as clean. `ruff check --fix` closed it in one line.
# Take the fix over the pin. A pinned violation is still a violation CI fails on, and
# a gate that disagrees with CI tells the next session the tree is clean when it is not.
#
# Anything that does go in is pinned by COUNT: (file, rule) -> how many times that
# rule may fire in that file. The gate fails three ways.
#
#   - a (file, rule) pair that is not on this list appears
#   - a pinned pair fires MORE times than its count, which is a new violation
#     wearing an already-pinned name
#   - a pinned pair fires FEWER times, or stops firing, which makes the line stale.
#     Lower the count or delete the line, and the gate holds the tree to the
#     smaller number
#
# The counts are what make the second case visible. Pinning bare pairs would let any
# number of same-rule violations in a pinned file collapse onto the one pair and pass.
#
#   .venv/Scripts/python.exe -m ruff check app tests --output-format=concise
#   (2026-08-29, after the chunk.py fix)
#     All checks passed!
#
# tests/unit/test_gates.py snapshots this dict and goes red on an addition.
RUFF_BASELINE = {}

# CI has run this exact line since the workflow was written, and nothing local ran it,
# so the Type-check job was its only reader. That job went red on 2026-08-11 and stayed
# red (#92), and a job that is red on the trunk stops being read as a signal. Branches
# added errors under that red and every review recorded "red as on main" (FM-017). The
# count was the only thing that moved, and nothing compared counts.
#
# This gate makes the count the check, and it runs CI's invocation rather than one of
# its own, so green here and a green Type-check job are the same claim.
MYPY_COMMAND = [PYTHON, "-m", "mypy", "app/", "--ignore-missing-imports", "--strict-optional"]

# One mypy error line, verbatim, 2026-09-02. mypy prints the platform separator, so this
# machine reports the path with backslashes where CI reports it with slashes:
#
#   app\services\deployment_service.py:419: error: Argument 3 to "tool" has incompatible
#   type "Collection[str]"; expected "dict[str, Any]"  [arg-type]
#
# parse_mypy_output normalises to forward slashes before counting, so one pin reads
# both. The optional second number is the column `--show-column-numbers` would add;
# without it a lazy match would swallow the line number into the path.
MYPY_ERROR = re.compile(r"^(?P<file>.+?\.py):\d+(?::\d+)?: error:")

# The summary line, verbatim, 2026-09-02:
#
#   Found 153 errors in 13 files (checked 159 source files)
#
# `checked 159 source files` is the denominator FM-013 asks every gate for. mypy prints
# `Success: no issues found in N source files` instead when the tree is clean, and
# neither line when it aborts, which is the case that has to fail rather than pass.
MYPY_SUMMARY = re.compile(r"^Found (?P<errors>\d+) errors? in \d+ files? \(checked \d+ source files?\)")
MYPY_CLEAN = re.compile(r"^Success: no issues found in \d+ source files?")

# THERE IS NO MYPY BASELINE. The standard is zero errors, and #92 closed on the
# measurement that made it affordable:
#
#   .venv/Scripts/python.exe -m mypy app/ --ignore-missing-imports --strict-optional
#   Success: no issues found in 161 source files
#
# MYPY_BASELINE held 153 errors in 13 files on 2026-09-02 and 22 in 6 by the time the
# last of them was fixed. A count-pinned baseline is the right shape while a number is
# coming down, and the wrong shape once it reaches zero: a file the pin does not name
# can gain errors freely, so the pin has to name every file that has any, and the only
# list with no such hole is the empty one. Nothing to add to, nothing to lower, nothing
# to go stale. The other three gates keep their baselines because their numbers are not
# zero yet.
#
# A new type error fails this gate on the branch that writes it. Fix it; do not
# reintroduce a pin here to carry it.

# The complexity standard the repo holds itself to from now on. CCN 15 and 60 lines are
# the numbers a function has to meet. `-a 11` sits on the worst parameter count in the
# tree. It reported nothing on its own in the 2026-08-24 measurement, because every
# function it would have caught is already over CCN or over length, and it stays so that
# the next wide signature has a number to cross.
LIZARD_FLAGS = ["-C", "15", "-L", "60", "-a", "11", "--warnings_only"]

# Every function that is over that standard today, pinned at what it measures today.
# Measured 2026-08-24, 131 warning lines, exit 1:
#
#   .venv/Scripts/python.exe -m lizard app -C 15 -L 60 -a 11 --warnings_only
#
# (file, function) -> (ccn, length). The gate fails three ways.
#
#   - lizard warns about a (file, function) pair that is not on this list
#   - a pinned function grew, in ccn or in length
#   - a pinned function stopped being reported, or came in under its pin. That entry is
#     stale, so lower the numbers or delete the entry, and the gate holds the tree to
#     the smaller number
#
# ENTRIES MAY BE DELETED. ENTRIES MAY NEVER BE ADDED. Deleting is the only way this list
# changes size, and the third failure is what forces the deletion the moment a function
# improves. A function over the standard is work to do, never a line to add here.
# tests/unit/test_gates.py snapshots this dict and goes red on an addition.
LIZARD_BASELINE = {
    ("app/api/deps.py", "get_current_tenant"): (19, 110),
    ("app/api/v1/agent_chat.py", "get_agent_conversations"): (4, 69),
    ("app/api/v1/agent_chat.py", "post_agent_chat"): (8, 160),
    ("app/api/v1/agents.py", "patch_agent"): (13, 61),
    ("app/api/v1/capability_envelopes.py", "patch_capability_envelope"): (12, 102),
    ("app/api/v1/deployment.py", "_refuse_if_a_critical_finding_is_open"): (4, 77),
    ("app/api/v1/deployment.py", "acknowledge_warnings"): (17, 66),
    ("app/api/v1/deployment.py", "approve_deployment"): (12, 143),
    ("app/api/v1/documents.py", "delete_document"): (10, 175),
    ("app/api/v1/documents.py", "get_document_detail"): (12, 133),
    ("app/api/v1/documents.py", "upload_documents"): (15, 188),
    ("app/api/v1/evals.py", "get_eval_run_results"): (16, 98),
    ("app/api/v1/evals.py", "list_eval_runs"): (13, 87),
    ("app/api/v1/pending_confirmations.py", "resolve_pending_confirmation"): (8, 177),
    ("app/api/v1/query.py", "post_agent_query"): (3, 77),
    ("app/api/v1/red_team.py", "_contain_finding_sync"): (6, 64),
    ("app/api/v1/webhooks.py", "clerk_webhook"): (9, 79),
    ("app/api/v1/webhooks.py", "provision_me"): (5, 89),
    ("app/api/v1/widget.py", "get_widget_config"): (5, 64),
    ("app/api/v1/widget.py", "post_widget_chat"): (9, 144),
    ("app/api/v1/widget.py", "post_widget_feedback"): (4, 88),
    ("app/api/v1/widget.py", "post_widget_identity_request"): (3, 63),
    ("app/api/v1/widget.py", "post_widget_identity_verify"): (5, 88),
    ("app/api/v1/widget.py", "widget_job_events"): (2, 73),
    ("app/services/actor_seam.py", "call_actor_gate"): (10, 152),
    ("app/services/agent_prompt.py", "build_system_prompt"): (14, 81),
    ("app/services/agent_tools.py", "escalate_to_human_tool"): (4, 101),
    ("app/services/agent_tools.py", "lookup_structured_tool"): (5, 67),
    ("app/services/agent_tools.py", "retrieve_tool"): (28, 244),
    ("app/services/bench_service.py", "grade_trace"): (5, 66),
    ("app/services/bench_service.py", "list_failing_traces"): (8, 87),
    ("app/services/capability_service.py", "validate_tighten_only"): (31, 154),
    ("app/services/chunking_service.py", "chunk_document"): (10, 98),
    ("app/services/deployment_service.py", "_agent_not_invoked_warning"): (3, 76),
    ("app/services/deployment_service.py", "_eval_summary"): (2, 63),
    ("app/services/deployment_service.py", "_fetch_blast_radius_sync"): (11, 147),
    ("app/services/deployment_service.py", "_fetch_eval_summary_sync"): (12, 200),
    ("app/services/deployment_service.py", "_fetch_red_team_summary_sync"): (11, 114),
    ("app/services/deployment_service.py", "apply_signal_evidence_gate"): (7, 133),
    ("app/services/deployment_service.py", "derive_blast_radius_warnings"): (8, 77),
    ("app/services/digest_service.py", "_collect_digest_stats"): (10, 71),
    ("app/services/eval_service.py", "build_eval_run_config"): (11, 191),
    ("app/services/eval_service.py", "insert_eval_run"): (4, 62),
    ("app/services/eval_service.py", "run_ragas_eval"): (11, 153),
    ("app/services/eval_service.py", "summarise_agent_invocation"): (31, 186),
    ("app/services/eval_service.py", "summarise_run_validity"): (17, 117),
    ("app/services/eval_service.py", "update_eval_run_config"): (4, 70),
    ("app/services/identity_service.py", "verify_otp"): (8, 103),
    ("app/services/label_service.py", "record_human_label"): (6, 120),
    ("app/services/metrics_service.py", "_build_metrics_dict"): (16, 74),
    ("app/services/neon.py", "create_neon_project"): (6, 76),
    ("app/services/red_team_probe.py", "_build_transactional_probe_fn"): (1, 119),
    ("app/services/red_team_service.py", "_run_attacker"): (5, 81),
    ("app/services/red_team_service.py", "build_probe_tools"): (1, 87),
    ("app/services/red_team_service.py", "classify_severity"): (2, 77),
    ("app/services/red_team_service.py", "run_confused_deputy_agent"): (1, 69),
    ("app/services/red_team_service.py", "run_content_injection_agent"): (13, 153),
    ("app/services/red_team_service.py", "run_identity_bypass_agent"): (17, 159),
    ("app/services/red_team_service.py", "run_value_bound_evasion_agent"): (17, 146),
    ("app/services/red_team_service.py", "seed_poisoned_chunk"): (2, 75),
    ("app/services/redteam_programme_service.py", "read_programme"): (16, 98),
    ("app/services/retrieval_service.py", "rrf_fuse"): (3, 65),
    ("app/services/retrieval_service.py", "rrf_fuse_with_expansion"): (8, 68),
    ("app/services/retrieval_service.py", "verified_qa_lookup"): (3, 61),
    ("app/services/scenario_service.py", "generate_eval_suite_for_agent"): (7, 61),
    ("app/services/scenario_service.py", "generate_scenarios_from_chunks"): (5, 65),
    ("app/services/scenario_service.py", "mine_production_scenarios"): (11, 114),
    ("app/services/sse.py", "event_generator"): (13, 96),
    ("app/services/strategy_service.py", "_fetch_corpus_signals_sync"): (10, 74),
    ("app/services/transactional/adapters/calendly_adapter.py", "book_slot"): (2, 81),
    ("app/services/transactional/adapters/shopify_adapter.py", "issue_refund"): (3, 89),
    ("app/services/transactional/adapters/shopify_adapter.py", "place_order"): (3, 76),
    ("app/services/transactional/adapters/stripe_adapter.py", "place_order"): (1, 63),
    ("app/services/transactional/audit.py", "write_audit_row"): (2, 69),
    ("app/services/transactional/confirmation_resolution.py", "execute_approved_confirmation"): (12, 290),
    ("app/services/transactional/credential_service.py", "_fetch_credential_config"): (3, 65),
    ("app/services/transactional/enforcement.py", "apply_rate_and_constraint_checks"): (8, 96),
    ("app/services/transactional/enforcement.py", "check_capability_access"): (4, 76),
    ("app/services/transactional/idempotency.py", "reserve_idempotency"): (1, 185),
    ("app/services/transactional/idempotency.py", "reserve_idempotency._inner"): (14, 150),
    ("app/services/transactional/provider_adapter.py", "get_adapter_for_skill"): (10, 150),
    ("app/services/transactional/tools.py", "_execute_adapter_and_audit"): (3, 142),
    ("app/services/transactional/tools.py", "_execute_transactional_tool"): (34, 788),
    ("app/services/validation_service.py", "call_auditor"): (2, 88),
    ("app/services/validation_service.py", "call_strategist"): (6, 77),
    ("app/worker/tasks/pipeline/chunk.py", "chunk_documents"): (15, 208),
    ("app/worker/tasks/pipeline/embed.py", "embed_and_migrate"): (13, 238),
    ("app/worker/tasks/pipeline/migrations.py", "apply_migrations"): (6, 119),
    ("app/worker/tasks/pipeline/parse.py", "parse_documents"): (17, 248),
    ("app/worker/tasks/pipeline/provision.py", "provision_neon"): (17, 208),
    ("app/worker/tasks/pipeline/reembed.py", "reembed_corpus"): (13, 169),
    ("app/worker/tasks/pipeline/staleness.py", "compute_index_staleness_summary"): (11, 77),
    ("app/worker/tasks/pipeline/strategy.py", "synthesize_retrieval_strategy"): (12, 124),
    ("app/worker/tasks/runtime/agent.py", "_judge_retrieved_context"): (11, 98),
    ("app/worker/tasks/runtime/agent.py", "_persist_messages"): (2, 73),
    ("app/worker/tasks/runtime/agent.py", "_resolve_turn_prompt_version"): (5, 69),
    ("app/worker/tasks/runtime/agent.py", "_write_turn_metrics"): (1, 65),
    ("app/worker/tasks/runtime/agent.py", "run_agent_turn"): (18, 461),
    ("app/worker/tasks/runtime/bench.py", "promote_trace_to_scenario"): (12, 158),
    ("app/worker/tasks/runtime/confirmations.py", "resolve_approved_confirmation"): (5, 111),
    ("app/worker/tasks/runtime/deployment.py", "run_deployment_checklist"): (11, 221),
    ("app/worker/tasks/runtime/eval.py", "_invoke_agent_for_scenarios"): (14, 221),
    ("app/worker/tasks/runtime/eval.py", "_run_one_eval_turn"): (3, 80),
    ("app/worker/tasks/runtime/eval.py", "generate_eval_suite"): (8, 73),
    ("app/worker/tasks/runtime/eval.py", "run_eval_suite"): (28, 576),
    ("app/worker/tasks/runtime/red_team.py", "_build_probe_fn"): (11, 65),
    ("app/worker/tasks/runtime/red_team.py", "run_red_team"): (28, 425),
    ("app/worker/tasks/runtime/retrieval_eval.py", "run_retrieval_faithfulness"): (12, 82),
    ("app/worker/tasks/runtime/retrieve.py", "retrieve_and_rank"): (12, 212),
    ("app/worker/tasks/runtime/validators.py", "run_auditor"): (15, 184),
    ("app/worker/tasks/runtime/validators.py", "run_gatekeeper"): (5, 102),
    ("app/worker/tasks/runtime/validators.py", "run_strategist"): (9, 111),
}

# One lizard warning line, verbatim, 2026-08-24:
#
#   app\api\v1\agents.py:154: warning: patch_agent has 32 NLOC, 13 CCN, 227 token,
#   3 PARAM, 61 length, 0 ND
#
# The trailing ND field is not anchored, so a lizard that stops printing it still parses.
LIZARD_WARNING = re.compile(
    r"^(?P<file>.+?):\d+: warning: (?P<function>.+?) has \d+ NLOC, (?P<ccn>\d+) CCN, "
    r"\d+ token, \d+ PARAM, (?P<length>\d+) length"
)

# A test file may not read app source as text or as a syntax tree.
#
# A test asserts on behaviour through the module's interface, or on a structural fact
# through an import-linter contract or a ruff rule. Asserting on the characters of a
# function pins a shape that is free to change while the behaviour stays correct, and it
# passes while the behaviour is wrong. Both directions cost more than they return.
#
# Three patterns count, one site per matching line per pattern.
#
#   getsource    inspect.getsource, and a bare getsource(
#   ast.parse    any parse of source text into a tree
#   read_text    a read whose target is app source, decided by source_assertion_sites
#
# A line that matches two patterns counts once per pattern, which is why the nineteen
# ast.parse(path.read_text(...)) lines in the tree count twice.
#
# Measured 2026-08-24 over 199 files under tests/, 138 sites in 46 files. The gate fails
# the same three ways as the complexity gate, on an unpinned file, on a file whose count
# grew, and on a stale entry.
#
# ENTRIES MAY BE DELETED. ENTRIES MAY NEVER BE ADDED. A new test that wants one of these
# patterns is a test that should assert on behaviour instead.
# tests/unit/test_gates.py snapshots this dict and goes red on an addition.
SOURCE_ASSERTION_BASELINE = {
    "tests/integration/test_paramstyle_real_db.py": 2,
    "tests/unit/retrieval/test_retrieval_service.py": 1,
    "tests/unit/test_agent_options_seam.py": 3,
    "tests/unit/test_agreement_threshold.py": 1,
    "tests/unit/test_calibration_harness.py": 3,
    "tests/unit/test_capability_service.py": 2,
    "tests/unit/test_confirmation_resolution.py": 1,
    "tests/unit/test_deployment_service.py": 1,
    "tests/unit/test_eval_agent_invocation.py": 5,
    "tests/unit/test_eval_service.py": 5,
    "tests/unit/test_eval_task.py": 1,
    "tests/unit/test_idv_message_verdict_pin.py": 2,
    "tests/unit/test_index_staleness.py": 1,
    "tests/unit/test_ingestion_chain_docling_gate.py": 4,
    "tests/unit/test_ingestion_reads_from_s3.py": 9,
    "tests/unit/test_ingestion_sse.py": 1,
    "tests/unit/test_integration_worker_cwd.py": 1,
    "tests/unit/test_judgement_temperature.py": 1,
    "tests/unit/test_label_downstream.py": 4,
    "tests/unit/test_label_provenance.py": 6,
    "tests/unit/test_migration_0010.py": 2,
    "tests/unit/test_migration_0011.py": 2,
    "tests/unit/test_migration_0012.py": 2,
    "tests/unit/test_migration_0013.py": 6,
    "tests/unit/test_migration_tenant_0014.py": 8,
    "tests/unit/test_migration_tenant_0015.py": 8,
    "tests/unit/test_migration_tenant_0016.py": 3,
    "tests/unit/test_migration_tenant_0017.py": 1,
    "tests/unit/test_neon_teardown.py": 1,
    "tests/unit/test_orchestrator_timeout_diagnostic.py": 1,
    "tests/unit/test_patch_targets_resolve.py": 2,
    "tests/unit/test_pipeline_patch_targets.py": 3,
    "tests/unit/test_promote_trace.py": 3,
    "tests/unit/test_recorded_side_effects.py": 2,
    "tests/unit/test_red_team_rtx_runners.py": 1,
    "tests/unit/test_red_team_service.py": 2,
    "tests/unit/test_s3_uploads.py": 1,
    "tests/unit/test_tool_loop_agents_are_given_tools.py": 2,
    "tests/unit/test_shopify_adapter.py": 1,
    "tests/unit/test_sql_paramstyle_collisions.py": 4,
    "tests/unit/test_stripe_adapter.py": 1,
    "tests/unit/test_test_route_paths_resolve.py": 1,
    "tests/unit/test_transactional_tools.py": 1,
    "tests/unit/test_widget_feedback.py": 1,
}

# A quoted path segment naming the app package, so "app", 'app' and "app/services/x.py".
APP_LITERAL = re.compile(r"""['"/]app['"/.]""")
# `<name>.__file__`, a module object's own source file. Bare `Path(__file__)` is every
# test file's own location and is deliberately not a marker.
MODULE_FILE = re.compile(r"\w\.__file__")
GETFILE_CALL = re.compile(r"\bgetfile\s*\(")

ASSIGNMENT = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)", re.M)
FOR_TARGET = re.compile(r"\bfor\s+([A-Za-z_]\w*)\s+in\b")
PARAMETRIZE_ARG = re.compile(r"""parametrize\s*\(\s*['"]([A-Za-z_]\w*)['"]""")

# A binding carries app source onward only when it is PATH shaped. Without this
# guard the fixpoint leaks through `tree = ast.parse(path.read_text(...))` and every
# generic loop name in the file, node and alias and target, becomes app-bound.
PATH_SHAPED = re.compile(
    r"Path\s*\(|\.rglob\s*\(|\.glob\s*\(|\.joinpath\s*\(|\.with_suffix\s*\("
    r"|\.parents\s*\[|\.parent\b|__file__|getfile\s*\(|\s/\s"
)


def run(command):
    """Print the command, stream its output, return its exit code."""
    print("\n$ " + " ".join(command), flush=True)
    return subprocess.call(command, cwd=API_DIR)


def run_ruff():
    """Fail on any ruff violation beyond the counts pinned in RUFF_BASELINE."""
    command = [tool("ruff"), "check", "app", "tests", "--output-format=concise"]
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
        # "Found 2 errors." is absent when ruff finds nothing.
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


def mypy_did_not_run(returncode, found):
    """True when mypy exited nonzero and this parser read no error line out of it.

    mypy exits 1 when it reports errors and 0 when it reports none. A nonzero exit with
    nothing parsed means mypy crashed before its first error line, or the line format
    changed under MYPY_ERROR. Either way the gate did not run, and a gate that did not
    run is never a pass (FM-013). The #92 shape, one error line and then `Found 1 error
    in 1 file (errors prevented further checking)`, parses one line and is caught by the
    summary check in run_mypy instead, because that line carries no denominator.
    """
    return returncode != 0 and not found


def parse_mypy_output(output):
    """One mypy run read as (found, parsed, reported).

    found maps each file with errors to its count, paths with forward slashes on every
    platform. parsed is the number of error lines read. reported is the count mypy's own
    summary line names, 0 for `Success: no issues found`, and None when mypy printed no
    summary at all, which is what an aborted run looks like and what #92 spent seventeen
    days calling "one error".
    """
    found = {}
    parsed = 0
    reported = None
    for line in output.splitlines():
        match = MYPY_ERROR.match(line)
        if match:
            path = match.group("file").replace("\\", "/")
            found[path] = found.get(path, 0) + 1
            parsed += 1
            continue
        match = MYPY_SUMMARY.match(line)
        if match:
            reported = int(match.group("errors"))
        elif MYPY_CLEAN.match(line):
            reported = 0
    return found, parsed, reported


def mypy_failures(found):
    """Failure lines for one mypy reading. Empty means the tree is type-clean.

    One rule, because the standard is zero: a file with any type error fails the gate.
    Every file is named with its count, so the reader sees the whole of what to fix
    rather than the first one mypy printed.
    """
    if not found:
        return []
    lines = [
        "mypy: %d file(s) have type errors, and the standard is zero." % len(found),
        "Fix them. There is no MYPY_BASELINE to add them to:",
    ]
    for path in sorted(found):
        lines.append("  %s  x%d" % (path, found[path]))
    return lines


def run_mypy():
    """Fail on any type error anywhere in `app/`."""
    print("\n$ " + " ".join(MYPY_COMMAND), flush=True)
    result = subprocess.run(MYPY_COMMAND, cwd=API_DIR, capture_output=True, text=True)
    output = result.stdout + result.stderr
    print(output, end="", flush=True)

    found, parsed, reported = parse_mypy_output(output)

    if mypy_did_not_run(result.returncode, found):
        print("\nmypy: exited %d and this parser read 0 error line(s)." % result.returncode)
        print("mypy gave up before it checked anything, or the line format changed.")
        print("Either way this is not a pass. Fix the file mypy choked on, or fix")
        print("MYPY_ERROR in scripts/gates.py.")
        return 1

    # The summary carries the denominator. Its absence means mypy stopped before it
    # reached the end, which is exactly the reading #92 spent seventeen days calling
    # "one error".
    if reported is None:
        print("\nmypy: no summary line, so nothing says how many files it opened.")
        print("A run that never finished cannot be read as a count. Find out why mypy")
        print("stopped, or fix MYPY_SUMMARY in scripts/gates.py.")
        return 1

    # mypy's own count is the check on the parser: if they disagree, fail rather than
    # pass on an incomplete reading. Same guard run_ruff puts on ruff.
    if reported != parsed:
        print("\nmypy: parsed %d error(s), mypy reported %d." % (parsed, reported))
        print("The output format changed. Fix the parser in scripts/gates.py.")
        return 1

    failures = mypy_failures(found)
    if failures:
        print("")
        for line in failures:
            print(line)
        return 1

    print("mypy: clean. %d error(s) reported across the tree." % reported)
    return 0


def parse_lizard_warnings(output):
    """(file, function) -> (ccn, length) per warning line, and the keys lizard repeated.

    Lizard prints one line per function, so a repeated key means two functions in one
    file share a name. The first reading wins and the key goes into the second list,
    because merging the two readings would let a new offender hide under the other's pin.
    """
    found = {}
    duplicates = []
    for line in output.splitlines():
        match = LIZARD_WARNING.match(line.strip())
        if not match:
            continue
        key = (match.group("file").replace("\\", "/"), match.group("function"))
        if key in found:
            duplicates.append(key)
            continue
        found[key] = (int(match.group("ccn")), int(match.group("length")))
    return found, duplicates


def parser_missed_warnings(returncode, found):
    """True when lizard warned about something and this parser read none of it.

    Lizard exits 1 when it warns about at least one function and 0 when it warns about
    none, both measured 2026-08-24. A nonzero exit with nothing parsed therefore means
    the warning line changed shape, and the gate would read an empty result as a pass.
    """
    return returncode != 0 and not found


def duplicate_failures(duplicates):
    """Failure lines for keys lizard printed twice. Empty means pass."""
    if not duplicates:
        return []
    keys = sorted(set(duplicates))
    lines = [
        "complexity: %d (file, function) key(s) came back twice from one lizard run." % len(keys),
        "Two functions in that file share a name, so one of them reads as the other and",
        "hides under its pin. Bring one under the standard, or rename it:",
    ]
    for key in keys:
        lines.append("  %s  %s" % key)
    return lines


def complexity_unpinned_lines(unpinned, found):
    """Failure lines for functions over the standard that the baseline does not name."""
    if not unpinned:
        return []
    lines = [
        "complexity: %d function(s) over the standard and not in the baseline." % len(unpinned),
        "Bring each one under the standard. Never add it to LIZARD_BASELINE:",
    ]
    for key in unpinned:
        lines.append("  %s  %s  ccn %d, length %d" % (key + found[key]))
    return lines


def complexity_grown_lines(grown, found, baseline):
    """Failure lines for pinned functions that measure worse than their pin."""
    if not grown:
        return []
    lines = ["complexity: %d pinned function(s) grew past the baseline:" % len(grown)]
    for key in grown:
        lines.append(
            "  %s  %s  pinned ccn %d length %d, found ccn %d length %d"
            % (key + baseline[key] + found[key])
        )
    return lines


def complexity_stale_lines(stale, found, baseline):
    """Failure lines for pinned functions that measure better than their pin."""
    if not stale:
        return []
    lines = [
        "complexity: %d baseline entry(ies) are stale. Lower the pinned" % len(stale),
        "numbers, or delete the entry, in LIZARD_BASELINE in scripts/gates.py so",
        "the smaller number is what the tree is held to:",
    ]
    for key in stale:
        observed = found.get(key)
        shown = "gone" if observed is None else "ccn %d length %d" % observed
        lines.append("  %s  %s  pinned ccn %d length %d, found %s" % (key + baseline[key] + (shown,)))
    return lines


def complexity_failures(found, baseline):
    """Failure lines for one lizard reading against a baseline. Empty means pass."""
    unpinned = sorted(key for key in found if key not in baseline)
    grown = sorted(
        key
        for key in baseline
        if key in found and (found[key][0] > baseline[key][0] or found[key][1] > baseline[key][1])
    )
    # A function whose ccn rose while its length fell is reported once, as growth. The
    # two remedies contradict each other, and fixing the growth is what makes the shrunk
    # dimension stale on a later run.
    stale = sorted(
        key
        for key in baseline
        if key not in grown
        and (
            key not in found
            or found[key][0] < baseline[key][0]
            or found[key][1] < baseline[key][1]
        )
    )
    return (
        complexity_unpinned_lines(unpinned, found)
        + complexity_grown_lines(grown, found, baseline)
        + complexity_stale_lines(stale, found, baseline)
    )


def run_complexity():
    """Fail on any function over the standard that LIZARD_BASELINE does not pin."""
    command = [PYTHON, "-m", "lizard", "app"] + LIZARD_FLAGS
    print("\n$ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=API_DIR, capture_output=True, text=True)
    output = result.stdout + result.stderr
    print(output, end="", flush=True)

    found, duplicates = parse_lizard_warnings(output)

    if parser_missed_warnings(result.returncode, found):
        print("\ncomplexity: lizard exited %d and this parser read 0 warning(s)." % result.returncode)
        print("The output format changed. Fix LIZARD_WARNING in scripts/gates.py.")
        return 1

    failures = duplicate_failures(duplicates) + complexity_failures(found, LIZARD_BASELINE)
    if failures:
        print("")
        for line in failures:
            print(line)
        return 1

    print("complexity: clean against the %d pinned function(s)." % len(LIZARD_BASELINE))
    return 0


def scan_line(line, quote, depth):
    """The open quote and bracket depth left after reading one physical line."""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if line.startswith(quote, index):
                index += len(quote)
                quote = None
                continue
            index += 1
            continue
        opened = False
        for candidate in ('"""', "'''", '"', "'"):
            if line.startswith(candidate, index):
                quote = candidate
                index += len(candidate)
                opened = True
                break
        if opened:
            continue
        if char == "#":
            break
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        index += 1
    if quote in ('"', "'"):
        quote = None  # a single-quoted literal cannot cross a physical line
    return quote, depth


def logical_statements(text):
    """One joined string per statement, holding physical lines open across brackets.

    A read split across physical lines shows its target only once the lines are joined,
    so the source-assertion matcher works on statements rather than on lines.
    """
    out, buf, depth = [], [], 0
    quote = None
    for line in text.splitlines():
        buf.append(line)
        quote, depth = scan_line(line, quote, depth)
        if depth == 0 and quote is None and not line.rstrip().endswith("\\"):
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


def has_app_marker(statement):
    """True when the statement names app source outright."""
    return bool(
        APP_LITERAL.search(statement)
        or MODULE_FILE.search(statement)
        or GETFILE_CALL.search(statement)
    )


def mentions_any(statement, names):
    """True when the statement uses one of the names already bound to app source."""
    return any(re.search(r"\b%s\b" % re.escape(name), statement) for name in names)


def app_path_names(statements):
    """Names bound to an app-source path anywhere in one file, to a fixpoint."""
    names = set()
    grew = True
    # A pass only ever adds names, and the file holds finitely many, so this terminates.
    while grew:
        grew = False
        for statement in statements:
            if not (has_app_marker(statement) or mentions_any(statement, names)):
                continue
            bound = set()
            if PATH_SHAPED.search(statement):
                match = ASSIGNMENT.search(statement)
                if match:
                    bound.add(match.group(1))
                bound.update(FOR_TARGET.findall(statement))
            # parametrize binds the test argument to a sequence that is already app
            # bound, and it carries no path syntax of its own.
            bound.update(PARAMETRIZE_ARG.findall(statement))
            for name in bound:
                if name not in names:
                    names.add(name)
                    grew = True
    return names


def source_assertion_sites(text):
    """How many times one test file reads app source as text or as a syntax tree.

    A pattern written inside a line comment is prose about the gate, not a read, so
    every count drops the comment first. Marker collection still reads the whole line,
    because a commented-out path is still a path this file knows about.
    """
    sites = 0
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        if "getsource" in code:
            sites += 1
        if "ast.parse" in code:
            sites += 1

    statements = logical_statements(text)
    names = app_path_names(statements)
    for statement in statements:
        if "read_text(" not in statement:
            continue
        if not (has_app_marker(statement) or mentions_any(statement, names)):
            continue
        for line in statement.splitlines():
            if "read_text(" in line.split("#", 1)[0]:
                sites += 1
    return sites


def walk_test_files():
    """(path relative to apps/api, absolute path) for every .py file under tests/."""
    root = os.path.join(API_DIR, "tests")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, API_DIR).replace("\\", "/"), full


def source_assertion_failures(found, baseline):
    """Failure lines for one scan of tests/ against a baseline. Empty means pass."""
    failures = []

    unpinned = sorted(path for path in found if path not in baseline)
    if unpinned:
        failures.append(
            "source assertions: %d test file(s) read app source and nothing pins them."
            % len(unpinned)
        )
        failures.append("Assert on behaviour instead. Never add a file to")
        failures.append("SOURCE_ASSERTION_BASELINE:")
        for path in unpinned:
            failures.append("  %s  x%d" % (path, found[path]))

    grown = sorted(path for path in baseline if found.get(path, 0) > baseline[path])
    if grown:
        failures.append("source assertions: %d pinned file(s) gained sites:" % len(grown))
        for path in grown:
            failures.append("  %s  pinned %d, found %d" % (path, baseline[path], found.get(path, 0)))

    stale = sorted(path for path in baseline if found.get(path, 0) < baseline[path])
    if stale:
        failures.append(
            "source assertions: %d baseline line(s) are stale. Lower the count, or" % len(stale)
        )
        failures.append("delete the line, in SOURCE_ASSERTION_BASELINE in scripts/gates.py so")
        failures.append("the smaller number is what the tree is held to:")
        for path in stale:
            failures.append("  %s  pinned %d, found %d" % (path, baseline[path], found.get(path, 0)))

    return failures


def run_source_assertions():
    """Fail on any test file reading app source that SOURCE_ASSERTION_BASELINE misses."""
    print("\n$ source assertions over tests/", flush=True)

    found = {}
    scanned = 0
    for relative, full in walk_test_files():
        with open(full, encoding="utf-8", errors="replace") as handle:
            sites = source_assertion_sites(handle.read())
        scanned += 1
        if sites:
            found[relative] = sites
    print("scanned %d file(s), %d with sites." % (scanned, len(found)), flush=True)

    failures = source_assertion_failures(found, SOURCE_ASSERTION_BASELINE)
    if failures:
        print("")
        for line in failures:
            print(line)
        return 1

    print(
        "source assertions: clean against the %d pinned file(s), %d site(s)."
        % (len(SOURCE_ASSERTION_BASELINE), sum(SOURCE_ASSERTION_BASELINE.values()))
    )
    return 0


# ---------------------------------------------------------------------------
# Log bounds (#166): an exception's own text may not reach a log line whole.
#
# `log.warning("x.failed", error=str(exc))` publishes whatever the exception
# carries. psycopg2 puts the failing statement in DETAIL and CONTEXT and the
# statements this tree fails on bind model output as a parameter; pydantic puts
# `input_value=` below line one; a connect failure renders the DSN. #164 bounded
# the red-team task, #166 converted the 120 sites in the other 46 modules to
# `app.core.log_bounds.log_failure`, and this gate is what stops the 121st.
#
# A site is a NAME THE MODULE BINDS AS AN EXCEPTION, read inside a keyword
# argument of a `log.<level>(...)` call. Binding is structural, not a spelling:
# `except ... as name` anywhere in the module, or a parameter annotated with an
# exception type. That is why `agent_id=str(agent_id)` is not a site while
# `error=f"{type(exc).__name__}: {exc}"` is, and why the scan sees the shapes a
# grep for `error=str(exc)` misses: `repr(exc)`, `exc.args`, `"%s" % exc`,
# `str(exc)[:200]`.
#
# What the gate is protecting is THE EXCEPTION'S OWN RENDERING, which is the part
# no author chose and no author bounded. Three readings are allowed through:
#
#   type(exc).__name__       a class name, and the field log_failure adds itself
#   bounded_error_detail(x)  the one function permitted to render the message
#   exc.<field>              a value the exception's own author named and sized,
#                            `NeonHTTPError.status_code` and `.message` being the
#                            two in this tree, the second cut to 200 where it is
#                            raised. `exc.args` is NOT a field in that sense: it
#                            is the message every exception carries, so it is a
#                            site. A chain like `exc.response.text` is not read
#                            past its first field and the gate would miss it;
#                            nothing in the tree does that today.
#
# `log_failure(log, event, exc, ...)` is not a `log.<level>` call at all, so a
# converted site never reaches the walk.
#
# THE PIN IS ZERO. There is no baseline to add a file to. A new site is a new
# leak, and the remedy is `log_failure`, never an entry here.
LOG_LEVELS = ("debug", "info", "warning", "error", "critical", "exception")

#: The two renderings of an exception that are bounded by construction.
BOUNDED_READERS = ("bounded_error_detail",)


def exception_bound_names(tree):
    """Every name this module binds to an exception, by either structural route."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
                if arg.annotation is None:
                    continue
                text = ast.dump(arg.annotation)
                if "Exception" in text or "Error" in text:
                    names.add(arg.arg)
    return names


def is_a_log_call(node):
    """True for `log.<level>(...)` and `logger.<level>(...)`, the two spellings here."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in ("log", "logger")
        and func.attr in LOG_LEVELS
    )


def reads_an_exception(value, names):
    """True when this keyword value reads one of `names` outside a bounded reader.

    The walk is explicit rather than `ast.walk` so a bounded subtree can be cut
    whole: descending into `type(exc).__name__` would find the Name and report a
    site on the one reading that carries no message at all.
    """
    if isinstance(value, ast.Name):
        return value.id in names
    if isinstance(value, ast.Attribute):
        if value.attr == "__name__" and _is_type_of_call(value.value, names):
            return False
        if isinstance(value.value, ast.Name) and value.value.id in names:
            return value.attr == "args"
        return reads_an_exception(value.value, names)
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Name) and value.func.id in BOUNDED_READERS:
            return False
    for child in ast.iter_child_nodes(value):
        if reads_an_exception(child, names):
            return True
    return False


def _is_type_of_call(node, names):
    """True for `type(<an exception name>)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "type"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in names
    )


def unbounded_log_sites(text):
    """(line, keyword) for every log keyword in one module that reads an exception."""
    tree = ast.parse(text)
    names = exception_bound_names(tree)
    if not names:
        return []
    sites = []
    for node in ast.walk(tree):
        if not is_a_log_call(node):
            continue
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            if reads_an_exception(keyword.value, names):
                sites.append((keyword.value.lineno, keyword.arg))
    return sorted(sites)


def walk_app_files():
    """(path relative to apps/api, absolute path) for every .py file under app/."""
    root = os.path.join(API_DIR, "app")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, API_DIR).replace("\\", "/"), full


def log_bound_failures(found, scanned):
    """Failure lines for one scan of app/. Empty means pass.

    An empty scan fails. A rename of `app/` would otherwise make this gate report
    a clean tree while reading nothing, which is the shape FM-013 already cost.
    """
    if not scanned:
        return [
            "log bounds: the scan read 0 files, so it proved nothing.",
            "walk_app_files found no app/ tree under " + API_DIR + ".",
        ]
    if not found:
        return []
    total = sum(len(sites) for sites in found.values())
    failures = [
        "log bounds: %d log line(s) in %d file(s) read an exception directly."
        % (total, len(found)),
        "Each one publishes whatever the exception carries. Use",
        "app.core.log_bounds.log_failure(log, event, exc, **fields) instead:",
    ]
    for path in sorted(found):
        for line, keyword in found[path]:
            failures.append("  %s:%d  %s=" % (path, line, keyword))
    return failures


def run_log_bounds():
    """Fail on any log line in app/ that reads an exception outside a bounded reader."""
    print("\n$ log bounds over app/", flush=True)

    found = {}
    scanned = 0
    for relative, full in walk_app_files():
        with open(full, encoding="utf-8", errors="replace") as handle:
            sites = unbounded_log_sites(handle.read())
        scanned += 1
        if sites:
            found[relative] = sites
    print("scanned %d file(s), %d with sites." % (scanned, len(found)), flush=True)

    failures = log_bound_failures(found, scanned)
    if failures:
        print("")
        for line in failures:
            print(line)
        return 1

    print("log bounds: clean, every failure line goes through log_failure.")
    return 0


# ---------------------------------------------------------------------------
# Process-wide keys (#101, #178): one source decides them, and it is
# tests/integration/conftest.py.
#
# `settings` freezes on the environment at the first `import app` in a process.
# pytest then IMPORTS every module under tests/integration, and a module-scope
# write after that freeze changes what a SPAWNED WORKER inherits while this
# process keeps the frozen value. The observed failure was every CI Integration
# run from 33150052552 onward: the worker encrypted with K2 and pytest decrypted
# with K1, and the error was InvalidToken, which names neither process.
#
# conftest.py is exempt, and it is the only exemption. pytest loads a directory's
# conftest before any test module in it, so it is the one place that can decide a
# value the rest of the directory inherits. Its own writes are unconditional on
# purpose: the root conftest already set CONTROL_DB_URL with setdefault, so an
# integration run pointing at the local cluster has to overwrite it.
#
# THE SCAN IS STRUCTURAL. The guard this replaces matched
# `^os.environ["KEY"] =` at column 0, and a probe file carrying five module-scope
# rebinds passed it: an assignment indented under `if True:`, one inside `try:`,
# `os.environ.update({...})`, `os.putenv`, and a helper called at import. Every
# one of those is module scope in Python, and column 0 is not what module scope
# means. This walks the tree instead: every statement outside a `def` or `class`,
# including the bodies of `if`, `try`, `with`, `for` and `while`, plus any
# module-level helper such a statement calls.
#
# `os.environ.setdefault` is the remedy and reads as no write at all: it takes the
# value already in the environment when there is one, which is the whole point.
#
# THE PIN IS ZERO, and an empty scan FAILS. A renamed tests/integration would
# otherwise leave this reporting a clean tree over nothing, which is the shape
# FM-013 already cost.
PROCESS_WIDE_KEYS = (
    "NEON_ENCRYPTION_KEY",
    "PLATFORM_CREDENTIAL_KEY",
    "CONTROL_DB_URL",
    "CONTROL_DB_SYNC_URL",
)

#: A write whose key is not a literal. Nothing can say which key it sets, so it
#: counts against every one of them rather than none.
UNKNOWN_KEY = "<not a literal>"

#: os.environ methods that change the environment. `setdefault` is absent
#: deliberately: it is what a module is told to use instead.
ENV_MUTATORS = ("update", "pop", "clear", "__setitem__", "__delitem__")


def _is_the_environment(node):
    """True for `os.environ` and for a bare `environ` taken by from-import."""
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


def _literal_or_unknown(node):
    """One key name, or UNKNOWN_KEY when the expression is not a plain string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return UNKNOWN_KEY


def _keys_written_by(node):
    """Keys one statement or expression writes into the environment, any spelling."""
    keys = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _is_the_environment(target.value):
                    keys.add(_literal_or_unknown(target.slice))
        if isinstance(sub, ast.Delete):
            for target in sub.targets:
                if isinstance(target, ast.Subscript) and _is_the_environment(target.value):
                    keys.add(_literal_or_unknown(target.slice))
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "os" and func.attr in ("putenv", "unsetenv"):
                keys.add(_literal_or_unknown(sub.args[0]) if sub.args else UNKNOWN_KEY)
        if isinstance(func, ast.Attribute) and _is_the_environment(func.value):
            if func.attr not in ENV_MUTATORS:
                continue
            if func.attr == "update":
                keys.update(_keys_of_the_mapping(sub))
            elif sub.args:
                keys.add(_literal_or_unknown(sub.args[0]))
            else:
                keys.add(UNKNOWN_KEY)
    return keys


def _keys_of_the_mapping(call):
    """The keys an `os.environ.update(...)` sets, from a dict literal or keywords."""
    keys = set()
    for keyword in call.keywords:
        keys.add(keyword.arg if keyword.arg else UNKNOWN_KEY)
    for arg in call.args:
        if isinstance(arg, ast.Dict):
            for key in arg.keys:
                keys.add(_literal_or_unknown(key) if key is not None else UNKNOWN_KEY)
        else:
            keys.add(UNKNOWN_KEY)
    return keys


def module_scope_statements(body):
    """Every statement at module scope, walking into blocks but never into a def."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        for field in ("body", "orelse", "finalbody"):
            for child in getattr(node, field, []) or []:
                yield from module_scope_statements([child])
        for handler in getattr(node, "handlers", []) or []:
            yield from module_scope_statements(handler.body)


def helpers_that_write(tree):
    """name -> the keys that module-level function writes, for the ones that write."""
    writers = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            keys = set()
            for statement in node.body:
                keys |= _keys_written_by(statement)
            if keys:
                writers[node.name] = keys
    return writers


def process_wide_env_writes(text):
    """Process-wide keys one module rebinds at import time, by any spelling."""
    tree = ast.parse(text)
    writers = helpers_that_write(tree)
    keys = set()
    for statement in module_scope_statements(tree.body):
        keys |= _keys_written_by(statement)
        for sub in ast.walk(statement):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                keys |= writers.get(sub.func.id, set())
    guarded = set(PROCESS_WIDE_KEYS)
    if UNKNOWN_KEY in keys:
        return sorted(guarded | {UNKNOWN_KEY})
    return sorted(keys & guarded)


def walk_integration_files():
    """(path relative to apps/api, absolute path) for every .py under tests/integration.

    RECURSIVE. The glob this replaces was not, so a future tests/integration/<dir>/
    was unscanned. conftest.py is the directory's single source and is skipped.
    """
    root = os.path.join(API_DIR, "tests", "integration")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py") or name == "conftest.py":
                continue
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, API_DIR).replace("\\", "/"), full


def process_wide_key_failures(found, scanned):
    """Failure lines for one scan of tests/integration. Empty means pass."""
    if not scanned:
        return [
            "process-wide keys: the scan read 0 files, so it proved nothing.",
            "walk_integration_files found no tests/integration tree under " + API_DIR + ".",
        ]
    if not found:
        return []
    failures = [
        "process-wide keys: %d module(s) rebind a process-wide key at import time."
        % len(found),
        "Every worker spawned after collection inherits a value this process has",
        "already frozen past (#101). Use os.environ.setdefault, or put the value in",
        "tests/integration/conftest.py, which is the one source that decides these:",
    ]
    for path in sorted(found):
        failures.append("  %s  %s" % (path, ", ".join(found[path])))
    return failures


def run_process_wide_keys():
    """Fail on any module under tests/integration that rebinds a process-wide key."""
    print("\n$ process-wide keys over tests/integration", flush=True)

    found = {}
    scanned = 0
    for relative, full in walk_integration_files():
        with open(full, encoding="utf-8", errors="replace") as handle:
            keys = process_wide_env_writes(handle.read())
        scanned += 1
        if keys:
            found[relative] = keys
    print("scanned %d file(s), %d with writes." % (scanned, len(found)), flush=True)

    failures = process_wide_key_failures(found, scanned)
    if failures:
        print("")
        for line in failures:
            print(line)
        return 1

    print("process-wide keys: clean, conftest.py is the only source.")
    return 0

#: The unit suite's argv, named so a test can read what `steps("full")` runs.
#: `-rs` prints every skip with its reason. Under `-q` alone a test that stopped
#: running because its database or its marker went missing is one more dot, and a
#: skip is unobserved rather than passing. The lambda in `steps` builds its command
#: from this list, so dropping a flag here is a change a test can see.
UNIT_PYTEST_ARGS = ["-m", "pytest", "tests/unit", "-q", "-rs"]


def steps(mode):
    """Ordered (label, callable) pairs for the requested mode.

    The split is by COST OF IMPORT, not by conceptual tidiness. Nothing in
    `static` imports app code, so its runtime is bounded by the size of the
    source rather than by the dependency tree; everything after it pays the
    docling/transformers/torch import once. That is the line the 170s hook clamp
    actually sits on.
    """
    static = [
        ("ruff", run_ruff),
        # lint-imports must be the console script. `python -m importlinter.cli`
        # exits 0 without checking anything, which would make this a silent pass.
        ("import contracts", lambda: run([tool("lint-imports")])),
        ("complexity", run_complexity),
        ("source assertions", run_source_assertions),
        ("log bounds", run_log_bounds),
        ("process-wide keys", run_process_wide_keys),
    ]
    if mode == "static":
        return static
    # mypy stays out of `static`. It builds a type graph over the whole tree, and warm
    # runs here took 26s to 80s on 2026-09-02 against 11s to 16s for the six static
    # steps together. `static` is what the Stop hook runs. CI's Type-check job runs this
    # mode on its own.
    if mode == "mypy":
        return [("mypy", run_mypy)]
    fast = static + [
        ("mypy", run_mypy),
        ("test collection", lambda: run([PYTHON, "-m", "pytest", "tests/unit", "-q", "--collect-only"])),
    ]
    if mode == "fast":
        return fast
    return fast + [
        ("unit tests", lambda: run([PYTHON] + UNIT_PYTEST_ARGS)),
    ]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("static", "mypy", "fast", "full"):
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
