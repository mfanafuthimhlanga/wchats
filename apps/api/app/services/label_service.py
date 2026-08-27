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
       Only `app/api/v1/evals.py` may reference this module. Nothing under
       `app/worker/` (every Celery task), nothing else under `app/services/`
       (every agent tool, judge, scenario producer and eval service), nothing
       under `scripts/` or `_runlogs/`, and no test module but the one that
       tests it.
       Pinned by test_only_the_one_named_api_module_may_reference_the_writer
       and by test_no_worker_or_service_module_imports_the_api_layer, which
       closes the transitive route through a re-export.

       WHAT THIS IS NOT. It used to say "reachable from an authenticated HTTP
       request and from nowhere else in the tree", with `app/api/` as the
       permitted region. `app/api/` is not an authentication property: it also
       holds `widget.py`, whose own header records `/widget/{agent_id}/config`
       and `/widget/jobs/{job_id}/events` as no-auth, and whose chat routes run
       behind a JWT issued to an anonymous website visitor. The test asserts a
       module path, so the claim is a module path.

    3. The model-driven writers do not write the label columns — and do not
       name them.
       `scenario_service.store_scenarios` and
       `scenario_service.insert_provenance_scenario` are the only INSERT paths
       into `eval_scenarios` that go through a service, and between them they
       carry every model-driven producer: generated suites, mined production
       failures, promoted traces, contained red-team findings. Neither
       statement names a label-provenance column, so the failure mode of that
       route is a NULL tier, which reads as "no human labelled this".

       THIS SAID "PHYSICALLY CANNOT" UNTIL 2026-08-09, AND THAT WAS FALSE. The
       P1 adversarial review appended a function to a real Celery task module
       that issued an f-string `UPDATE eval_scenarios SET ...
       label_trust_tier = 'human_authored'` — importing nothing, calling
       nothing — and every test in test_label_provenance.py stayed green,
       because R3 was a substring scan over single string constants. R3 is now
       two scans with different blind spots: a composed-SQL reconstruction
       (f-strings, `+`, `%`, `.format`, `.join`, `public.` and quoted
       identifiers) and a name-level absence pin over `app/worker/`, the rest
       of `app/services/`, `scripts/` and `_runlogs/`. What is true is that no
       forgery shape anyone has yet devised passes unnoticed; what is NOT true
       is that raw SQL cannot reach the column.
       Pinned by test_only_the_label_writer_writes_the_label_columns,
       test_no_model_driven_module_names_a_label_column_at_all, and the eight
       forgery fixtures in
       test_the_write_scan_sees_a_forged_label_write_however_it_is_spelled.

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

WHAT THE FOUR RESTRICTIONS DO NOT COVER, AND WHAT P2 OWES
    They authenticate the CALL SITE. They say nothing about the CONTENT of
    `reference_answer` or about the identity in `labelled_by`, both of which
    this function takes on the caller's word — which is, at the human, exactly
    the defect restriction 1 forbids at the tier. An `app/api/` route that asks
    a model to draft an answer and forwards it as
    `record_human_label(reference_answer=<model prose>,
    labelled_by='owner@example.com')` produces a `human_authored` row of model
    output and trips none of R1-R4: no Celery task, no agent ContextVar, no
    import violation, no SQL scan hit.

    THE DECISION, TAKEN NOW SO THAT P2 INHERITS IT RATHER THAN INVENTING IT:

      - `labelled_by` is DERIVED FROM THE AUTHENTICATED PRINCIPAL inside the
        handler. It is never read from the request body, and no route may
        accept it as a field. Same argument as restriction 1: a caller able to
        name the human is a caller able to name any human.
      - `reference_answer` must arrive ON the authenticated request, as text
        the principal submitted. A server-side composition step between a model
        and this call is what makes `human_authored` mean "some code said so".
      - If a machine-drafted candidate is ever offered for a human to approve,
        that is `human_verified`, not `human_authored`, and it needs its own
        writer recording who approved what — which is why 0016's CHECK admits
        `human_verified` although nothing produces it yet.

    Nothing here can pin those today: the route does not exist, and a test
    asserting a property of a module that has not been written is a test that
    passes vacuously. It is written down, and it is a BACKLOG row against P2.

    AND ONE MORE THING THE FOUR DO NOT COVER, WHICH P2 CLOSED ON 2026-08-09.
    R1-R4 are all IN-PROCESS facts: a parameter list, an import graph, a Celery
    thread-local, a ContextVar. An automation in a DIFFERENT process trips none
    of them. `app/api/deps.get_current_tenant` accepts `X-API-Key`, a machine
    credential, so any script or scheduler holding a tenant key could POST model
    prose to the labelling route and have it stored as `human_authored` — making
    the hierarchy worth the secrecy of an API key rather than any
    human-in-the-loop property. The credential is the only evidence about the
    caller that survives a process boundary, so the check has to live at the auth
    layer: `get_credential_kind` reports which credential resolved, and any route
    driving this writer must refuse anything but a Clerk JWT with a 403. That is
    a restriction on the ROUTE, not on this module, and it is recorded here
    because this is where a reader comes to find out what "human_authored" is
    worth.

    NO ROUTE DRIVES THIS WRITER TODAY. The labelling queue and its POST were
    deleted by ADR 0003; this module is the writer with no caller until a
    console feature asks for one.

WHAT THIS MODULE DOES NOT DO
    It does not promote anything into `verified_qa`. A human label improves what
    the eval can measure; it reaches no customer. That is the owner's settled
    decision of 2026-08-08 (`.dev/plans/260808-d6-labelling-loop.md`), and
    `eval_service.VERIFIED_QA_PROMOTION_DECISION` carries the disablement and
    its reason onto every run. No promotion writer exists in this build at all,
    and this module does not change a row's `source`.

    It opens no connection and holds no connection string. The caller passes an
    already-open psycopg2 connection and owns the transaction, matching
    `scenario_service.insert_provenance_scenario` — which also keeps the
    "connection strings never leave the control DB fetch" rule trivially true
    here, because this module never sees one.
"""

from __future__ import annotations

import unicodedata

import structlog

from app.services.eval_service import (
    HUMAN_LABEL_TIERS,
    SELECTOR_ELIGIBILITY_PREDICATE,
    is_human_label_tier,
)

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
    lazily so that this module stays importable in an API process that has no
    Celery wiring at all — a guard that raises on import is a guard that gets
    deleted.

    THE TWO FAILURE CASES ARE NOT THE SAME, and treating them as one made the
    one function whose entire job is to fail closed the only place in this
    module that failed open:

      ImportError — Celery is not installed in this process. There is then no
          Celery task to be inside, so `None` is the true answer and the guard
          stays silent. This is what the lazy import is for.
      anything else — the detector itself malfunctioned. A detector that
          malfunctioned cannot certify that no model is driving this call, and
          "I could not tell" must never resolve to "go ahead and stamp
          `human_authored`". It refuses.
    """
    try:
        from celery import _state  # noqa: PLC0415
    except ImportError:
        return None
    try:
        return _state.get_current_task()
    except Exception as exc:
        raise HumanLabelRefused(
            "could not determine whether a Celery task is driving this call "
            f"({type(exc).__name__}: {exc}); a human trust tier is never "
            "stamped on an unverified context"
        ) from exc


def _current_agent_id() -> str:
    """The agent id of the tool-server context in scope, or ''.

    Set by `agent_tools.bind_tool_context()` for the duration of an agent turn,
    so a non-empty value means a model is driving this call stack.

    Same split as `_current_celery_task`: a missing `agent_tools` means there is
    no agent context in this process (`''`); any other failure means the
    detector could not answer, and an unanswerable question about who is driving
    the call refuses rather than proceeds.
    """
    try:
        from app.services.agent_tools import _agent_id_var  # noqa: PLC0415
    except ImportError:
        return ""
    try:
        return str(_agent_id_var.get() or "")
    except Exception as exc:
        raise HumanLabelRefused(
            "could not determine whether an agent tool context is driving this "
            f"call ({type(exc).__name__}: {exc}); a human trust tier is never "
            "stamped on an unverified context"
        ) from exc


# Unicode general categories that render as nothing a reader can see:
#   Cc  control characters (\n, \t, \r)
#   Cf  format characters — U+200B ZERO WIDTH SPACE, U+FEFF BOM,
#       U+200C/U+200D zero-width non-joiner/joiner, the bidi overrides
#   Zs  space separators, including U+00A0 NBSP and U+2007 FIGURE SPACE
#   Zl / Zp  line and paragraph separators
#
# WHY THIS EXISTS RATHER THAN str.strip() ALONE. `str.strip()` removes Cc and Zs
# but NOT Cf, so `reference_answer = "\u200b"` survived it, was stamped
# `human_authored`, and satisfied BOTH `run_eval_suite`'s
# `WHERE reference_answer != ''` and 0016's `COALESCE(reference_answer,'') <> ''`
# CHECK. The row was then simultaneously marked "a human wrote this" and
# effectively still unlabelled — the exact state the emptiness guard exists to
# prevent, reached by a stray zero-width space from a rich-text paste rather than
# by an attacker. Observed through the real route on 2026-08-09: U+200B, U+FEFF
# and U+200C each returned 200 and bound tier='human_authored'.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})


def visible_answer(reference_answer: str | None) -> str:
    """*reference_answer* stripped, or `''` when it carries nothing visible.

    The single definition of "this answer is empty", used by `record_human_label`
    below AND by the route's request model, so the boundary rejection and the
    writer's own guard cannot come to different conclusions about the same
    string. Returns the stripped text unchanged when it holds at least one
    character a reader could see — normalising the CONTENT is not this
    function's business, only deciding whether there is any.
    """
    answer = (reference_answer or "").strip()
    if not any(
        unicodedata.category(char) not in _INVISIBLE_CATEGORIES for char in answer
    ):
        return ""
    return answer


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


# The UPDATE.
#
# THE SECOND PREDICATE IS THE POINT, AND IT WAS MISSING UNTIL 2026-08-09. The
# WHERE was `id = %(scenario_id)s::uuid` alone, which meant this write reached
# ANY row in the agent's database rather than only a row the labelling queue had
# offered. One POST with the id of an already-answered scenario silently replaced
# its `reference_answer` and re-stamped its provenance, with no record of what
# had been there. The blast radius was worst on a `dataset='golden'` row:
# `eval.py`'s golden half runs in full every night precisely so consecutive runs
# are a PAIRED per-item comparison, and changing one item's reference answer
# breaks that comparison while the run report has no way to say so.
#
# `NOT (SELECTOR_ELIGIBILITY_PREDICATE)` is the queue's own population, spelled
# with the queue's own constant rather than a hand-written `= ''`. So the write's
# reach is now exactly the set of rows the GET can return, and the two cannot
# drift: the same string defines both.
#
# RELABELLING IS THEREFORE REFUSED, NOT SILENTLY PERFORMED — see
# `record_human_label`'s `already_labelled`. If a correction path is wanted later
# it is an explicit second act (which answer is being superseded, by whom, and
# whether a golden row may move at all), not a side effect of the queue's write.
#
# Idempotent by construction in the direction that matters for a retry: the first
# application labels the row, and a retry of the same request now matches zero
# rows and reports `already_labelled` instead of moving `labelled_at` again.
_LABEL_SQL = f"""
    UPDATE eval_scenarios
    SET reference_answer = %(reference_answer)s,
        label_trust_tier = %(tier)s,
        labelled_by = %(labelled_by)s,
        labelled_at = NOW()
    WHERE id = %(scenario_id)s::uuid
      AND NOT ({SELECTOR_ELIGIBILITY_PREDICATE})
"""

# Run ONLY when the UPDATE matched nothing, to tell the two reasons apart: the
# row is not in this database at all (404 — also the cross-tenant outcome, and
# the two must stay indistinguishable), or it is here and already carries an
# answer (409). Without it both collapse into 404 and a caller told "not found"
# about a row it can see in its own queue history has been told something false.
#
# Deliberately projects no column: existence is the whole question, and `SELECT
# 1` needs neither 0016's columns nor 0011's, so this probe cannot itself become
# a migration-state failure on the error path.
_SCENARIO_EXISTS_SQL = """
    SELECT 1
    FROM eval_scenarios
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
    alembic_tenant 0016's separate label column exists to prevent.

    Args:
        conn: An open psycopg2 connection. This function does NOT commit or
            close it — the caller owns the transaction, matching
            scenario_service.insert_provenance_scenario.
        scenario_id: UUID string of the eval_scenarios row to label.
        reference_answer: The answer the human wrote. Must carry at least one
            VISIBLE character — see `visible_answer`. An empty label is what the
            row already has, and writing a human tier over one would claim a
            human authored nothing while making the row eligible to a selector
            that filters on `reference_answer != ''`. A zero-width string
            satisfies that selector and every CHECK the schema has, so
            "non-empty" is decided on Unicode category, not on `str.strip()`.
        labelled_by: Identifier of the human. Must be non-empty — a label with
            no author is a tier with nothing behind it. NON-EMPTY IS ALL THIS
            FUNCTION CAN CHECK: it is caller-asserted free text, and nothing
            here binds it to an authenticated principal. The caller must derive
            it from the request's principal and must never read it from a
            request body — see "WHAT THE FOUR RESTRICTIONS DO NOT COVER" in the
            module docstring.

    Returns:
        {"scenario_id": str, "label_trust_tier": str, "labelled_by": str,
         "rows_updated": int, "already_labelled": bool}. `rows_updated` is 0
        when the UPDATE matched nothing, and `already_labelled` says WHICH of
        the two reasons applies: the row is absent from this database
        (False — the caller's 404) or it is present and already answered
        (True — the caller's 409). Both are reported, never raised, so the
        caller counts outcomes rather than catching them. `already_labelled` is
        False whenever `rows_updated` is 1: the probe is not run on a successful
        write.

    Raises:
        HumanLabelRefused: called from inside a Celery task or an agent tool.
        LabelRejected: visibly-empty reference_answer, or empty labelled_by.
    """
    # First statement in the body, before validation and before a cursor is
    # opened: a refused context must not be able to reach the database at all.
    assert_human_context()

    answer = visible_answer(reference_answer)
    if not answer:
        raise LabelRejected(
            "reference_answer carries no visible character — an unlabelled row "
            "is already the state this write exists to leave, and a zero-width "
            "answer would leave it there while claiming a human wrote it"
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

    already_labelled = False
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

        if rows_updated == 0:
            # The UPDATE's two predicates failed as one. Ask which.
            cur.execute(_SCENARIO_EXISTS_SQL, {"scenario_id": str(scenario_id)})
            already_labelled = bool(cur.fetchall())

    log.info(
        "label_service.human_label_recorded",
        scenario_id=str(scenario_id),
        label_trust_tier=HUMAN_AUTHORED_TIER,
        labelled_by=author,
        rows_updated=rows_updated,
        already_labelled=already_labelled,
        # The answer text itself is never logged — it is customer-domain
        # content, and the log line's job is provenance, not content.
    )

    return {
        "scenario_id": str(scenario_id),
        "label_trust_tier": HUMAN_AUTHORED_TIER,
        "labelled_by": author,
        "rows_updated": rows_updated,
        "already_labelled": already_labelled,
    }
