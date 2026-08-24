"""Structural gates for apps/api. Standard library only, no dependencies.

    python scripts/gates.py static  ruff, import contracts, complexity, source
                                    assertions. None of them imports app code
    python scripts/gates.py fast    the above, plus whole-suite test collection
    python scripts/gates.py full    the above, plus the unit suite

`static` exists because of a measured ceiling, not a preference. The Stop hook
clamps its gate to 170s, and above that the harness kills the hook and the gate
disappears silently, which reads exactly like a gate that was never declared.
Test collection imports the whole app: docling pulls transformers and torch, and
it was measured at 142.5s on 2026-08-18 against 78.8s on 2026-08-15, growing with
the dependency tree rather than with the suite. It crossed the clamp and the hook
gate was killed mid-run. `static` runs the four steps that never import app code:

    ruff                1.8s   measured 2026-08-18
    import contracts    1.4s   measured 2026-08-18
    complexity          2.6s   measured 2026-08-24, lizard parses, it does not import
    source assertions   3.6s   measured 2026-08-24, one text pass over tests/

That is the hook's gate now. Collection and the suite belong to `fast` and `full`,
which are run deliberately and detached, not at the end of every session.

Three of the four steps are baselines rather than ceilings. RUFF_BASELINE,
LIZARD_BASELINE and SOURCE_ASSERTION_BASELINE each hold what the tree contains
today, and each fails three ways: on something new, on something that grew, and on
an entry that has gone stale. Entries come out as the code improves. Entries never
go in. tests/unit/test_gates.py holds a snapshot of all three baselines and fails on
an addition.

Tightening a threshold is a deliberate act: move it down, watch the gate go red,
fix the code, move it down again. Never raise one to make a red gate green.
"""

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

# ruff check app exits 1 today on one pre-existing I001, so it cannot be the gate
# as-is. It is pinned by COUNT: (file, rule) -> how many times that rule may
# fire in that file. The gate fails three ways.
#
#   - a (file, rule) pair that is not on this list appears
#   - a pinned pair fires MORE times than its count, e.g. a second I001 in
#     chunk.py, which is a new violation wearing an already-pinned name
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
#
# tests/unit/test_gates.py snapshots this dict and goes red on an addition.
RUFF_BASELINE = {
    ("app/worker/tasks/pipeline/chunk.py", "I001"): 1,
}

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
    ("app/api/deps.py", "get_current_tenant"): (19, 120),
    ("app/api/v1/agent_chat.py", "get_agent_conversations"): (4, 69),
    ("app/api/v1/agent_chat.py", "post_agent_chat"): (8, 160),
    ("app/api/v1/agents.py", "patch_agent"): (13, 61),
    ("app/api/v1/capability_envelopes.py", "patch_capability_envelope"): (12, 102),
    ("app/api/v1/deployment.py", "_refuse_if_a_critical_finding_is_open"): (4, 77),
    ("app/api/v1/deployment.py", "acknowledge_warnings"): (17, 66),
    ("app/api/v1/deployment.py", "approve_deployment"): (12, 143),
    ("app/api/v1/documents.py", "delete_document"): (10, 176),
    ("app/api/v1/documents.py", "get_document_detail"): (12, 133),
    ("app/api/v1/documents.py", "upload_documents"): (15, 194),
    ("app/api/v1/evals.py", "get_eval_run_results"): (20, 107),
    ("app/api/v1/evals.py", "list_eval_runs"): (25, 160),
    ("app/api/v1/pending_confirmations.py", "resolve_pending_confirmation"): (8, 178),
    ("app/api/v1/query.py", "post_agent_query"): (3, 77),
    ("app/api/v1/red_team.py", "_contain_finding_sync"): (6, 64),
    ("app/api/v1/webhooks.py", "clerk_webhook"): (9, 79),
    ("app/api/v1/webhooks.py", "provision_me"): (5, 89),
    ("app/api/v1/widget.py", "get_widget_config"): (5, 64),
    ("app/api/v1/widget.py", "post_widget_chat"): (9, 144),
    ("app/api/v1/widget.py", "post_widget_feedback"): (4, 88),
    ("app/api/v1/widget.py", "post_widget_identity_request"): (3, 63),
    ("app/api/v1/widget.py", "post_widget_identity_verify"): (5, 88),
    ("app/api/v1/widget.py", "widget_job_events"): (3, 76),
    ("app/services/actor_seam.py", "call_actor_gate"): (13, 183),
    ("app/services/agent_prompt.py", "build_system_prompt"): (14, 81),
    ("app/services/agent_tools.py", "build_tool_server"): (2, 141),
    ("app/services/agent_tools.py", "escalate_to_human_tool"): (4, 101),
    ("app/services/agent_tools.py", "lookup_structured_tool"): (5, 67),
    ("app/services/agent_tools.py", "retrieve_tool"): (35, 251),
    ("app/services/alert_service.py", "check_and_write_alerts"): (17, 50),
    ("app/services/bench_service.py", "grade_trace"): (5, 66),
    ("app/services/bench_service.py", "list_failing_traces"): (8, 87),
    ("app/services/capability_service.py", "validate_tighten_only"): (31, 154),
    ("app/services/chunking_service.py", "chunk_document"): (10, 98),
    ("app/services/deployment_service.py", "_agent_not_invoked_warning"): (3, 76),
    ("app/services/deployment_service.py", "_eval_summary"): (5, 69),
    ("app/services/deployment_service.py", "_fetch_blast_radius_sync"): (11, 147),
    ("app/services/deployment_service.py", "_fetch_eval_summary_sync"): (18, 312),
    ("app/services/deployment_service.py", "_fetch_red_team_summary_sync"): (11, 122),
    ("app/services/deployment_service.py", "apply_signal_evidence_gate"): (20, 292),
    ("app/services/deployment_service.py", "derive_blast_radius_warnings"): (8, 77),
    ("app/services/digest_service.py", "_collect_digest_stats"): (12, 81),
    ("app/services/eval_service.py", "build_eval_run_config"): (11, 207),
    ("app/services/eval_service.py", "insert_eval_run"): (4, 62),
    ("app/services/eval_service.py", "run_ragas_eval"): (24, 169),
    ("app/services/eval_service.py", "summarise_agent_invocation"): (33, 197),
    ("app/services/eval_service.py", "summarise_run_validity"): (17, 117),
    ("app/services/eval_service.py", "update_eval_run_config"): (4, 70),
    ("app/services/identity_service.py", "verify_otp"): (8, 103),
    ("app/services/label_service.py", "record_human_label"): (6, 120),
    ("app/services/metrics_service.py", "_build_metrics_dict"): (16, 74),
    ("app/services/neon.py", "create_neon_project"): (6, 76),
    ("app/services/red_team_probe.py", "_build_transactional_probe_fn"): (1, 146),
    ("app/services/red_team_probe.py", "_build_transactional_probe_fn._inner"): (14, 112),
    ("app/services/red_team_service.py", "_run_sdk_attacker"): (5, 84),
    ("app/services/red_team_service.py", "build_probe_tools"): (1, 93),
    ("app/services/red_team_service.py", "classify_severity"): (4, 100),
    ("app/services/red_team_service.py", "run_confused_deputy_agent"): (1, 72),
    ("app/services/red_team_service.py", "run_content_injection_agent"): (13, 153),
    ("app/services/red_team_service.py", "run_coverage"): (10, 61),
    ("app/services/red_team_service.py", "run_identity_bypass_agent"): (17, 165),
    ("app/services/red_team_service.py", "run_value_bound_evasion_agent"): (17, 149),
    ("app/services/red_team_service.py", "seed_poisoned_chunk"): (2, 75),
    ("app/services/redteam_programme_service.py", "read_programme"): (16, 99),
    ("app/services/retrieval_service.py", "rrf_fuse"): (6, 73),
    ("app/services/retrieval_service.py", "rrf_fuse_with_expansion"): (10, 71),
    ("app/services/retrieval_service.py", "verified_qa_lookup"): (3, 61),
    ("app/services/scenario_service.py", "generate_eval_suite_for_agent"): (7, 68),
    ("app/services/scenario_service.py", "generate_scenarios_from_chunks"): (7, 67),
    ("app/services/scenario_service.py", "mine_production_scenarios"): (11, 114),
    ("app/services/sse.py", "event_generator"): (13, 99),
    ("app/services/strategy_service.py", "_fetch_corpus_signals_sync"): (10, 74),
    ("app/services/transactional/adapters/calendly_adapter.py", "book_slot"): (2, 81),
    ("app/services/transactional/adapters/shopify_adapter.py", "issue_refund"): (3, 89),
    ("app/services/transactional/adapters/shopify_adapter.py", "place_order"): (3, 76),
    ("app/services/transactional/adapters/stripe_adapter.py", "place_order"): (1, 63),
    ("app/services/transactional/audit.py", "write_audit_row"): (2, 69),
    ("app/services/transactional/confirmation_resolution.py", "execute_approved_confirmation"): (14, 290),
    ("app/services/transactional/credential_service.py", "_fetch_credential_config"): (3, 69),
    ("app/services/transactional/enforcement.py", "apply_rate_and_constraint_checks"): (9, 117),
    ("app/services/transactional/enforcement.py", "check_capability_access"): (4, 76),
    ("app/services/transactional/idempotency.py", "reserve_idempotency"): (1, 185),
    ("app/services/transactional/idempotency.py", "reserve_idempotency._inner"): (14, 150),
    ("app/services/transactional/provider_adapter.py", "get_adapter_for_skill"): (10, 150),
    ("app/services/transactional/tools.py", "_execute_adapter_and_audit"): (3, 142),
    ("app/services/transactional/tools.py", "_execute_transactional_tool"): (34, 804),
    ("app/services/transactional/tools.py", "confirm_action_tool"): (8, 188),
    ("app/services/validation_service.py", "call_auditor"): (5, 139),
    ("app/services/validation_service.py", "call_gatekeeper"): (4, 79),
    ("app/services/validation_service.py", "call_strategist"): (8, 106),
    ("app/worker/tasks/pipeline/chunk.py", "chunk_documents"): (19, 243),
    ("app/worker/tasks/pipeline/embed.py", "embed_and_migrate"): (19, 296),
    ("app/worker/tasks/pipeline/migrations.py", "apply_migrations"): (6, 124),
    ("app/worker/tasks/pipeline/parse.py", "parse_documents"): (17, 257),
    ("app/worker/tasks/pipeline/provision.py", "provision_neon"): (21, 224),
    ("app/worker/tasks/pipeline/reembed.py", "reembed_corpus"): (13, 177),
    ("app/worker/tasks/pipeline/staleness.py", "compute_index_staleness_summary"): (11, 77),
    ("app/worker/tasks/pipeline/strategy.py", "synthesize_retrieval_strategy"): (17, 153),
    ("app/worker/tasks/runtime/agent.py", "_judge_retrieved_context"): (11, 99),
    ("app/worker/tasks/runtime/agent.py", "_persist_messages"): (2, 73),
    ("app/worker/tasks/runtime/agent.py", "_record_tool_result"): (10, 100),
    ("app/worker/tasks/runtime/agent.py", "_resolve_turn_prompt_version"): (5, 70),
    ("app/worker/tasks/runtime/agent.py", "_run_sdk_turn"): (14, 173),
    ("app/worker/tasks/runtime/agent.py", "_write_turn_metrics"): (1, 65),
    ("app/worker/tasks/runtime/agent.py", "build_agent_options"): (4, 160),
    ("app/worker/tasks/runtime/agent.py", "run_agent_turn"): (27, 506),
    ("app/worker/tasks/runtime/bench.py", "promote_trace_to_scenario"): (12, 164),
    ("app/worker/tasks/runtime/confirmations.py", "resolve_approved_confirmation"): (5, 111),
    ("app/worker/tasks/runtime/deployment.py", "_dispatch_eval_run"): (2, 62),
    ("app/worker/tasks/runtime/deployment.py", "run_deployment_checklist"): (26, 372),
    ("app/worker/tasks/runtime/eval.py", "_invoke_agent_for_scenarios"): (23, 236),
    ("app/worker/tasks/runtime/eval.py", "_run_one_eval_turn"): (3, 91),
    ("app/worker/tasks/runtime/eval.py", "generate_eval_suite"): (8, 84),
    ("app/worker/tasks/runtime/eval.py", "run_eval_suite"): (28, 613),
    ("app/worker/tasks/runtime/red_team.py", "_build_probe_fn"): (11, 65),
    ("app/worker/tasks/runtime/red_team.py", "run_red_team"): (33, 528),
    ("app/worker/tasks/runtime/retrieval_eval.py", "run_retrieval_faithfulness"): (14, 91),
    ("app/worker/tasks/runtime/retrieve.py", "retrieve_and_rank"): (12, 217),
    ("app/worker/tasks/runtime/validators.py", "run_auditor"): (15, 190),
    ("app/worker/tasks/runtime/validators.py", "run_gatekeeper"): (5, 107),
    ("app/worker/tasks/runtime/validators.py", "run_strategist"): (9, 116),
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
    "tests/unit/test_agent_options_seam.py": 5,
    "tests/unit/test_agreement_threshold.py": 1,
    "tests/unit/test_calibration_harness.py": 3,
    "tests/unit/test_capability_service.py": 2,
    "tests/unit/test_confirmation_resolution.py": 1,
    "tests/unit/test_deployment_service.py": 1,
    "tests/unit/test_eval_agent_invocation.py": 8,
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
    "tests/unit/test_red_team_service.py": 3,
    "tests/unit/test_s3_uploads.py": 1,
    "tests/unit/test_sdk_tools_are_registered.py": 2,
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
    ]
    if mode == "static":
        return static
    fast = static + [
        ("test collection", lambda: run([PYTHON, "-m", "pytest", "tests/unit", "-q", "--collect-only"])),
    ]
    if mode == "fast":
        return fast
    return fast + [
        ("unit tests", lambda: run([PYTHON, "-m", "pytest", "tests/unit", "-q"])),
    ]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("static", "fast", "full"):
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
