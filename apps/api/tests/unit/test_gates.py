"""Pins the standards the static gate enforces and holds all four baselines to a snapshot.

scripts/ is not a package, so this loads gates.py by path.

The source-assertion gate scans this file too. Every pattern exercised below is built
by joining fragments, because writing one out whole would make this file a site and the
gate would go red on its own test.
"""

import importlib.util
import pathlib

import pytest

API_DIR = pathlib.Path(__file__).resolve().parents[2]
GATES_PATH = API_DIR / "scripts" / "gates.py"


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


def test_the_unit_suite_runs_with_rs(monkeypatch):
    """`steps("full")`'s unit-tests step passes -rs, so every skip prints its reason.

    Under -q alone a test that stopped running is one more dot, and a skip read as
    a pass is how a gate goes quiet without going red.

    The argv is recorded off a stubbed `run` rather than read from
    UNIT_PYTEST_ARGS, because the step is a lambda: one that stopped reading the
    constant and spelled its own command would still satisfy an assertion on the
    constant alone.
    """
    recorded = []
    monkeypatch.setattr(gates, "run", lambda command: recorded.append(command) or 0)

    unit = [step for label, step in gates.steps("full") if label == "unit tests"]
    assert len(unit) == 1, (
        'steps("full") no longer has exactly one step labelled "unit tests". '
        "re-point this test at whatever runs the unit suite now"
    )
    assert unit[0]() == 0
    assert len(recorded) == 1, "the unit-tests step ran %d commands" % len(recorded)

    argv = recorded[0]
    assert argv[0] == gates.PYTHON
    assert "tests/unit" in argv, (
        "the unit-tests step no longer names tests/unit, so this test is pinning "
        "the flags of some other command: %r" % (argv,)
    )
    assert "-rs" in argv, (
        "the unit suite runs without -rs: %r. A test that skips because its "
        "database or its marker went missing then reports as one more `s` with no "
        "reason anywhere in the log." % (argv,)
    )


# The four snapshots below mirror the baselines in scripts/gates.py exactly. The
# assertions that read them reject every difference in either direction: a new key, a
# dropped key, a larger value and a smaller one. Moving a pin therefore edits this file
# in the same commit, which is the point.
#
# A ceiling ("the gate is at or below the snapshot") looks like the same rule and is
# not. A pin that comes down under a ceiling leaves the gap between the two numbers as
# slack, and a later commit can raise the pin back up through that slack with no edit
# here and nothing red.

PINNED_RUFF = {}

PINNED_MYPY = {
    "app/core/model_client.py": 3,
    "app/domain/ingestion_job.py": 2,
    "app/services/agent_tools.py": 5,
    "app/services/deployment_service.py": 3,
    "app/services/red_team_service.py": 7,
    "app/worker/tasks/runtime/agent.py": 2,
}

PINNED_LIZARD = {
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
    ("app/services/agent_prompt.py", "build_system_prompt"): (14, 64),
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
    ("app/services/digest_service.py", "_collect_digest_stats"): (10, 70),
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

PINNED_SOURCE = {
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


def snapshot_differences(name, baseline, snapshot):
    """Every disagreement between one gate baseline and its snapshot. Empty means equal.

    Reports both directions. A key the snapshot lacks is an unpinned entry someone added
    to the baseline; a key the baseline lacks is a pin that left the gate and can come
    back at the snapshot's number unnoticed; a differing value is slack in either
    direction.
    """
    differences = []
    for key, value in baseline.items():
        if key not in snapshot:
            differences.append("%r was added to %s and the snapshot does not carry it" % (key, name))
        elif value != snapshot[key]:
            differences.append(
                "%r is %r in %s and %r in the snapshot" % (key, value, name, snapshot[key])
            )
    for key in snapshot:
        if key not in baseline:
            differences.append("%r left %s and the snapshot still carries it" % (key, name))
    return sorted(differences)


def test_ruff_baseline_equals_the_snapshot():
    """Every RUFF_BASELINE count equals its snapshot count, and neither side has a spare key."""
    assert snapshot_differences("RUFF_BASELINE", gates.RUFF_BASELINE, PINNED_RUFF) == []


def test_mypy_baseline_equals_the_snapshot():
    """Every MYPY_BASELINE count equals its snapshot count, is above zero, and names a real file.

    A pin at zero errors, or a pin on a path that has left the tree, is a line the gate
    can never satisfy: run_mypy reads both as stale and stays red until someone deletes
    the line. The loop catches that here rather than after a 44s mypy run.
    """
    assert snapshot_differences("MYPY_BASELINE", gates.MYPY_BASELINE, PINNED_MYPY) == []
    for path, count in gates.MYPY_BASELINE.items():
        assert count > 0, "%s is pinned at 0 errors. Delete the line" % path
        assert (API_DIR / path).exists(), "%s is pinned and is not in the tree" % path


def test_lizard_baseline_equals_the_snapshot():
    """Every LIZARD_BASELINE pin equals its snapshot pair, and neither side has a spare key."""
    assert snapshot_differences("LIZARD_BASELINE", gates.LIZARD_BASELINE, PINNED_LIZARD) == []


def test_source_assertion_baseline_equals_the_snapshot():
    """Every SOURCE_ASSERTION_BASELINE count equals its snapshot count, both directions."""
    assert (
        snapshot_differences(
            "SOURCE_ASSERTION_BASELINE",
            gates.SOURCE_ASSERTION_BASELINE,
            PINNED_SOURCE,
        )
        == []
    )


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


MYPY_PIN = {"app/one.py": 3, "app/two.py": 2}

# Every case carries app/two.py, so no case passes by an empty reading, and the unpinned
# case carries both pinned files EXACTLY at their counts, so it cannot pass with the
# unpinned branch deleted.
MYPY_CASES = [
    (
        "a file with type errors that nothing pins",
        {"app/one.py": 3, "app/two.py": 2, "app/three.py": 1},
        True,
    ),
    ("a pinned file whose count grew", {"app/one.py": 4, "app/two.py": 2}, True),
    ("a pinned file whose count shrank", {"app/one.py": 2, "app/two.py": 2}, True),
    ("a pinned file mypy no longer errors in", {"app/two.py": 2}, True),
    (
        "an unpinned file beside a pinned file that grew",
        {"app/one.py": 4, "app/two.py": 2, "app/three.py": 1},
        True,
    ),
    ("the tree exactly as pinned", {"app/one.py": 3, "app/two.py": 2}, False),
]


@pytest.mark.parametrize("label, found, expect_failure", MYPY_CASES)
def test_mypy_failures(label, found, expect_failure):
    """Unpinned, grown and stale each fail. An exact match passes."""
    assert bool(gates.mypy_failures(found, MYPY_PIN)) is expect_failure


def test_a_mixed_mypy_reading_reports_the_growth_alone():
    """One file up and another down reports growth only, so one remedy reaches the reader."""
    failures = gates.mypy_failures({"app/one.py": 4, "app/two.py": 1}, MYPY_PIN)
    report = "\n".join(failures)
    assert "gained type errors" in report
    assert "stale" not in report


def test_an_unpinned_file_is_still_reported_beside_growth():
    """Growth suppresses the stale lines, never the unpinned ones."""
    failures = gates.mypy_failures({"app/one.py": 4, "app/two.py": 2, "app/three.py": 1}, MYPY_PIN)
    report = "\n".join(failures)
    assert "gained type errors" in report
    assert "nothing pins them" in report
    assert "app/three.py" in report


# The two lines CI printed for seventeen days (#92): one error, then a summary with no
# denominator. mypy had opened 1 of 150 files.
NINETY_TWO = (
    "app/services/red_team_probe.py:434: error: Expected an indented block after "
    "'if' statement on line 428  [syntax]\n"
    "Found 1 error in 1 file (errors prevented further checking)\n"
)


def test_the_92_shape_parses_one_line_and_no_summary():
    """The aborted run reads as one error and reported=None, so run_mypy fails it."""
    found, parsed, reported = gates.parse_mypy_output(NINETY_TWO)
    assert found == {"app/services/red_team_probe.py": 1}
    assert parsed == 1
    assert reported is None
    assert gates.mypy_did_not_run(2, found) is False


def test_parse_mypy_output_reads_backslashes_and_columns():
    output = (
        "app\\services\\x.py:12: error: boom  [arg-type]\n"
        "app/services/y.py:3:5: error: boom  [arg-type]\n"
        "Found 2 errors in 2 files (checked 159 source files)\n"
    )
    found, parsed, reported = gates.parse_mypy_output(output)
    assert found == {"app/services/x.py": 1, "app/services/y.py": 1}
    assert parsed == 2
    assert reported == 2


def test_a_clean_tree_reads_as_zero_reported():
    found, parsed, reported = gates.parse_mypy_output("Success: no issues found in 159 source files\n")
    assert (found, parsed, reported) == ({}, 0, 0)


def test_a_broken_mypy_parse_is_not_a_pass():
    """mypy exiting nonzero with nothing this parser could read has to fail the gate.

    A crash before the first error line, or a changed line format, both read as exit
    nonzero with nothing parsed. Neither may reach mypy_failures, which would call an
    empty result clean. The #92 shape is the summary check's; see NINETY_TWO above.
    """
    assert gates.mypy_did_not_run(2, {}) is True
    assert gates.mypy_did_not_run(1, {}) is True
    assert gates.mypy_did_not_run(1, {"app/one.py": 3}) is False
    assert gates.mypy_did_not_run(0, {}) is False


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


# ---------------------------------------------------------------------------
# Log bounds (#166). Every snippet below is a whole module, because the scan
# reads a module: the names it treats as exceptions come from the `except`
# clauses and the annotated parameters of the same file.

HANDLER = """
def work():
    try:
        run()
    except Exception as exc:
        %s
"""

LOG_BOUND_CASES = [
    ("the shape #166 converted", 'log.warning("x.failed", error=str(exc))', 1),
    ("an id rendered beside it", 'log.warning("x.failed", agent_id=str(agent_id))', 0),
    ("the class name on its own", 'log.warning("x.failed", error_type=type(exc).__name__)', 0),
    ("the bounded reader", 'log.warning("x.failed", error=bounded_error_detail(exc))', 0),
    ("a repr instead of a str", 'log.warning("x.failed", error=repr(exc))', 1),
    ("a slice of the message", 'log.warning("x.failed", exc_msg=str(exc)[:200])', 1),
    ("an f-string carrying both", 'log.warning("x.failed", error=f"{type(exc).__name__}: {exc}")', 1),
    ("percent formatting", 'log.warning("x.failed", error="%s" % exc)', 1),
    ("the exception passed whole", 'log.warning("x.failed", error=exc)', 1),
    ("args, the message every exception has", 'log.warning("x.failed", error=exc.args)', 1),
    ("a field the raiser named and sized", 'log.warning("x.failed", detail=exc.message)', 0),
    ("a scalar field", 'log.warning("x.failed", status_code=exc.status_code)', 0),
    ("two leaks in one call", 'log.error("x.failed", a=str(exc), b=repr(exc))', 2),
    ("the converted call", 'log_failure(log, "x.failed", exc, agent_id=agent_id)', 0),
    ("the event, which is positional", 'log.warning(str(exc))', 0),
]


@pytest.mark.parametrize("label, call, expected", LOG_BOUND_CASES)
def test_unbounded_log_sites(label, call, expected):
    """The scan reports the exception's own rendering and nothing else."""
    assert len(gates.unbounded_log_sites(HANDLER % call)) == expected


def test_an_exception_reaches_the_scan_through_an_annotation_too():
    """A helper that takes the exception as a parameter has no except clause to find."""
    module = 'def note(exc: Exception) -> None:\n    log.warning("x.failed", error=str(exc))\n'
    assert len(gates.unbounded_log_sites(module)) == 1


def test_a_module_that_binds_no_exception_is_read_as_clean():
    """Without a bound name there is nothing to leak, and `str` is left alone."""
    assert gates.unbounded_log_sites('log.info("started", agent_id=str(agent_id))\n') == []


def test_the_log_bounds_pin_is_zero():
    """One site anywhere fails the gate. There is no baseline to add a file to."""
    assert gates.log_bound_failures({}, 162) == []
    report = "\n".join(gates.log_bound_failures({"app/one.py": [(12, "error")]}, 162))
    assert "app/one.py:12  error=" in report
    assert "log_failure" in report


def test_a_scan_that_read_nothing_is_not_a_pass():
    """A renamed app/ makes the walk return nothing, and a gate over nothing is vacuous."""
    assert gates.log_bound_failures({}, 0) != []


def test_log_bounds_runs_in_the_static_mode():
    """The gate the Stop hook runs is the one that carries this check."""
    assert "log bounds" in [label for label, _ in gates.steps("static")]


# ---------------------------------------------------------------------------
# Process-wide keys (#101, #178). The five spellings below are the ones a probe
# file carried straight past the column-0 regex this replaced. Each snippet is a
# whole module, because module scope is what the scan is about.

KEY = "NEON_ENCRYPTION_KEY"

ENV_WRITE_CASES = [
    ("the plain assignment, column 0", 'import os\nos.environ["%s"] = "k"\n' % KEY, [KEY]),
    (
        "indented under a module-scope if",
        'import os\nif True:\n    os.environ["%s"] = "k"\n' % KEY,
        [KEY],
    ),
    (
        "inside a try block",
        'import os\ntry:\n    os.environ["%s"] = "k"\nexcept Exception:\n    pass\n' % KEY,
        [KEY],
    ),
    (
        "inside the except block",
        'import os\ntry:\n    pass\nexcept Exception:\n    os.environ["%s"] = "k"\n' % KEY,
        [KEY],
    ),
    ("update with a dict literal", 'import os\nos.environ.update({"%s": "k"})\n' % KEY, [KEY]),
    ("update with a keyword", "import os\nos.environ.update(%s='k')\n" % KEY, [KEY]),
    ("putenv", 'import os\nos.putenv("%s", "k")\n' % KEY, [KEY]),
    ("pop", 'import os\nos.environ.pop("%s", None)\n' % KEY, [KEY]),
    (
        "a helper called at import time",
        'import os\ndef _bind():\n    os.environ["%s"] = "k"\n_bind()\n' % KEY,
        [KEY],
    ),
    (
        "environ taken by from-import",
        'from os import environ\nenviron["%s"] = "k"\n' % KEY,
        [KEY],
    ),
    (
        "the remedy, which is not a write",
        'import os\nos.environ.setdefault("%s", "k")\n' % KEY,
        [],
    ),
    (
        "a helper that is defined and never called",
        'import os\ndef _bind():\n    os.environ["%s"] = "k"\n' % KEY,
        [],
    ),
    (
        "a write inside a test function, which runs after collection",
        'import os\ndef test_it():\n    os.environ["%s"] = "k"\n' % KEY,
        [],
    ),
    ("a key nothing guards", 'import os\nos.environ["PATH"] = "k"\n', []),
    ("a read, not a write", 'import os\nvalue = os.environ["%s"]\n' % KEY, []),
]


@pytest.mark.parametrize("label, module, expected", ENV_WRITE_CASES)
def test_process_wide_env_writes(label, module, expected):
    """Module scope by any spelling, and nothing that runs later."""
    assert gates.process_wide_env_writes(module) == expected


def test_a_key_that_is_not_a_literal_counts_against_every_guarded_key():
    """Nothing can say which key it sets, so the scan fails closed rather than open."""
    found = gates.process_wide_env_writes("import os\nos.environ[name] = value\n")
    assert gates.UNKNOWN_KEY in found
    for key in gates.PROCESS_WIDE_KEYS:
        assert key in found


def test_control_db_url_is_guarded_too():
    """It is the same class of key, and it was outside the old guard's set."""
    assert "CONTROL_DB_URL" in gates.PROCESS_WIDE_KEYS
    assert gates.process_wide_env_writes(
        'import os\nos.environ["CONTROL_DB_URL"] = "x"\n'
    ) == ["CONTROL_DB_URL"]


def test_the_process_wide_key_pin_is_zero():
    """One rebind anywhere fails the gate. There is no baseline to add a file to."""
    assert gates.process_wide_key_failures({}, 32) == []
    report = "\n".join(
        gates.process_wide_key_failures({"tests/integration/test_x.py": [KEY]}, 32)
    )
    assert "tests/integration/test_x.py" in report
    assert "setdefault" in report


def test_a_key_scan_that_read_nothing_is_not_a_pass():
    """A renamed tests/integration would leave the old glob asserting over []."""
    assert gates.process_wide_key_failures({}, 0) != []


def test_the_scan_walks_the_integration_tree_recursively_and_skips_conftest():
    """conftest is the one source; a subdirectory was unscanned by the old glob."""
    scanned = [relative for relative, _ in gates.walk_integration_files()]
    assert scanned, "the walk found no integration modules at all"
    assert not [path for path in scanned if path.endswith("conftest.py")]
    assert "tests/integration/test_usage_rollup_e2e.py" in scanned


def test_process_wide_keys_runs_in_the_static_mode():
    """The gate the Stop hook runs is the one that carries this check."""
    assert "process-wide keys" in [label for label, _ in gates.steps("static")]
