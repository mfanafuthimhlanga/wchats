"""The one write path that may stamp a human trust tier on an eval label.

`eval_service.LABEL_TRUST_TIERS` has declared `human_verified` (2) and
`human_authored` (3) since D5 and nothing could produce either. This module
produces one of them — and the interesting part is not the UPDATE, it is the set
of restrictions that make the UPDATE unreachable from anything a model drives.

WHY THAT MATTERS MORE THAN THE WRITE ITSELF
    A trust tier is a claim about WHO WROTE a string. The claim is worth exactly
    as much as the difficulty of forging it. If a Celery task, an agent tool, a
    judge or a test fixture can call something that stamps `human_authored`, then
    `human_authored` means "some code said so", the hierarchy collapses to one
    tier, and `VERIFIED_QA_MIN_TRUST_TIER` — the gate standing between a model's
    prose and a real customer, via `retrieval_service.verified_qa_lookup` — is
    guarding a door with no wall attached.

THE FOUR RESTRICTIONS, EACH INDEPENDENTLY MUTABLE AND EACH SEPARATELY PINNED
    1. There is no tier parameter.
       `record_human_label()` does not accept a tier. It stamps
       HUMAN_AUTHORED_TIER, a module constant. A caller cannot ask for a tier
       because there is nowhere to put the request.
       Pinned by test_the_writer_has_no_tier_parameter.

    2. The import boundary.
       Only `app/api/` may reference this module. Nothing under `app/worker/`
       (every Celery task), nothing else under `app/services/` (every agent
       tool, judge, scenario producer and eval service), and no conftest
       fixture. The writer is reachable from an authenticated HTTP request and
       from nowhere else in the tree.
       Pinned by test_no_model_driven_module_may_import_the_human_label_writer.

    3. The model-driven writers cannot write the columns.
       `scenario_service.store_scenarios` and
       `scenario_service.insert_provenance_scenario` are the only INSERT paths
       into `eval_scenarios`, and between them they carry every model-driven
       producer: generated suites, mined production failures, promoted traces,
       contained red-team findings. Neither statement names a label-provenance
       column, so those producers physically cannot populate one — the failure
       mode is a NULL tier, which reads as "no human labelled this".
       Pinned by test_only_the_label_writer_writes_the_label_columns.

    4. The runtime context guard.
       Belt to the import boundary's braces, and the one that survives a caller
       who reaches this module by a route the static checks did not model
       (importlib, a monkeypatched attribute, a future refactor that moves a
       module across the boundary). `record_human_label` refuses outright when
       it finds itself executing inside a Celery task or inside an agent tool
       call.
       Pinned by test_a_celery_task_context_refuses_the_human_label and
       test_an_agent_tool_context_refuses_the_human_label.

    Restriction 4 is thread-local: Celery's current-task stack lives in
    `celery.utils.threads._LocalStack`, so a bare thread spawned from inside a
    task would not see the task, and `agent_tools`' ContextVars do not propagate
    into `run_in_executor` threads either (agent_tools.py:161). That hole is
    stated rather than papered over, and it is the reason restriction 4 is the
    last line rather than the only one: restrictions 2 and 3 do not depend on
    which thread is asking.

WHAT THIS MODULE DOES NOT DO
    It does not promote anything into `verified_qa`. A human label improves what
    the eval can measure; it reaches no customer. That is the owner's settled
    decision of 2026-08-08 (`.dev/plans/260808-d6-labelling-loop.md`), and
    `eval_service.VERIFIED_QA_PROMOTION_DECISION` carries the disablement and
    its reason onto every run. `is_promotable_to_verified_qa` still gates on
    `source`, still returns False for every source the schema allows, and this
    module does not change a row's `source`.

    It opens no connection and holds no connection string. The caller passes an
    already-open psycopg2 connection and owns the transaction, matching
    `scenario_service.insert_provenance_scenario` — which also keeps the
    "connection strings never leave the control DB fetch" rule trivially true
    here, because this module never sees one.
"""

from __future__ import annotations

import structlog

from app.services.eval_service import HUMAN_LABEL_TIERS, is_human_label_tier

log = structlog.get_logger(__name__)

# The tier this module stamps. `human_verified` (a human confirming a candidate
# someone else drafted) is a different act with no producer yet; 0016's CHECK
# admits it so that adding one later is a code change and not a migration.
HUMAN_AUTHORED_TIER = "human_authored"


class HumanLabelRefused(RuntimeError):
    """A human-tier write was attempted from a context a model drives.

    Deliberately not a subclass of ValueError: this is never a bad-input
    problem the caller can fix by passing something else. It means the call
    happened somewhere it must not happen.
    """


class LabelRejected(ValueError):
    """The label itself is unusable — empty answer, or no author named."""


def _current_celery_task():
    """The Celery task executing in this thread, or None.

    `celery._state.get_current_task()` is the plain function behind the
    `celery.current_task` proxy; it returns None outside a task. Imported
    lazily and defensively so that this module stays importable in an API
    process that has no Celery wiring at all — a guard that raises on import is
    a guard that gets deleted.
    """
    try:
        from celery import _state  # noqa: PLC0415
    except Exception:  # pragma: no cover - celery is a hard dependency here
        return None
    try:
        return _state.get_current_task()
    except Exception:  # pragma: no cover - defensive
        return None


def _current_agent_id() -> str:
    """The agent id of the tool-server context in scope, or ''.

    Set by `agent_tools.build_tool_server()` for the duration of an agent turn,
    so a non-empty value means a model is driving this call stack.
    """
    try:
        from app.services.agent_tools import _agent_id_var  # noqa: PLC0415

        return str(_agent_id_var.get() or "")
    except Exception:  # pragma: no cover - defensive
        return ""


def assert_human_context() -> None:
    """Refuse if a model is driving this call stack.

    Raises HumanLabelRefused inside a Celery task or an agent tool context.
    Split out from record_human_label so that the restriction is one named
    thing a reader can find, and so a future second human-tier write cannot
    reimplement a subtly weaker version of it.
    """
    task = _current_celery_task()
    if task is not None:
        raise HumanLabelRefused(
            "a human trust tier may not be stamped from inside a Celery task "
            f"(task={getattr(task, 'name', type(task).__name__)!r}); "
            "eval_scenarios.label_trust_tier means a human wrote this answer"
        )

    agent_id = _current_agent_id()
    if agent_id:
        raise HumanLabelRefused(
            "a human trust tier may not be stamped from inside an agent tool "
            f"context (agent_id={agent_id!r}); eval_scenarios.label_trust_tier "
            "means a human wrote this answer"
        )


# The UPDATE. Idempotent by construction: applying it twice with the same
# arguments leaves the same row state, so a retried request cannot produce a
# second label or a duplicated row. `labelled_at` moves on a genuine relabel,
# which is correct — it records when the answer now stored was written.
_LABEL_SQL = """
    UPDATE eval_scenarios
    SET reference_answer = %(reference_answer)s,
        label_trust_tier = %(tier)s,
        labelled_by = %(labelled_by)s,
        labelled_at = NOW()
    WHERE id = %(scenario_id)s::uuid
"""


def record_human_label(
    conn,
    *,
    scenario_id: str,
    reference_answer: str,
    labelled_by: str,
) -> dict:
    """Record a human-authored reference answer on one eval scenario.

    NOTE THE ABSENT PARAMETER. There is no `tier` argument and there must never
    be one: the tier is what this function asserts, not what its caller asks
    for. A caller able to name the tier is a caller able to name
    `human_authored` from anywhere, which is the whole thing the hierarchy is
    defending against.

    The row's `source` is not touched. `source` says where the QUESTION came
    from and stays true after the answer is written by someone else; a mined
    failure that the owner answers stays `source='mined'` and becomes
    `label_trust_tier='human_authored'`. Fusing the two is the defect
    `eval_service.label_trust_tier()` exists to prevent.

    Args:
        conn: An open psycopg2 connection. This function does NOT commit or
            close it — the caller owns the transaction, matching
            scenario_service.insert_provenance_scenario.
        scenario_id: UUID string of the eval_scenarios row to label.
        reference_answer: The answer the human wrote. Must be non-empty: an
            empty label is what the row already has, and writing a human tier
            over an empty string would claim a human authored nothing while
            making the row eligible to a selector that filters on
            `reference_answer != ''`.
        labelled_by: Identifier of the human. Must be non-empty — a label with
            no author is a tier with nothing behind it.

    Returns:
        {"scenario_id": str, "label_trust_tier": str, "labelled_by": str,
         "rows_updated": int}. `rows_updated` is 0 when no row has that id;
        that is reported, never raised, so the caller counts outcomes rather
        than catching them.

    Raises:
        HumanLabelRefused: called from inside a Celery task or an agent tool.
        LabelRejected: empty reference_answer or empty labelled_by.
    """
    # First statement in the body, before validation and before a cursor is
    # opened: a refused context must not be able to reach the database at all.
    assert_human_context()

    answer = (reference_answer or "").strip()
    if not answer:
        raise LabelRejected(
            "reference_answer is empty — an unlabelled row is already the "
            "state this write exists to leave"
        )

    author = (labelled_by or "").strip()
    if not author:
        raise LabelRejected(
            "labelled_by is empty — a human tier with no human named behind it "
            "is an unsourced claim"
        )

    # Belt for the constant: if HUMAN_AUTHORED_TIER is ever edited to something
    # 0016's CHECK does not admit, fail here rather than at the database, where
    # the error would arrive as a CheckViolation inside the caller's
    # transaction and take the rest of the request's writes down with it.
    if not is_human_label_tier(HUMAN_AUTHORED_TIER):
        raise LabelRejected(
            f"{HUMAN_AUTHORED_TIER!r} is not one of {HUMAN_LABEL_TIERS!r}"
        )

    with conn.cursor() as cur:
        cur.execute(
            _LABEL_SQL,
            {
                "reference_answer": answer,
                "tier": HUMAN_AUTHORED_TIER,
                "labelled_by": author,
                "scenario_id": str(scenario_id),
            },
        )
        rows_updated = cur.rowcount

    log.info(
        "label_service.human_label_recorded",
        scenario_id=str(scenario_id),
        label_trust_tier=HUMAN_AUTHORED_TIER,
        labelled_by=author,
        rows_updated=rows_updated,
        # The answer text itself is never logged — it is customer-domain
        # content, and the log line's job is provenance, not content.
    )

    return {
        "scenario_id": str(scenario_id),
        "label_trust_tier": HUMAN_AUTHORED_TIER,
        "labelled_by": author,
        "rows_updated": rows_updated,
    }
