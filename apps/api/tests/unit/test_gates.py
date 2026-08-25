"""Pins the standards the static gate enforces and the never-add rule on all three baselines.

scripts/ is not a package, so this loads gates.py by path.

The source-assertion gate scans this file too. Every pattern exercised below is built
by joining fragments, because writing one out whole would make this file a site and the
gate would go red on its own test.
"""

import importlib.util
import pathlib

import pytest

GATES_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "gates.py"


def load_gates():
    """Import scripts/gates.py under its own name, by path."""
    spec = importlib.util.spec_from_file_location("gates_under_test", GATES_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gates = load_gates()


def test_lizard_flags_pin_the_standard():
    """CCN 15, 60 lines, 11 parameters, warnings only, in that order and nothing else.

    The whole list is the assertion. Lizard honours the LAST value of a repeated flag,
    so checking each flag's first occurrence would pass an appended `-C 99`.
    """
    assert gates.LIZARD_FLAGS == ["-C", "15", "-L", "60", "-a", "11", "--warnings_only"]


# The three snapshots below are the baselines as measured on 2026-08-24. The assertions
# that read them accept a smaller value and a missing key, and reject a new key or a
# larger value. Shrinking a baseline therefore needs no edit here. Growing one cannot
# happen without editing this file, which is the point.

PINNED_RUFF = {
    ("app/worker/tasks/pipeline/chunk.py", "I001"): 1,
}

PINNED_LIZARD = {
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
    ("app/services/agent_tools.py", "retrieve_tool"): (28, 248),
    ("app/services/alert_service.py", "check_and_write_alerts"): (17, 50),
    ("app/services/bench_service.py", "grade_trace"): (5, 66),
    ("app/services/bench_service.py", "list_failing_traces"): (8, 87),
    ("app/services/capability_service.py", "validate_tighten_only"): (31, 154),
    ("app/services/chunking_service.py", "chunk_document"): (10, 102),
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
    ("app/services/retrieval_service.py", "rrf_fuse"): (3, 65),
    ("app/services/retrieval_service.py", "rrf_fuse_with_expansion"): (8, 69),
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
    ("app/worker/tasks/pipeline/metadata.py", "generate_metadata"): (17, 239),
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

PINNED_SOURCE = {
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


def test_ruff_baseline_only_shrinks():
    """Every pinned violation is in the snapshot, at or below its snapshot count."""
    for key, count in gates.RUFF_BASELINE.items():
        assert key in PINNED_RUFF, "%s was added to RUFF_BASELINE" % (key,)
        assert count <= PINNED_RUFF[key], "%s was raised to %d" % (key, count)


def test_lizard_baseline_only_shrinks():
    """Every pinned function is in the snapshot, at or below its snapshot numbers."""
    for key, (ccn, length) in gates.LIZARD_BASELINE.items():
        assert key in PINNED_LIZARD, "%s was added to LIZARD_BASELINE" % (key,)
        pinned_ccn, pinned_length = PINNED_LIZARD[key]
        assert ccn <= pinned_ccn, "%s was raised to ccn %d" % (key, ccn)
        assert length <= pinned_length, "%s was raised to length %d" % (key, length)


def test_source_assertion_baseline_only_shrinks():
    """Every pinned test file is in the snapshot, at or below its snapshot count."""
    for path, count in gates.SOURCE_ASSERTION_BASELINE.items():
        assert path in PINNED_SOURCE, "%s was added to SOURCE_ASSERTION_BASELINE" % path
        assert count <= PINNED_SOURCE[path], "%s was raised to %d" % (path, count)


COMPLEXITY_PIN = {("app/one.py", "f"): (10, 20)}

# The unpinned case carries the pinned function EXACTLY at its pin alongside the new
# offender. Leaving it out would let the stale branch fail that case on its own, and the
# case would pass with the unpinned branch deleted.
COMPLEXITY_CASES = [
    (
        "a function over the standard that nothing pins",
        {("app/one.py", "f"): (10, 20), ("app/two.py", "g"): (16, 61)},
        True,
    ),
    ("a pinned function whose ccn grew", {("app/one.py", "f"): (11, 20)}, True),
    ("a pinned function whose length grew", {("app/one.py", "f"): (10, 21)}, True),
    ("a pinned function lizard no longer warns about", {}, True),
    ("a pinned function that came in under its pin", {("app/one.py", "f"): (9, 20)}, True),
    ("the tree exactly as pinned", {("app/one.py", "f"): (10, 20)}, False),
]


@pytest.mark.parametrize("label, found, expect_failure", COMPLEXITY_CASES)
def test_complexity_failures(label, found, expect_failure):
    """Unpinned, grown and stale each fail. An exact match passes."""
    assert bool(gates.complexity_failures(found, COMPLEXITY_PIN)) is expect_failure


def test_a_mixed_regression_reports_the_growth_alone():
    """ccn up and length down reports growth only, so one remedy reaches the reader."""
    failures = gates.complexity_failures({("app/one.py", "f"): (11, 19)}, COMPLEXITY_PIN)
    report = "\n".join(failures)
    assert "grew past the baseline" in report
    assert "stale" not in report


SOURCE_PIN = {"tests/unit/test_one.py": 3}

# The unpinned case carries the pinned file EXACTLY at its count, for the same reason.
SOURCE_CASES = [
    (
        "a test file with sites that nothing pins",
        {"tests/unit/test_one.py": 3, "tests/unit/test_two.py": 1},
        True,
    ),
    ("a pinned file whose count grew", {"tests/unit/test_one.py": 4}, True),
    ("a pinned file whose count shrank", {"tests/unit/test_one.py": 2}, True),
    ("a pinned file with no sites left", {}, True),
    ("the tree exactly as pinned", {"tests/unit/test_one.py": 3}, False),
]


@pytest.mark.parametrize("label, found, expect_failure", SOURCE_CASES)
def test_source_assertion_failures(label, found, expect_failure):
    """Unpinned, grown and stale each fail. An exact match passes."""
    assert bool(gates.source_assertion_failures(found, SOURCE_PIN)) is expect_failure


# One warning line as lizard printed it on 2026-08-24, backslashes and all.
LIZARD_LINE = (
    r"app\api\v1\agents.py:154: warning: patch_agent has 32 NLOC, 13 CCN, "
    r"227 token, 3 PARAM, 61 length, 0 ND"
)
PATCH_AGENT = ("app/api/v1/agents.py", "patch_agent")


def test_parse_lizard_warnings_reads_a_measured_line():
    """The measured line yields its key with the path separators normalised."""
    found, duplicates = gates.parse_lizard_warnings(LIZARD_LINE)
    assert found == {PATCH_AGENT: (13, 61)}
    assert duplicates == []


def test_parse_lizard_warnings_survives_a_dropped_nd_field():
    """The trailing ND field is not anchored, so a lizard that drops it still parses."""
    found, duplicates = gates.parse_lizard_warnings(LIZARD_LINE.replace(", 0 ND", ""))
    assert found == {PATCH_AGENT: (13, 61)}
    assert duplicates == []


def test_parse_lizard_warnings_ignores_a_line_it_cannot_read():
    """A line that is not a warning contributes nothing."""
    found, duplicates = gates.parse_lizard_warnings("lizard is thinking about it")
    assert found == {}
    assert duplicates == []


def test_two_functions_sharing_a_name_cannot_hide_under_one_pin():
    """A repeated key is reported, and the gate arithmetic refuses the reading."""
    twice = LIZARD_LINE + "\n" + LIZARD_LINE.replace(":154:", ":900:").replace("13 CCN", "27 CCN")
    found, duplicates = gates.parse_lizard_warnings(twice)
    assert duplicates == [PATCH_AGENT]
    assert found == {PATCH_AGENT: (13, 61)}
    report = "\n".join(gates.duplicate_failures(duplicates))
    assert "app/api/v1/agents.py" in report
    assert "patch_agent" in report


def test_a_broken_parser_is_not_a_pass():
    """Lizard warning about something this parser cannot read has to fail the gate."""
    assert gates.parser_missed_warnings(1, {}) is True
    assert gates.parser_missed_warnings(1, {PATCH_AGENT: (13, 61)}) is False
    assert gates.parser_missed_warnings(0, {}) is False


# Fragments, not whole patterns. See the module docstring.
READ = "read_" "text"
REFLECT = "get" "source"
PARSE = "ast." "parse"

SITE_CASES = [
    (
        "a read through a quoted app path",
        'body = pathlib.Path("app/services/sse.py").' + READ + '(encoding="utf-8")\n',
        1,
    ),
    (
        "a fixture read under tmp_path",
        'body = (tmp_path / "fixture.json").' + READ + "()\n",
        0,
    ),
    (
        "a read through a name bound to an app path",
        'root = pathlib.Path("app")\nbody = (root / "services" / "sse.py").' + READ + "()\n",
        1,
    ),
    (
        "a test file reading its own location",
        "here = pathlib.Path(__file__)\nbody = here." + READ + "()\n",
        0,
    ),
    (
        "source reflection",
        "text = inspect." + REFLECT + "(target)\n",
        1,
    ),
    (
        "a parse of source text into a tree",
        "tree = " + PARSE + "(payload)\n",
        1,
    ),
    (
        "a pattern named in a line comment",
        "# never " + PARSE + " the app source in a test\n",
        0,
    ),
]


@pytest.mark.parametrize("label, snippet, expected", SITE_CASES)
def test_source_assertion_sites(label, snippet, expected):
    """The matcher counts app-source reads and skips fixture reads and self reads."""
    assert gates.source_assertion_sites(snippet) == expected
