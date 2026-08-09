# D1 — the bounded artifact for the tier-2 judge

**Branch:** `feat/d1-agent-invocation` · **Base:** `main` at `af0f601` · **Assembled:** 2026-08-08
· **Assembled by:** the collector (session model). The collector wrote no code and changed no file
under `apps/api/`.

**Plan:** `.dev/plans/260807-d1-agent-invocation.md` · **Audit:** `.dev/reference/measurement-layer-audit.md` §D1

---

## 0. How this artifact was built, and its known gaps

Everything below is quoted from files on disk or from `git` at `HEAD` (`a021118`). Where a claim
exists only in a commit message, this file says so. Where a claimed artifact does **not** exist on
disk, this file says that too rather than paraphrasing it into existence.

**Three gaps the judge must know about before reading §3 and §4:**

1. **The workflow journal did not survive.** `.dev/workflows/d1-agent-invocation.workflow.js` shows
   the orchestration ran `p1 → p1r → p1f → p1b → p1br → p1bf → p2 → p2r → p2f → p3 → p3r → p3f`,
   where the `*r` stages are the **tier-1 adversarial reviewers**. Their structured `REVIEW_SCHEMA`
   outputs — the verbatim finding lists — lived in the workflow journal in a temp directory and are
   **gone**. §4 therefore reconstructs each finding from the fixer's own commit message and trace,
   and then determines fixed / partial / not-fixed **from the diff**. A finding a fixer never
   mentioned is a finding this artifact cannot see. Treat §4's coverage as a lower bound.

2. **P1 and P1-fix have no trace file.** Every other phase has one in `.dev/traces/`. P1's report is
   its commit message (`ec5f445`) and P1-fix's is `d15be3a`. Both claim "12 mutations, each observed
   red and then green" and **neither records the verbatim output of any of them**. See §5.

3. **P1b-fix's 18 mutation proofs are not on disk.** `.dev/traces/260807-d1-p1b-tier2-fixes.md`
   says "Observed output recorded in the phase report rather than paraphrased" — the phase report
   being the lost journal. Two of the eighteen are described in prose in that trace; sixteen are
   named nowhere.

**Naming caution.** The traces on this branch call the per-phase adversarial reviewers "tier-2".
They are not: the workflow file at line 488 tells the final judge *"a tier-1 adversarial reviewer
already asked that against the code"*, and `9d81e34`'s commit message corrects the same
misattribution for P1. `.dev/BACKLOG.md` §2 now states it correctly. **Everything the traces label a
"tier-2 read" is a tier-1 finding set.** No tier-2 judge has read any part of this branch; that is
what this artifact is for.

---

## 1. The diff

`git diff main...HEAD` is **12,561 insertions / 354 deletions across 37 files**. Per the collector's
instructions: `app/` is reproduced **in full** (§1.2, **4,212 diff lines across all 11 changed `app/` files** —
every hunk, nothing elided); `alembic_tenant/` is reported in §1.1; `apps/api/tests/` and `.dev/` are
**summarised** in §1.3 and §1.4, with exact dropped-line counts.

### 1.0 Diffstat, complete

```
 .dev/BACKLOG.md                                    |  102 +-
 .dev/HANDOFF.md                                    |   59 +-
 .dev/plans/260807-d1-agent-invocation.md           |  165 ++
 .dev/reference/p1b-mutation-proofs.md              |  182 ++
 .dev/reference/p2-mutation-proofs.md               |  227 ++
 .dev/reference/p2-review-mutation-proofs.md        |  336 ++++
 .dev/reference/p3-review-mutation-proofs.md        |  216 ++
 .dev/traces/260807-d1-p1b-recorded-mode.md         |  188 ++
 .dev/traces/260807-d1-p1b-tier2-fixes.md           |  142 ++
 .dev/traces/260808-d1-p2-invoke.md                 |  174 ++
 .dev/traces/260808-d1-p2-review-fixes.md           |  155 ++
 .dev/traces/260808-d1-p3-gate.md                   |  112 ++
 .dev/traces/260808-d1-p3-review-fixes.md           |  151 ++
 .dev/workflows/d1-agent-invocation.workflow.js     |  546 ++++++
 apps/api/app/api/v1/deployment.py                  |   31 +-
 apps/api/app/services/agent_tools.py               |  281 ++-
 apps/api/app/services/decision_eval_service.py     |   14 +
 apps/api/app/services/deployment_service.py        |  441 ++++-
 apps/api/app/services/eval_service.py              |  634 ++++++-
 apps/api/app/services/transactional/enforcement.py |   23 +-
 apps/api/app/services/transactional/tools.py       |  499 +++++-
 apps/api/app/worker/celery_app.py                  |   26 +-
 apps/api/app/worker/tasks/runtime/agent.py         |  566 +++++-
 apps/api/app/worker/tasks/runtime/deployment.py    |   65 +-
 apps/api/app/worker/tasks/runtime/eval.py          |  656 ++++++-
 apps/api/tests/integration/test_prompt_versions_e2e.py |  68 +-
 apps/api/tests/unit/test_agent_options_seam.py     | 1730 ++++++++++++++++++
 apps/api/tests/unit/test_decision_eval_service.py  |   71 +-
 apps/api/tests/unit/test_deployment_routes.py      |  189 +-
 apps/api/tests/unit/test_deployment_service.py     |  815 ++++++++-
 apps/api/tests/unit/test_deployment_task.py        |  121 +-
 apps/api/tests/unit/test_eval_agent_invocation.py  | 1878 ++++++++++++++++++
 apps/api/tests/unit/test_eval_service.py           |  369 +++-
 apps/api/tests/unit/test_eval_task.py              |  143 +-
 apps/api/tests/unit/test_recorded_side_effects.py  | 1398 +++++++++++++++
 apps/api/tests/unit/test_retrieval_metrics.py      |  110 ++
 apps/api/tests/unit/test_transactional_tools.py    |   32 +-
 37 files changed, 12561 insertions(+), 354 deletions(-)
```

### 1.1 `alembic_tenant/` — EMPTY. No migration exists on this branch.

```
$ git diff main...HEAD --name-only -- '*alembic*'
(no output)
```

The plan's P3 section says *"New provenance field `agent_invoked` on the eval run, written by P2,
**migration in `alembic_tenant`**"*, and the workflow prompt for P3 says *"The alembic_tenant
migration for the new field"* is "also needed". **No migration was written.** The implementers'
stated reason (P2 trace deviation 2; P3 trace "Deviations from the plan"; `.dev/BACKLOG.md` §2) is
that `agent_invoked` lives inside `eval_runs.config`, the JSONB column migration `0013` already
added, so there is no DDL. `0015` remains the tenant head. **This is a deviation from a written
contract, argued but not owner-settled in the plan.** It is the judge's to weigh.

### 1.2 `apps/api/app/` — the complete diff, all 11 changed files, nothing elided

Order: `transactional/tools.py`, `agent_tools.py` + `transactional/enforcement.py`,
`worker/tasks/runtime/agent.py`, `worker/tasks/runtime/eval.py`, `services/deployment_service.py`,
`services/eval_service.py`, then the four smaller files (`api/v1/deployment.py`,
`decision_eval_service.py`, `celery_app.py`, `worker/tasks/runtime/deployment.py`).


#### 1.2.1 `app/services/transactional/tools.py` (+499/-25) — the six mutating skills' dispatcher

```diff
diff --git a/apps/api/app/services/transactional/tools.py b/apps/api/app/services/transactional/tools.py
index eb81b29..a892525 100644
--- a/apps/api/app/services/transactional/tools.py
+++ b/apps/api/app/services/transactional/tools.py
@@ -28,7 +28,9 @@ AUD-01 symmetry:
 
 confirm_action_tool (mutating=False, WR-05 closed):
   Gated behind check_capability_access + IN-03 agent_id guard before writing a
-  pending_confirmations row. Takes NO idempotency key, calls NO provider adapter.
+  pending_confirmations row. Under side_effects='recorded' it writes no row at
+  all and records the attempt instead — it does not use the dispatcher, so it
+  would otherwise pollute the owner's triage queue on every eval scenario. Takes NO idempotency key, calls NO provider adapter.
   Minimal dedup (T-14-08-05): the partial unique index
   uq_pending_confirmations_unresolved (migration 0016) bounds OUTSTANDING
   confirmations to one per (agent_id, skill, action_reference). A duplicate
@@ -103,6 +105,111 @@ log = structlog.get_logger(__name__)
 # Default TTL for pending_confirmations rows (Phase 18 will extend/configure this).
 _CONFIRM_TTL_HOURS: int = 24
 
+#: tool_calls_audit.error marker for a call recorded-mode declined to execute
+#: (D1/P1b, BACKLOG 2.5). A constant rather than a literal because the eval and
+#: any future Actor-labelling pass have to filter on the same string: an
+#: unmarked recorded row and a real execution tell the same story in that table.
+#:
+#: EVERY audit row written under recorded mode carries it, not only the
+#: adapter-suppression row. A recorded `actor_block` row that was byte-identical
+#: to a production `actor_block` row is exactly the contamination this constant
+#: exists to prevent, and the *refused* column of the audit's confusion matrix
+#: is entirely made of those rows. `startswith(RECORDED_NOT_EXECUTED)` is the
+#: filter; the suppression row is the bare marker, every other recorded row is
+#: `f"{RECORDED_NOT_EXECUTED}|{the real reason}"`.
+RECORDED_NOT_EXECUTED: str = "side_effects.recorded:not_executed"
+
+#: In-process sink `kind` for a call that steps 1-5 stopped under recorded mode.
+#: Distinct from "transactional.adapter" (which recorded mode suppressed at the
+#: outer edge) because the two are opposite cells of the confusion matrix: one
+#: is "would have executed", the other is "the envelope refused". An eval that
+#: recorded only the first cannot tell "the agent never tried" from "the agent
+#: tried and was stopped".
+RECORDED_DECLINED: str = "transactional.declined"
+
+
+def _recorded_error(recorded: bool, error: str) -> str:
+    """Stamp an audit row's `error` with the recorded marker, in recorded mode only.
+
+    Live rows are returned byte-unchanged — every pre-existing assertion about
+    `capability.denial:disabled`, `actor_block`, `idempotency.args_mismatch`
+    and friends still reads exactly what it read before.
+    """
+    return f"{RECORDED_NOT_EXECUTED}|{error}" if recorded else error
+
+
+def _not_executed_result(skill: str, detail: str = "") -> dict:
+    """The tool result recorded mode hands the agent in place of a real execution.
+
+    Two requirements pull in opposite directions here and both are met.
+
+    **Unmissable, never a silent success** (the owner, 2026-08-07). A recorded
+    `issue_refund` that returned a cheerful confirmation would teach the agent
+    the money moved, and every sentence it produced afterwards would reason from
+    a false premise. So: `is_error`, and text that says in words that nothing
+    happened, carrying none of the adapter's artefacts.
+
+    **No evaluation frame in the model's context.** The first version of this
+    text told the agent "this agent is running in evaluation mode
+    (side_effects='recorded')" and instructed it not to tell the customer the
+    action completed. Every token after that was produced by an
+    evaluation-AWARE agent — and those are the tokens Faithfulness and
+    AnswerRelevancy then score. That is a production-fidelity divergence of
+    exactly the class approach (b) and the seam exist to close: measure the
+    agent production serves, not one that knows it is being watched. Production
+    never emits either sentence, so neither does this. What a real provider
+    outage produces — a failed tool call whose text says the action did not
+    happen — is what the agent sees.
+
+    The eval-only marker did not disappear; it moved to where the readers who
+    need it actually read. `tool_calls_audit.error` carries
+    `RECORDED_NOT_EXECUTED` for the human grader and the labelled Actor set, and
+    `get_recorded_side_effects()` carries the full attempt for P2.
+    """
+    tail = f" {detail}" if detail else ""
+    return {
+        "content": [
+            {
+                "type": "text",
+                "text": (
+                    f"NOT EXECUTED: the {skill} request did not reach the provider "
+                    f"and nothing was changed. No money moved and no record was "
+                    f"updated.{tail}"
+                ),
+            }
+        ],
+        "is_error": True,
+    }
+
+
+def _declined_detail(
+    *,
+    skill: str,
+    raw_args: dict,
+    agent_id: str,
+    conversation_id: str | None,
+    reason: str,
+    snapshot: dict | None = None,
+    actor_decision: str = "",
+    actor_rationale: str = "",
+) -> dict:
+    """The in-process record of an attempt steps 1-5 declined under recorded mode.
+
+    `reason` is the same string the audit row carries, so the durable and the
+    in-process halves of the recording join on one value rather than on two
+    vocabularies that drift.
+    """
+    return {
+        "skill": skill,
+        "arguments": raw_args,
+        "agent_id": agent_id,
+        "conversation_id": conversation_id,
+        "reason": reason,
+        "capability_snapshot": snapshot,
+        "actor_decision": actor_decision,
+        "actor_rationale": actor_rationale,
+    }
+
 
 # ---------------------------------------------------------------------------
 # Shared steps 6-7 — adapter execute + audit row + finalize
@@ -284,6 +391,23 @@ async def _execute_transactional_tool(
                            ONLY for the fresh reserved winner — never for replays
                            denial → release + audit + is_error
       5. Actor seam      — call_actor_gate: block → release + audit + is_error
+      5.5 Recorded mode  — D1/P1b: on the eval path (side_effects='recorded') the
+                           ProviderAdapter is suppressed. Records the attempt,
+                           releases, writes the audit row marked
+                           RECORDED_NOT_EXECUTED, returns is_error. Steps 1-5 all
+                           ran; only the money did not move.
+                           This is the APPROVE path's branch. Every other
+                           non-executing outcome above has its own — the two that
+                           matter are the step-3 replay (returns a stored REAL
+                           provider result) and the step-5 require_human verdict
+                           (writes a pending_confirmations row the owner's
+                           approval queue dispatches into a live adapter). Both
+                           return BEFORE 5.5, so 5.5 alone was not a money guard.
+                           Under recorded mode every audit row this function
+                           writes carries the RECORDED_NOT_EXECUTED prefix, and
+                           every declined attempt is recorded in the in-process
+                           sink — the *refused* column of the audit's confusion
+                           matrix is made entirely of those.
       6. Adapter execute — try/except; error → release + audit + is_error
       7. Audit + finalize— success path: audit row (no error) + finalize_idempotency + return
 
@@ -304,7 +428,9 @@ async def _execute_transactional_tool(
         _agent_id_var,
         _conn_str_var,
         _conversation_id_var,
+        _side_effects_var,
         _verified_session_token_var,
+        record_suppressed_side_effect,
     )
 
     agent_id = _agent_id_var.get()
@@ -312,7 +438,33 @@ async def _execute_transactional_tool(
     conversation_id_str = _conversation_id_var.get()
     conversation_id: str | None = conversation_id_str if conversation_id_str else None
 
+    # D1/P1b: read ONCE, at the top. The mode is consulted by every
+    # decline-to-execute branch below, not only by step 5.5 — reading it at each
+    # branch would be nine chances to forget one, and the one forgotten is the
+    # one that moves money.
+    recorded: bool = _side_effects_var.get() == "recorded"
+
+    # The idempotency key is MODEL-SUPPLIED (every mutating Input model in
+    # schemas.py declares it) and models produce deterministic ones —
+    # "refund-ORD-9001", "order-12345-refund". Two consequences, both real, both
+    # closed by giving recorded mode its own keyspace:
+    #   * an eval scenario mined from a production conversation can hit the key a
+    #     real completed call used, and step 3 would hand the agent that call's
+    #     stored REAL provider result;
+    #   * an eval that reserves a key first makes the real customer's later call
+    #     with the same key read as a replay or a stranded reservation.
+    # A recorded execution never finalizes (it releases), so nothing is ever
+    # stored under a "recorded:" key and a recorded replay cannot occur at all.
+    # The step-3 mode check below is kept anyway: this namespace is one edit away
+    # from being lost, and the check fails loudly if it ever is.
+    idem_key: str = (
+        f"recorded:{validated.idempotency_key}" if recorded else validated.idempotency_key
+    )
+
     # -------------------------------------------------------- 1. IN-03 agent_id precondition
+    # No recorded-mode branch: this is the harness failing to set context, not
+    # the agent choosing anything, and there is no agent_id to attribute a
+    # recording to. Nothing durable is written here either.
     if not agent_id:
         return {
             "content": [
@@ -332,6 +484,18 @@ async def _execute_transactional_tool(
     # Runs on EVERY call including replays — fail-closed for existence + enabled (T-14-04-03).
     snapshot, denial = await check_capability_access(agent_id, skill)
     if denial is not None:
+        if recorded:
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason=f"capability.denial:{denial}",
+                    snapshot=snapshot,
+                ),
+            )
         # AUD-01 symmetry: capability denial writes one audit row (matching actor_block).
         await write_audit_row(
             agent_id=agent_id,
@@ -343,7 +507,7 @@ async def _execute_transactional_tool(
             actor_rationale="",
             capability_snapshot=snapshot,
             latency_ms=None,
-            error=f"capability.denial:{denial}",
+            error=_recorded_error(recorded, f"capability.denial:{denial}"),
         )
         return {
             "content": [
@@ -368,6 +532,18 @@ async def _execute_transactional_tool(
         vst = _verified_session_token_var.get()
         if not vst:
             # No verified session token present — block before reservation.
+            if recorded:
+                record_suppressed_side_effect(
+                    RECORDED_DECLINED,
+                    _declined_detail(
+                        skill=skill,
+                        raw_args=raw_args,
+                        agent_id=agent_id,
+                        conversation_id=conversation_id,
+                        reason="identity_verification.required",
+                        snapshot=snapshot,
+                    ),
+                )
             await write_audit_row(
                 agent_id=agent_id,
                 conversation_id=conversation_id,
@@ -378,7 +554,7 @@ async def _execute_transactional_tool(
                 actor_rationale="",
                 capability_snapshot=snapshot,
                 latency_ms=None,
-                error="identity_verification.required",
+                error=_recorded_error(recorded, "identity_verification.required"),
             )
             return {
                 "content": [
@@ -405,6 +581,18 @@ async def _execute_transactional_tool(
                 skill=skill,
                 error=str(exc),
             )
+            if recorded:
+                record_suppressed_side_effect(
+                    RECORDED_DECLINED,
+                    _declined_detail(
+                        skill=skill,
+                        raw_args=raw_args,
+                        agent_id=agent_id,
+                        conversation_id=conversation_id,
+                        reason="identity_verification.check_failed",
+                        snapshot=snapshot,
+                    ),
+                )
             await write_audit_row(
                 agent_id=agent_id,
                 conversation_id=conversation_id,
@@ -415,7 +603,7 @@ async def _execute_transactional_tool(
                 actor_rationale="",
                 capability_snapshot=snapshot,
                 latency_ms=None,
-                error="identity_verification.check_failed",
+                error=_recorded_error(recorded, "identity_verification.check_failed"),
             )
             return {
                 "content": [
@@ -428,6 +616,18 @@ async def _execute_transactional_tool(
             }
         if not session_valid:
             # Token present but expired or not found in tenant DB — block before reservation.
+            if recorded:
+                record_suppressed_side_effect(
+                    RECORDED_DECLINED,
+                    _declined_detail(
+                        skill=skill,
+                        raw_args=raw_args,
+                        agent_id=agent_id,
+                        conversation_id=conversation_id,
+                        reason="identity_verification.invalid_or_expired",
+                        snapshot=snapshot,
+                    ),
+                )
             await write_audit_row(
                 agent_id=agent_id,
                 conversation_id=conversation_id,
@@ -438,7 +638,7 @@ async def _execute_transactional_tool(
                 actor_rationale="",
                 capability_snapshot=snapshot,
                 latency_ms=None,
-                error="identity_verification.invalid_or_expired",
+                error=_recorded_error(recorded, "identity_verification.invalid_or_expired"),
             )
             return {
                 "content": [
@@ -456,9 +656,7 @@ async def _execute_transactional_tool(
     # -------------------------------------------------------- 3. Reserve idempotency (atomic)
     # compute_args_hash excludes idempotency_key internally — used to detect WR-02 key reuse.
     args_hash = compute_args_hash(raw_args)
-    reservation = await reserve_idempotency(
-        agent_id, skill, validated.idempotency_key, args_hash
-    )
+    reservation = await reserve_idempotency(agent_id, skill, idem_key, args_hash)
 
     if reservation.state == "replay":
         # WR-01 closed: replay short-circuits BEFORE apply_rate_and_constraint_checks.
@@ -468,6 +666,28 @@ async def _execute_transactional_tool(
             agent_id=agent_id,
             skill=skill,
         )
+        if recorded:
+            # The stored result is a REAL provider result from a REAL earlier
+            # call. Returning it here would hand the eval agent "Refund of
+            # R45.00 issued" and every sentence after it would reason from money
+            # having moved — the silent success the owner ruled out, arriving
+            # through the one door step 5.5 sits behind rather than in front of.
+            # The "recorded:" keyspace above should make this unreachable; if it
+            # is ever reached, this is the guard that keeps it harmless.
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason="idempotency.replay",
+                    snapshot=snapshot,
+                ),
+            )
+            # No audit row: AUD-01 exempts replays in both modes, because the
+            # call that stored the result already wrote one.
+            return _not_executed_result(skill)
         return reservation.result  # type: ignore[return-value]
 
     if reservation.state == "args_mismatch":
@@ -476,6 +696,18 @@ async def _execute_transactional_tool(
         # AUD-01: this is a security-relevant rejection (suspicious key reuse) — audit it,
         # matching the capability.denial / actor_block paths. (in_progress is NOT audited
         # here: it is a concurrent-duplicate no-op; the reserved winner audits the real call.)
+        if recorded:
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason="idempotency.args_mismatch",
+                    snapshot=snapshot,
+                ),
+            )
         await write_audit_row(
             agent_id=agent_id,
             conversation_id=conversation_id,
@@ -486,7 +718,7 @@ async def _execute_transactional_tool(
             actor_rationale="",
             capability_snapshot=snapshot,
             latency_ms=None,
-            error="idempotency.args_mismatch",
+            error=_recorded_error(recorded, "idempotency.args_mismatch"),
         )
         return {
             "content": [
@@ -504,6 +736,22 @@ async def _execute_transactional_tool(
     if reservation.state == "in_progress":
         # Concurrent duplicate delivery — another worker is executing the same key.
         # Return a benign is_error without executing (caller should retry later).
+        if recorded:
+            # No audit row here in either mode (AUD-01: the reserved winner
+            # audits the real call), so the in-process sink is the ONLY record
+            # that the agent tried. Without it, P2 reads this turn as one where
+            # no mutating call was attempted at all.
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason="idempotency.in_progress",
+                    snapshot=snapshot,
+                ),
+            )
         return {
             "content": [
                 {
@@ -528,6 +776,18 @@ async def _execute_transactional_tool(
             agent_id=agent_id,
             skill=skill,
         )
+        if recorded:
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason="idempotency.stranded_reservation",
+                    snapshot=snapshot,
+                ),
+            )
         await write_audit_row(
             agent_id=agent_id,
             conversation_id=conversation_id,
@@ -538,7 +798,7 @@ async def _execute_transactional_tool(
             actor_rationale="",
             capability_snapshot=snapshot,
             latency_ms=None,
-            error="idempotency.stranded_reservation",
+            error=_recorded_error(recorded, "idempotency.stranded_reservation"),
         )
         return {
             "content": [
@@ -563,7 +823,19 @@ async def _execute_transactional_tool(
     rate_denial = await apply_rate_and_constraint_checks(agent_id, skill, snapshot, raw_args)
     if rate_denial is not None:
         # Release the reservation so a later retry can attempt the key again.
-        await release_idempotency(agent_id, skill, validated.idempotency_key)
+        await release_idempotency(agent_id, skill, idem_key)
+        if recorded:
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason=f"capability.denial:{rate_denial}",
+                    snapshot=snapshot,
+                ),
+            )
         await write_audit_row(
             agent_id=agent_id,
             conversation_id=conversation_id,
@@ -574,7 +846,7 @@ async def _execute_transactional_tool(
             actor_rationale="",
             capability_snapshot=snapshot,
             latency_ms=None,
-            error=f"capability.denial:{rate_denial}",
+            error=_recorded_error(recorded, f"capability.denial:{rate_denial}"),
         )
         return {
             "content": [
@@ -594,7 +866,21 @@ async def _execute_transactional_tool(
         skill, raw_args, snapshot, conversation_id or "", agent_id, conn_str
     )
     if decision == "block":
-        await release_idempotency(agent_id, skill, validated.idempotency_key)
+        await release_idempotency(agent_id, skill, idem_key)
+        if recorded:
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason="actor_block",
+                    snapshot=snapshot,
+                    actor_decision=decision,
+                    actor_rationale=rationale,
+                ),
+            )
         await write_audit_row(
             agent_id=agent_id,
             conversation_id=conversation_id,
@@ -605,7 +891,7 @@ async def _execute_transactional_tool(
             actor_rationale=rationale,
             capability_snapshot=snapshot,
             latency_ms=None,
-            error="actor_block",
+            error=_recorded_error(recorded, "actor_block"),
         )
         return {
             "content": [
@@ -620,7 +906,76 @@ async def _execute_transactional_tool(
     elif decision == "require_human":
         # Pitfall 4 (15-RESEARCH.md): release reservation FIRST — the action will NOT
         # proceed. Free the reservation so a later retry (after approval) can re-enter.
-        await release_idempotency(agent_id, skill, validated.idempotency_key)
+        await release_idempotency(agent_id, skill, idem_key)
+
+        # ---------------------------------------------------------------
+        # D1/P1b: THE SECOND DOOR TO A LIVE ADAPTER, and the one step 5.5
+        # cannot see because this arm returns before it.
+        #
+        # A require_human verdict writes a durable `pending_confirmations`
+        # row. That row is not inert: it appears in
+        # GET /agents/{agent_id}/pending-confirmations, it carries no marker
+        # distinguishing it from a customer's, and `_is_confirm_action_shaped`
+        # does NOT filter it (it holds `idempotency_key`, never
+        # `action_reference`). Approving it dispatches
+        # resolve_confirmation_task -> execute_approved_confirmation ->
+        # _execute_adapter_and_audit -> get_adapter_for_skill -> a real
+        # Stripe/Shopify/Woo/Calendly call. So a nightly eval scenario that
+        # provokes a large refund silently queues a real refund for the owner
+        # to approve, hours later, with nothing in the queue saying it came
+        # from an eval. Recorded mode that stops at step 5.5 stops the fast
+        # path to the adapter and leaves the slow one open.
+        #
+        # Stamping the row instead of skipping it was the alternative. It was
+        # rejected: it needs the approval route and the resolver to fail
+        # closed on the stamp, which spreads the eval's concern into the
+        # human-approval path — the same coupling
+        # test_the_shared_adapter_helper_stays_free_of_the_mode exists to
+        # prevent. Not writing a row nobody should ever act on is the smaller,
+        # more local change.
+        #
+        # The Actor's verdict is not lost: the recording carries decision and
+        # rationale, and the audit row is written and marked. That verdict IS
+        # the eval signal — "the agent tried and the gate escalated it" is a
+        # cell of the confusion matrix, and the pending row was never the
+        # thing that carried it.
+        # ---------------------------------------------------------------
+        if recorded:
+            record_suppressed_side_effect(
+                RECORDED_DECLINED,
+                _declined_detail(
+                    skill=skill,
+                    raw_args=raw_args,
+                    agent_id=agent_id,
+                    conversation_id=conversation_id,
+                    reason="actor_require_human",
+                    snapshot=snapshot,
+                    actor_decision=decision,
+                    actor_rationale=rationale,
+                ),
+            )
+            await write_audit_row(
+                agent_id=agent_id,
+                conversation_id=conversation_id,
+                skill=skill,
+                arguments=raw_args,
+                result=None,
+                actor_decision=decision,
+                actor_rationale=rationale,
+                capability_snapshot=snapshot,
+                latency_ms=None,
+                error=_recorded_error(recorded, "actor_require_human"),
+            )
+            log.info(
+                "transactional_tool.require_human_not_queued",
+                agent_id=agent_id,
+                skill=skill,
+            )
+            return _not_executed_result(
+                skill,
+                "It requires human approval and no approval request was created.",
+            )
+
         now = datetime.now(timezone.utc)
         expires_at = now + timedelta(hours=_CONFIRM_TTL_HOURS)
 
@@ -722,6 +1077,75 @@ async def _execute_transactional_tool(
             ]
         }
 
+    # ---------------------------------------------- 5.5 Recorded mode (D1/P1b, BACKLOG 2.5)
+    # The eval invokes this agent through the same seam production uses, which is
+    # the whole point of approach (b) — and which means an eval scenario in which
+    # the agent decides to refund would execute a real refund. On the eval path
+    # the ProviderAdapter is suppressed and the attempt is recorded instead.
+    #
+    # Placed HERE, after step 5, and not inside _execute_adapter_and_audit. Two
+    # reasons, both load-bearing:
+    #
+    #   * Everything above still runs. The capability envelope, the IDV gate, the
+    #     idempotency reservation, the rate ceiling and the Actor seam are what
+    #     the eval is measuring. Short-circuiting ahead of them would hand the
+    #     recorded agent "not executed" where production hands it "access
+    #     denied", and the remainder of the turn would diverge from the product —
+    #     the exact drift the seam exists to close.
+    #   * _execute_adapter_and_audit is shared with
+    #     confirmation_resolution.execute_approved_confirmation, the human-approval
+    #     resolver. That resolver runs hours later, in another task, with no
+    #     per-turn context, and is forbidden from reading dispatcher ContextVars
+    #     (OD-5, test_resolver_reads_no_dispatcher_contextvar). Putting the check
+    #     in the shared helper would make an approved refund's fate depend on
+    #     ambient state nobody in that call stack set.
+    #
+    # AUD-01 symmetry is preserved: this is a non-replay entry, so it writes
+    # exactly one tool_calls_audit row, marked as recorded so the labelled Actor
+    # set is never contaminated by an action that did not run. The reservation is
+    # released like every other decline-to-execute path, because a key held by an
+    # action that never happened makes the next caller's "unknown" a lie.
+    #
+    # This branch is no longer the ONLY one. Two arms above return before it —
+    # the step-3 idempotency replay and the step-5 require_human verdict — and
+    # both reached durable, real effects (a stored provider result, and a
+    # `pending_confirmations` row the owner's approval queue dispatches into a
+    # live adapter). Each now has its own recorded branch. `recorded` is read
+    # once at the top of this function for that reason.
+    if recorded:
+        record_suppressed_side_effect(
+            "transactional.adapter",
+            {
+                "skill": skill,
+                "adapter_method": adapter_method,
+                "arguments": raw_args,
+                "agent_id": agent_id,
+                "conversation_id": conversation_id,
+                "actor_decision": decision,
+                "actor_rationale": rationale,
+                "capability_snapshot": snapshot,
+            },
+        )
+        await release_idempotency(agent_id, skill, idem_key)
+        await write_audit_row(
+            agent_id=agent_id,
+            conversation_id=conversation_id,
+            skill=skill,
+            arguments=raw_args,
+            result=None,
+            actor_decision=decision,
+            actor_rationale=rationale,
+            capability_snapshot=snapshot,
+            latency_ms=None,
+            error=RECORDED_NOT_EXECUTED,
+        )
+        log.info(
+            "transactional_tool.side_effect_recorded",
+            agent_id=agent_id,
+            skill=skill,
+        )
+        return _not_executed_result(skill)
+
     # -------------------------------------------------------- 6-7. Adapter + audit
     # Delegated to the shared helper (T-22-ACT-15) — see _execute_adapter_and_audit
     # above for the full step 6/7 implementation. Pure extraction; no behaviour
@@ -944,10 +1368,16 @@ async def confirm_action_tool(args: dict) -> dict:
             "is_error": True,
         }
 
-    # Lazy import to access the ContextVar set by build_tool_server.
-    from app.services.agent_tools import _agent_id_var  # noqa: PLC0415
+    # Lazy import to access the ContextVars set by build_tool_server.
+    from app.services.agent_tools import (  # noqa: PLC0415
+        _agent_id_var,
+        _conversation_id_var,
+        _side_effects_var,
+        record_suppressed_side_effect,
+    )
 
     agent_id = _agent_id_var.get()
+    recorded: bool = _side_effects_var.get() == "recorded"
 
     # IN-03: agent_id guard — fail before any DB write
     if not agent_id:
@@ -982,6 +1412,41 @@ async def confirm_action_tool(args: dict) -> dict:
             "is_error": True,
         }
 
+    # -------------------------------------------------------------------
+    # D1/P1b: confirm_action does NOT route through _execute_transactional_tool,
+    # so step 5.5 never sees it — and it is in `allowed_tools` in both modes, by
+    # design (an eval agent that cannot request approval cannot be scored on
+    # choosing to). Left ungated it writes a durable row into the owner's triage
+    # queue on every eval scenario where the agent decides to ask, nightly.
+    #
+    # Less dangerous than the require_human row above — `_is_confirm_action_shaped`
+    # DOES filter this shape, so approving one never reaches an adapter — so this
+    # is queue pollution rather than money. It is still lost eval signal: nothing
+    # recorded that the agent chose to ask for approval, which is a decision worth
+    # scoring. Both halves are fixed here: no row, and a recording.
+    # -------------------------------------------------------------------
+    if recorded:
+        record_suppressed_side_effect(
+            "transactional.confirm_action",
+            {
+                "skill": validated.skill,
+                "action_reference": validated.action_reference,
+                "agent_id": agent_id,
+                "conversation_id": _conversation_id_var.get() or None,
+                "reason": "confirm_action.not_queued",
+            },
+        )
+        log.info(
+            "confirm_action.not_queued",
+            agent_id=agent_id,
+            skill=validated.skill,
+        )
+        return _not_executed_result(
+            "confirm_action",
+            f"No approval request was created for the '{validated.skill}' action "
+            f"(reference: {validated.action_reference}).",
+        )
+
     now = datetime.now(timezone.utc)
     expires_at = now + timedelta(hours=_CONFIRM_TTL_HOURS)
 
```

#### 1.2.2 `app/services/agent_tools.py` (+281/-19) and `app/services/transactional/enforcement.py` (+23/-2) — the tool layer, the mode, the sink

```diff
diff --git a/apps/api/app/services/agent_tools.py b/apps/api/app/services/agent_tools.py
index 11e9acf..b8bc5c6 100644
--- a/apps/api/app/services/agent_tools.py
+++ b/apps/api/app/services/agent_tools.py
@@ -39,7 +39,7 @@ import math
 import re
 import ssl
 from contextvars import ContextVar
-from typing import Any
+from typing import Any, Literal
 
 import psycopg2
 import redis as redis_lib
@@ -118,7 +118,13 @@ def _sanitise_escalation_field(value: str, max_len: int = 500) -> str:
 MAX_CHUNKS: int = 5
 MAX_CHUNK_TOKENS: int = 500  # approximate; character proxy = MAX_CHUNK_TOKENS * 4 = 2000
 
-_CONTENT_CHAR_LIMIT: int = MAX_CHUNK_TOKENS * 4  # 2000 chars
+#: Per-CHUNK cap on retrieved content. Public, and the name changed from
+#: `_CONTENT_CHAR_LIMIT` because a second reader arrived: the eval scores
+#: Faithfulness against these chunks (D1/P2) and stamps this number on the run.
+#: A claim whose support was cut at THIS boundary is marked unsupported by the
+#: judge, and a run that does not record the cap cannot tell that apart from a
+#: genuinely ungrounded claim. One copy of the number, imported by the reader.
+CHUNK_CONTENT_CHAR_LIMIT: int = MAX_CHUNK_TOKENS * 4  # 2000 chars
 
 # ---------------------------------------------------------------------------
 # SEC-02/L6 (OD-5): data-not-instructions framing on retrieve_tool's tool-result
@@ -190,6 +196,140 @@ _job_id_var: ContextVar[str] = ContextVar("job_id", default="")
 # 21-DOMAIN-NOTES.md §3 (context rot).
 CONTEXT_WINDOW_BUDGET: int = 200_000
 
+# ---------------------------------------------------------------------------
+# D1/P1b — the side-effect mode (BACKLOG 2.5).
+#
+# From P2 the nightly eval drives this same tool layer through the same seam the
+# customer's chat turn goes through, which is the entire point of approach (b):
+# the agent that is measured has to be the agent that is served. What must NOT
+# come with it is the outer edge. Three calls here leave this process and change
+# something a customer or a bank can see:
+#
+#     notify_fn                escalation mail to the owner
+#     write_retrieval_metrics  a row in the tenant's retrieval_metrics
+#     ProviderAdapter          money and tenant state, via the six mutating skills
+#
+# "recorded" suppresses exactly those three and records each one instead.
+#
+# What recorded mode deliberately does NOT do is give the eval a smaller agent.
+# The alternative the owner rejected was handing the eval a read-only
+# allowed_tools subset; it would have stopped the refund too, and would have made
+# "the agent should have refused to refund here" unfalsifiable, because an agent
+# that cannot attempt the wrong thing cannot be measured on refusing it. So the
+# tool list, the system prompt, the capability envelope, the IDV gate and the
+# Actor seam are identical in both modes.
+#
+# On the default being "live": that is the safe direction here, not the reckless
+# one. Every path that reaches these tools calls build_tool_server, and the seam
+# above it (agent.build_agent_options) takes the mode as a MANDATORY parameter
+# with no default, so the eval cannot arrive here by forgetting to choose. A
+# "recorded" default would instead mean that a caller who forgot silently stops
+# refunding real customers — a failure that produces no error anywhere and would
+# be found by a customer, not by us. The red-team probes (red_team.py,
+# red_team_probe.py) rely on this default: they read real dispatcher verdict tags
+# and are the two genuinely sound vectors in the measurement audit.
+# ---------------------------------------------------------------------------
+
+SideEffectMode = Literal["live", "recorded"]
+
+#: The only two accepted values, checked at runtime. `Literal` is a type-checker
+#: annotation and enforces nothing at run time; `side_effects="dry_run"` would
+#: otherwise compare unequal to "recorded", read as live, and move real money on
+#: the eval path.
+SIDE_EFFECT_MODES: tuple[str, ...] = ("live", "recorded")
+
+_side_effects_var: ContextVar[str] = ContextVar("side_effects", default="live")
+
+#: Per-turn sink for suppressed side effects. Holds the LIST OBJECT itself, set
+#: once by build_tool_server in the sync task body: asyncio.run() copies the
+#: context, so a .set() inside the turn would not be visible to the caller
+#: afterwards, but appends to a list installed BEFORE the copy are — the list is
+#: one shared object, not a per-context value. That is what lets P2 read back,
+#: after the turn returns, what the agent tried to do during it.
+_recorded_side_effects_var: ContextVar[list | None] = ContextVar(
+    "recorded_side_effects", default=None
+)
+
+
+def current_side_effect_mode() -> str:
+    """The side-effect mode in force for the current task context."""
+    return _side_effects_var.get()
+
+
+def record_suppressed_side_effect(kind: str, detail: dict) -> dict:
+    """Record one attempt recorded mode observed, and return the entry.
+
+    This is eval signal, not bookkeeping. That the agent CHOSE to call
+    issue_refund is capability-envelope adherence — the measurement audit's
+    confusion matrix has a whole cell for it ("executed when it should have
+    refused: money moves wrongly, critical") — and it is the observation an eval
+    would otherwise throw away, scoring only the prose that followed.
+
+    Two kinds of entry, and the distinction is the whole point of the confusion
+    matrix, so both are recorded:
+
+      * **suppressed** — the envelope let the call through and recorded mode
+        swapped the outer edge (`transactional.adapter`, `escalation.notify`,
+        `retrieval_metrics.write`, `conversation.escalated_marker`). This is the
+        matrix's *executed* column.
+      * **declined** — the agent tried and something in steps 1-5 stopped it
+        (`transactional.declined`, `transactional.confirm_action`). This is the
+        matrix's *refused* column, and recording ONLY the first kind is how an
+        eval ends up unable to tell "the agent never tried" from "the agent
+        tried and the envelope stopped it" (the two are scored oppositely).
+
+    Never raises: a recording failure must not fail the turn it is observing.
+    A missing sink is logged at WARNING rather than swallowed, because the one
+    way this becomes dangerous is by looking like it worked.
+    """
+    entry = {"kind": kind, "detail": detail}
+    sink = _recorded_side_effects_var.get()
+    if sink is None:
+        log.warning(
+            "side_effects.recorded_without_sink",
+            kind=kind,
+            note=(
+                "recorded mode suppressed a side effect but no sink was installed "
+                "— build_tool_server was not called for this context"
+            ),
+        )
+    else:
+        sink.append(entry)
+    log.info("side_effects.suppressed", kind=kind)
+    return entry
+
+
+def get_recorded_side_effects() -> list[dict]:
+    """Everything recorded mode suppressed or declined during the current turn.
+
+    Returns a copy, so a caller iterating it cannot be surprised by a late
+    append, and cannot clear the sink by mutating what it got back.
+    """
+    sink = _recorded_side_effects_var.get()
+    return list(sink) if sink else []
+
+
+def reset_side_effect_context() -> None:
+    """Return this context to the safe default: live, with an empty sink.
+
+    The mode is process-context sticky and nothing resets it between Celery
+    tasks — the prefork pool does not isolate contextvars per task. Today every
+    entry point calls `build_tool_server`, which republishes the mode, so a
+    leaked "recorded" is closed by a coincidence of the call graph rather than
+    by construction. It stops being a coincidence the moment a caller raises
+    BEFORE reaching `build_tool_server`: `build_agent_options` validates its
+    arguments and parses `RetrievalStrategy` first, and either can throw.
+
+    So `build_agent_options` calls this before it can fail. The direction is
+    deliberate: a stale "recorded" surviving into a customer's chat turn stops
+    refunding real customers with no error anywhere, and would be found by a
+    customer rather than by us. A stale "live" surviving into an eval turn is
+    the loud failure — it moves money, and every other guard in this phase
+    exists to catch it.
+    """
+    _side_effects_var.set("live")
+    _recorded_side_effects_var.set([])
+
 
 # ---------------------------------------------------------------------------
 # Internal helpers
@@ -307,6 +447,9 @@ async def retrieve_tool(args: dict[str, Any]) -> dict[str, Any]:
     # OPS-05/06: job_id is read into a local here too — never .get() inside the
     # write's executor lambda below (Pitfall 4).
     job_id = _job_id_var.get()
+    # D1/P1b: same rule, same reason — read the mode into a local here rather
+    # than inside the executor lambda, which would see the default.
+    side_effects = _side_effects_var.get()
 
     if count > _RETRIEVE_CALLS_PER_TURN_MAX:
         log.warning(
@@ -385,11 +528,14 @@ async def retrieve_tool(args: dict[str, Any]) -> dict[str, Any]:
         None, lambda: rerank(query, rrf_result["fused"], strategy)
     )
 
-    # Truncate to MAX_CHUNKS and cap content at _CONTENT_CHAR_LIMIT chars each.
+    # Truncate to MAX_CHUNKS and cap content at CHUNK_CONTENT_CHAR_LIMIT chars each.
     chunks = reranked[:MAX_CHUNKS]
     for chunk in chunks:
-        if isinstance(chunk.get("content"), str) and len(chunk["content"]) > _CONTENT_CHAR_LIMIT:
-            chunk["content"] = chunk["content"][:_CONTENT_CHAR_LIMIT]
+        if (
+            isinstance(chunk.get("content"), str)
+            and len(chunk["content"]) > CHUNK_CONTENT_CHAR_LIMIT
+        ):
+            chunk["content"] = chunk["content"][:CHUNK_CONTENT_CHAR_LIMIT]
 
     citations = [
         {
@@ -498,11 +644,24 @@ async def retrieve_tool(args: dict[str, Any]) -> dict[str, Any]:
 
     # job_id/conn_str/metrics_row are locals captured from the async body —
     # safe for the executor thread (no ContextVar.get() calls inside the lambda).
-    await loop.run_in_executor(
-        None, lambda: write_retrieval_metrics(conn_str, metrics_row)
-    )
+    #
+    # D1/P1b: on the eval path the row is recorded rather than written. These are
+    # observations about the tenant's PRODUCTION retrieval quality — OPS-05/06
+    # feeds the ops room's recall/nDCG tiles — and an eval's scenario queries
+    # would move those numbers without a single customer having asked anything.
+    # The retrieve RESULT below is unchanged: retrieval is a read, the agent must
+    # see exactly what production would hand it, and only the write is suppressed.
+    if side_effects == "recorded":
+        record_suppressed_side_effect(
+            "retrieval_metrics.write",
+            {"job_id": job_id, "conversation_id": conversation_id, "row": metrics_row},
+        )
+    else:
+        await loop.run_in_executor(
+            None, lambda: write_retrieval_metrics(conn_str, metrics_row)
+        )
 
-    # SEC-02/L6: framing is applied after the _CONTENT_CHAR_LIMIT truncation loop
+    # SEC-02/L6: framing is applied after the CHUNK_CONTENT_CHAR_LIMIT truncation loop
     # above, so a truncated chunk is still fully enclosed by the header/footer.
     # sanitize_chunk_text at ingest is complementary rather than superseded — this
     # is the retrieval-time layer, that is the admit-time layer, against the same
@@ -660,19 +819,66 @@ async def escalate_to_human_tool(args: dict[str, Any]) -> dict[str, Any]:
     agent_id = _agent_id_var.get()
     conn_str = _conn_str_var.get()
     notify_fn = _notify_fn_var.get()
+    # D1/P1b: read into a local before any run_in_executor handoff, same rule and
+    # same reason as every other ContextVar above.
+    side_effects = _side_effects_var.get()
 
     loop = asyncio.get_running_loop()
 
-    # Write escalation marker to conversations table (idempotency guard inside).
-    result = await loop.run_in_executor(
-        None,
-        lambda: _mark_conversation_escalated(
-            conversation_id, agent_id, reason, context, conn_str
-        ),
-    )
+    # -------------------------------------------------------------------
+    # D1/P1b: the escalation edge has TWO outer effects, not one. The mail is
+    # swapped at the seam (agent.build_agent_options builds a recording
+    # notify_fn), but this UPDATE is the other half and it lands in the
+    # TENANT's `conversations` table. An eval scenario that escalates would
+    # otherwise mark a real customer conversation as escalated — changing what
+    # the owner's inbox and every escalation dashboard show — and mined
+    # scenarios come from real conversations, so the id it is handed is
+    # precisely the kind that exists.
+    #
+    # Suppressing it also removes the dependency BACKLOG 2.7 named: with the
+    # UPDATE gone there is no rowcount to be zero, so the recorded escalation
+    # notification fires regardless of what conversation_id P2 chooses. The
+    # eval signal no longer hangs on a decision P2 has not made yet.
+    # -------------------------------------------------------------------
+    if side_effects == "recorded":
+        record_suppressed_side_effect(
+            "conversation.escalated_marker",
+            {
+                "conversation_id": conversation_id,
+                "agent_id": agent_id,
+                "reason": reason,
+                "context": context,
+            },
+        )
+        result: dict = {}
+    else:
+        # Write escalation marker to conversations table (idempotency guard inside).
+        result = await loop.run_in_executor(
+            None,
+            lambda: _mark_conversation_escalated(
+                conversation_id, agent_id, reason, context, conn_str
+            ),
+        )
 
     if result.get("already_escalated"):
-        return result
+        # A duplicate escalation is a benign no-op, not a failure — the
+        # conversation IS flagged and a human IS coming — so no is_error. What
+        # it must not do is hand the SDK a bare {"already_escalated": True}:
+        # every other tool in this file returns a "content" list, and the
+        # agent's next turn reasons over whatever text it finds there. A dict
+        # with no content leaves it reasoning over nothing.
+        return {
+            "already_escalated": True,
+            "content": [
+                {
+                    "type": "text",
+                    "text": (
+                        "This conversation is already flagged for our support team. "
+                        "A human will follow up shortly."
+                    ),
+                }
+            ],
+        }
 
     # Fire-and-forget notification (email / webhook / slack — injected by task).
     # Prefix with [AGENT-DETECTED — UNVERIFIED] so recipients know this is LLM-sourced.
@@ -733,6 +939,7 @@ def build_tool_server(
     tenant_id: str = "",
     verified_session_token: str = "",
     job_id: str = "",
+    side_effects: str = "live",
 ) -> object:
     """Inject tenant-scoped state into ContextVars and return the MCP server.
 
@@ -761,6 +968,18 @@ def build_tool_server(
         job_id:                  OPS-05/06 (Phase 21 Plan 03): Celery job_id, threaded into
                                  retrieve_tool's retrieval_metrics write path via _job_id_var.
                                  Empty string when omitted (backward compatible).
+        side_effects:            D1/P1b (BACKLOG 2.5): "live" or "recorded". Defaults to
+                                 "live" so every pre-existing caller — notably the red-team
+                                 probes, which must read REAL dispatcher verdict tags —
+                                 keeps the behaviour it had. The mandatory-no-default rule
+                                 lives one layer up, on agent.build_agent_options, which is
+                                 where the eval path is chosen. See the SideEffectMode block
+                                 above for why the default points this way.
+
+    Raises:
+        ValueError: side_effects is neither "live" nor "recorded". Deliberately loud:
+            a typo that silently read as "not recorded, therefore live" would move
+            real money on the eval path.
 
     Returns:
         MCP server object (create_sdk_mcp_server result) registering all 11 tools:
@@ -768,6 +987,14 @@ def build_tool_server(
         7 transactional tools added in Phase 14 Plan 04 (place_order, cancel_order,
         issue_refund, update_subscription, book_slot, update_customer_record, confirm_action).
     """
+    if side_effects not in SIDE_EFFECT_MODES:
+        raise ValueError(
+            f"build_tool_server: side_effects must be one of {SIDE_EFFECT_MODES}, "
+            f"got {side_effects!r}. An unrecognised value would compare unequal to "
+            f"'recorded' and be served as live — which on the eval path means a "
+            f"real refund against the tenant's provider (BACKLOG 2.5)."
+        )
+
     _conn_str_var.set(conn_str)
     _agent_id_var.set(agent_id)
     _tenant_id_var.set(tenant_id)
@@ -806,7 +1033,7 @@ def build_tool_server(
         update_subscription_tool,
     )
 
-    return create_sdk_mcp_server(
+    server = create_sdk_mcp_server(
         name="customer-tools",
         version="1.0.0",
         tools=[
@@ -825,3 +1052,21 @@ def build_tool_server(
             confirm_action_tool,
         ],
     )
+
+    # D1/P1b: publish the mode and install a FRESH recording sink for this turn.
+    #
+    # LAST, after every step that can raise. The mode is process-context sticky
+    # and the prefork pool does not isolate contextvars per task, so publishing
+    # it before create_sdk_mcp_server would mean a half-built tool server leaves
+    # a "recorded" behind for whatever runs next in this worker's context — a
+    # customer turn that then silently stops refunding, with no error anywhere.
+    # Nothing between here and the return reads either variable, so the move
+    # costs nothing.
+    #
+    # Fresh sink matters as much as the mode: one carried over from the previous
+    # turn would report one eval scenario's refund attempt as another's, which
+    # is worse than no recording at all — a wrong observation that looks right.
+    _side_effects_var.set(side_effects)
+    _recorded_side_effects_var.set([])
+
+    return server
diff --git a/apps/api/app/services/transactional/enforcement.py b/apps/api/app/services/transactional/enforcement.py
index 7ee8cc0..e9e4cb8 100644
--- a/apps/api/app/services/transactional/enforcement.py
+++ b/apps/api/app/services/transactional/enforcement.py
@@ -309,7 +309,28 @@ async def apply_rate_and_constraint_checks(
     if parsed is not None:
         max_calls, window_secs = parsed
         window_key = int(time.time()) // window_secs
-        redis_key = f"ratelimit:{agent_id_str}:{skill}:{window_key}"
+        # D1/P1b: the rate counter is SHARED STATE, and the eval drives this
+        # same dispatcher. Keyed only on (agent, skill, window) an overnight
+        # eval with six refund-shaped scenarios exhausts an envelope that
+        # allows five refunds an hour, and the next REAL customer refund in
+        # that window comes back "Request denied by rate or constraint check
+        # (reason: rate_limit)" — silent from the eval's side, and reading as
+        # an ordinary envelope denial from the customer's side.
+        #
+        # Namespacing rather than suppressing: the eval still measures the
+        # ceiling, on its own counter. Suppressing the INCR would make
+        # "the agent kept refunding past its limit" unfalsifiable, which is the
+        # same mistake as handing the eval a read-only tool subset. Rolling the
+        # INCR back is not an option either — the pipeline is not transactional
+        # against a concurrent real caller.
+        #
+        # Lazy import: agent_tools imports transactional.tools (which imports
+        # this module) inside build_tool_server, so a module-level import here
+        # would close that loop.
+        from app.services.agent_tools import _side_effects_var  # noqa: PLC0415
+
+        mode_prefix = "recorded:" if _side_effects_var.get() == "recorded" else ""
+        redis_key = f"ratelimit:{mode_prefix}{agent_id_str}:{skill}:{window_key}"
 
         def _do_rate_limit_pipeline() -> tuple[int, Any]:
             client = _get_redis()
```

#### 1.2.3 `app/worker/tasks/runtime/agent.py` (+566/-107) — THE SEAM, recorded mode's entry, the canary reorder, the retrieve decode

```diff
diff --git a/apps/api/app/worker/tasks/runtime/agent.py b/apps/api/app/worker/tasks/runtime/agent.py
index caaa7d6..acdcfb6 100644
--- a/apps/api/app/worker/tasks/runtime/agent.py
+++ b/apps/api/app/worker/tasks/runtime/agent.py
@@ -29,6 +29,7 @@ SSE event sequence:
 Queue: runtime (CLAUDE.md non-negotiable: both Celery queues always present)
 """
 
+import ast
 import asyncio
 import json
 import os
@@ -61,7 +62,16 @@ from app.models.agent import Agent
 from app.models.job import Job
 from app.models.prompt_version import PromptVersion
 from app.services.agent_prompt import build_system_prompt
-from app.services.agent_tools import RetrievalStrategy, build_tool_server
+from app.services.agent_tools import (
+    RETRIEVED_CONTEXT_FOOTER,
+    RETRIEVED_CONTEXT_HEADER,
+    SIDE_EFFECT_MODES,
+    RetrievalStrategy,
+    SideEffectMode,
+    build_tool_server,
+    record_suppressed_side_effect,
+    reset_side_effect_context,
+)
 from app.services.escalation import send_escalation_email
 from app.services.events import emit
 from app.services.prompt_version_service import resolve_prompt_version
@@ -99,6 +109,138 @@ except Exception:
 CITATIONS_REGEX = re.compile(r"CITATIONS:\n((?:- Document: .+ \| Section: .+\n?)+)")
 _CITATION_ENTRY = re.compile(r"- Document: (.+) \| Section: (.+)")
 
+# ---------------------------------------------------------------------------
+# Two bounds on a turn that were literals inside the functions below and are now
+# named, because a SECOND caller reads them (D1/P2, .dev/plans/260807-d1-agent-
+# invocation.md). Neither value changes; this is extraction, not tuning.
+#
+# The eval task drives the same `_run_sdk_turn` with the same wall-clock ceiling,
+# and it stamps the retrieve cap on the run's provenance. A second copy of either
+# number in eval.py would be the audit's D3 defect wearing new clothes: the
+# deploy gate's eval query fails open to this day because one call site kept its
+# own copy of a column name. So there is one copy, here, and the other reader
+# imports it.
+# ---------------------------------------------------------------------------
+
+#: How much of a `retrieve` tool result is captured onto `tool_calls_log`.
+#: The Auditor reads it (further trimmed to 600 chars per context in the
+#: validator dispatch below) and, from P2, the eval scores Faithfulness against
+#: it. That is why the number has to travel: a claim whose support was CUT at
+#: this boundary is marked unsupported by the judge, and a run that does not
+#: record the cap cannot tell that apart from a genuinely ungrounded claim.
+RETRIEVE_RESULT_CAPTURE_CHARS = 1800
+
+#: The key on a `tool_calls_log` retrieve entry that carries the retrieved
+#: chunks as ONE STRING PER CHUNK, untruncated. Beside it, `result` keeps the
+#: audit capture unchanged — `str(block.content)[:RETRIEVE_RESULT_CAPTURE_CHARS]`,
+#: which is a Python repr of the SDK content block cut mid-structure.
+#:
+#: WHY BOTH. The eval scores Faithfulness / ContextPrecision / ContextRecall
+#: against what this turn retrieved. Handing it `result` handed the judge (a) a
+#: repr — `[{'type': 'text', 'text': "<<<HEADER>>>\n[{'chunk_id': ...` — whose
+#: dict-syntax noise is most of the token budget, (b) cut at 1800 chars, which is
+#: below ONE full chunk on any realistic corpus, so essentially every retrieving
+#: turn was at the cap and `retrieved_context_at_cap` was a constant rather than
+#: an observation, and (c) as a SINGLE element, which collapses ContextPrecision's
+#: ranking semantics to a coin flip over one blob. Three ways for the capture
+#: format to dominate the score of the thing being measured.
+#:
+#: `result` is deliberately NOT changed: the Auditor and the retrieval-faithfulness
+#: sampler read it and the chat path stays byte-for-byte.
+RETRIEVE_CHUNKS_KEY = "retrieved_chunks"
+
+#: Companion to the key above: 'chunks' when the framed payload was split back
+#: into per-chunk strings, 'unparsed' when it could not be. Never absent, so the
+#: eval reports a turn whose contexts could not be read as an unparsed
+#: observation instead of as a turn that retrieved nothing.
+RETRIEVE_CHUNKS_SOURCE_KEY = "retrieved_chunks_source"
+RETRIEVE_CHUNKS_PARSED = "chunks"
+RETRIEVE_CHUNKS_UNPARSED = "unparsed"
+
+#: Wall-clock ceiling on one SDK turn, enforced by asyncio.wait_for.
+#: D-11 raised it from 30s to 90s — a warm-but-not-hot Agent SDK subprocess needs
+#: up to 90s on slower ARM VMs; the SSE layer retains 120s (30s headroom). The
+#: eval's per-run cost ceiling is derived from this value rather than from a
+#: guess about it.
+AGENT_TURN_TIMEOUT_S = 90
+
+
+# ---------------------------------------------------------------------------
+# Reading a retrieve tool result back out of the SDK stream (D1/P2 review)
+# ---------------------------------------------------------------------------
+
+
+def _tool_result_text(content: object) -> str:
+    """The TEXT of a ToolResultBlock, whatever shape the SDK handed it in.
+
+    `str(block.content)` — what the audit capture below still does — is a Python
+    repr when the block carries the MCP list-of-blocks shape our tools return
+    (`[{'type': 'text', 'text': '...'}]`). Reprs are fine for an audit column and
+    ruinous for a judge: the dict syntax is noise the metric cannot distinguish
+    from evidence.
+    """
+    if content is None:
+        return ""
+    if isinstance(content, str):
+        return content
+    if isinstance(content, list):
+        parts: list[str] = []
+        for block in content:
+            if isinstance(block, dict):
+                if block.get("type") == "text":
+                    parts.append(str(block.get("text", "")))
+            else:
+                text = getattr(block, "text", None)
+                if text is not None:
+                    parts.append(str(text))
+        if parts:
+            return "\n".join(parts)
+    return str(content)
+
+
+def _retrieved_chunk_texts(result_text: str) -> list[str] | None:
+    """Split a framed retrieve result back into ONE STRING PER CHUNK.
+
+    `agent_tools.retrieve_tool` returns
+    `_frame_retrieved_context(str(chunks))` — the header, then the repr of a
+    list of chunk dicts, then the footer. This undoes exactly that, so what the
+    eval scores is the chunk text the agent was shown rather than the transport
+    encoding around it.
+
+    Returns None — never `[]` — when the payload cannot be read, because "this
+    turn retrieved nothing" and "this turn retrieved something this function
+    could not parse" are different observations and the second must not be
+    reported as the first. `ast.literal_eval` (never `eval`) is the parser: the
+    payload originates from a tool result and is therefore attacker-influenced
+    text, so it may only ever become data.
+    """
+    text = result_text
+    header_at = text.find(RETRIEVED_CONTEXT_HEADER)
+    if header_at == -1:
+        return None
+    payload = text[header_at + len(RETRIEVED_CONTEXT_HEADER):]
+    footer_at = payload.rfind(RETRIEVED_CONTEXT_FOOTER)
+    if footer_at != -1:
+        payload = payload[:footer_at]
+
+    try:
+        chunks = ast.literal_eval(payload.strip())
+    except (ValueError, SyntaxError, MemoryError, RecursionError):
+        return None
+    if not isinstance(chunks, list):
+        return None
+
+    texts: list[str] = []
+    for chunk in chunks:
+        if isinstance(chunk, dict):
+            content = chunk.get("content", "")
+        else:
+            content = chunk
+        rendered = str(content) if content is not None else ""
+        if rendered:
+            texts.append(rendered)
+    return texts
+
 
 # ---------------------------------------------------------------------------
 # Module-level helpers — tenant DB writes via psycopg2 (parameterised only)
@@ -217,22 +359,31 @@ def _set_prompt_version_id(conn, conv_id: str, prompt_version_id: str) -> None:
 
 def _resolve_turn_prompt_version(
     db,
-    tenant_conn,
     *,
     agent_id: str,
     local_conversation_id: str,
     existing_prompt_version_id: str | None,
-) -> tuple[str | None, dict | None]:
+) -> tuple[str | None, dict | None, bool]:
     """Resolve the prompt version to serve this turn, sticky per conversation (OPS-16).
 
+    READ ONLY — control DB. This function used to also WRITE the resolved id to
+    conversations.metadata, and P1 moved the whole thing ahead of the seam
+    because the soul fields it returns are an input to the system prompt. The
+    write came along for the ride, so a turn that then died in
+    build_agent_options left the conversation permanently sticky to a version
+    that never served it, where the Celery retry previously re-rolled (BACKLOG
+    2.6). Settled 2026-08-07: resolve before, commit after. The read stays here;
+    the write is the caller's, behind a successful options build.
+
     First turn of a conversation (existing_prompt_version_id is None): calls
-    resolve_prompt_version (weighted pick, control DB) and — if a version was
-    found — persists the choice on conversations.metadata (tenant DB) so every
-    subsequent turn on this conversation reuses it (A-CANARY: no mid-
-    conversation persona flip; the version is never re-rolled).
+    resolve_prompt_version (weighted pick, control DB) and reports back that the
+    caller must persist the choice, so every subsequent turn on this
+    conversation reuses it (A-CANARY: no mid-conversation persona flip; the
+    version is never re-rolled).
 
     Subsequent turns (existing_prompt_version_id provided): re-fetches that
-    EXACT version's soul fields by id — never re-rolls, never re-picks.
+    EXACT version's soul fields by id — never re-rolls, never re-picks, and
+    nothing to persist because the id is already stored.
 
     T-21-09-05 (never fails a turn): any exception here is caught and treated
     as "no version resolved" — the caller falls back to the agent's live
@@ -242,27 +393,32 @@ def _resolve_turn_prompt_version(
     blocks or fails the served turn.
 
     Returns:
-        (prompt_version_id, soul_override) — both None on no-version-exists,
-        resolution failure, or a stale/deleted stored version id.
+        (prompt_version_id, soul_override, needs_persist).
+
+        needs_persist is True only for a first turn that actually resolved a
+        version — the one case where conversations.metadata does not yet hold
+        the id. It is returned rather than re-derived by the caller from
+        `existing_prompt_version_id is None` so that a future change to the
+        resolution rules (a stale id that re-rolls, say) cannot leave the
+        caller's copy of the logic silently disagreeing with this one.
     """
     try:
         if existing_prompt_version_id:
             pv = db.get(PromptVersion, existing_prompt_version_id)
             if pv is None:
-                return None, None
+                return None, None, False
             return str(pv.id), {
                 "soul_role": pv.soul_role,
                 "soul_voice": pv.soul_voice,
                 "soul_do_list": pv.soul_do_list,
                 "soul_donot_list": pv.soul_donot_list,
-            }
+            }, False
 
         resolved_id, soul_override = resolve_prompt_version(db, agent_id)
         if resolved_id is None:
-            return None, None
+            return None, None, False
 
-        _set_prompt_version_id(tenant_conn, local_conversation_id, resolved_id)
-        return resolved_id, soul_override
+        return resolved_id, soul_override, True
     except Exception as exc:
         log.warning(
             "run_agent_turn.prompt_version_resolve_failed",
@@ -270,7 +426,7 @@ def _resolve_turn_prompt_version(
             conversation_id=local_conversation_id,
             error=str(exc),
         )
-        return None, None
+        return None, None, False
 
 
 def _persist_messages(
@@ -496,6 +652,218 @@ def _extract_citations(text: str) -> list[dict]:
     return citations
 
 
+# ---------------------------------------------------------------------------
+# THE SEAM — the one place ClaudeAgentOptions is constructed
+#
+# D1 (.dev/plans/260807-d1-agent-invocation.md, P1). The nightly eval scored
+# reference answers against the contexts those answers were written from and
+# never invoked the agent at all. The fix is to make the eval invoke it — and
+# the only version of that fix worth having is one where the eval and the
+# customer are served by the SAME agent. "Same agent" is not the same model id;
+# it is the system prompt, the tool server (which is where the capability
+# envelope is enforced), the allowed-tool list, the turn and budget ceilings and
+# the model, together. Assemble any of those twice and the eval measures
+# something adjacent to the product, which the measurement-layer audit records
+# as this repo's recurring defect.
+#
+# So they are assembled exactly once, here, and both callers go through it.
+# tests/unit/test_agent_options_seam.py fails if run_agent_turn constructs
+# options — or a tool server, or a system prompt — by any other route. That test
+# is the mechanism; this comment is only its explanation.
+#
+# SETTLED, AND IT IS WHY P2 CAN NOW PROCEED (BACKLOG 2.5, owner, 2026-08-07).
+# The options this returns carry a LIVE tool server bound to the tenant's real
+# connection string. Every caller of this seam therefore reaches, by default:
+#   * retrieve            -> write_retrieval_metrics(conn_str, …) into the tenant DB
+#   * escalate_to_human   -> _mark_conversation_escalated(…) + send_escalation_email
+#   * the 6 mutating transactional skills -> a tool_calls_audit row AND the real
+#     ProviderAdapter: place_order, cancel_order, issue_refund,
+#     update_subscription, book_slot, update_customer_record.
+# The plan chose approach (b) over (a) precisely to keep eval traffic out of
+# tenant data; (b) as built still wrote to tenant tables and could move money —
+# one eval scenario in which the agent decides to refund executed a refund.
+#
+# The answer is `side_effects`, below: MANDATORY, no default. A default is
+# exactly the mechanism by which the eval path silently ends up live, so a caller
+# that does not state which it wants raises TypeError at the call site rather
+# than discovering the question against a real tenant at 3am.
+#
+# The alternative — a read-only allowed_tools subset for the eval — was rejected,
+# and the reason is worth keeping here because it constrains every future change
+# to this function: removing the mutating skills would make the eval measure an
+# agent with fewer capabilities than production serves, and a scenario testing
+# "the agent should refuse to refund here" could no longer FAIL, because the
+# agent could not even try. An agent that cannot attempt the wrong thing cannot
+# be measured on refusing it. So allowed_tools is identical in both modes, and
+# the capability envelope, IDV gate and Actor seam all still run; what changes is
+# only the outer edge, where a call would leave this process. notify_fn is now a
+# parameter of that edge (it was deliberately hardcoded in P1 — "an unused escape
+# hatch added before the caller that needs it exists is how a seam starts
+# drifting"; P2 is that caller, so the hatch is no longer unused).
+# ---------------------------------------------------------------------------
+
+def build_agent_options(
+    *,
+    agent,
+    conn_str: str,
+    conversation_id: str,
+    job_id: str,
+    side_effects: SideEffectMode,
+    verified_session_token: str = "",
+    soul_override: dict | None = None,
+    resume: str | None = None,
+) -> "ClaudeAgentOptions":
+    """Build the ClaudeAgentOptions for one turn of `agent`.
+
+    Side effect, and it is the point: build_tool_server sets the per-task
+    ContextVars (conn_str, agent_id, tenant_id, strategy, conversation_id,
+    notify_fn, job_id, verified session token, retrieve counter) that every tool
+    handler reads. Calling this twice for one turn would leave the second call's
+    context in force — hence the "exactly once" pin in the seam test.
+
+    Args:
+        agent:                  Control-DB Agent row. Supplies id, tenant_id,
+                                name, retrieval_strategy and the soul fields
+                                build_system_prompt reads.
+        conn_str:               Decrypted tenant DB connection string. Never
+                                logged, never a task arg (CTL-08).
+        conversation_id:        Conversation UUID string — escalation writes and
+                                tool-side conversation scoping.
+        job_id:                 Celery job id (OPS-05/06 retrieval metrics).
+        side_effects:           MANDATORY, no default (BACKLOG 2.5). "live" is
+                                production, byte for byte what the chat path has
+                                always done. "recorded" is the eval path: the
+                                escalation notification, the retrieval_metrics
+                                write and the transactional ProviderAdapter are
+                                suppressed and recorded instead. Everything the
+                                agent can see or choose is identical.
+        verified_session_token: IDV-05 token, "" when there is no verified
+                                session. NEVER logged (T-04-03-05).
+        soul_override:          Prompt-version soul fields (OPS-16) or None to
+                                serve the agent's live soul_* columns.
+        resume:                 SDK session id to continue, or None to start one.
+
+    Raises:
+        ValueError: side_effects is neither "live" nor "recorded". Literal is a
+            type-checker annotation and enforces nothing at run time, so an
+            unrecognised value would compare unequal to "recorded" and be served
+            as live — a real refund on the eval path.
+    """
+    # The mode is process-context sticky and the Celery prefork pool does not
+    # isolate contextvars per task, so a previous turn's value is still in force
+    # on entry. Today build_tool_server below always republishes it — but only
+    # if we REACH it, and three things above it raise: this validation, the
+    # RetrievalStrategy parse, and build_tool_server's own. A turn that dies in
+    # any of them would leave a stale "recorded" behind for whatever ran next in
+    # this context. Resetting FIRST, before anything that can throw, makes that
+    # a property of this function rather than of the call graph's current shape.
+    reset_side_effect_context()
+
+    if side_effects not in SIDE_EFFECT_MODES:
+        raise ValueError(
+            f"build_agent_options: side_effects must be one of {SIDE_EFFECT_MODES}, "
+            f"got {side_effects!r}. There is no third mode and no fallback: an "
+            f"unrecognised value read as live is how an eval scenario issues a real "
+            f"refund against the tenant's provider (BACKLOG 2.5)."
+        )
+
+    strategy = RetrievalStrategy.model_validate(agent.retrieval_strategy or {})
+
+    # The escalation edge. On the eval path the mail is recorded rather than
+    # sent — a scenario that drives the agent to escalate would otherwise page
+    # the owner about a customer who does not exist, and would do it nightly.
+    # A conditional expression rather than two `def`s: nested function
+    # definitions in this module are banned by the seam suite, which attributes
+    # every call to the module-scope function containing it.
+    notify_fn = (
+        (lambda reason, context: send_escalation_email(agent, reason, context))
+        if side_effects == "live"
+        else (
+            lambda reason, context: record_suppressed_side_effect(
+                "escalation.notify",
+                {
+                    "agent_id": str(agent.id),
+                    "conversation_id": str(conversation_id),
+                    "reason": reason,
+                    "context": context,
+                },
+            )
+        )
+    )
+
+    tool_server = build_tool_server(
+        conn_str=conn_str,
+        agent_id=str(agent.id),
+        agent_name=agent.name,
+        strategy=strategy,
+        conversation_id=str(conversation_id),
+        notify_fn=notify_fn,
+        tenant_id=str(agent.tenant_id),
+        verified_session_token=verified_session_token,
+        job_id=job_id,
+        side_effects=side_effects,
+    )
+
+    system_prompt = build_system_prompt(agent, soul_override=soul_override)
+
+    # D-10 note (13-07): The Voyage 3 RPM free-tier prompt-level retrieve-cap
+    # instruction was removed now that embeddings move to Bedrock (PROD-06).
+    # Bedrock has no comparable RPM constraint; the per-turn retrieve counter in
+    # agent_tools.retrieve_tool remains active as a DoS guard (ceiling raised to 8).
+
+    # R-05: allowed_tools use full MCP namespace mcp__customer-tools__*
+    # D-10 fix phase 1 (2026-06-01): max_turns raised from 3 to 6.
+    #   Root cause: max_turns=3 cut the agent off after the retrieve tool
+    #   round-trip (tool_use + tool_result = 2 turns), leaving no turn to
+    #   compose the final text answer → empty response_text.
+    #   The Voyage RPM guard is now enforced solely by the tool-level counter
+    #   in agent_tools.retrieve_tool (blocks the 3rd call per turn), making
+    #   max_turns free to cover the full retrieve → synthesis cycle.
+    #   6 turns is sufficient for: thinking + retrieve + synthesis + any
+    #   clarify/escalate follow-ups while still bounding DoS risk (T-04-03-06).
+    # D-10 fix phase 2 (2026-06-01): max_budget_usd raised from 0.05 to
+    #   settings.AGENT_MAX_BUDGET_USD (default 0.50).
+    #   Root cause (additional): the 0.05 USD cap was too tight for a
+    #   turn that uses extended thinking (~38s) + retrieved context + synthesis.
+    #   A Haiku extended-thinking + retrieve + synthesis turn can exceed $0.05.
+    #   When the budget is exceeded the CLI emits result{subtype:error_max_budget,
+    #   is_error:true} → receive_response() terminates → response_text stays ""
+    #   with no exception raised (identical empty-text signature to max_turns).
+    #   0.50 USD gives headroom while still serving as a DoS guardrail.
+    #   Configure via AGENT_MAX_BUDGET_USD env var for tighter prod limits.
+    # R-05: allowed_tools suppresses SDK permission prompts only.
+    # The capability envelope check inside each transactional tool handler
+    # is the real access gate (fail-closed) — T-14-04-03.
+    return ClaudeAgentOptions(
+        # AGENT_TURN_MODEL, not a literal — eval_runs.config.model_id
+        # reads the same constant, so a score can never be attributed
+        # to a model that did not serve the turn (migration 0013).
+        model=AGENT_TURN_MODEL,
+        system_prompt=system_prompt,
+        mcp_servers={"customer-tools": tool_server},  # type: ignore[dict-item]  # agent-sdk/anthropic stubs are narrower than the runtime contract
+        allowed_tools=[
+            # Original 4 tools — retained (TXN-04 requirement)
+            "mcp__customer-tools__retrieve",
+            "mcp__customer-tools__lookup_structured",
+            "mcp__customer-tools__escalate_to_human",
+            "mcp__customer-tools__clarify",
+            # Phase 14 Plan 04 — 7 transactional tools
+            # Listing here suppresses SDK permission prompts only;
+            # the capability envelope in each handler is the real gate.
+            "mcp__customer-tools__place_order",
+            "mcp__customer-tools__cancel_order",
+            "mcp__customer-tools__issue_refund",
+            "mcp__customer-tools__update_subscription",
+            "mcp__customer-tools__book_slot",
+            "mcp__customer-tools__update_customer_record",
+            "mcp__customer-tools__confirm_action",
+        ],
+        resume=resume,
+        max_turns=6,   # D-10 fix: was 3 (too low — cut off synthesis after retrieve)
+        max_budget_usd=settings.AGENT_MAX_BUDGET_USD,  # D-10 fix phase 2: was 0.05 (too low for thinking+retrieve+synthesis)
+    )
+
+
 # ---------------------------------------------------------------------------
 # Async SDK turn helper — bridged into sync Celery task via asyncio.run()
 # ---------------------------------------------------------------------------
@@ -582,10 +950,31 @@ async def _run_sdk_turn(
                             db,
                             redis,
                         )
-                        # Capture retrieve result for Auditor (M5 — plan 05-04)
+                        # TWO captures of one retrieve result, for two readers.
+                        #
+                        # `result` — unchanged, byte-for-byte: the Auditor's
+                        # retrieved_context_json and the retrieval-faithfulness
+                        # sampler read it, and RETRIEVE_RESULT_CAPTURE_CHARS
+                        # bounds it because it also reaches a jsonb column.
+                        #
+                        # RETRIEVE_CHUNKS_KEY — the same result decoded into one
+                        # string per CHUNK, untruncated, for the eval (D1/P2
+                        # review). Handing Ragas `result` handed it a repr, cut
+                        # below one full chunk, in a single-element list; the
+                        # capture format then dominated Faithfulness and
+                        # ContextPrecision. Not persisted: _persist_messages
+                        # writes tool_name / input / result only.
                         for tc in reversed(tool_calls_log):
                             if tc.get("tool_name") == "retrieve" and "result" not in tc:
-                                tc["result"] = str(getattr(block, "content", ""))[:1800]
+                                raw = getattr(block, "content", "")
+                                tc["result"] = str(raw)[:RETRIEVE_RESULT_CAPTURE_CHARS]
+                                chunks = _retrieved_chunk_texts(_tool_result_text(raw))
+                                tc[RETRIEVE_CHUNKS_KEY] = chunks or []
+                                tc[RETRIEVE_CHUNKS_SOURCE_KEY] = (
+                                    RETRIEVE_CHUNKS_PARSED
+                                    if chunks is not None
+                                    else RETRIEVE_CHUNKS_UNPARSED
+                                )
                                 break
 
             elif isinstance(msg, ResultMessage):
@@ -780,95 +1169,84 @@ def run_agent_turn(
                 sdk_resume = conv_row["metadata"].get("sdk_session_id")
                 existing_prompt_version_id = conv_row["metadata"].get("prompt_version_id")
 
-            # --------------------------------------------------------------
-            # Build retrieval strategy, tool server, system prompt, options
-            # --------------------------------------------------------------
-            strategy = RetrievalStrategy.model_validate(agent.retrieval_strategy or {})
-
-            tool_server = build_tool_server(
-                conn_str=conn_str,
-                agent_id=str(agent.id),
-                agent_name=agent.name,
-                strategy=strategy,
-                conversation_id=str(local_conversation_id),
-                notify_fn=lambda r, c: send_escalation_email(agent, r, c),
-                tenant_id=str(agent.tenant_id),
-                verified_session_token=verified_session_token,
-                job_id=job_id,
-            )
-
             # ----------------------------------------------------------------
             # OPS-16: canary prompt-version resolution — sticky per conversation,
             # never fails a turn (T-21-09-05). See _resolve_turn_prompt_version's
             # own docstring for the first-turn-vs-subsequent-turn distinction.
+            #
+            # This RESOLUTION runs BEFORE the tool server is built rather than
+            # after. The soul fields it returns are an input to the system
+            # prompt, and the system prompt is built inside build_agent_options
+            # together with the tool server, so the resolution has to precede the
+            # one call that consumes both. That part of P1's move stands.
+            #
+            # The WRITE does not: it now happens after build_agent_options
+            # returns (BACKLOG 2.6, settled 2026-08-07 — "resolve before, commit
+            # after"). _resolve_turn_prompt_version used to call
+            # _set_prompt_version_id itself, so P1's move carried the commit
+            # forward with the read, and a turn that then died in
+            # RetrievalStrategy.model_validate or build_tool_server left the
+            # conversation permanently sticky to a version that never served it
+            # — where before P1 the Celery retry re-rolled. Pinned in both
+            # directions by test_the_canary_choice_is_not_committed_when_the_
+            # options_build_fails and ..._is_committed_once_the_options_exist.
             # ----------------------------------------------------------------
-            prompt_version_id, soul_override = _resolve_turn_prompt_version(
+            prompt_version_id, soul_override, canary_needs_persist = _resolve_turn_prompt_version(
                 db,
-                tenant_conn,
                 agent_id=agent_id,
                 local_conversation_id=str(local_conversation_id),
                 existing_prompt_version_id=existing_prompt_version_id,
             )
 
-            system_prompt = build_system_prompt(agent, soul_override=soul_override)
-
-            # D-10 note (13-07): The Voyage 3 RPM free-tier prompt-level retrieve-cap
-            # instruction was removed now that embeddings move to Bedrock (PROD-06).
-            # Bedrock has no comparable RPM constraint; the per-turn retrieve counter in
-            # agent_tools.retrieve_tool remains active as a DoS guard (ceiling raised to 8).
-
-            # R-05: allowed_tools use full MCP namespace mcp__customer-tools__*
-            # D-10 fix phase 1 (2026-06-01): max_turns raised from 3 to 6.
-            #   Root cause: max_turns=3 cut the agent off after the retrieve tool
-            #   round-trip (tool_use + tool_result = 2 turns), leaving no turn to
-            #   compose the final text answer → empty response_text.
-            #   The Voyage RPM guard is now enforced solely by the tool-level counter
-            #   in agent_tools.retrieve_tool (blocks the 3rd call per turn), making
-            #   max_turns free to cover the full retrieve → synthesis cycle.
-            #   6 turns is sufficient for: thinking + retrieve + synthesis + any
-            #   clarify/escalate follow-ups while still bounding DoS risk (T-04-03-06).
-            # D-10 fix phase 2 (2026-06-01): max_budget_usd raised from 0.05 to
-            #   settings.AGENT_MAX_BUDGET_USD (default 0.50).
-            #   Root cause (additional): the 0.05 USD cap was too tight for a
-            #   turn that uses extended thinking (~38s) + retrieved context + synthesis.
-            #   A Haiku extended-thinking + retrieve + synthesis turn can exceed $0.05.
-            #   When the budget is exceeded the CLI emits result{subtype:error_max_budget,
-            #   is_error:true} → receive_response() terminates → response_text stays ""
-            #   with no exception raised (identical empty-text signature to max_turns).
-            #   0.50 USD gives headroom while still serving as a DoS guardrail.
-            #   Configure via AGENT_MAX_BUDGET_USD env var for tighter prod limits.
-            # R-05: allowed_tools suppresses SDK permission prompts only.
-            # The capability envelope check inside each transactional tool handler
-            # is the real access gate (fail-closed) — T-14-04-03.
-            options = ClaudeAgentOptions(
-                # AGENT_TURN_MODEL, not a literal — eval_runs.config.model_id
-                # reads the same constant, so a score can never be attributed
-                # to a model that did not serve the turn (migration 0013).
-                model=AGENT_TURN_MODEL,
-                system_prompt=system_prompt,
-                mcp_servers={"customer-tools": tool_server},  # type: ignore[dict-item]  # agent-sdk/anthropic stubs are narrower than the runtime contract
-                allowed_tools=[
-                    # Original 4 tools — retained (TXN-04 requirement)
-                    "mcp__customer-tools__retrieve",
-                    "mcp__customer-tools__lookup_structured",
-                    "mcp__customer-tools__escalate_to_human",
-                    "mcp__customer-tools__clarify",
-                    # Phase 14 Plan 04 — 7 transactional tools
-                    # Listing here suppresses SDK permission prompts only;
-                    # the capability envelope in each handler is the real gate.
-                    "mcp__customer-tools__place_order",
-                    "mcp__customer-tools__cancel_order",
-                    "mcp__customer-tools__issue_refund",
-                    "mcp__customer-tools__update_subscription",
-                    "mcp__customer-tools__book_slot",
-                    "mcp__customer-tools__update_customer_record",
-                    "mcp__customer-tools__confirm_action",
-                ],
+            # --------------------------------------------------------------
+            # THE SEAM (D1/P1). Retrieval strategy, tool server, system prompt,
+            # model, allowed tools and the turn/budget ceilings are assembled in
+            # build_agent_options above — the same callable the eval task goes
+            # through — so the agent measured is the agent served. Constructing
+            # any of them here instead is what test_agent_options_seam.py fails on.
+            #
+            # side_effects="live" is the chat path, stated rather than defaulted
+            # (BACKLOG 2.5). This is the turn a customer is waiting on: its
+            # refunds are real, its escalation mail must arrive, and its
+            # retrieval_metrics row is what the ops room reads.
+            # --------------------------------------------------------------
+            options = build_agent_options(
+                agent=agent,
+                conn_str=conn_str,
+                conversation_id=str(local_conversation_id),
+                job_id=job_id,
+                side_effects="live",
+                verified_session_token=verified_session_token,
+                soul_override=soul_override,
                 resume=sdk_resume,
-                max_turns=6,   # D-10 fix: was 3 (too low — cut off synthesis after retrieve)
-                max_budget_usd=settings.AGENT_MAX_BUDGET_USD,  # D-10 fix phase 2: was 0.05 (too low for thinking+retrieve+synthesis)
             )
 
+            # --------------------------------------------------------------
+            # BACKLOG 2.6: the canary choice becomes sticky only now that there
+            # is an agent for it to be sticky to. A turn that died above
+            # re-rolls on retry, as it did before P1.
+            #
+            # Wrapped, and never fatal (T-21-09-05): a tenant-DB failure here
+            # must not fail a turn whose options are already built. The
+            # consequence of that failure is narrower than it was — the version
+            # still served this turn and turn_metrics still attributes the turn
+            # to it, which is the honest record; only the stickiness is lost, so
+            # the next turn of this conversation re-rolls.
+            # --------------------------------------------------------------
+            if canary_needs_persist and prompt_version_id:
+                try:
+                    _set_prompt_version_id(
+                        tenant_conn, str(local_conversation_id), prompt_version_id
+                    )
+                except Exception as canary_exc:
+                    log.warning(
+                        "run_agent_turn.prompt_version_persist_failed",
+                        job_id=job_id,
+                        agent_id=agent_id,
+                        conversation_id=str(local_conversation_id),
+                        error=str(canary_exc),
+                    )
+
             # --------------------------------------------------------------
             # Bridge async SDK into sync Celery worker.
             # asyncio.run() is the required pattern for Python 3.12 (see CLAUDE.md).
@@ -892,7 +1270,7 @@ def run_agent_turn(
                         db=db,
                         redis=_redis,
                     ),
-                    timeout=90,
+                    timeout=AGENT_TURN_TIMEOUT_S,
                 )
             )
             latency_ms = int((time.monotonic() - _turn_start_monotonic) * 1000)
```

#### 1.2.4 `app/worker/tasks/runtime/eval.py` (+656/-31) — the invocation itself: the line that was D1

```diff
diff --git a/apps/api/app/worker/tasks/runtime/eval.py b/apps/api/app/worker/tasks/runtime/eval.py
index 1267504..cfa3690 100644
--- a/apps/api/app/worker/tasks/runtime/eval.py
+++ b/apps/api/app/worker/tasks/runtime/eval.py
@@ -51,10 +51,54 @@ score and an exploratory score are different measurements.
 Every report carries (attempted, valid, scored): rows fetched, rows carrying a
 label, rows Ragas returned a real number for. A rate without its denominator
 must not be constructible from what this task returns.
+
+The agent is invoked (audit D1, plan P2)
+----------------------------------------
+Until this phase the task built every sample with
+
+    # For M6: use reference_answer as proxy agent_response to test the eval harness
+    "agent_response": row[3],       # row[3] IS reference_answer
+
+so Ragas scored each reference answer against the contexts that answer was
+written from. Faithfulness and AnswerRelevancy approached 1.0 BY CONSTRUCTION,
+the score was invariant to the agent's model, prompt, retrieval configuration
+and capability envelope, and every layer built on top — the configuration tuple,
+the deploy gate's eval half — was reasoning about a number that measured
+nothing. Three years of scaffolding on one line of scaffolding.
+
+Now each scenario's question is put to the customer agent, through the SAME
+constructor run_agent_turn uses (agent.build_agent_options — the seam, P1), and
+the agent's own response is what gets scored. Four properties, each of which is
+a way this could have gone wrong and been invisible:
+
+  * ALWAYS side_effects="recorded", never "live". The seam grants eleven tools
+    and six of them reach a real ProviderAdapter; one eval scenario in which the
+    agent decides to refund would execute a refund against the tenant's
+    provider. The parameter is mandatory precisely so it cannot be forgotten,
+    and tests/unit/test_eval_agent_invocation.py fails if this module ever asks
+    for "live".
+  * retrieved_contexts come from the AGENT'S OWN retrieve result, never from the
+    scenario's stored column. Scoring faithfulness against contexts the agent
+    never saw is D1 wearing a different hat, so the stored column is carried
+    under a name run_ragas_eval does not read (`stored_retrieved_contexts`)
+    rather than left where a future edit could reconnect it.
+  * A scenario whose agent call FAILS is EXCLUDED AND COUNTED, never scored 0.
+    Zero is not a low score, it is the absence of one.
+  * A run where too few scenarios answered reports 'unknown', never 'pass', at
+    the MIN_RESPONSE_RATE floor.
+
+And the mutating-skill attempts recorded mode captured travel out with the run.
+That the agent CHOSE to call issue_refund is capability-envelope adherence and
+one of the more valuable things an eval can observe; it is invisible unless it
+is carried out of the turn.
+
+EXPECT THE NUMBERS TO GET WORSE. Faithfulness falls from ~1.0 to whatever is
+true. That is the instrument starting to work, not a regression.
 """
 
 from __future__ import annotations
 
+import asyncio
 import uuid
 
 import psycopg2
@@ -65,7 +109,11 @@ from app.core.database import get_sync_db
 from app.core.security import fernet_decrypt
 from app.models.agent import Agent
 from app.services.eval_service import (
+    AGENT_INVOCATION_CONCURRENCY,
+    AGENT_INVOCATION_MAX_CALLS_PER_RUN,
+    AGENT_INVOCATION_MEASURED,
     DATASET_GOLDEN,
+    EVAL_RUN_IDEMPOTENCY_SLACK_S,
     EVAL_SCORING_REQUIRES_BRANCH,
     EXPLORATORY_SAMPLE_SIZE,
     VERIFIED_QA_PROMOTION_DECISION,
@@ -73,8 +121,11 @@ from app.services.eval_service import (
     dataset_composition,
     dataset_of,
     insert_eval_run,
+    invocation_provenance,
     run_ragas_eval,
+    summarise_agent_invocation,
     summarise_run_validity,
+    update_eval_run_config,
     update_eval_run_status,
     write_eval_results,
 )
@@ -116,6 +167,402 @@ def _mark_failed_on_production(run_id: str, conn_str: str, agent_id: str) -> Non
         )
 
 
+def _agent_turn_timeout_s() -> int:
+    """agent.py's per-turn wall-clock bound. ONE copy of the number, imported.
+
+    Lazy for the reason the block comment below gives for every other agent.py
+    import in this module, and a function rather than a module constant so the
+    laziness survives: a second literal here would be the audit's D3 defect
+    wearing new clothes, and this one would decide the idempotency window a
+    redelivered message is judged against.
+    """
+    from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S  # noqa: PLC0415
+
+    return AGENT_TURN_TIMEOUT_S
+
+
+# ---------------------------------------------------------------------------
+# Invoking the agent, per scenario (audit D1 / plan P2)
+# ---------------------------------------------------------------------------
+# WHY THE IMPORTS BELOW ARE LAZY. `agent.py` and `agent_tools.py` both import
+# `claude_agent_sdk` at module scope, and several test modules install a FAKE
+# `claude_agent_sdk` into `sys.modules` at import time. Pulling either into THIS
+# module's import graph would make `tests/unit/test_eval_task.py` — which has
+# nothing to do with the SDK — depend on pytest's collection order for whether it
+# gets the real package or a stand-in. `test_agent_options_seam.py` records that
+# exact failure ("a guard whose meaning depends on collection order is not a
+# guard"), and `eval_service.build_eval_run_config` already imports
+# `deployment_service` inside the function body for the same class of reason.
+#
+# They are imported BY NAME rather than through an accessor, because the static
+# half of tests/unit/test_eval_agent_invocation.py reads this module's AST to
+# prove every `build_agent_options(...)` call asks for recorded side effects, and
+# a computed callee has no name to read.
+
+
+class _EvalEventSink:
+    """The db/redis double `_run_sdk_turn` emits SSE events through.
+
+    `_run_sdk_turn` calls `emit(job_id, "agent.tool_call", …, db, redis)` for
+    every tool use it observes. On the chat path those rows are the durable
+    replay log a late-joining widget reads. On the eval path there is no widget,
+    no SSE subscriber and — this is the part that matters — NO `jobs` ROW: the
+    job_id is synthesised per scenario. Writing sixty scenarios' worth of
+    `job_events` into the CONTROL DB under ids that name no job would put eval
+    traffic into the same table the ops room and the SSE replay endpoint read,
+    which is the tenant-data pollution approach (b) was chosen to avoid, one
+    table over.
+
+    So the events are dropped, deliberately and visibly, rather than persisted
+    to a place nothing will ever read them from. `emit` is unchanged: it still
+    publishes and still commits, into this.
+
+    This is the SSE/persistence divergence the plan named as inherent to
+    approach (b) — "Persistence and SSE differ by design" — and it is confined
+    to this class so that the divergence has one location and a name.
+    """
+
+    def publish(self, channel: str, message: str) -> int:  # redis half
+        return 0
+
+    def add(self, obj) -> None:  # SQLAlchemy Session half
+        return None
+
+    def commit(self) -> None:
+        return None
+
+
+def _run_one_eval_turn(
+    *,
+    agent_id: str,
+    conn_str: str,
+    question: str,
+    prompt_version_id: str | None,
+) -> dict:
+    """Put one scenario question to the customer agent. Returns `_run_sdk_turn`'s dict.
+
+    Same constructor as the chat path (`build_agent_options` — the seam, P1) and
+    same turn loop (`_run_sdk_turn`), so what is measured is what is served. What
+    differs is stated here rather than discovered later:
+
+      * `side_effects="recorded"` — ALWAYS, never "live". Six of the eleven tools
+        the seam grants reach a real ProviderAdapter, and this loop runs nightly,
+        unattended, against a real tenant.
+      * `verified_session_token=""` — an eval scenario is an UNVERIFIED customer.
+        Every identity-gated skill therefore refuses, which is the correct
+        posture for a question that arrived with no IDV session, and it is the
+        posture a mined production scenario carries no evidence against.
+      * `resume=None` and a fresh conversation id per scenario — scenarios are
+        independent by construction; a shared session would let scenario 12's
+        answer be shaped by scenario 11.
+      * No `conversations` row is created. Nothing writes one because recorded
+        mode suppresses every tenant write the tools would make, and creating one
+        would put eval traffic into the table `mine_production_scenarios` reads —
+        the eval would begin generating its own future test set from its own
+        output, which is the reason approach (a) was rejected.
+
+    The canary is deliberately NOT re-rolled. `prompt_version_id` is the
+    PRODUCTION label already resolved by `build_eval_run_config` for this run's
+    attribution, and the same helper the chat path uses re-fetches that exact
+    version's soul fields by id. Passing None instead would serve the agent's
+    live `soul_*` columns while `eval_runs.prompt_version_id` still named the
+    production version — a score attributed to a prompt that never produced it,
+    which is BACKLOG 2.3's defect exactly.
+    """
+    from app.worker.tasks.runtime.agent import (  # noqa: PLC0415
+        AGENT_TURN_TIMEOUT_S,
+        _resolve_turn_prompt_version,
+        _run_sdk_turn,
+        build_agent_options,
+    )
+
+    conversation_id = str(uuid.uuid4())
+    job_id = str(uuid.uuid4())
+
+    # The control-DB session is held only for as long as the options need it.
+    # build_agent_options reads every field it wants off the agent row before it
+    # returns, so the SDK turn — up to AGENT_TURN_TIMEOUT_S of it, sixty times a
+    # night — runs with no session open.
+    with get_sync_db() as db:
+        agent = db.get(Agent, agent_id)
+        if agent is None:
+            raise RuntimeError("agent row disappeared mid-run")
+
+        soul_override: dict | None = None
+        if prompt_version_id:
+            _pv_id, soul_override, _needs_persist = _resolve_turn_prompt_version(
+                db,
+                agent_id=agent_id,
+                local_conversation_id=conversation_id,
+                existing_prompt_version_id=prompt_version_id,
+            )
+
+        options = build_agent_options(
+            agent=agent,
+            conn_str=conn_str,
+            conversation_id=conversation_id,
+            job_id=job_id,
+            side_effects="recorded",
+            verified_session_token="",
+            soul_override=soul_override,
+            resume=None,
+        )
+
+    sink = _EvalEventSink()
+    return asyncio.run(
+        asyncio.wait_for(
+            _run_sdk_turn(
+                message=question,
+                options=options,
+                job_id=job_id,
+                local_conversation_id=conversation_id,
+                conn_str=conn_str,
+                db=sink,
+                redis=sink,
+            ),
+            timeout=AGENT_TURN_TIMEOUT_S,
+        )
+    )
+
+
+def _invoke_agent_for_scenarios(
+    *,
+    agent_id: str,
+    conn_str: str,
+    scenarios: list[dict],
+    prompt_version_id: str | None,
+) -> tuple[list[dict], dict]:
+    """Drive the agent over the run's scenarios. Returns (scored_rows, observation).
+
+    `scored_rows` is the subset that produced a response, each carrying the
+    agent's own `agent_response` and the contexts the AGENT retrieved. Those are
+    the only rows handed to Ragas. A row that is not in this list was not scored
+    — not scored 0, not scored against its reference answer, not scored at all —
+    and the observation says how many there were and why.
+
+    Args:
+        scenarios: the VALID rows of the run (those carrying a label), golden
+            first. Order is load-bearing: the per-run ceiling takes a prefix, so
+            golden-first means the fixed set is invoked before the rotating one.
+        prompt_version_id: the production prompt version this run is attributed
+            to, or None.
+
+    Returns:
+        (scored_rows, summarise_agent_invocation(...)).
+
+    No SCENARIO can raise out of here. An invocation phase where every turn fails
+    yields zero scored rows and an observation saying so, and the run still
+    completes and still records its provenance — which is what lets the deploy
+    gate refuse it for a stated reason instead of blocking on an absence.
+
+    The one exception is the concurrency guard below, and it is deliberate: that
+    is a programming error in this file, not a runtime condition, and it fires
+    before any turn has cost anything.
+    """
+    from app.services.agent_tools import (  # noqa: PLC0415
+        CHUNK_CONTENT_CHAR_LIMIT,
+        get_recorded_side_effects,
+        reset_side_effect_context,
+    )
+    from app.worker.tasks.runtime.agent import (  # noqa: PLC0415
+        AGENT_TURN_TIMEOUT_S,
+        RETRIEVE_CHUNKS_KEY,
+        RETRIEVE_CHUNKS_SOURCE_KEY,
+        RETRIEVE_CHUNKS_UNPARSED,
+        RETRIEVE_RESULT_CAPTURE_CHARS,
+    )
+
+    # The provenance says concurrency=1 and the loop below is sequential. Rather
+    # than let those two drift into disagreement — a run whose record claims a
+    # bound it did not run under is this phase's whole subject — raise. 4 GB of
+    # RAM and one Agent SDK subprocess per turn is why the number is 1.
+    if AGENT_INVOCATION_CONCURRENCY != 1:
+        raise RuntimeError(
+            "AGENT_INVOCATION_CONCURRENCY is "
+            f"{AGENT_INVOCATION_CONCURRENCY}, but this loop invokes scenarios "
+            "one at a time. Change the loop in the same edit, or the run's "
+            "provenance describes a bound nothing enforced."
+        )
+
+    invocable = scenarios[:AGENT_INVOCATION_MAX_CALLS_PER_RUN]
+    skipped = scenarios[AGENT_INVOCATION_MAX_CALLS_PER_RUN:]
+    skipped_golden = sum(
+        1 for s in skipped if dataset_of(s.get("dataset")) == DATASET_GOLDEN
+    )
+    if skipped:
+        log.warning(
+            "run_eval_suite.invocation_ceiling_reached",
+            agent_id=agent_id,
+            invoked=len(invocable),
+            skipped=len(skipped),
+            skipped_golden=skipped_golden,
+            ceiling=AGENT_INVOCATION_MAX_CALLS_PER_RUN,
+            detail=(
+                "golden rows beyond the ceiling were not invoked — the paired "
+                "per-item delta does not cover them this run"
+                if skipped_golden
+                else "exploratory rows beyond the ceiling were not invoked"
+            ),
+        )
+
+    records: list[dict] = []
+    scored_rows: list[dict] = []
+    try:
+        for scenario in invocable:
+            # THE SINK IS EMPTIED BEFORE THE TURN, NOT ONLY INSIDE IT.
+            # build_agent_options resets it on entry, but everything
+            # _run_one_eval_turn does BEFORE reaching the seam can raise:
+            # get_sync_db(), the agent row lookup (which raises when the row is
+            # gone), _resolve_turn_prompt_version. The unconditional read below
+            # then returned the PREVIOUS scenario's sink, so a scenario 5 that
+            # attempted a refund and a scenario 6 whose control-DB session
+            # blipped produced two transactional.adapter entries, the second
+            # carrying scenario_id 's6' for an attempt s6 never made — a
+            # fabricated observation in the exact confusion-matrix cell the
+            # recording exists to populate.
+            reset_side_effect_context()
+            record: dict = {
+                "scenario_id": str(scenario.get("id", "")),
+                "responded": False,
+                "scorable": False,
+                "error": None,
+                "retrieve_calls": 0,
+                "retrieve_at_cap": False,
+                "retrieve_unparsed": 0,
+                "retrieved_chunks": 0,
+                "side_effects": [],
+            }
+            turn: dict | None = None
+            try:
+                turn = _run_one_eval_turn(
+                    agent_id=agent_id,
+                    conn_str=conn_str,
+                    question=scenario.get("question", ""),
+                    prompt_version_id=prompt_version_id,
+                )
+            except Exception as exc:
+                # EXCLUDED AND COUNTED, never scored 0 — the lesson
+                # tests/evals/calibration/compute_correlation.py:485 learned
+                # about a judge that errors, applied one layer earlier. A zero
+                # here would move every metric with the failure rate of the
+                # Agent SDK rather than with the agent's behaviour, and it would
+                # do it in the direction that looks like a quality regression.
+                record["error"] = type(exc).__name__
+                log.warning(
+                    "run_eval_suite.scenario_invocation_failed",
+                    agent_id=agent_id,
+                    scenario_id=record["scenario_id"],
+                    error_type=type(exc).__name__,
+                    error=str(exc),
+                )
+
+            # Read on BOTH paths and before the next turn resets the sink: a
+            # scenario that drove the agent to attempt a refund and then timed
+            # out still observed the attempt, and the attempt is the eval signal.
+            record["side_effects"] = get_recorded_side_effects()
+
+            if turn is not None:
+                # ONE STRING PER CHUNK, not one repr per tool call. `result` is
+                # the audit capture — a Python repr of the SDK content block, cut
+                # at RETRIEVE_RESULT_CAPTURE_CHARS, which is below one full
+                # retrieval — and scoring it made the capture format the dominant
+                # term in Faithfulness and collapsed ContextPrecision's ranking
+                # to a single element. agent.py decodes the framed payload back
+                # into the chunks the agent was shown; those are what is scored.
+                contexts: list[str] = []
+                for tc in turn.get("tool_calls_log", []):
+                    if tc.get("tool_name") != "retrieve" or "result" not in tc:
+                        continue
+                    record["retrieve_calls"] += 1
+                    if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
+                        record["retrieve_unparsed"] += 1
+                    chunks = [str(c) for c in (tc.get(RETRIEVE_CHUNKS_KEY) or []) if c]
+                    if any(len(c) >= CHUNK_CONTENT_CHAR_LIMIT for c in chunks):
+                        record["retrieve_at_cap"] = True
+                    contexts.extend(chunks)
+                record["retrieved_chunks"] = len(contexts)
+
+                response_text = str(turn.get("response_text") or "")
+                if response_text.strip():
+                    record["responded"] = True
+                # EXCLUDED AND COUNTED, one metric over. A responded turn with no
+                # retrieved context scores Faithfulness / ContextPrecision /
+                # ContextRecall over an empty list, which is structurally 0 or
+                # NaN — and a 0 for a question the agent answered correctly from
+                # its system prompt ("what are your opening hours?") is the same
+                # "zero is not a low score" error the failure path already
+                # refuses. summarise_agent_invocation reports these as
+                # `no_retrieval` / `retrieved_nothing_scorable`; they are not
+                # failures and do not depress `response_rate`.
+                if record["responded"] and contexts:
+                    record["scorable"] = True
+                    scored_rows.append(
+                        {
+                            **scenario,
+                            # THE LINE THAT WAS D1. It used to be row[3], the
+                            # reference answer, making the label the prediction.
+                            "agent_response": response_text,
+                            # THE OTHER HALF OF D1. The contexts the AGENT
+                            # retrieved during this turn, never the scenario's
+                            # stored column — scoring faithfulness against
+                            # contexts the agent never saw measures the corpus
+                            # the scenario was written from, not the retrieval
+                            # the customer gets. NO FALLBACK: `contexts or
+                            # scenario["stored_retrieved_contexts"]` is one token
+                            # of D1 restored, and it fires precisely in the case
+                            # no dynamic test covers.
+                            "retrieved_contexts": contexts,
+                        }
+                    )
+
+            records.append(record)
+    finally:
+        # The mode is process-context sticky and the Celery prefork pool does not
+        # isolate contextvars per task. Leaving "recorded" in force would mean the
+        # next thing to run in this context stops refunding real customers with no
+        # error anywhere — a failure a customer finds, not us. build_agent_options
+        # resets on entry too; this closes the window between the two.
+        reset_side_effect_context()
+
+    summary = summarise_agent_invocation(
+        records,
+        valid=len(scenarios),
+        ceiling_skipped=len(skipped),
+        ceiling_skipped_golden=skipped_golden,
+        per_turn_timeout_s=AGENT_TURN_TIMEOUT_S,
+        # Two caps, and only the second bounds the evidence the judge saw. The
+        # first bounds `tool_calls_log[*]["result"]`, the audit copy, which five
+        # 2000-char chunks exceed by construction — reporting it as THE context
+        # cap made `retrieved_context_at_cap` ~100% on every retrieving turn and
+        # therefore a constant dressed as an observation.
+        audit_capture_char_cap=RETRIEVE_RESULT_CAPTURE_CHARS,
+        retrieved_context_chunk_char_cap=CHUNK_CONTENT_CHAR_LIMIT,
+        # The served path deflects a response that trips the PII firewall
+        # (agent.py's scan_response) before a customer sees it. The eval does
+        # NOT, and the reason is that the deflection is not an answer: scoring it
+        # would measure the firewall's hit rate as if it were the agent's
+        # grounding. Recorded rather than left implicit, because it is a real
+        # difference between the text scored here and the text a customer reads.
+        pii_firewall_applied=False,
+    )
+    log.info(
+        "run_eval_suite.invocation_complete",
+        agent_id=agent_id,
+        status=summary["status"],
+        attempted=summary["attempted"],
+        responded=summary["responded"],
+        scorable=summary["scorable"],
+        failed=summary["failed"],
+        empty=summary["empty"],
+        no_retrieval=summary["no_retrieval"],
+        retrieved_context_unparsed=summary["retrieved_context_unparsed"],
+        response_rate=summary["response_rate"],
+        coverage_rate=summary["coverage_rate"],
+        ceiling_skipped=summary["ceiling_skipped"],
+    )
+    return scored_rows, summary
+
+
 # ---------------------------------------------------------------------------
 # EVL-04: run_eval_suite_beat — beat dispatcher (D-19 LOCKED)
 # Task name must match beat_schedule entry in celery_app.py exactly.
@@ -190,8 +637,10 @@ def run_eval_suite(self, agent_id: str) -> dict:
         5. Create the Neon branch. Readiness is probed only if scoring is going
            to connect to it, and a branch that cannot be created is fatal only
            then — see EVAL_SCORING_REQUIRES_BRANCH and the block comment below.
-        6. try: run Ragas eval (no database) → write results to PRODUCTION →
-                mark complete on PRODUCTION.
+        6. try: INVOKE THE AGENT once per valid scenario (recorded side effects)
+                → patch the observation onto the run's config on PRODUCTION
+                → run Ragas eval over the rows that answered (no database)
+                → write results to PRODUCTION → mark complete on PRODUCTION.
            except: mark failed on PRODUCTION.
            finally: delete the Neon branch if one was created (D-10 — always
                 runs, even on exception).
@@ -208,7 +657,8 @@ def run_eval_suite(self, agent_id: str) -> dict:
         {"run_id", "scenario_count", "attempted", "valid", "scored", "datasets",
          "dataset_column_available", "golden_set_present", "promoted",
          "config_recorded", "promotion_disabled_reason",
-         "branch_isolation"}                                     on success.
+         "branch_isolation", "agent_invoked", "agent_invocation",
+         "invocation_recorded"}                                  on success.
         {"status": "already_running"}                            on idempotent skip.
         {"status": "no_scenarios", "run_id", "run_recorded", "attempted",
          "valid", "scored", "dataset_column_available"}          when nothing was
@@ -240,7 +690,20 @@ def run_eval_suite(self, agent_id: str) -> dict:
         conn_str = fernet_decrypt(agent.neon_connection_string)
         neon_project_id = agent.neon_project_id
 
-    # Check eval_runs table on tenant DB for a recent running run
+    # Check eval_runs table on tenant DB for a recent running run.
+    #
+    # THE WINDOW HAS TO COVER A RUN THAT CONSUMES ITS OWN CEILING. It was a flat
+    # 10 minutes, written when a run was seconds of arithmetic. P2 made the worst
+    # case AGENT_INVOCATION_MAX_CALLS_PER_RUN x AGENT_TURN_TIMEOUT_S — 90 minutes
+    # — so a 10-minute window let a redelivered or re-dispatched message start a
+    # SECOND concurrent invocation of the same agent while the first was still
+    # running: two live agents, two sets of turns, two eval_runs rows. Derived
+    # from the same two constants the run stamps on itself rather than guessed
+    # beside them.
+    idempotency_window_s = (
+        AGENT_INVOCATION_MAX_CALLS_PER_RUN * _agent_turn_timeout_s()
+        + EVAL_RUN_IDEMPOTENCY_SLACK_S
+    )
     try:
         _check_conn = psycopg2.connect(conn_str, connect_timeout=5)
         try:
@@ -250,17 +713,21 @@ def run_eval_suite(self, agent_id: str) -> dict:
                     SELECT id FROM eval_runs
                     WHERE kind = %s
                       AND status = 'running'
-                      AND started_at > NOW() - INTERVAL '10 minutes'
+                      AND started_at > NOW() - (%s * INTERVAL '1 second')
                     LIMIT 1
                     """,
-                    (f"m6:{agent_id}",),
+                    (f"m6:{agent_id}", idempotency_window_s),
                 )
                 _existing = _cur.fetchone()
         finally:
             _check_conn.close()
 
         if _existing:
-            log.info("run_eval_suite.idempotent_skip", agent_id=agent_id)
+            log.info(
+                "run_eval_suite.idempotent_skip",
+                agent_id=agent_id,
+                window_s=idempotency_window_s,
+            )
             return {"status": "already_running"}
     except Exception as exc:
         # If we cannot check, proceed — idempotency guard is best-effort
@@ -367,12 +834,29 @@ def run_eval_suite(self, agent_id: str) -> dict:
             "source": row[1],
             "question": row[2],
             "reference_answer": row[3],
-            "retrieved_contexts": row[4] if isinstance(row[4], list) else [],
+            # NOT `retrieved_contexts`, AND THE NAME IS THE GUARD. run_ragas_eval
+            # reads `retrieved_contexts` off each sample; this column holds the
+            # chunks the SCENARIO was written from, which for a source='generated'
+            # row are the exact chunks Haiku was told to answer from
+            # (scenario_service.py:118). Scoring the agent's answer against them
+            # measures the corpus the question came out of rather than the
+            # retrieval the customer gets, and scoring the REFERENCE answer
+            # against them was D1 itself. The key is carried under a name the
+            # scorer does not read so that reconnecting the two is an edit
+            # somebody has to make on purpose.
+            "stored_retrieved_contexts": row[4] if isinstance(row[4], list) else [],
             # NULL (never designated) resolves to exploratory — membership of
             # the golden set is asserted, never inherited.
             "dataset": dataset_of(row[5] if len(row) > 5 else None),
-            # For M6: use reference_answer as proxy agent_response to test the eval harness
-            "agent_response": row[3],
+            # NO `agent_response` KEY. This is where D1 lived:
+            #     # For M6: use reference_answer as proxy agent_response …
+            #     "agent_response": row[3],   # row[3] IS reference_answer
+            # It is set by _invoke_agent_for_scenarios, from the agent's own
+            # turn, and ONLY on rows that produced one. A row that never reached
+            # the agent has no response key at all rather than a plausible
+            # placeholder, so the failure mode is a missing row in the scored
+            # set — visible in (attempted, valid, scored) — instead of a number
+            # that looks like a measurement.
         }
         for row in rows
     ]
@@ -505,6 +989,19 @@ def run_eval_suite(self, agent_id: str) -> dict:
     # above. So the branch is isolation held IN RESERVE, and this block says
     # which of the two it is instead of asserting the one that is false.
     #
+    # P2 DID NOT CHANGE THAT, and the reason is worth stating because it is the
+    # obvious place to be wrong. The agent turns below run against the tenant's
+    # PRODUCTION connection string, not the branch, and they must: `retrieve`
+    # has to see the corpus the customer is served, and a branch is a copy taken
+    # at run start. What stops those turns writing is RECORDED MODE (BACKLOG
+    # 2.5) — the retrieval_metrics row, the escalation marker and mail, and the
+    # six mutating skills' ProviderAdapter calls are all suppressed and recorded
+    # at the tool layer. Two independent mechanisms for two different jobs:
+    # the branch would isolate a WRITE, recorded mode prevents one. Pointing the
+    # agent at the branch instead would swap a real measurement for a measurement
+    # against a snapshot, and would still not stop the ProviderAdapter, which is
+    # outside the database entirely.
+    #
     #   * It is still created and still deleted in `finally`, so the guarantee
     #     is already in place the day scoring starts issuing statements.
     #   * A branch that cannot be created or readied no longer abandons the
@@ -527,6 +1024,9 @@ def run_eval_suite(self, agent_id: str) -> dict:
     # ------------------------------------------------------------------
     branch_id_for_finally: str | None = None
     branch_isolation = "provisioned_unused"
+    # Set the moment the first SDK turn could have run. A retry after that point
+    # re-invokes the whole set — see the `except` below.
+    agent_was_invoked = False
     try:
         try:
             branch_id_for_finally, branch_conn_str = create_branch(
@@ -556,14 +1056,96 @@ def run_eval_suite(self, agent_id: str) -> dict:
         # fetched (attempted) or the rows Ragas came back with (scored).
         valid_scenarios = [s for s in scenarios if s.get("reference_answer")]
 
-        # No connection string is passed: scoring opens nothing (audit D1 —
-        # each sample's "response" is its own reference answer).
-        results = run_ragas_eval(valid_scenarios)
-
-        # Observations about the run land on PRODUCTION, which is the whole
-        # point of the split: the branch below is about to be destroyed.
-        write_eval_results(run_id, results["scores"], conn_str)
-        update_eval_run_status(run_id, "complete", finished_at=True, conn_str=conn_str)
+        # ------------------------------------------------------------------
+        # THE AGENT RUNS (audit D1). One turn per valid scenario, through the
+        # same seam run_agent_turn uses, with side_effects="recorded" so a
+        # scenario in which the agent decides to refund records the attempt
+        # instead of moving money. Rows that produced no response are excluded
+        # here and counted in `invocation` — never scored 0, and never scored
+        # against their own reference answer, which is the defect being closed.
+        # ------------------------------------------------------------------
+        scored_scenarios, invocation = _invoke_agent_for_scenarios(
+            agent_id=agent_id,
+            conn_str=conn_str,
+            scenarios=valid_scenarios,
+            prompt_version_id=attribution["prompt_version_id"],
+        )
+        # From here on a retry would re-run every turn above. See the `except`.
+        agent_was_invoked = True
+
+        # WRITTEN BEFORE SCORING, DELIBERATELY. The invocation is the expensive,
+        # unrepeatable half of the run; scoring can fail on a judge outage and be
+        # retried. Patching the observation in first means a run that dies in
+        # Ragas still carries what its agent actually did, and a run that dies
+        # BEFORE this point keeps the agent_invoked=false it was inserted with —
+        # so the deploy gate refuses it rather than inheriting a hopeful default.
+        provenance = invocation_provenance(invocation)
+        invocation_recorded = update_eval_run_config(run_id, provenance, conn_str)
+
+        # ------------------------------------------------------------------
+        # A RUN THAT DID NOT MEASURE THE AGENT WRITES NO SCORES.
+        #
+        # `agent_invocation.status` was 'unknown' for a below-floor run and the
+        # run scored anyway: 2 surviving rows out of 40 produced 2x4 eval_results
+        # rows, update_eval_run_status marked it 'complete', and
+        # deployment_service._fetch_eval_summary_sync built a non-empty
+        # pass_rates from them and returned EVAL_SIGNAL_MEASURED. The 'unknown'
+        # lived in a config key that nothing outside this module reads, so
+        # everything a consumer actually reads reported a pass over two
+        # observations. Before P2 that state was unreachable — every fetched row
+        # was always 'scored'.
+        #
+        # The deploy gate learning to read `agent_invoked` is P3. Until it does,
+        # the refusal has to be here, where the observation is: no eval_results
+        # rows means _fetch_eval_summary_sync finds an empty pass_rates and
+        # returns EVAL_SIGNAL_NO_VALID_SCORES, which apply_signal_evidence_gate
+        # already refuses. Fail-closed with the machinery that exists rather than
+        # a window in which the plan's "reports unknown, never pass" is true of
+        # one key and false of the run.
+        #
+        # The run still ends terminally and still carries its whole invocation
+        # observation, so "this run measured too little" stays readable — it is
+        # the SCORES that are withheld, not the record.
+        # ------------------------------------------------------------------
+        # Annotated, because the two branches below assign different literal
+        # types and the join would otherwise be dict[str, object] — which makes
+        # `results["scores"]` an `object` that write_eval_results and
+        # summarise_run_validity both reject.
+        results: dict
+        if invocation["status"] != AGENT_INVOCATION_MEASURED:
+            log.warning(
+                "run_eval_suite.below_measurement_floor",
+                agent_id=agent_id,
+                run_id=run_id,
+                invocation_status=invocation["status"],
+                attempted=invocation["attempted"],
+                responded=invocation["responded"],
+                scorable=invocation["scorable"],
+                response_rate=invocation["response_rate"],
+                min_response_rate=invocation["min_response_rate"],
+                min_scored_observations=invocation["min_scored_observations"],
+                detail=(
+                    "no eval_results written and no judge call billed — a run "
+                    "below the floor is not a measurement, and writing its "
+                    "scores would make the deploy gate read it as one"
+                ),
+            )
+            update_eval_run_status(
+                run_id, "complete", finished_at=True, conn_str=conn_str
+            )
+            results = {"scores": [], "means": {}, "sent": 0, "returned": 0,
+                       "unattributed": 0}
+        else:
+            # No connection string is passed: scoring opens nothing. It scores
+            # the AGENT'S responses against the contexts the AGENT retrieved.
+            results = run_ragas_eval(scored_scenarios)
+
+            # Observations about the run land on PRODUCTION, which is the whole
+            # point of the split: the branch below is about to be destroyed.
+            write_eval_results(run_id, results["scores"], conn_str)
+            update_eval_run_status(
+                run_id, "complete", finished_at=True, conn_str=conn_str
+            )
 
         # (attempted, valid, scored) for the run and for each dataset. Computed
         # over the FETCHED set, not the valid one, so the two counts stay
@@ -587,6 +1169,11 @@ def run_eval_suite(self, agent_id: str) -> dict:
             promoted=0,
             promotion_enabled=VERIFIED_QA_PROMOTION_DECISION["enabled"],
             branch_isolation=branch_isolation,
+            agent_invoked=provenance["agent_invoked"],
+            invocation_status=invocation["status"],
+            invocation_responded=invocation["responded"],
+            invocation_attempted=invocation["attempted"],
+            invocation_recorded=invocation_recorded,
         )
         return {
             "run_id": run_id,
@@ -619,6 +1206,17 @@ def run_eval_suite(self, agent_id: str) -> dict:
             # against it; 'unavailable' — Neon could not give us one and the
             # run scored anyway. Never absent, so the state is always readable.
             "branch_isolation": branch_isolation,
+            # --- audit D1: did this run measure the agent? ------------------
+            # The gate-facing conjunction (the agent produced the scored
+            # responses AND enough rows answered to be a measurement), and
+            # beside it the observation it was derived from, so "invoked but
+            # below the floor" and "never invoked" stay different claims.
+            "agent_invoked": provenance["agent_invoked"],
+            "agent_invocation": invocation,
+            # False means the run's config could not be patched — the row still
+            # reads agent_invoked=false and the deploy gate will refuse it. A
+            # measurement lost, in the fail-closed direction.
+            "invocation_recorded": invocation_recorded,
         }
 
     except Exception as exc:
@@ -627,8 +1225,30 @@ def run_eval_suite(self, agent_id: str) -> dict:
             agent_id=agent_id,
             run_id=run_id,
             error=str(exc),
+            agent_was_invoked=agent_was_invoked,
         )
         _mark_failed_on_production(run_id, conn_str, agent_id)
+        # A RETRY AFTER THE INVOCATION RE-BUYS THE INVOCATION. `max_retries=2`
+        # meant a judge outage in run_ragas_eval re-entered this task body, drew
+        # a fresh run_id and put all sixty scenarios to the agent again — one
+        # nightly dispatch costing three times the ceiling the run stamps on
+        # itself, and no field on the run expressing that. Losing one night's
+        # scores to a judge outage is the cheaper failure by two orders of
+        # magnitude, and tonight's beat repeats tomorrow. Retries before the
+        # first turn (an insert failure, a branch failure) are unaffected and
+        # still cost nothing.
+        if agent_was_invoked:
+            log.error(
+                "run_eval_suite.not_retrying_after_invocation",
+                agent_id=agent_id,
+                run_id=run_id,
+                detail=(
+                    "the agent was already invoked for this run; retrying would "
+                    "re-run every SDK turn. The run is recorded failed and the "
+                    "next nightly dispatch is the retry."
+                ),
+            )
+            return {}
         if self.request.retries >= self.max_retries:
             return {}
         raise self.retry(exc=exc, countdown=2 ** self.request.retries)
```

#### 1.2.5 `app/services/deployment_service.py` (+441/-31) — the fifth and sixth signal states, the gate, the stored-run re-read

```diff
diff --git a/apps/api/app/services/deployment_service.py b/apps/api/app/services/deployment_service.py
index 19adf2f..f6b56bf 100644
--- a/apps/api/app/services/deployment_service.py
+++ b/apps/api/app/services/deployment_service.py
@@ -55,6 +55,40 @@ EVAL_SIGNAL_MEASURED = "measured"
 EVAL_SIGNAL_NO_RUNS = "no_runs"
 EVAL_SIGNAL_NO_VALID_SCORES = "no_valid_scores"
 EVAL_SIGNAL_UNAVAILABLE = "unavailable"
+# Audit D1 (P3). The fifth state, and the only one whose scores EXIST and are
+# still not evidence. eval.py:374-375 read
+#     "agent_response": row[3],   # row[3] IS reference_answer
+# so Ragas scored the reference answer against the contexts that reference
+# answer was written from. Faithfulness and AnswerRelevancy approach 1.0 BY
+# CONSTRUCTION, the agent is never invoked, and the resulting run arrives at
+# this collector as a full set of high pass_rates over thirty scenarios. Every
+# other absent-signal state is absent by having nothing; this one is absent by
+# having a number that is about the label rather than about the agent.
+#
+# So the score is suppressed, exactly as it is in the other three states.
+# Letting a tautology's 0.99 travel while the recommendation blocks would
+# reproduce BACKLOG 5.4 one layer down: the orchestrator narrates the number it
+# was given, the owner reads "excellent answer quality" above a refusal, and
+# the prose is the part they believe.
+EVAL_SIGNAL_AGENT_NOT_INVOKED = "agent_not_invoked"
+
+# The sixth state (P3 review). A run whose own terminal status is 'failed' is
+# not a completed measurement, however many scores survived it — and after P2
+# "failed WITH a full set of scores and agent_invoked=true" is an ORDINARY
+# outcome rather than an exotic one. `run_eval_suite` patches the invocation
+# claim in BEFORE scoring (eval.py:1082-1083, deliberately: the invocation is
+# the expensive, unrepeatable half) and marks the run 'complete' at
+# eval.py:1146 — but `summarise_run_validity` runs AFTER that write, at :1155,
+# and anything raising from there to the end of the body drops into the except
+# at :1222, whose `_mark_failed_on_production` writes status='failed' over a
+# row that already carries True and a full set of eval_results.
+#
+# The collector read that as EVAL_SIGNAL_MEASURED and the gate shipped on it:
+# `last_run_status` has travelled on the payload since P1 and nothing anywhere
+# gated on it. Same family as every other state here — a run that did not
+# reach the end of its own body has no admissible account of what it covered,
+# so its numbers are withheld like the rest.
+EVAL_SIGNAL_RUN_FAILED = "run_failed"
 
 RED_TEAM_SIGNAL_MEASURED = "measured"
 # An agent that has never been security-tested. This state is the whole reason
@@ -71,6 +105,16 @@ RED_TEAM_SIGNAL_UNAVAILABLE = "unavailable"
 # The only state either signal may be in for `ship` to survive the gate.
 SHIPPABLE_SIGNAL = "measured"
 
+# The one `eval_runs.status` that means "this run reached the end of its own
+# body". eval_service.update_eval_run_status writes exactly two terminal values
+# ('complete' at eval.py:1133/1146, 'failed' from _mark_failed_on_production),
+# and the collector's selector already excludes 'running'. Written as an
+# allow-list of one rather than a deny-list containing 'failed': a status this
+# code has not heard of must fail closed, which is the same reasoning the
+# selector's `status <> 'running'` uses in the other direction (unknown is
+# still terminal, so it must not shadow a good run).
+EVAL_RUN_STATUS_COMPLETE = "complete"
+
 
 # ---------------------------------------------------------------------------
 # Pydantic models
@@ -113,12 +157,23 @@ Blocking conditions (always use recommendation='block'):
 - red_team_summary.deployment_blocked == True
 - DEP_BLOCK_ON_HIGH_RED_TEAM is True and red_team_summary.high_count > 0
 - Any eval metric pass_rate < 0.70
-- eval_summary.eval_signal is anything other than 'measured'. The four states
+- eval_summary.eval_signal is anything other than 'measured'. The six states
   are 'measured', 'no_runs' (never evaluated), 'no_valid_scores' (a run that
-  produced no valid score for any metric) and 'unavailable' (the signal could
-  not be read). Only 'measured' is evidence. An absent measurement is UNKNOWN
-  quality, never acceptable quality, and eval_summary.pass_rates is null — not
-  an empty object — in every one of the other three states.
+  produced no valid score for any metric), 'agent_not_invoked' (a run that
+  scored something OTHER than this agent's own answers), 'run_failed' (a run
+  whose own terminal status is not 'complete', whatever it managed to score on
+  the way) and 'unavailable' (the signal could not be read). Only 'measured' is
+  evidence. An absent measurement is UNKNOWN quality, never acceptable quality,
+  and eval_summary.pass_rates is null — not an empty object — in every one of
+  the other five states.
+- eval_summary.agent_invoked is anything other than true. Until this release
+  the eval scored each scenario's own reference answer instead of asking the
+  agent, so its metrics were near-perfect by construction and said nothing
+  about the deployed agent. A run that does not record having invoked the agent
+  gets no benefit of the doubt: false and absent are refused identically,
+  because every run stored before the fix is silent rather than false. Do not
+  describe such a run's quality at all — you have not been given its numbers,
+  and their absence here is deliberate.
 - red_team_summary.signal is anything other than 'measured'. The three states
   are 'measured', 'no_runs' (this agent has NEVER been security-tested) and
   'unavailable' (the signal could not be read). Zero open findings from zero
@@ -140,7 +195,8 @@ Warning conditions (recommendation='ship_with_warnings'):
   uncertainty; do not describe the result as full coverage.
 
 Ship condition (recommendation='ship'):
-- eval_summary.eval_signal == 'measured' AND all eval metrics >= 0.85
+- eval_summary.eval_signal == 'measured' AND eval_summary.agent_invoked is true
+  AND all eval metrics >= 0.85
 - deployment_blocked=False and high_count=0
 - verified_qa_stats.row_count >= 50
 
@@ -190,6 +246,15 @@ Call submit_report exactly once with your assessment.
 # model's SUMMARY does not contradict the recommendation the platform imposed;
 # the gate exists so the recommendation does not depend on the model at all.
 #
+# AND NOTHING HERE OBSERVES THE MODEL OBEYING ANY OF IT (P3 review). No test in
+# the repo executes run_orchestrator — BACKLOG 3.10 records `_run_orchestrator_loop`
+# reporting "was never awaited" — so the prompt tests are drift protection over a
+# string, never evidence that the narration is constrained. What actually
+# prevents the summary from praising a tautology's 0.99 is that _eval_summary
+# does not put pass_rates on the payload at all outside EVAL_SIGNAL_MEASURED:
+# the model cannot narrate a number it was not given. Read every "the prompt
+# says X" claim in this module as consistency, not as a control.
+#
 # P4 review: until then only the two signal-state conditions were enforced.
 # DEP_BLOCK_ON_HIGH_RED_TEAM occurred exactly twice in the codebase — its
 # definition in config.py and the sentence above — so a run that left four
@@ -291,6 +356,7 @@ def _eval_summary(
     scored_scenario_count: int = 0,
     denominator_source: str | None = None,
     pass_rates: dict | None = None,
+    agent_invoked: bool | None = None,
     detail: str | None = None,
 ) -> dict:
     """Build an eval signal payload in which absence is always distinguishable.
@@ -312,12 +378,26 @@ def _eval_summary(
     says which of the two possible origins the attempted count has, because an
     attempted count derived from eval_results is bounded below by the scored
     count and its equality with it means nothing.
+
+    `agent_invoked` DEFAULTS TO None, NOT False (audit D1, P3). False is the
+    claim "this run looked and the agent was not invoked"; None is "no run said
+    either way", which is what a state with no run at all — no_runs,
+    unavailable — actually has. Both are refused by apply_signal_evidence_gate,
+    which tests `is not True`, so the distinction costs nothing at the gate and
+    keeps the payload from asserting a measurement it never made. Same
+    discipline as `valid_scenario_count`.
     """
     measured = signal == EVAL_SIGNAL_MEASURED
     rates = pass_rates if measured else None
     return {
         "eval_signal": signal,
         "signal_detail": detail,
+        # The D1 provenance claim, read out of eval_runs.config where
+        # eval_service.invocation_provenance() writes it. True only when the
+        # run both invoked the agent and got enough answers back to constitute
+        # a measurement — it is a conjunction on the writing side, and this
+        # side must not try to reconstitute either half.
+        "agent_invoked": agent_invoked,
         "last_run_at": last_run_at,
         # A run that FAILED still has a started_at and now, since the P1
         # persistence split, still lands a terminal status on production. Its
@@ -366,6 +446,48 @@ def _attempted_from_run_config(config: object) -> tuple[int | None, int | None]:
     )
 
 
+def _agent_invoked_from_run_config(config: object) -> bool | None:
+    """Read the D1 provenance claim out of an eval_runs.config JSONB payload.
+
+    Returns True / False when the run recorded one, and None when it recorded
+    nothing readable. THE THREE ARE KEPT APART AND ONLY ONE OF THEM SHIPS:
+
+      True   — eval_service.invocation_provenance() observed that the agent was
+               invoked AND that enough scenarios answered to be a measurement.
+      False  — the same function looked and said no. A run below
+               MIN_RESPONSE_RATE, or one that died before its first turn.
+      None   — no claim exists. A run from before D1 (the whole of history), a
+               run on a tenant DB provisioned before alembic_tenant 0013 and so
+               having no `config` column at all, or a config whose value is not
+               a bool.
+
+    None IS NOT A MILDER FAILURE THAN False, and the caller must not treat it
+    as one. Every eval run persisted before this branch was produced by the
+    tautology at eval.py:374-375 and carries no such key, so a gate that
+    refused only False would keep shipping on all of it — the exact shape of
+    BACKLOG 3.1, where pre-P4 red-team runs still read signal='measured' with
+    clean findings because absence was read as assent. The accepted consequence
+    is that every pre-D1 run, and every pre-0013 tenant, fails closed until a
+    fresh eval runs on the current build.
+
+    A non-bool value is None rather than passed through. `bool("false")` is
+    True, and a string is the shape a hand-written or externally-patched config
+    would most plausibly arrive in; coercing it would turn the string "false"
+    into a shipping signal.
+    """
+    if not isinstance(config, dict):
+        return None
+    invoked = config.get("agent_invoked")
+    # `is True` / `is False` rather than isinstance: numpy bools and 0/1 ints
+    # are not this claim either, and the gate's fail-closed direction means
+    # anything unrecognised costs a blocked deploy rather than a shipped one.
+    if invoked is True:
+        return True
+    if invoked is False:
+        return False
+    return None
+
+
 def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
     """Fetch the most recent eval run summary from the tenant DB.
 
@@ -386,13 +508,28 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
     and apply_signal_evidence_gate() refuses to ship on it. Missing data is
     never passing data.
 
-    The four states this can report, all different claims:
+    The six states this can report, all different claims:
         measured         — a run exists, it produced at least one real score.
-        no_runs          — the eval_runs table is empty. Nothing has ever been
-                           measured for this agent.
+        no_runs          — no FINISHED eval run exists for this agent: either
+                           nothing has ever been measured, or the only run is
+                           still in flight. Both mean "there is no result to
+                           read", and both are remedied by waiting for or
+                           starting a run — the gate's day-1 path dispatches one
+                           and run_eval_suite's own idempotency guard, whose
+                           window now covers a full run, refuses the duplicate.
         no_valid_scores  — a run exists and every score is NULL. The judge
                            produced no valid observation; the run measured
                            nothing.
+        agent_not_invoked— a run exists and may well carry excellent scores,
+                           but it does not record having asked the agent
+                           anything (audit D1). The scores are about the
+                           dataset's own reference answers. Suppressed, for the
+                           same reason the other absent states suppress theirs.
+        run_failed       — the run's own terminal status is not 'complete'. It
+                           may carry a full set of scores and an invocation
+                           claim; it did not reach the end of its own body, so
+                           its account of what it covered is unreliable and its
+                           numbers are withheld like every other absent state's.
         unavailable      — the query could not be executed. We did not look.
 
     THE ATTEMPTED COUNT COMES FROM THE RUN, NOT FROM ITS RESULTS (P2 review).
@@ -406,8 +543,8 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
     when present and the eval_results-derived count is used only as a labelled
     floor. `denominator_source` says which of the two happened.
 
-    Returns dict with keys: eval_signal, signal_detail, last_run_at,
-    last_run_status, scenario_count, valid_scenario_count,
+    Returns dict with keys: eval_signal, signal_detail, agent_invoked,
+    last_run_at, last_run_status, scenario_count, valid_scenario_count,
     scored_scenario_count, denominator_source, pass_rates, failing_scenarios.
     """
     conn = psycopg2.connect(conn_str, connect_timeout=10)
@@ -426,11 +563,25 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
                 # eval_service.insert_eval_run's pre-0013 fallback. The narrow
                 # except matters — a broad one would hide a real read failure
                 # behind a payload that looks like a successful degraded read.
+                #
+                # AN IN-FLIGHT RUN MUST NOT SHADOW THE LAST FINISHED ONE. This
+                # took the newest row with no status filter, so for the whole
+                # duration of a run the gate read a 'running' row that has no
+                # eval_results yet, returned EVAL_SIGNAL_NO_VALID_SCORES and
+                # blocked the deploy with "this agent's answer quality has not
+                # been measured" — while a perfectly good completed run sat one
+                # row below. That window was minutes before P2 and is up to
+                # ninety per agent per night after it (the nightly beat fires at
+                # 02:00 UTC and invokes up to sixty live turns at 90 s each).
+                # `status <> 'running'` rather than an IN-list of terminal names:
+                # a status this query has not heard of is still terminal, and
+                # excluding it would resurrect the same shadowing.
                 run_config: object = None
                 try:
                     cur.execute(
                         "SELECT id, finished_at, status, config FROM eval_runs "
-                        "WHERE kind = %s ORDER BY started_at DESC LIMIT 1",
+                        "WHERE kind = %s AND status <> 'running' "
+                        "ORDER BY started_at DESC LIMIT 1",
                         (f"m6:{agent_id}",),
                     )
                     wide_row = cur.fetchone()
@@ -450,7 +601,8 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
                     )
                     cur.execute(
                         "SELECT id, finished_at, status FROM eval_runs "
-                        "WHERE kind = %s ORDER BY started_at DESC LIMIT 1",
+                        "WHERE kind = %s AND status <> 'running' "
+                        "ORDER BY started_at DESC LIMIT 1",
                         (f"m6:{agent_id}",),
                     )
                     run_row = cur.fetchone()
@@ -501,6 +653,8 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
                 # dropped. The run stamped what it covered into
                 # config["dataset"] before the judge was ever called; that is
                 # the only figure in the system that knows the difference.
+                agent_invoked = _agent_invoked_from_run_config(run_config)
+
                 config_attempted, config_valid = _attempted_from_run_config(run_config)
                 if config_attempted is not None:
                     attempted = config_attempted
@@ -515,6 +669,81 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
                     valid = None
                     denominator_source = DENOMINATOR_SOURCE_EVAL_RESULTS
 
+                # A RUN THAT DID NOT COMPLETE IS NOT A COMPLETED MEASUREMENT
+                # (P3 review), and this is checked ahead of everything below it
+                # because it is the coarsest admissibility question there is:
+                # a run that fell out of its own body part-way has no reliable
+                # account of what it covered, so neither its scores nor its
+                # config claims are worth interpreting. `last_run_status` has
+                # travelled on this payload since P1 and nothing gated on it.
+                #
+                # The reachable shape, after P2, is not exotic: the invocation
+                # claim is patched in BEFORE scoring, so a run that scored
+                # everything, wrote its eval_results, marked itself 'complete'
+                # and then raised in summarise_run_validity (eval.py:1155, one
+                # line after the status write) ends as status='failed' carrying
+                # agent_invoked=true and a full set of high pass_rates. That
+                # combination reached this collector as EVAL_SIGNAL_MEASURED
+                # and shipped.
+                if last_run_status != EVAL_RUN_STATUS_COMPLETE:
+                    log.warning(
+                        "deployment_service.eval_summary.run_did_not_complete",
+                        agent_id=agent_id,
+                        run_status=last_run_status,
+                        recorded_claim=agent_invoked,
+                        scored=scored,
+                    )
+                    return _eval_summary(
+                        EVAL_SIGNAL_RUN_FAILED,
+                        last_run_at=last_run_at,
+                        last_run_status=last_run_status,
+                        scenario_count=attempted,
+                        valid_scenario_count=valid,
+                        scored_scenario_count=scored,
+                        denominator_source=denominator_source,
+                        agent_invoked=agent_invoked,
+                        detail=(
+                            "the most recent eval run did not complete "
+                            f"(status {last_run_status!r})"
+                        ),
+                    )
+
+                # THE ROOT CAUSE IS REPORTED BEFORE THE SYMPTOM (audit D1, P3).
+                # This is checked ahead of `not pass_rates` because a run can be
+                # in both states at once and only one of them names what is
+                # wrong. A pre-D1 run has scores AND no invocation claim; a
+                # below-floor P2 run writes no eval_results AND records
+                # agent_invoked=false. Reporting the second as
+                # 'no_valid_scores' would send the owner after the judge when
+                # the judge was never the problem, and would leave the far
+                # larger population — every historical run, all of which DO have
+                # scores — with no state of its own at all.
+                if agent_invoked is not True:
+                    log.warning(
+                        "deployment_service.eval_summary.agent_not_invoked",
+                        agent_id=agent_id,
+                        run_status=last_run_status,
+                        recorded_claim=agent_invoked,
+                        scored=scored,
+                    )
+                    return _eval_summary(
+                        EVAL_SIGNAL_AGENT_NOT_INVOKED,
+                        last_run_at=last_run_at,
+                        last_run_status=last_run_status,
+                        scenario_count=attempted,
+                        valid_scenario_count=valid,
+                        scored_scenario_count=scored,
+                        denominator_source=denominator_source,
+                        agent_invoked=agent_invoked,
+                        detail=(
+                            "the most recent eval run recorded that the agent "
+                            "was not invoked"
+                            if agent_invoked is False
+                            else "the most recent eval run does not record "
+                            "whether the agent was invoked at all"
+                        ),
+                    )
+
                 if not pass_rates:
                     # The run exists and scored nothing — every score NULL, or
                     # no eval_results rows at all. Unknown, not clean.
@@ -537,6 +766,7 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
                         valid_scenario_count=valid,
                         scored_scenario_count=scored,
                         denominator_source=denominator_source,
+                        agent_invoked=agent_invoked,
                         detail=(
                             "the most recent eval run produced no valid score "
                             "for any metric"
@@ -551,6 +781,7 @@ def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
                     valid_scenario_count=valid,
                     scored_scenario_count=scored,
                     denominator_source=denominator_source,
+                    agent_invoked=agent_invoked,
                     pass_rates=pass_rates,
                 )
             except Exception as exc:
@@ -1115,6 +1346,10 @@ def _compute_envelope_hash_sync(agent_id: str) -> str:
 EVAL_SUMMARY_UNAVAILABLE_SIGNAL: dict = {
     "eval_signal": EVAL_SIGNAL_UNAVAILABLE,
     "signal_detail": "the eval signal collector raised",
+    # None, never False: the collector raised, so no run was asked whether it
+    # invoked the agent. The gate refuses None and False identically, so this
+    # costs nothing and avoids attributing a claim to a run nobody read.
+    "agent_invoked": None,
     "last_run_at": None,
     "last_run_status": None,
     "scenario_count": 0,
@@ -1135,6 +1370,84 @@ RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL: dict = _red_team_summary(
 )
 
 
+def _agent_not_invoked_warning(eval_summary: dict) -> DeploymentWarning:
+    """The owner-facing half of the D1 refusal (P3).
+
+    ONE warning_id for both routes into it — the collector's
+    EVAL_SIGNAL_AGENT_NOT_INVOKED and the gate's own `agent_invoked is not
+    True` — because the precedent this module sets is that a distinct
+    warning_id marks a distinct REMEDY, not a distinct cause (see the
+    eval_never_run / eval_signal_unavailable pair, split precisely because
+    "wait for the run we started" and "try again in a few minutes" are
+    different instructions). Here the remedy is identical in every case: a
+    fresh run on the current build.
+
+    THE MESSAGE STILL HAS TO BRANCH, AND IT DID NOT (P3 review). One warning_id
+    is not one sentence. The shipped text told every owner that the check
+    "scored a set of pre-written model answers", which is true of the ABSENT
+    case (all of history, produced by the tautology at eval.py:374-375) and
+    false in every particular of the FALSE case: a below-floor P2 run invoked
+    the agent, scored nothing at all, wrote no eval_results, and involved no
+    pre-written answers anywhere. It also promised the numbers would come out
+    "lower than the old ones", when a below-floor run has no old ones to be
+    lower than. Narrating a cause we did not observe is the exact defect class
+    this phase exists to remove, and the console renders nothing else — a grep
+    of apps/admin for `agent_invoked` or `eval_signal` returns nothing, so this
+    sentence IS the owner-visible account.
+
+    Even the absent branch does not assert the tautology as fact: absence also
+    arrives from a pre-0013 tenant DB with no `config` column, and from a P2 run
+    whose config patch failed. It names the historical cause as a conditional
+    and lets the drop be explained if it applies.
+
+    The message does not use the word "eval": a non-technical owner reading
+    "the evaluation did not invoke the agent" learns nothing actionable. It
+    says what was measured instead.
+
+    `eval_summary` IS READ, both for `agent_invoked` and for `eval_dispatched`
+    — the same wait-vs-go-find-a-page split the eval_never_run warning makes,
+    for the same reason. It used to be an unread parameter, which made
+    test_the_collector_state_and_the_gate_arm_reach_the_same_warning true by
+    construction: two call sites of a constant-returning function cannot
+    produce different payloads.
+    """
+    invoked = eval_summary.get("agent_invoked")
+    started = bool(eval_summary.get("eval_dispatched"))
+    if invoked is False:
+        # The run looked and said no: below MIN_RESPONSE_RATE, below
+        # MIN_SCORED_OBSERVATIONS, or dead before its first turn (the value
+        # every eval_runs row is INSERTed with). No scores exist for it —
+        # run_eval_suite skips the scorer entirely below the floor — so there
+        # is nothing here to describe as pre-written, and nothing to fall from.
+        cause = (
+            "This agent's last quality check could not get enough of the "
+            "agent's own replies back to judge, so it measured nothing and "
+            "cannot be used to approve a launch."
+        )
+    else:
+        cause = (
+            "This agent's last quality check does not record whether it ever "
+            "put a question to this agent, so it cannot be used to approve a "
+            "launch. Checks from before this release scored pre-written model "
+            "answers rather than the agent's own replies, which is why their "
+            "scores were near-perfect. If this was one of those, the fresh "
+            "numbers will look lower, and that drop is the measurement "
+            "starting to work rather than the agent getting worse."
+        )
+    remedy = (
+        " We have started a fresh check and it takes a few minutes. Run this "
+        "readiness check again once it finishes."
+        if started
+        else " Run a fresh check from the Evaluation page and try again."
+    )
+    return DeploymentWarning(
+        warning_id="eval_agent_not_invoked",
+        category="eval_quality",
+        message=cause + remedy,
+        severity_level="warning",
+    )
+
+
 def apply_signal_evidence_gate(
     recommendation: str,
     eval_summary: dict,
@@ -1182,6 +1495,38 @@ def apply_signal_evidence_gate(
     were prose in a system prompt and nothing else; run against the shipped
     code, this function returned 'ship' for all three.
 
+    FIVE NOW: `agent_invoked is not True` (audit D1, P3). A signal that says
+    'measured' is a claim that a run produced scores, not a claim that the
+    scores are about this agent — and until this release they were not. The
+    eval set `agent_response` to the scenario's own `reference_answer`
+    (eval.py:374-375), so every stored run reports near-perfect faithfulness
+    over answers the agent never wrote, and this gate shipped on all of them.
+
+    THE COLLECTOR IS THE ENFORCEMENT; THE `elif` BELOW IS THE INVARIANT (P3
+    review corrects the original claim here, which said both were load-bearing
+    today). _fetch_eval_summary_sync already downgrades such a run to
+    EVAL_SIGNAL_AGENT_NOT_INVOKED, and it is the only producer of a 'measured'
+    payload in the tree — neuter the `elif` alone and every collector test stays
+    green, because the production path never reaches it. The other payload that
+    exists, EVAL_SUMMARY_UNAVAILABLE_SIGNAL, carries eval_signal='unavailable'
+    and cannot reach it either. So the arm guards a payload shape that does not
+    exist yet: a hand-built summary, a second collector added later, a caller
+    that copies the dict and drops a key. That is a real defence and it is
+    defence against a FUTURE caller — the same shape as the "A MISSING key"
+    paragraph above, and worth keeping for the same reason, but do not read it
+    as a second live layer under today's code.
+
+    ABSENT IS REFUSED EXACTLY AS FALSE IS, and this is the whole decision.
+    `is not True`, never `is False`: None must fail the same way, because None
+    is what the entire history of stored runs carries. A gate refusing only
+    False would have been satisfied by every tautological run ever written —
+    the same failure as BACKLOG 3.1, where pre-P4 red-team runs still read
+    'measured' with clean findings because nobody had recorded the absence.
+    The accepted consequence, settled by the owner 2026-08-07, is that every
+    pre-D1 run and every tenant DB older than alembic_tenant 0013 fails closed
+    until a fresh eval runs. That costs blocked deploys; the alternative costs
+    shipped agents nobody measured.
+
     Args:
         recommendation: the orchestrator's own recommendation.
         eval_summary: _fetch_eval_summary_sync's payload, or the unavailable
@@ -1200,13 +1545,15 @@ def apply_signal_evidence_gate(
     if eval_signal != SHIPPABLE_SIGNAL:
         blocked = True
         detail = eval_summary.get("signal_detail") or "no eval signal was produced"
-        if eval_signal == EVAL_SIGNAL_NO_RUNS:
+        if eval_signal == EVAL_SIGNAL_AGENT_NOT_INVOKED:
+            warnings.append(_agent_not_invoked_warning(eval_summary))
+        elif eval_signal == EVAL_SIGNAL_NO_RUNS:
             # The day-1 state, and the one the owner can actually act on. The
             # checklist task starts the first eval itself when it finds this
             # (run_deployment_checklist step 4b) and records that on the signal,
             # so the message says "wait" rather than sending a non-technical
             # owner to a page the onboarding flow never routes to.
-            started = bool(eval_summary.get("first_eval_dispatched"))
+            started = bool(eval_summary.get("eval_dispatched"))
             warnings.append(
                 DeploymentWarning(
                     warning_id="eval_never_run",
@@ -1239,6 +1586,16 @@ def apply_signal_evidence_gate(
                     severity_level="warning",
                 )
             )
+    elif eval_summary.get("agent_invoked") is not True:
+        # A payload that claims 'measured' and does not claim to have invoked
+        # the agent. In production _fetch_eval_summary_sync has already turned
+        # this into EVAL_SIGNAL_AGENT_NOT_INVOKED above, so reaching here means
+        # the payload came from somewhere else — and somewhere else is exactly
+        # where the next fail-open comes from. Unreachable today; see the
+        # docstring's THE COLLECTOR IS THE ENFORCEMENT paragraph, which says so
+        # rather than claiming a second live layer.
+        blocked = True
+        warnings.append(_agent_not_invoked_warning(eval_summary))
 
     red_team_signal = red_team_summary.get("signal")
     if red_team_signal != SHIPPABLE_SIGNAL:
@@ -1385,6 +1742,60 @@ def apply_signal_evidence_gate(
     return recommendation, warnings
 
 
+# The message the approve route answers 422 with when the stored run's own
+# evidence does not claim the agent was ever asked anything. Module-level so the
+# route and its tests cannot drift on the wording.
+STORED_RUN_NOT_INVOKED_DETAIL = (
+    "This readiness check was decided on a quality result that does not record "
+    "having put a question to the agent. Run a fresh check from the Evaluation "
+    "page, then run the readiness check again."
+)
+
+
+def stored_run_records_agent_invocation(report: object) -> bool:
+    """Does a PERSISTED checklist run's own report claim the agent was invoked?
+
+    THE GATE DOES NOT REACH A RUN THAT IS ALREADY FINISHED (P3 review), and this
+    is the hole the rest of the phase left open. apply_signal_evidence_gate has
+    exactly one caller — run_deployment_checklist, at checklist time — and
+    `agent.is_deployed` has exactly one writer: POST /approve-deployment, which
+    validates against `checklist_runs.recommendation`, a value FROZEN by whatever
+    gate was running the day the row was written. So every readiness check
+    completed before this release carries a 'ship' computed by the pre-P3 gate
+    over a tautological eval, and stays approvable indefinitely: status is
+    'complete', recommendation is not 'block', warnings do not apply, and the
+    envelope hash has not moved. `{"deployed": true}`, and the agent this phase
+    exists to refuse is live.
+
+    That is BACKLOG 3.1's shape — pre-P4 red-team runs still reading
+    'measured' with clean findings — which is the very argument P3's commit
+    message used to justify refusing an ABSENT claim, applied one layer up to
+    the artifact the approve route actually reads. Nothing on checklist_runs
+    expires (no TTL, no gate-version column, app/models/checklist_run.py), so
+    the run has to be re-read rather than aged out.
+
+    `is True`, matching the gate arm exactly, so absence and falsehood and the
+    string "true" all fail the same way. Every non-dict shape on the path
+    (report NULL on a run that never reached step 6, an eval_summary key the
+    orchestrator never wrote, a JSONB payload of some other shape) returns
+    False: this is a gate, and a gate that cannot read its evidence has not been
+    satisfied.
+
+    Args:
+        report: `ChecklistRun.report` as stored — a JSONB dict carrying the five
+            signal payloads, or None.
+
+    Returns:
+        True only when report["eval_summary"]["agent_invoked"] is exactly True.
+    """
+    if not isinstance(report, dict):
+        return False
+    eval_summary = report.get("eval_summary")
+    if not isinstance(eval_summary, dict):
+        return False
+    return eval_summary.get("agent_invoked") is True
+
+
 def derive_blast_radius_warnings(blast_radius: dict) -> list[DeploymentWarning]:
     """Derive blast-radius warnings deterministically in Python (OD-1b).
 
```

#### 1.2.6 `app/services/eval_service.py` (+634/-70) — the observation, the floors, the provenance, the promotion label

```diff
diff --git a/apps/api/app/services/eval_service.py b/apps/api/app/services/eval_service.py
index ee09270..d6da723 100644
--- a/apps/api/app/services/eval_service.py
+++ b/apps/api/app/services/eval_service.py
@@ -98,41 +98,64 @@ HAIKU_MODEL = "claude-haiku-4-5"
 # ---------------------------------------------------------------------------
 # What this harness measures — and what it does not (audit D1)
 # ---------------------------------------------------------------------------
-# Two properties of the shipped scoring half that every consumer of a score has
-# to know. They are constants rather than prose so they can be stamped on the
-# run record and pinned by a test, instead of being rediscovered by reading
-# three files.
+# Two properties of the scoring half that every consumer of a score has to know.
+# They are constants rather than prose so they can be stamped on the run record
+# and pinned by a test, instead of being rediscovered by reading three files.
 #
-# 1. THE AGENT IS NEVER INVOKED. eval.py builds every sample with
-#    agent_response = reference_answer, so Ragas scores the reference answer
-#    against the contexts that reference answer was written from. Faithfulness
-#    and AnswerRelevancy approach 1.0 by construction, and — the part that
-#    matters for the configuration tuple — the score is INVARIANT to the
-#    agent's model, prompt, retrieval configuration, capability envelope and
-#    corpus. Recording those dimensions on a run without recording this makes
-#    an uncomparable measurement look comparable: two runs differing only on
-#    config.model_id would carry statistically identical scores and read as
-#    "the model swap was quality-neutral". Fixing D1 is not this phase's scope;
-#    hiding it would be worse than leaving it.
+# 1. THE AGENT IS NOW INVOKED — and whether it was is an OBSERVATION, never an
+#    assumption. Until D1/P2 (.dev/plans/260807-d1-agent-invocation.md) eval.py
+#    built every sample with agent_response = reference_answer, so Ragas scored
+#    the reference answer against the contexts that answer was written from:
+#    Faithfulness and AnswerRelevancy approached 1.0 by construction and the
+#    score was INVARIANT to the agent's model, prompt, retrieval configuration,
+#    capability envelope and corpus. Recording those dimensions on a run without
+#    recording that made an uncomparable measurement look comparable — two runs
+#    differing only on config.model_id carried statistically identical scores and
+#    read as "the model swap was quality-neutral".
+#
+#    EVAL_INVOKES_AGENT below is a claim about the CODE: this harness drives the
+#    customer agent per scenario, through agent.build_agent_options. It is NOT
+#    the same claim as `config["agent_invoked"]`, which is a claim about ONE RUN
+#    and is written from what that run observed. The distinction is the whole
+#    lesson of D1: a constant that says the agent was invoked, stamped on a run
+#    that invoked nothing, is the tautology with a newer comment.
 #
 # 2. SCORING TOUCHES NO DATABASE. run_ragas_eval executes no statement against
 #    anything — it takes rows already in memory and calls the judge API. The
 #    Neon branch the caller creates per run (D-10) is therefore isolation held
-#    IN RESERVE for the day this harness starts invoking retrieval or the agent
-#    against tenant data, not isolation in use. The caller reads
+#    IN RESERVE, not isolation in use. The caller reads
 #    EVAL_SCORING_REQUIRES_BRANCH to decide whether a branch it cannot create
 #    is fatal; while it is False, abandoning a run over that branch would throw
 #    away a night's measurement for a resource nothing reads.
-
-EVAL_INVOKES_AGENT = False
-
-# Where the text scored as the "response" comes from while D1 stands.
-EVAL_SCORED_RESPONSE_SOURCE = "reference_answer"
+#
+#    P2 does not change this. The agent turns happen in eval.py BEFORE scoring
+#    and they read the tenant's PRODUCTION connection string, because retrieval
+#    has to see the corpus the customer is served; what stops them writing is
+#    recorded mode (BACKLOG 2.5), not the branch.
+
+EVAL_INVOKES_AGENT = True
+
+# Where the text scored as the "response" comes from now that D1 is closed. The
+# per-run key `config["scored_response_source"]` is derived from the run's own
+# observation, not from this constant — see invocation_provenance.
+EVAL_SCORED_RESPONSE_SOURCE = "agent_response"
+
+# What the same key says on a run whose eval_runs row exists but whose
+# invocation phase has not reported yet. It is the value every run carries at
+# INSERT time, and it is what a run that died mid-invocation keeps.
+EVAL_RESPONSE_SOURCE_PENDING = "pending_invocation"
+
+# And what it says when the invocation phase DID report and nothing reached the
+# scorer — every turn raised, or every response came back with no usable
+# retrieved context. Distinct from 'pending_invocation' (the phase never ran) and
+# from 'agent_response' (a set of scored rows exists and came from the agent),
+# because a claim about an empty set is neither of those.
+EVAL_RESPONSE_SOURCE_NONE_SCORED = "no_response_scored"
 
 # Dimensions of the run record — config keys plus the prompt_version_id column —
-# that cannot influence a score while EVAL_INVOKES_AGENT is False. judge_model_id
-# is deliberately NOT here: the judge does run, so a judge change does move the
-# numbers.
+# that cannot influence a score when the run did not measure an invoked agent.
+# judge_model_id is deliberately NOT here: the judge does run, so a judge change
+# does move the numbers whatever the agent did.
 AGENT_DEPENDENT_DIMENSIONS: list[str] = [
     "prompt_version_id",
     "model_id",
@@ -248,6 +271,19 @@ def trust_tier_rank(tier: str) -> int:
     return LABEL_TRUST_TIERS.get(tier, LABEL_TRUST_TIERS["unknown"])
 
 
+def promotable_answer(scenario: dict) -> str:
+    """The ONE text that may be written into verified_qa for a scenario.
+
+    It is the scenario's `reference_answer` and never its `agent_response`. The
+    trust gate reasons about `scenario["source"]`, which is the provenance of the
+    LABEL; writing the agent's own turn under that gate would admit a
+    model_generated string on the strength of a human_authored tier. Callers must
+    not reach past this to pick a field themselves — that is exactly how the two
+    came apart.
+    """
+    return str(scenario.get("reference_answer") or "")
+
+
 def is_promotable_to_verified_qa(source: str | None) -> bool:
     """True iff a scenario from *source* may have its answer served to customers.
 
@@ -318,6 +354,341 @@ def dataset_of(value: str | None) -> str:
     return DATASET_GOLDEN if value == DATASET_GOLDEN else DATASET_EXPLORATORY
 
 
+# ---------------------------------------------------------------------------
+# Invoking the agent: the bounds, the floor, and the observation (D1/P2)
+# ---------------------------------------------------------------------------
+# THE COST SHAPE CHANGED, AND SILENTLY IF NOBODY BOUNDS IT. Before P2 a nightly
+# eval was seconds of arithmetic plus judge calls. Now every selected row costs
+# one live SDK turn — the whole golden set unsampled, plus EXPLORATORY_SAMPLE_SIZE
+# rotating rows — at a 90s per-turn ceiling, per agent, every night, billed. Two
+# bounds, and both are stamped on the run so a reader can tell a cheap run from a
+# truncated one instead of inferring it from a bill.
+
+#: How many agent turns run at once. ONE, and the implementation asserts it
+#: rather than trusting it: this box has 4 GB of RAM (CLAUDE.md environment
+#: constraints) and each turn is an Agent SDK subprocess. Raising the number
+#: without changing the loop would make the provenance say something the run did
+#: not do, which is the defect class this whole phase exists to remove — so
+#: eval.py raises on any other value instead of quietly running sequentially.
+AGENT_INVOCATION_CONCURRENCY = 1
+
+#: The per-run ceiling on live SDK turns. The binding cost control: worst-case
+#: wall clock for a run is this times the per-turn timeout.
+#:
+#: It sits BELOW GOLDEN_SET_SOFT_CEILING (200) on purpose, and the two disagree
+#: on purpose. The golden set is unsampled because a paired per-item delta is the
+#: only regression signal available at n=30; a tenant who designates more golden
+#: rows than this gets the first AGENT_INVOCATION_MAX_CALLS_PER_RUN of them
+#: invoked and the remainder reported as `ceiling_skipped`, golden-first, never
+#: silently. Truncating the golden set breaks the pairing, so the breakage is
+#: made loud (a warning and a counter) rather than resolved by guessing which of
+#: the two ceilings the owner meant.
+AGENT_INVOCATION_MAX_CALLS_PER_RUN = 60
+
+#: The floor under a response rate, same shape and same value as
+#: tests/evals/calibration/compute_correlation.py's MIN_PAIR_RATE (0.8) — and
+#: the same argument: a metric computed over the rows that happened to succeed
+#: is not a measurement of the set that was scored. Below it the run reports
+#: 'unknown'. Not zero, not a low score: the absence of one.
+MIN_RESPONSE_RATE = 0.8
+
+#: The ABSOLUTE floor, and compute_correlation.py's MIN_PAIRS (3) is both the
+#: shape and the value. A rate alone cannot refuse a one-observation run: a
+#: tenant with a single labelled scenario that answers gives response_rate 1.0
+#: and would certify itself as measured off one turn.
+#:
+#: An earlier comment here argued the opposite — "the denominator travels, so a
+#: consumer can apply its own absolute floor". No consumer does, and the one
+#: that would (the deploy gate) reads `agent_invoked`, which is computed HERE.
+#: A floor that every consumer must remember to reapply is a floor nobody has.
+#:
+#: It is applied to the rows that reached the SCORER, not to the rows that
+#: answered: those are different numbers once a responded-but-never-retrieved
+#: row is excluded from context scoring, and the smaller of the two is the one
+#: the metrics were actually computed over.
+MIN_SCORED_OBSERVATIONS = 3
+
+#: Slack added to a run's worst-case wall clock when deriving the idempotency
+#: window in eval.py. The window has to COVER a run that consumes its whole
+#: ceiling, or a redelivered message starts a second concurrent invocation of the
+#: same agent; it was a flat 10 minutes against a 90-minute worst case.
+EVAL_RUN_IDEMPOTENCY_SLACK_S = 600
+
+#: Statuses for the invocation phase of a run.
+AGENT_INVOCATION_NOT_STARTED = "not_started"   # the row exists; no turn ran yet
+AGENT_INVOCATION_MEASURED = "measured"         # enough rows answered to measure
+AGENT_INVOCATION_UNKNOWN = "unknown"           # too few did — never 'pass'
+
+#: Recorded side-effect kinds that are TELEMETRY about a turn rather than a
+#: decision the agent made. Counted on the run, never carried in full: one
+#: retrieval_metrics row per retrieve call, times sixty scenarios, is tens of
+#: kilobytes of float in a jsonb column nobody queries for it.
+#:
+#: Everything NOT named here is carried in full, which is the fail-open
+#: direction that matters: a new `kind` someone adds to the tool layer arrives
+#: as eval signal by default instead of vanishing into a counter.
+SIDE_EFFECT_KINDS_TELEMETRY: tuple[str, ...] = ("retrieval_metrics.write",)
+
+#: Cap on how many capability attempts are carried verbatim on one run. A run
+#: that exceeds it says so (`capability_attempts_truncated`) rather than
+#: silently reporting the first hundred as if they were all of them.
+MAX_CAPABILITY_ATTEMPTS_RECORDED = 100
+
+
+def summarise_agent_invocation(
+    records: list[dict],
+    *,
+    valid: int,
+    ceiling_skipped: int,
+    ceiling_skipped_golden: int,
+    per_turn_timeout_s: int,
+    audit_capture_char_cap: int,
+    retrieved_context_chunk_char_cap: int,
+    pii_firewall_applied: bool,
+) -> dict:
+    """Turn per-scenario invocation records into the run's observation. Pure.
+
+    A SCENARIO WHOSE AGENT CALL FAILED IS EXCLUDED AND COUNTED, NEVER SCORED 0.
+    Zero is not a low score, it is the absence of one — the lesson
+    tests/evals/calibration/compute_correlation.py:485 already learned about a
+    judge that errors. The same rule applies one layer earlier here: a turn that
+    timed out, raised, or came back with no text produced no observation about
+    answer quality, and averaging a zero in would move every metric with the
+    failure rate of the Agent SDK rather than with the agent's behaviour.
+
+    Args:
+        records: one dict per scenario an agent turn was ATTEMPTED for, each
+            carrying `responded` (bool), `scorable` (bool — reached the scorer),
+            `error` (str|None), `retrieve_calls` (int), `retrieve_at_cap` (bool),
+            `retrieve_unparsed` (int), `retrieved_chunks` (int) and
+            `side_effects` (list of the entries recorded mode collected during
+            that turn).
+        valid: rows in the run that carry a label, i.e. that could have been
+            invoked. `valid == len(records) + ceiling_skipped` always.
+        ceiling_skipped: valid rows the per-run ceiling did not invoke.
+        ceiling_skipped_golden: how many of those were golden rows — reported
+            separately because skipping a golden row breaks the paired per-item
+            delta the golden set exists for.
+        per_turn_timeout_s: the wall-clock bound each turn ran under, carried so
+            a reader never has to look it up in a different module's source at a
+            different commit.
+        audit_capture_char_cap: the cap on `tool_calls_log[*]["result"]`. It
+            bounds the AUDIT copy of a retrieve result and NOT the contexts that
+            were scored — recorded under a name that says so, because while it
+            was called `retrieved_context_char_cap` it read as the bound on the
+            evidence the judge saw, and the derived `retrieved_context_at_cap`
+            was consequently true on essentially every retrieving turn.
+        retrieved_context_chunk_char_cap: the cap that DOES bound the scored
+            evidence — agent_tools.CHUNK_CONTENT_CHAR_LIMIT, applied per chunk.
+        pii_firewall_applied: whether the served-path PII deflection ran over
+            these responses. False, and stated: see eval.py's invocation block.
+
+    Returns:
+        The `agent_invocation` provenance object. `status` is
+        AGENT_INVOCATION_MEASURED only when all three hold: at least one turn was
+        attempted, the response rate cleared MIN_RESPONSE_RATE, and at least
+        MIN_SCORED_OBSERVATIONS rows reached the scorer. Otherwise
+        AGENT_INVOCATION_UNKNOWN, which includes the zero-attempt case — a rate
+        over an empty denominator is unknown, never a pass.
+    """
+    attempted = len(records)
+    responded = sum(1 for r in records if r.get("responded"))
+    scorable = sum(1 for r in records if r.get("scorable"))
+    failed = sum(1 for r in records if r.get("error"))
+    # Neither responded nor errored: the SDK returned, with no text. That is the
+    # max_turns / max_budget signature (agent.py's D-10 notes), and it is a
+    # different failure from an exception, so it is counted apart from one.
+    empty = attempted - responded - failed
+
+    errors: dict[str, int] = {}
+    for record in records:
+        error = record.get("error")
+        if error:
+            errors[str(error)] = errors.get(str(error), 0) + 1
+
+    counts: dict[str, int] = {}
+    capability_attempts: list[dict] = []
+    truncated = False
+    for record in records:
+        for entry in record.get("side_effects") or []:
+            kind = str(entry.get("kind", "unknown"))
+            counts[kind] = counts.get(kind, 0) + 1
+            if kind in SIDE_EFFECT_KINDS_TELEMETRY:
+                continue
+            if len(capability_attempts) >= MAX_CAPABILITY_ATTEMPTS_RECORDED:
+                truncated = True
+                continue
+            capability_attempts.append(
+                {
+                    "scenario_id": record.get("scenario_id"),
+                    "kind": kind,
+                    "detail": entry.get("detail"),
+                }
+            )
+
+    response_rate = (responded / attempted) if attempted else None
+    # A SECOND, EXPLICIT DENOMINATOR. `response_rate` divides by what the ceiling
+    # allowed; this divides by what the tenant designated, so a run that put 60
+    # of 200 labelled rows to the agent cannot report full coverage. It is the
+    # shape compute_correlation.py:498 uses (`pairs / parsed["valid"]`) and this
+    # function's rate silently diverged from it.
+    coverage_rate = (responded / valid) if valid else None
+    status = (
+        AGENT_INVOCATION_MEASURED
+        if (
+            attempted
+            and response_rate is not None
+            and response_rate >= MIN_RESPONSE_RATE
+            # THE ABSOLUTE FLOOR, applied to the rows that reached the SCORER.
+            # Without it a one-scenario run answers once, reports 1.0, and
+            # certifies a deploy off a single observation; and a run where 38 of
+            # 40 responses never retrieved would report 'measured' over the two
+            # rows that did.
+            and scorable >= MIN_SCORED_OBSERVATIONS
+        )
+        else AGENT_INVOCATION_UNKNOWN
+    )
+
+    return {
+        "status": status,
+        # (valid, attempted, responded) are three different claims, exactly like
+        # (attempted, valid, scored) one layer up. `valid` is what could have
+        # been invoked, `attempted` is what the ceiling allowed, `responded` is
+        # what produced text. A rate built from any two of them without the
+        # third understates or overstates.
+        "valid": valid,
+        "attempted": attempted,
+        "responded": responded,
+        # Rows that reached run_ragas_eval. Smaller than `responded` by exactly
+        # the rows excluded for having no retrieved context — see `no_retrieval`
+        # below — and it, not `responded`, is the denominator the metrics were
+        # computed over.
+        "scorable": scorable,
+        "failed": failed,
+        "empty": empty,
+        "errors": errors,
+        "ceiling_skipped": ceiling_skipped,
+        "ceiling_skipped_golden": ceiling_skipped_golden,
+        "response_rate": response_rate,
+        "min_response_rate": MIN_RESPONSE_RATE,
+        "coverage_rate": coverage_rate,
+        "min_scored_observations": MIN_SCORED_OBSERVATIONS,
+        "concurrency": AGENT_INVOCATION_CONCURRENCY,
+        "max_calls_per_run": AGENT_INVOCATION_MAX_CALLS_PER_RUN,
+        "per_turn_timeout_s": per_turn_timeout_s,
+        # The worst case this run could have cost in wall clock, derived from the
+        # two bounds rather than asserted beside them.
+        "max_wall_clock_s": AGENT_INVOCATION_MAX_CALLS_PER_RUN * per_turn_timeout_s,
+        # THE TRUNCATION, MADE EXPLICIT (the plan's P2, third bullet) — and
+        # pointed at the cap that actually bounds the evidence. Faithfulness over
+        # a context that was CUT marks a claim unsupported when the support was
+        # merely beyond the cap, so `retrieved_context_at_cap` counts the turns
+        # where at least one SCORED CHUNK came back exactly at the per-chunk
+        # boundary. It used to count turns where the 1800-char AUDIT capture was
+        # at ITS boundary, which five 2000-char chunks exceed by construction:
+        # the figure was ~100% on every retrieving turn and read as signal.
+        "audit_capture_char_cap": audit_capture_char_cap,
+        "retrieved_context_source": "agent_retrieve_chunks",
+        "retrieved_context_chunk_char_cap": retrieved_context_chunk_char_cap,
+        "retrieved_context_at_cap": sum(
+            1 for r in records if r.get("retrieve_at_cap")
+        ),
+        "retrieved_context_chunks": sum(
+            int(r.get("retrieved_chunks") or 0) for r in records
+        ),
+        # Retrieve results whose framed payload could not be split back into
+        # chunks. Counted apart from "retrieved nothing": a turn whose evidence
+        # this build could not read did not retrieve nothing, and reporting it as
+        # such would be the missing-data-as-passing-data error inverted.
+        "retrieved_context_unparsed": sum(
+            int(r.get("retrieve_unparsed") or 0) for r in records
+        ),
+        # Responded, called retrieve zero times. EXCLUDED FROM SCORING and
+        # counted here: Faithfulness / ContextPrecision / ContextRecall over an
+        # empty context list are structurally 0 or NaN, and a 0 for an answer the
+        # agent gave correctly from its system prompt is the "zero is not a low
+        # score" error one metric over. It is a bucket, not a failure — an agent
+        # answering "what are your opening hours?" without retrieving is behaving
+        # correctly, so these rows do not depress `response_rate`.
+        "no_retrieval": sum(
+            1 for r in records if r.get("responded") and not r.get("retrieve_calls")
+        ),
+        # Responded, retrieved, and still reached the scorer with nothing: every
+        # retrieve result was unparsed or empty. Also excluded, also counted, and
+        # kept apart from `no_retrieval` because the remedy is different.
+        "retrieved_nothing_scorable": sum(
+            1
+            for r in records
+            if r.get("responded")
+            and r.get("retrieve_calls")
+            and not r.get("scorable")
+        ),
+        # False, and said out loud: the eval scores the agent's own text, not the
+        # deflection a customer would receive if the output firewall fired.
+        "pii_firewall_applied": pii_firewall_applied,
+        "side_effect_attempts": {
+            "counts": counts,
+            "capability_attempts": capability_attempts,
+            "capability_attempts_truncated": truncated,
+        },
+    }
+
+
+def invocation_provenance(agent_invocation: dict | None) -> dict:
+    """The four D1 keys of `eval_runs.config`, derived from ONE observation.
+
+    Called twice per run and that is the point: once by build_eval_run_config
+    at INSERT, with None, and once by the task after the invocation phase, with
+    the summary. Two derivations of "was the agent invoked" would be two chances
+    to disagree, and the one that disagreed would be the one the deploy gate
+    reads.
+
+    `agent_invoked` IS THE GATE-FACING CLAIM AND IT IS A CONJUNCTION: the scored
+    responses came from real agent turns AND enough rows answered to constitute a
+    measurement. A run where six of sixty scenarios answered did invoke the agent
+    and measured nothing, and a gate reading a bare "we called it" would ship on
+    it — missing data treated as passing data, which is the rule this repo wrote
+    down after the last time. A reader who wants the raw fact reads
+    `agent_invocation["attempted"]`; the two claims stay separable, they just
+    stay separate.
+
+    None (the INSERT case) yields agent_invoked False, so a run that dies between
+    its eval_runs row and its first turn fails closed at the gate rather than
+    inheriting a hopeful default.
+
+    `scored_response_source` is derived from what was SCORED, not from what was
+    attempted. Deriving it from `attempted` meant a run that attempted sixty
+    turns and got zero responses still claimed its scored responses came from
+    the agent — a claim about a set that does not exist, and one a future
+    consumer could read as evidence of an agent-sourced measurement.
+    """
+    invoked = bool(
+        agent_invocation
+        and agent_invocation.get("status") == AGENT_INVOCATION_MEASURED
+    )
+    observation = agent_invocation or {}
+    attempted = int(observation.get("attempted") or 0)
+    scorable = int(observation.get("scorable") or 0)
+    if scorable:
+        scored_response_source = EVAL_SCORED_RESPONSE_SOURCE
+    elif attempted:
+        scored_response_source = EVAL_RESPONSE_SOURCE_NONE_SCORED
+    else:
+        scored_response_source = EVAL_RESPONSE_SOURCE_PENDING
+    return {
+        "agent_invoked": invoked,
+        "scored_response_source": scored_response_source,
+        "dimensions_not_exercised": (
+            [] if invoked else list(AGENT_DEPENDENT_DIMENSIONS)
+        ),
+        "agent_invocation": (
+            dict(agent_invocation)
+            if agent_invocation is not None
+            else {"status": AGENT_INVOCATION_NOT_STARTED}
+        ),
+    }
+
+
 # ---------------------------------------------------------------------------
 # Attributing a returned score to the scenario it is about
 # ---------------------------------------------------------------------------
@@ -612,10 +983,16 @@ def run_ragas_eval(scenarios: list[dict]) -> dict:
         scenarios: List of scenario dicts. Each must contain:
             - question (str): The user question.
             - reference_answer (str): Ground-truth answer (D-02).
-            - agent_response (str, optional): The agent's generated answer.
-              While EVAL_INVOKES_AGENT is False this is the reference answer
-              itself (audit D1), so the metrics below are self-scoring.
-            - retrieved_contexts (list[str], optional): Retrieved chunk contents.
+            - agent_response (str): The agent's own response text, produced by
+              the turn eval.py drove through agent.build_agent_options. It was
+              the reference answer itself until D1/P2 — the metrics were then
+              self-scoring and approached 1.0 by construction. A caller handing
+              this the reference answer again reinstates the tautology, which is
+              why the pin lives in the task's tests rather than here.
+            - retrieved_contexts (list[str], optional): the contexts the AGENT
+              retrieved during that turn — not the scenario's stored
+              `retrieved_contexts` column. Scoring faithfulness against contexts
+              the agent never saw is D1 in a different costume.
 
     Returns:
         Dict with five keys:
@@ -909,6 +1286,7 @@ def build_eval_run_config(
     agent_id: str,
     conn_str: str,
     dataset: dict | None = None,
+    agent_invocation: dict | None = None,
 ) -> dict:
     """Collect the configuration tuple an eval run is an assertion about.
 
@@ -917,14 +1295,25 @@ def build_eval_run_config(
     of continuous improvement. Every dimension below was already captured
     somewhere in the system; none of them was ever stamped on the run.
 
-    WHAT THE TUPLE CERTIFIES IS NOT WHAT THE RUN EXERCISED. The dimensions
-    describe the configuration the agent is deployed with; while
-    EVAL_INVOKES_AGENT is False the scores are invariant to all of them
-    (audit D1), and `config["agent_invoked"]` / `config["scored_response_source"]`
-    / `config["dimensions_not_exercised"]` say so on every run. That pairing is
+    WHAT THE TUPLE CERTIFIES IS NOT AUTOMATICALLY WHAT THE RUN EXERCISED. The
+    dimensions describe the configuration the agent is deployed with; whether the
+    run's scores are a function of any of them depends on whether the agent was
+    actually invoked and enough of it answered. `config["agent_invoked"]` /
+    `config["scored_response_source"]` / `config["dimensions_not_exercised"]` /
+    `config["agent_invocation"]` carry that, and they come from
+    invocation_provenance so there is one derivation. The pairing is
     load-bearing: a tuple that records a dimension the measurement cannot see
     turns "two runs, one difference, identical scores" into a false finding of
-    quality-neutrality.
+    quality-neutrality — which is exactly what every pre-P2 run said.
+
+    AT INSERT TIME THE HONEST ANSWER IS ALWAYS "NOT YET". This function runs
+    before the first agent turn, because the eval_runs row is also the per-agent
+    idempotency key and inserting it after sixty SDK calls would let a concurrent
+    dispatch double-invoke. So `agent_invocation` is None here on the live path
+    and the run is stamped agent_invoked=False; run_eval_suite patches the
+    observed value in afterwards with update_eval_run_config. A run that dies in
+    between keeps the False and fails closed at the deploy gate, which is the
+    direction that costs a blocked deploy rather than a shipped tautology.
 
     Read from the same sources the deploy gate already reads, so a checklist and
     an eval run can never disagree about what the live configuration was:
@@ -957,6 +1346,9 @@ def build_eval_run_config(
             which rows it covered. None is stored as null rather than as an
             empty composition — "this run did not record its dataset" is not
             "this run scored no rows".
+        agent_invocation: summarise_agent_invocation()'s observation, or None
+            when the invocation phase has not reported. None is the live path's
+            only value — see the paragraph above.
 
     Returns:
         {"prompt_version_id": str | None, "config": dict} — ready to hand
@@ -1047,9 +1439,9 @@ def build_eval_run_config(
 
     config = {
         # The model that serves a customer turn, read from the one constant
-        # run_agent_turn uses. It describes the DEPLOYED configuration; while
-        # agent_invoked below is False it is not a dimension these scores
-        # measure — see dimensions_not_exercised.
+        # run_agent_turn uses. It describes the DEPLOYED configuration; whether
+        # these scores are a function of it is a separate claim, carried by
+        # agent_invoked / dimensions_not_exercised below.
         "model_id": AGENT_TURN_MODEL,
         # The model grading the run. A different dimension entirely: a judge
         # change moves every score without the agent changing at all.
@@ -1071,23 +1463,16 @@ def build_eval_run_config(
         "dataset": dict(dataset) if dataset is not None else None,
         # --- What the run actually exercised (audit D1) -----------------
         # The keys above certify the configuration the agent is DEPLOYED with.
-        # These three say which of them the score is a function of, and the
-        # honest answer today is: none. eval.py scores each scenario's
-        # reference answer against the contexts it was written from, so no
-        # agent turn, no retrieval and no capability envelope participates.
-        # Without this, the tuple actively misleads: two nightly runs
+        # These four say which of them the score is a function of, and they are
+        # derived from the run's OWN observation rather than from a module
+        # constant. Without them the tuple actively misleads: two nightly runs
         # differing on exactly one recorded dimension (config.model_id, say)
-        # would carry statistically identical scores, and the reader the tuple
-        # was built for would conclude the model swap is quality-neutral and
-        # ship it. A configuration tuple that makes an uncomparable
-        # measurement look comparable is worse than no tuple at all, so the
-        # exclusion travels with every run rather than living in an audit
-        # nobody queries.
-        "agent_invoked": EVAL_INVOKES_AGENT,
-        "scored_response_source": EVAL_SCORED_RESPONSE_SOURCE,
-        "dimensions_not_exercised": (
-            [] if EVAL_INVOKES_AGENT else list(AGENT_DEPENDENT_DIMENSIONS)
-        ),
+        # carrying statistically identical scores read as "the model swap is
+        # quality-neutral", which is what a tautology looks like from the
+        # outside. A configuration tuple that makes an uncomparable measurement
+        # look comparable is worse than no tuple at all, so the exclusion
+        # travels with every run rather than living in an audit nobody queries.
+        **invocation_provenance(agent_invocation),
         # Names the dimensions that could not be READ. Empty list = every
         # dimension was collected; a None value with nothing here means the
         # dimension was read and is genuinely absent.
@@ -1170,6 +1555,85 @@ def insert_eval_run(
         conn.close()
 
 
+_UPDATE_EVAL_RUN_CONFIG_SQL = """
+    UPDATE eval_runs
+    SET config = COALESCE(config, '{}'::jsonb) || %(patch)s::jsonb
+    WHERE id = %(id)s::uuid
+"""
+
+
+def update_eval_run_config(run_id: str, patch: dict, conn_str: str) -> bool:
+    """Merge observed provenance into an existing eval_runs.config. PRODUCTION.
+
+    The one write that turns `agent_invoked` from a hope into an observation.
+    The row has to exist before the first agent turn — it is the per-agent
+    idempotency key, and inserting it after sixty SDK calls would let a
+    concurrent dispatch double-invoke — so the run is stamped agent_invoked=False
+    at INSERT and corrected here once the invocation phase has reported.
+
+    `||` is a SHALLOW jsonb merge, which is the semantics wanted: the whole
+    `agent_invocation` object is replaced by the observed one rather than
+    half-merged with the `{"status": "not_started"}` placeholder, and no key the
+    caller did not name is disturbed.
+
+    FAILURE LEAVES THE RUN CLAIMING LESS THAN IT DID, NEVER MORE. If this write
+    does not land, the run keeps agent_invoked=False and the deploy gate refuses
+    it. That is a blocked deploy on a run that was fine — annoying, and the right
+    direction, because the other direction ships on a run whose measurement
+    nobody can vouch for. So the exception is caught, logged at error level, and
+    reported as False rather than failing a run that has already been scored.
+
+    Tolerates a tenant DB that predates migration 0013 exactly as insert_eval_run
+    does, and by the same narrow `UndefinedColumn` catch: a broad `except` here
+    would swallow a genuine write failure and report the patch as applied.
+
+    Args:
+        run_id: UUID string of the eval_runs row.
+        patch: the config keys to merge. Serialised as jsonb by this function.
+        conn_str: PRODUCTION tenant connection string — never the eval branch.
+
+    Returns:
+        True when the patch landed; False when the column is absent or the write
+        failed. Never raises.
+    """
+    try:
+        conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
+        try:
+            try:
+                with conn.cursor() as cur:
+                    cur.execute(
+                        _UPDATE_EVAL_RUN_CONFIG_SQL,
+                        {"patch": json.dumps(patch), "id": run_id},
+                    )
+                conn.commit()
+                return True
+            except psycopg2.errors.UndefinedColumn:
+                conn.rollback()
+                log.warning(
+                    "update_eval_run_config.config_column_absent",
+                    run_id=run_id,
+                    detail=(
+                        "tenant DB predates alembic_tenant 0013 — the run cannot "
+                        "record that the agent was invoked, so the deploy gate "
+                        "will refuse it"
+                    ),
+                )
+                return False
+        finally:
+            conn.close()
+    except Exception as exc:
+        log.error(
+            "update_eval_run_config.failed",
+            run_id=run_id,
+            error=str(exc),
+            detail=(
+                "the run keeps agent_invoked=false and will be refused by the "
+                "deploy gate — fail-closed, but the measurement is lost"
+            ),
+        )
+        return False
+
+
 # ---------------------------------------------------------------------------
 # Task 2: verified_qa promotion helper
 # ---------------------------------------------------------------------------
@@ -1239,6 +1703,13 @@ def select_promotion_candidates(
             _refuse(f"trust_tier:{scenario_trust_tier(source)}")
             continue
 
+        # The tier just cleared is a claim about the LABEL. A row whose label is
+        # empty would be promoted on the strength of a tier describing a string
+        # it does not have, and would serve a blank answer to a customer.
+        if not promotable_answer(scenario):
+            _refuse("no_promotable_answer")
+            continue
+
         if not _meets_score_thresholds(score):
             _refuse("below_score_threshold")
             continue
@@ -1269,6 +1740,10 @@ def promote_to_verified_qa(
     surviving second lock on the door means a future caller that reintroduces
     the call still cannot serve a model-written answer to a customer.
 
+    THE ANSWER WRITTEN IS THE SCENARIO'S LABEL, never the agent's own turn — see
+    promotable_answer. The gate reasons about the label's provenance, so the
+    label is what may be admitted.
+
     Promoted rows are written with source='sandbox_test', promoted_by='system'
     (D-22 LOCKED) and a Voyage question_vector (D-23 LOCKED). Idempotency on
     Celery retry (acks_late rule) is ensured by a SELECT-first existence check
@@ -1353,7 +1828,20 @@ def promote_to_verified_qa(
                     [question], model="voyage-3", input_type="query"
                 ).embeddings[0]
 
-                answer = scenario.get("agent_response", "")
+                # THE GATE AND THE PAYLOAD MUST DESCRIBE THE SAME ARTIFACT.
+                # This wrote `scenario["agent_response"]`, and the trust gate
+                # above inspects `scenario["source"]` — the provenance of the
+                # REFERENCE answer. Before D1/P2 those were the same string
+                # (eval.py set agent_response = reference_answer), so gating on
+                # the source was correct by accident. After P2, agent_response is
+                # model-generated output whose tier is `model_generated` whatever
+                # the scenario's source says — so the day a human_authored source
+                # exists and the gate opens, the row retrieval_service serves to
+                # a real customer ahead of hybrid search would be the agent's own
+                # answer. The written answer is the LABEL, which is the text the
+                # tier the gate checked is about. Pinned by
+                # test_the_promoted_answer_is_the_label_not_the_agents_own_text.
+                answer = promotable_answer(scenario)
                 citations = scenario.get("citations", [])
 
                 cur.execute(insert_sql, {
@@ -1421,13 +1909,29 @@ def run_eval_for_agent(
     VERIFIED_QA_PROMOTION_DECISION. Restoring it means clearing the trust gate
     in promote_to_verified_qa, not re-adding the call here.
 
+    IT REFUSES A TAUTOLOGY AT THE DOOR (D1/P2 review). This is a SECOND
+    orchestrator: it takes caller-supplied scenario dicts, invokes no agent, and
+    hands them straight to run_ragas_eval. Every guard P2 built reads eval.py's
+    AST or drives eval.py's loop, so none of them reach here — a future caller
+    wiring a synchronous "score these rows" route could pass
+    agent_response = reference_answer and reinstate D1 with all of P2 still
+    green. So the refusal lives here, in the only place that can see these rows:
+    every scenario must carry a non-empty `agent_response` that DIFFERS from its
+    `reference_answer`, and a batch that does not raises ValueError before a
+    single judge call is billed.
+
     On exception: update_eval_run_status → 'failed' on production, then re-raise.
 
     Args:
         eval_run_id: UUID string — the eval_runs row already created by caller.
-        scenarios: List of scenario dicts from the eval_scenarios table.
+        scenarios: List of scenario dicts from the eval_scenarios table, each
+            carrying an `agent_response` distinct from its `reference_answer`.
         conn_str: PRODUCTION tenant connection string — status + results land here.
 
+    Raises:
+        ValueError: a scenario has no agent_response, or its agent_response is
+            its own reference_answer.
+
     Returns:
         Dict: {
             "eval_run_id": str,
@@ -1436,6 +1940,24 @@ def run_eval_for_agent(
             "promoted_count": int,   # always 0 while promotion is disabled
         }
     """
+    tautologies = [
+        str(s.get("id", ""))
+        for s in scenarios
+        if s.get("reference_answer")
+        and (
+            not str(s.get("agent_response") or "").strip()
+            or s.get("agent_response") == s.get("reference_answer")
+        )
+    ]
+    if tautologies:
+        raise ValueError(
+            "run_eval_for_agent was handed rows whose prediction is their own "
+            f"label (or is empty): {tautologies[:10]}. Faithfulness and "
+            "AnswerRelevancy would approach 1.0 by construction and no change "
+            "to the agent could move them — that is audit D1, and this function "
+            "is the door P2's guards do not cover."
+        )
+
     log.info("run_eval_for_agent.start", eval_run_id=eval_run_id)
     update_eval_run_status(eval_run_id, "running", finished_at=False, conn_str=conn_str)
 
```

#### 1.2.7 `app/api/v1/deployment.py` (+31/-3), `app/services/decision_eval_service.py` (+14/-0), `app/worker/celery_app.py` (+26/-6), `app/worker/tasks/runtime/deployment.py` (+65/-17)

```diff
diff --git a/apps/api/app/api/v1/deployment.py b/apps/api/app/api/v1/deployment.py
index fb124d9..879ea8b 100644
--- a/apps/api/app/api/v1/deployment.py
+++ b/apps/api/app/api/v1/deployment.py
@@ -38,7 +38,11 @@ from app.schemas.deployment import (
     ApproveDeploymentRequest,
 )
 from app.services.capability_service import canonical_envelope_hash, envelope_drift
-from app.services.deployment_service import _make_iframe_snippet
+from app.services.deployment_service import (
+    STORED_RUN_NOT_INVOKED_DETAIL,
+    _make_iframe_snippet,
+    stored_run_records_agent_invocation,
+)
 from app.worker.tasks.runtime.deployment import run_deployment_checklist
 
 router = APIRouter(tags=["deployment"])
@@ -358,10 +362,16 @@ async def approve_deployment(
         T-08-04-02: Server-side validation before any mutation —
         blocked or incomplete runs are rejected (422).
 
-    Validation sequence (CONTEXT.md §Approval Validation; Phase 18 BLR-02 adds #4):
+    Validation sequence (CONTEXT.md §Approval Validation; Phase 18 BLR-02 adds
+    #4; audit D1 / P3 review adds #3b):
         1. run.status != "complete"     → 422 "Checklist is still running"
         2. recommendation == "block"    → 422 "Cannot approve a blocked deployment..."
         3. ship_with_warnings + not all acknowledged → 422 "Acknowledge all warnings..."
+        3b. the run's own report does not record eval_summary.agent_invoked is
+           True → 422. `recommendation` is frozen at checklist time, so a run
+           completed before the D1 gate landed still says 'ship' over an eval
+           that scored the dataset's own reference answers. Absence, falsehood
+           and an unreadable report shape all fail identically.
         4. live envelope hash drifted from the run's recorded hash (or the
            recorded hash is NULL — an absent acknowledgement is drift, never
            a match) → 422 "Capability envelope changed..."
@@ -403,6 +413,23 @@ async def approve_deployment(
             status_code=422,
             detail="Acknowledge all warnings before approving",
         )
+    # 3b. Audit D1 / P3 review: the stored run's own eval evidence must claim
+    #     the agent was invoked. `recommendation` is FROZEN at checklist time by
+    #     whatever gate was running that day, so refusing an uninvoked run in
+    #     apply_signal_evidence_gate closes nothing for a run that already
+    #     completed — and every run completed before this release carries a
+    #     'ship' computed over the tautology at eval.py:374-375. This is the
+    #     same read the gate makes, made again here against the artifact the
+    #     approve decision is actually taken from. Fail-closed on any shape it
+    #     cannot read, exactly as the NULL envelope hash below does.
+    #
+    #     Placed AHEAD of the envelope check and BEHIND the three shipped
+    #     validations: a blocked or incomplete run still reports its own, more
+    #     severe 422, but an owner whose run measured nothing needs to know
+    #     they must run a fresh eval FIRST, which is a step the envelope-drift
+    #     message ("re-run the checklist") does not mention.
+    if not stored_run_records_agent_invocation(run.report):
+        raise HTTPException(status_code=422, detail=STORED_RUN_NOT_INVOKED_DETAIL)
     # 4b. BLR-02: the live capability envelope must still match what this
     #     checklist run recorded. envelope_drift returns True for a NULL
     #     recorded hash too — a pre-0019 historical run or a run whose hash
diff --git a/apps/api/app/services/decision_eval_service.py b/apps/api/app/services/decision_eval_service.py
index 36c4fe8..9a71267 100644
--- a/apps/api/app/services/decision_eval_service.py
+++ b/apps/api/app/services/decision_eval_service.py
@@ -240,6 +240,7 @@ from app.services.red_team_probe import CLEAN_TENANT_ENVELOPES
 from app.services.transactional.enforcement import _parse_rate_limit
 from app.services.transactional.registry import TOOL_REGISTRY
 from app.services.transactional.schemas import SKILL_INPUT_MODELS
+from app.services.transactional.tools import RECORDED_NOT_EXECUTED
 
 log = structlog.get_logger(__name__)
 
@@ -1063,6 +1064,19 @@ _APPROVING_DECISIONS: tuple[str, ...] = ("approve",)
 CAPABILITY_DENIAL_PREFIX = "capability.denial:"
 
 _ERROR_DISPOSITIONS: tuple[tuple[str, str | None, str], ...] = (
+    # FIRST, and it must stay first: every audit row recorded mode writes carries
+    # this as a PREFIX on the reason it would otherwise have written, so any
+    # entry placed above it would classify an eval's row as a real decision.
+    #
+    # `None` — not a disposition — because the row records a decision that did
+    # not act on anything. The Actor genuinely decided, so the row is not
+    # meaningless; but it decided about a scenario, not a customer, and the
+    # adapter, the pending_confirmations row and the money were all suppressed.
+    # Admitting it is exactly the contamination `RECORDED_NOT_EXECUTED` exists to
+    # prevent: a supervised set for the Actor gate assembled half from requests
+    # that happened and half from requests that did not — with the eval, whose
+    # scenarios are chosen to provoke refusals, supplying the second half.
+    (RECORDED_NOT_EXECUTED, None, "recorded_not_executed"),
     # The gate escalated: a pending_confirmations row exists and the adapter did
     # not run (tools.py step 5, require_human branch).
     ("actor_require_human", DISPOSITION_REQUIRE_HUMAN, "actor_require_human"),
diff --git a/apps/api/app/worker/celery_app.py b/apps/api/app/worker/celery_app.py
index 5de9763..44f846e 100644
--- a/apps/api/app/worker/celery_app.py
+++ b/apps/api/app/worker/celery_app.py
@@ -54,6 +54,26 @@ from kombu import Exchange, Queue
 
 from app.core.config import settings
 
+# ---------------------------------------------------------------------------
+# How long the broker waits before deciding a delivered message was lost
+# ---------------------------------------------------------------------------
+# THE LONGEST TASK IN THIS SYSTEM IS NO LONGER PROVISIONING. It was 3600 s with
+# a comment reasoning about "provision + migrations can take ~60 s". D1/P2 made
+# `run_eval_suite` invoke the customer agent once per scenario: sixty turns at a
+# 90 s ceiling is 5400 s of worst case, which the run STAMPS ON ITSELF as
+# `max_wall_clock_s`. A run that actually consumes the bound it advertises was
+# therefore redelivered at 60 minutes and a second worker began running the same
+# agent concurrently — the run's own record describing a bound the broker would
+# not let it reach.
+#
+# Deliberately NOT imported from eval_service: that module pulls ragas,
+# instructor and anthropic at import time, and celery_app is imported by every
+# task module and by the API process. The relation is pinned by a test instead —
+# tests/unit/test_eval_agent_invocation.py asserts this exceeds
+# AGENT_INVOCATION_MAX_CALLS_PER_RUN x AGENT_TURN_TIMEOUT_S, so the two cannot
+# drift apart silently the way a copied number would.
+BROKER_VISIBILITY_TIMEOUT_S = 7200
+
 # ---------------------------------------------------------------------------
 # Celery application instance
 # ---------------------------------------------------------------------------
@@ -158,8 +178,8 @@ celery_app.conf.update(
     # keepalive probes; socket_timeout causes a blocked socket op to raise after
     # 30 s so Kombu reconnects. retry_on_timeout retries BLPOP instead of
     # propagating the timeout exception. visibility_timeout must exceed the
-    # longest expected task runtime (provision + migrations can take ~60 s;
-    # 3600 s is a safe ceiling). On Windows, TCP_KEEPIDLE/INTVL/CNT are set
+    # longest expected task runtime — see BROKER_VISIBILITY_TIMEOUT_S above, which
+    # is no longer about provisioning. On Windows, TCP_KEEPIDLE/INTVL/CNT are set
     # at the OS level and socket_keepalive_options is ignored — but
     # socket_keepalive=True and retry_on_timeout=True still apply.
     broker_transport_options={
@@ -175,7 +195,7 @@ celery_app.conf.update(
             ]
             if k is not None
         },
-        "visibility_timeout": 3600,
+        "visibility_timeout": BROKER_VISIBILITY_TIMEOUT_S,
         "retry_on_timeout": True,
     },
 
diff --git a/apps/api/app/worker/tasks/runtime/deployment.py b/apps/api/app/worker/tasks/runtime/deployment.py
index b229cc6..89f7c5b 100644
--- a/apps/api/app/worker/tasks/runtime/deployment.py
+++ b/apps/api/app/worker/tasks/runtime/deployment.py
@@ -27,11 +27,15 @@ Flow (run_deployment_checklist):
        signals via psycopg2 against the tenant DB, the 5th signal —
        blast_radius — and the envelope hash both via get_sync_db() against
        the control DB)
-    4b. If the agent has never been evaluated, start its first eval suite
-       (_dispatch_first_eval_run) and record that on the eval signal. The
-       verdict is unaffected — an eval that is running is not evidence — but the
-       block becomes one the owner can wait out rather than a dead end no route
-       in the primary journey can clear.
+    4b. If the agent has never been evaluated, OR its last run recorded nothing
+       about whether the agent was invoked (audit D1 — every run stored before
+       that release), start an eval suite (_dispatch_eval_run) and record that
+       on the eval signal. The verdict is unaffected — an eval that is running
+       is not evidence — but the block becomes one the owner can wait out
+       rather than a dead end no route in the primary journey can clear. Not
+       dispatched for a run that recorded an explicit `false` or a failed run:
+       those states recur, so firing on them is a spend loop rather than
+       convergence.
     5. Call run_orchestrator(signals_json, result_container) via asyncio.run bridge
     6. Parse result, apply the deterministic evidence gate (P2 — an eval or
        red-team signal that is not 'measured' forces recommendation='block'
@@ -57,6 +61,7 @@ from app.models.agent import Agent
 from app.models.checklist_run import ChecklistRun
 from app.services.deployment_service import (
     BLAST_RADIUS_DEFAULT_SIGNAL,
+    EVAL_SIGNAL_AGENT_NOT_INVOKED,
     EVAL_SIGNAL_NO_RUNS,
     EVAL_SUMMARY_UNAVAILABLE_SIGNAL,
     RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL,
@@ -75,8 +80,8 @@ from app.worker.celery_app import celery_app
 log = structlog.get_logger(__name__)
 
 
-def _dispatch_first_eval_run(agent_id: str) -> bool:
-    """Start this agent's first eval suite. Returns True iff it was dispatched.
+def _dispatch_eval_run(agent_id: str) -> bool:
+    """Start an eval suite for this agent. Returns True iff it was dispatched.
 
     THE DAY-1 PATH HAD NO WAY TO PRODUCE AN EVAL RUN (P2 review). Making
     EVAL_SIGNAL_NO_RUNS hard-block is right — an agent with no measurement has
@@ -93,6 +98,15 @@ def _dispatch_first_eval_run(agent_id: str) -> bool:
     `block` — this run has no evidence and must not ship on the promise of some
     — but the block now converges instead of being terminal.
 
+    NO LONGER ONLY THE FIRST (P3 review), hence the rename from
+    `_dispatch_first_eval_run`. P3 made every EXISTING tenant block too, and
+    those agents are not in `no_runs` — they have runs, produced by the
+    tautology, which now report EVAL_SIGNAL_AGENT_NOT_INVOKED. The wall the
+    paragraph above describes had simply moved to the far larger population,
+    with the warning routing them to the same page the onboarding flow does not
+    reach. See the caller for which half of that state dispatches and why the
+    other half must not.
+
     `generate_eval_suite` runs first because a tenant whose scenario generation
     has never run has nothing to evaluate against; both tasks carry their own
     idempotency guards (>= 10 scenarios, and a 'running' run inside 10 minutes),
@@ -119,11 +133,11 @@ def _dispatch_first_eval_run(agent_id: str) -> bool:
             generate_eval_suite.si(agent_id),
             run_eval_suite.si(agent_id),
         ).apply_async(queue="runtime")
-        log.info("run_deployment_checklist.first_eval_dispatched", agent_id=agent_id)
+        log.info("run_deployment_checklist.eval_dispatched", agent_id=agent_id)
         return True
     except Exception as exc:
         log.warning(
-            "run_deployment_checklist.first_eval_dispatch_failed",
+            "run_deployment_checklist.eval_dispatch_failed",
             agent_id=agent_id,
             error=str(exc),
         )
@@ -241,8 +255,37 @@ def run_deployment_checklist(self, agent_id: str) -> dict:
     # page the onboarding flow does not route to. It does not soften the
     # verdict: apply_signal_evidence_gate still blocks on the signal, because a
     # measurement that has been STARTED is not a measurement.
-    if eval_summary.get("eval_signal") == EVAL_SIGNAL_NO_RUNS:
-        eval_summary["first_eval_dispatched"] = _dispatch_first_eval_run(agent_id)
+    #
+    # TWO STATES REACH IT NOW, AND ONLY ONE HALF OF THE SECOND (P3 review).
+    # P3 blocks every existing tenant, and none of them is in `no_runs` — they
+    # have runs, produced by the tautology, which report AGENT_NOT_INVOKED. So
+    # the convergence mechanism built for day 1 did not fire for the population
+    # P3 actually creates, and the warning routed them to the same unreachable
+    # page.
+    #
+    # But it fires only where it CONVERGES. `agent_invoked is None` is the
+    # historical population: a fresh run on a 0013+ tenant writes the key
+    # either way, so the state cannot recur and the dispatch is one-shot per
+    # agent, exactly like the day-1 case. `agent_invoked is False` is a run
+    # that looked and said no — a broken or unreachable agent produces it again
+    # every night — so dispatching on it would buy a fresh set of up to
+    # AGENT_INVOCATION_MAX_CALLS_PER_RUN live SDK turns on every readiness
+    # check the owner runs, and the state would still be False afterwards. That
+    # is not convergence, it is a spend loop with a button on it; the warning
+    # names the page instead. Same for `run_failed`, which repeats for the same
+    # reason. BACKLOG 2.18 carries the one residual: a pre-0013 tenant DB
+    # cannot record the key at all, so absence recurs there and the dispatch
+    # repeats.
+    #
+    # run_eval_suite's own idempotency guard (a 'running' run inside a window
+    # covering a full run) absorbs repeated readiness checks while one is in
+    # flight, so the bound here is one live run per agent, not one per click.
+    eval_signal = eval_summary.get("eval_signal")
+    if eval_signal == EVAL_SIGNAL_NO_RUNS or (
+        eval_signal == EVAL_SIGNAL_AGENT_NOT_INVOKED
+        and eval_summary.get("agent_invoked") is None
+    ):
+        eval_summary["eval_dispatched"] = _dispatch_eval_run(agent_id)
 
     try:
         red_team_summary = _fetch_red_team_summary_sync(agent_id, conn_str)
```

### 1.3 `apps/api/tests/` — SUMMARISED. 7,374 diff lines dropped.

**Nothing in `app/` was summarised.** These twelve test files are the only summarised code.

| file | +lines | -lines | added `def test_` | what it pins |
|---|---:|---:|---:|---|
| `tests/unit/test_agent_options_seam.py` | 1730 | 0 | 24 | **The drift guard — P1's whole deliverable.** Static half: an AST read of `agent.py` failing if `run_agent_turn` constructs `ClaudeAgentOptions`, a tool server or a system prompt; an alias graph (`opts = options`) so the options object may only reach `_tighten(options)`-shaped sinks; no attribute rebinding on the `agent` ORM row; no dynamic dispatch (`getattr(mod,"X")()`); a **repo-wide allowlist** of the four modules that may construct options. Dynamic half: the object `_run_sdk_turn` receives must be the seam's own; every kwarg the served options were built from is compared against an independently-built reference. Plus P1b's seam-mode guards and the two inverted canary guards. |
| `tests/unit/test_eval_agent_invocation.py` | 1878 | 0 | 41 | **P2's guards.** No scenario dict may carry the label as the prediction; the stored context column is pinned on the READ not the name; the eval may not name `run_agent_turn`; `EVAL_INVOKES_AGENT` pinned to a call site; the turn asks for `recorded`; the ceiling, the concurrency assert, the two caps, the broker/idempotency relations, the below-floor no-scores path. |
| `tests/unit/test_recorded_side_effects.py` | 1398 | 0 | 30 | **P1b + P1b-fix.** Recorded mode reaches no adapter for **any** of the six mutating skills (parametrized over the registry) and live mode does; `require_human` queues no `pending_confirmations` row; the replay arm returns no stored provider result; every declined outcome is recorded and its audit row marked; the rate counter is namespaced; `confirm_action` writes no row. |
| `tests/unit/test_deployment_service.py` | 805 | 10 | 35 | **P3 + P3-fix.** `TestAgentInvokedGate`, `TestAgentInvokedCollector`, `TestFailedRunIsNotEvidence`, `TestStoredRunEvidence`, `TestNarrowRowWidth`, plus the in-flight-run selector. |
| `tests/unit/test_eval_service.py` | 337 | 32 | 12 | `TestPromoteToVerifiedQA` (the label, not the agent's text), `TestTheSecondOrchestrator` (`run_eval_for_agent` refuses a tautology), `TestUpdateEvalRunConfig`, `TestBuildEvalRunConfig`. |
| `tests/unit/test_deployment_routes.py` | 187 | 2 | 7 | `TestApproveRefusesAnUninvokedRun` — the HIGH finding's fix at `POST /approve-deployment`. |
| `tests/unit/test_eval_task.py` | 129 | 14 | 1 | `test_a_failure_after_the_invocation_does_not_re_buy_sixty_sdk_turns`. |
| `tests/unit/test_deployment_task.py` | 118 | 3 | 6 | `TestExistingTenantEvalPath(TestEvidenceGateWiring)` — step 4b for the historical population, and the two "does not re-dispatch" bounds. |
| `tests/unit/test_retrieval_metrics.py` | 110 | 0 | 2 | recorded mode writes no `retrieval_metrics` row; live mode still does. |
| `tests/unit/test_decision_eval_service.py` | 62 | 9 | 1 | a recorded audit row is never scored as an Actor decision. |
| `tests/integration/test_prompt_versions_e2e.py` | 48 | 20 | 1 | updated for the new `_resolve_turn_prompt_version` signature. **`-m integration`. NEVER EXECUTED — see §5.** |
| `tests/unit/test_transactional_tools.py` | 25 | 7 | 0 | `test_actor_gate_called_before_get_adapter_in_dispatcher` rewritten from a fixed 22,000-char source window to `ast.unparse` of the dispatcher node. |

**Four test functions were DELETED, and each deletion is a behaviour claim the judge should check:**

```
- def test_resolve_turn_prompt_version_never_raises_on_bad_db(control_session):
      → replaced by test_resolve_turn_prompt_version_never_raises_on_bad_control_db
        (the function no longer takes tenant_conn)
- def test_config_says_the_agent_was_not_invoked(self, config_env):
      → replaced by test_config_at_insert_says_the_agent_has_not_been_invoked_yet
- def test_nothing_is_excluded_once_the_agent_is_invoked(
      → replaced by test_nothing_is_excluded_once_a_run_measured_an_invoked_agent
- def test_the_task_still_scores_the_reference_answer_against_itself(self):
      → DELETED OUTRIGHT. This was the test pinning D1. Its removal is correct
        (the behaviour it pinned is the defect being fixed) and it is the one
        deletion with no replacement of the same shape; the inverse pin is
        test_a_scored_row_never_carries_the_reference_answer_as_its_response.
```

### 1.4 `.dev/` — SUMMARISED. 2,886 diff lines dropped, all documentation.

Plan (165), workflow script (546), 6 traces (922), 4 mutation-proof references (961), BACKLOG
(+89/-13), HANDOFF (+50/-9). No code. Their content is quoted where load-bearing in §3–§5.

**Total dropped: 10,260 diff lines (7,374 test + 2,886 `.dev/`), out of 14,472 in the whole diff.
Total reproduced in full: 4,212 lines — every line of `app/`. Nothing was silently truncated.**

---

## 2. The gate

### 2.1 The verbatim final line, run by the collector on the final tree

```
1873 passed, 11 skipped, 30 warnings in 365.26s (0:06:05)
```

Command, exactly as CLAUDE.md specifies, run from `apps/api` on `feat/d1-agent-invocation` at
`a021118` with a clean working tree:

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py
```

Exit code 0. **0 failed.** This is the collector's own run, not a relayed figure.

### 2.2 Comparison to the 1675 / 11 / 0 baseline at `af0f601`

| | passed | skipped | failed | delta vs baseline |
|---|---:|---:|---:|---|
| `af0f601` (`main`), observed 2026-08-07 | 1675 | 11 | 0 | — |
| **`a021118` (branch HEAD), observed by the collector** | **1873** | **11** | **0** | **+198 passed, +0 skipped, +0 failed** |

**+198 passed. The skip count did not move; the fail count did not move.**

The per-commit ladder, as reported by each implementer (only the last line is the collector's own):

| commit | phase | passed | skipped | delta | observed by |
|---|---|---:|---:|---:|---|
| `af0f601` | main | 1675 | 11 | — | prior session |
| `ec5f445` | P1 | 1687 | 11 | +12 | P1 implementer |
| `d15be3a` | P1 fix | 1695 | 11 | +8 | P1 fixer |
| `487ebbe`+`117de05` | P1b | 1716 | 11 | +21 | P1b implementer |
| `df0a0b7` | P1b fix | 1766 | 11 | +50 | P1b fixer |
| `7a7486e` | P2 | 1795 | 11 | +29 | P2 implementer |
| `b62186f`/`075550d` | P2 fix | 1821 | 11 | +26 | P2 fixer |
| `5011f97` | P3 | 1839 | 11 | +18 | P3 impl **and** its reviewer, independently |
| `8b124d4`/`9106412` | P3 fix | 1873 | 11 | +34 | P3 fixer |
| `a021118` | final | **1873** | **11** | — | **the collector** |

Sum of the claimed deltas: 12+8+21+50+29+26+18+34 = **198**. 1675 + 198 = **1873**. The ladder is
arithmetically consistent with both endpoints, and both endpoints were observed. **The intermediate
rows were not re-derived by the collector** — checking them would require checking out eight commits
and running a six-minute suite eight times.

Two implementers additionally reported "the delta is exactly the tests added and nothing pre-existing
moved". P1 is the only one that proved it structurally: `ec5f445` ran the suite a second time with
the new file ignored and got `1675 passed, 11 skipped` twice. **No other phase reproduced that
control.**

Not re-run by the collector: `mypy app` (claimed clean at three separate commits) and
`uvx ruff@latest check app tests` (claimed clean; ruff is **not** installed in `apps/api/.venv`, so
every ruff claim on this branch depends on a network `uvx` fetch that leaves no artifact).

### 2.3 Two suite-hygiene facts the traces record and the judge should not read past

- **A source-reading guard raced an editor.** One intermediate P2 run reported `1 failed` —
  `test_promote_trace.py::test_the_scenario_is_inert_to_the_eval_selector_by_construction`, which
  reads `eval.py` from disk at test time and read it mid-write. The repo has several such guards.
  Recorded in `.dev/traces/260808-d1-p2-invoke.md`.
- **A file-selection order can produce 74 failures that are not real.** Running
  `test_recorded_side_effects.py` or `test_retrieval_metrics.py` **before** `test_transactional_tools.py`
  in a hand-picked subset yields `74 failed` with `'function' object has no attribute 'handler'` —
  those files install a passthrough `@tool` fake when no SDK is in `sys.modules`. Confirmed identical
  at `b7619fe`, i.e. **pre-existing**. Alphabetical full-suite order does not hit it.

---

## 3. Every implementer claim, and every mutation proof, verbatim

Eight reports. For each: what it claimed, what it deviated on, and its mutation evidence **quoted
exactly** from the file that holds it — or the statement that no such file exists.

### 3.1 P1 — the seam (`ec5f445`)

Claims, from the commit message:

> "everything that determines agent behaviour is now assembled in exactly one callable,
> build_agent_options: retrieval strategy, MCP tool server (which is where the capability envelope is
> enforced), system prompt, model, allowed tools, resume, max_turns and max_budget_usd.
> run_agent_turn calls it and does nothing else to the options it receives."

> "The seam lives in agent.py rather than a new service module so that every existing patch target
> (agent.ClaudeAgentOptions, agent.build_tool_server, agent.build_system_prompt) keeps resolving. Not
> a convenience: it means the 1675 pre-existing unit tests are byte-identical, so their passing is
> evidence that the chat path did not change, rather than evidence that its tests were rewritten
> alongside it."

> "One ordering change: _resolve_turn_prompt_version now runs before the tool server is built instead
> of after. […] The two are order-independent — the resolver takes the control-DB session and the
> tenant connection explicitly and reads none of the ContextVars build_tool_server sets."

> "Observed, gate command from apps/api:
>   before  1675 passed, 11 skipped in 302.77s
>   after   1687 passed, 11 skipped in 334.78s
>   after, ignoring the new file: 1675 passed, 11 skipped (twice, 298s and 324s)
> so the whole delta is the 12 new guards and no pre-existing test changed status."

**Mutation proofs: NONE ON DISK.** The commit message says only *"12 mutations, each observed red and
then green."* No verbatim output, no mutation table, no `.dev/reference/p1-mutation-proofs.md`. This
is the single largest evidentiary gap on the branch, and it covers the phase the plan calls
*"the whole bet"*.

### 3.2 P1 fix — the drift class the guard only spelled (`d15be3a`)

Claims, from the commit message:

> "Tier-2 probed P1's guard with eight realistic drift edits. **Seven left all 12 guards green; the
> eighth went red only because the sentinel lacked the attribute, not because anything evaluated
> capability drift.** The guard recognised one spelling of one mutation, in one file."

(The "Tier-2" attribution here is the misattribution `9d81e34` later corrects. It was tier-1.)

> "new: a repo-wide allowlist for ClaudeAgentOptions construction. eval.py is not on it, which is what
> makes approach (b) structural rather than intended"

> "new VALUE guard. The turn runs through the real seam and every kwarg the served options were built
> from is compared against an options object built after the turn from a pristine copy of the same
> agent spec. It pins the property rather than the spelling: alias-then-mutate, a module-level helper,
> a mutated agent row, a dropped soul_override, a blanked verified_session_token and
> allowed_tools.append all fall out of it with no rule written for any of them"

> "The value guard uses a recording stand-in for ClaudeAgentOptions rather than the SDK class:
> test_agent_task.py installs a fake claude_agent_sdk into sys.modules and never removes it, so which
> class you get depends on collection order. Observed directly — the test passed alone and raised
> 'isinstance() arg 2 must be a type' when run after that module."

> "agent.py is comments only. 'Order-independent' is downgraded to 'ContextVar-independent' with the
> failure-path consequence stated, and the seam header now discloses that it hands every caller a live
> tool server that writes retrieval_metrics and tool_calls_audit and can call issue_refund for real
> (BACKLOG 2.5 — blocks P2)."

> "12 mutations, each observed red and then green.
> Gate: 1695 passed, 11 skipped, 28 warnings in 328.97s (0:05:28)"

**Mutation proofs: NONE ON DISK.** Same gap as P1: twelve claimed, zero recorded.

### 3.3 P1b — recorded mode + canary write order (`487ebbe`, `117de05`)

Trace: `.dev/traces/260807-d1-p1b-recorded-mode.md`. Gate, quoted verbatim from the trace:

| | |
|---|---|
| baseline at `9d81e34` | `1695 passed, 11 skipped, 28 warnings in 390.08s (0:06:30)` |
| after `487ebbe` | `1716 passed, 11 skipped, 30 warnings in 374.22s (0:06:14)` |
| after `117de05` (final) | `1716 passed, 11 skipped, 28 warnings in 352.60s (0:05:52)` |

Claims:

> "`side_effects: Literal["live", "recorded"]`, **mandatory, no default**. `live` is the chat path
> unchanged. `recorded` swaps exactly the three edges the decision named:
> `notify_fn` → recorded, no mail; retrieval-metrics writer → recorded, no row; transactional
> `ProviderAdapter` → recorded, no provider call."

> "Nothing the agent can **see** or **choose** differs — all eleven tools, the same system prompt, the
> same capability envelope, IDV gate, rate ceiling and Actor seam."

> "**Unmissable, never a silent success.** `is_error: True`, text beginning `NOT EXECUTED:`, and none
> of the adapter's output […] A first draft of that guard banned cheerful *words*; it flagged the
> honest sentence 'no money moved', so it now bans the adapter's *artefacts* instead."

> "**Where the branch lives.** Step 5.5 of `_execute_transactional_tool`, after the Actor gate, **not**
> inside `_execute_adapter_and_audit`."

> "**'TWO changes to `agent.py`' was not achievable literally.** The metrics writer lives in
> `agent_tools.retrieve_tool` and the adapter in `transactional/tools.py` […] Both were changed."

> "**`build_tool_server` takes the mode WITH a `"live"` default**, unlike the seam. Its pre-existing
> callers are `red_team.py` and `red_team_probe.py`, which must read real dispatcher verdict tags."

**Mutation proofs: `.dev/reference/p1b-mutation-proofs.md`, 21 guards, verbatim:**

```
M1 seam side_effects gains a default
  RED:   1 failed in 15.46s
  GREEN: 1 passed in 10.33s
M2 seam drops the unknown-mode ValueError
  RED:   1 failed in 8.95s
  GREEN: 1 passed in 8.61s
M3 chat path asks for recorded side effects
  RED:   1 failed in 23.42s
  GREEN: 1 passed in 26.58s
M4 seam always wires the real escalation mail
  RED:   1 failed in 10.79s
  GREEN: 1 passed in 11.41s
M5 recorded mode strips issue_refund from allowed_tools
  RED:   1 failed in 11.68s
  GREEN: 1 passed in 10.56s
M6 seam accepts the mode and hardcodes live into the tool server
  RED:   1 failed, 1 passed in 10.66s
  GREEN: 2 passed in 9.67s
M7 canary write deleted entirely
  RED:   1 failed in 24.97s
  GREEN: 1 passed in 23.89s
M8 canary write moved back ahead of the options build (the P1 behaviour)
  RED:   2 failed in 24.11s
  GREEN: 2 passed in 25.75s
M9 dispatcher never takes the recorded branch
  RED:   1 failed in 8.89s
  GREEN: 1 passed in 7.37s
M10 dispatcher always takes the recorded branch
  RED:   1 failed in 7.29s
  GREEN: 1 passed in 7.24s
M11 recorded branch suppresses but does not record
  RED:   1 failed in 7.42s
  GREEN: 1 passed in 7.19s
M12 recorded audit row is not marked as recorded
  RED:   1 failed in 7.13s
  GREEN: 1 passed in 7.19s
M13 recorded branch strands the idempotency reservation
  RED:   1 failed in 7.40s
  GREEN: 1 passed in 7.25s
M14 recorded return is a cheerful confirmation
  RED:   1 failed in 7.27s
  GREEN: 1 passed in 7.23s
M15 recorded branch moved into the shared resolver helper
  RED:   1 failed in 0.80s
  GREEN: 1 passed in 0.79s
M16 retrieve always writes its metrics row
  RED:   1 failed in 0.85s
  GREEN: 1 passed in 0.79s
M17 retrieve never writes its metrics row
  RED:   1 failed in 0.78s
  GREEN: 1 passed in 0.80s
M18 build_tool_server does not publish the mode
  RED:   1 failed in 7.19s
  GREEN: 1 passed in 7.26s
M19 build_tool_server does not reset the recording sink
  RED:   1 failed in 7.17s
  GREEN: 1 passed in 7.59s
M20 build_tool_server accepts an unknown mode
  RED:   1 failed in 7.12s
  GREEN: 1 passed in 0.73s
M21 build_tool_server defaults to recorded
  RED:   1 failed in 7.09s
  GREEN: 1 passed in 7.60s
```

**ONE OF THE 21 DID NOT GO RED**, self-reported, first run against `487ebbe`:

```
M2 seam drops the unknown-mode ValueError
  RED:   1 passed in 10.57s
  GREEN: 1 passed in 10.02s
```

> "`test_the_seam_rejects_a_mode_it_does_not_implement` called the REAL `build_tool_server`, which
> carries the same check one layer down and raises a `ValueError` whose message also contains
> 'side_effects'. The test was demonstrating the tool layer's guard while claiming to demonstrate the
> seam's […] Fixed in `117de05` […] The M2 lines above are the **post-fix** re-run."

The two inverted canary guards, observed red against the **previous commit** rather than an injected
mutation:

```
E  AssertionError: the canary choice was committed even though the options build failed
   (call_count=1). The conversation is now sticky to a prompt version that never served a turn,
   and the Celery retry can no longer re-roll it. ...
   assert 1 == 0
    +  where 1 = <MagicMock name='_set_prompt_version_id'>.call_count

E  AssertionError: the commit did not follow the seam call (order=['commit', 'seam', 'sdk_turn']).
   Committing first is exactly the P1 behaviour BACKLOG 2.6 settled against ...
   assert 0 > 1

FAILED tests/unit/test_agent_options_seam.py::test_the_canary_choice_is_not_committed_when_the_options_build_fails
FAILED tests/unit/test_agent_options_seam.py::test_the_canary_choice_is_committed_once_the_options_exist
2 failed, 19 deselected in 29.94s
```

And one guard caught vacuous **before** it was trusted:

> "The first refund fixture used `amount_cents` where `IssueRefundInput` declares
> `refund_amount_cents`, so `issue_refund_tool` returned a `ValidationError` before the dispatcher was
> ever entered — and `test_recorded_mode_never_reaches_the_provider_adapter` was green with the adapter
> never called for a reason that had nothing to do with recorded mode. Its live-mode partner failed and
> exposed it."

### 3.4 P1b fix — the eval's remaining live edges (`0580ea8`, `df0a0b7`)

Trace: `.dev/traces/260807-d1-p1b-tier2-fixes.md`. Gate, verbatim:

```
before: 1716 passed, 11 skipped, 28 warnings in 351.56s (0:05:51)   (at b7619fe)
after:  1766 passed, 11 skipped, 28 warnings in 404.69s (0:06:44)   (at df0a0b7)
```

> "The intermediate run at `0580ea8` was `1 failed, 1762 passed` — the failure was
> `test_the_ast_walk_actually_finds_the_dispatcher_vocabulary`, an existing guard catching the marker
> wrapper, and it is the reason the decision-eval work in `df0a0b7` exists."

> "Lint/types after: `ruff … app/ tests/` → `All checks passed!`; `mypy app/` → `Success: no issues
> found in 132 source files`."

Claims — **two doors to a live ProviderAdapter that step 5.5 could not see:**

> "1. `require_human` (tools.py step 5) returns before 5.5 and wrote a `pending_confirmations` row with
> the agent's full arguments. That row is not inert: `_is_confirm_action_shaped` does not filter it (it
> carries `idempotency_key`, never `action_reference`), it appears in the owner's approval queue
> unmarked, and approving it runs `resolve_confirmation_task → execute_approved_confirmation →
> _execute_adapter_and_audit → get_adapter_for_skill` — a real Stripe/Shopify/Woo/Calendly call. **A
> nightly eval scenario provoking a large refund queued a real refund for the owner to approve.**"

> "2. The step-3 idempotency `replay` arm returned a previously stored **real** provider result.
> `idempotency_key` is model-supplied on every mutating Input model and models produce deterministic
> keys, so an eval scenario mined from a production conversation could collide with a completed call
> and read 'Refund of R45.00 issued'."

Three more edges:

> "3. `escalate_to_human_tool` still committed `UPDATE conversations … escalated=true` against the
> tenant DB. Only the mail was swapped, at the seam. **The existing guard called the seam's `notify_fn`
> closure directly and never entered the tool, so it could not see the UPDATE.**"
> "4. Step 4 INCR'd `ratelimit:{agent}:{skill}:{window}` — no mode dimension — so an overnight eval
> consumed the tenant's real per-skill budget and the next real customer refund read `rate_limit`."
> "5. `confirm_action_tool` does not use the dispatcher and wrote a real row on every eval scenario in
> which the agent chose to ask for approval."

> "**Measurement that could not move.** `record_suppressed_side_effect` had three call sites and not one
> was on a denial, block, IDV-refusal or `require_human` path, so `get_recorded_side_effects()` omitted
> the entire *refused* column of the audit's confusion matrix."

> "**Fidelity.** The recorded tool result told the agent, in its own context, that it was running in
> evaluation mode and instructed it not to tell the customer the action completed. Every token after
> that was produced by an evaluation-aware agent, and Faithfulness/AnswerRelevancy score exactly those
> tokens."

> "**Stated as a tradeoff:** a human reading only a transcript can no longer distinguish a recorded call
> from a real provider outage; they must read the audit row."

**Mutation proofs: 18 CLAIMED, NOT ON DISK.** The trace says:

> "18 guards, each mutated, run red, restored with `git checkout HEAD -- <file>`, run green. Observed
> output recorded in the phase report rather than paraphrased."

The phase report is the lost workflow journal. Two of the eighteen are described in prose and are the
only surviving evidence:

> "**M8 was incomplete and said so.** The bulk replacement reached 6 of the declined-recording sites and
> not the `in_progress` one; the `in_progress` case passed through a mutation meant to kill it. M10 was
> added to mutate that site specifically, and it went red."
> "**The first draft of the sticky-mode test passed for no reason.** `build_agent_options` was not
> imported in that scope, `pytest.raises(Exception)` swallowed the `NameError`, and the seam was never
> called. Caught because the assertion still failed; fixed by matching the expected exception by message
> rather than by `Exception`."

### 3.5 P2 — the eval invokes the agent (`d127b4d`, `7a7486e`)

Trace: `.dev/traces/260808-d1-p2-invoke.md`. Gate, verbatim:

```
before:  1766 passed, 11 skipped, 28 warnings in 361.63s (0:06:01)   # clean, at 1d3a7bd, work stashed
after:   1795 passed, 11 skipped, 30 warnings in 351.02s (0:05:51)
```

Claims:

> "**`agent_invoked` is an observation, written twice.** The `eval_runs` row is also the per-agent
> idempotency key (`m6:{agent_id}`, 10-minute window), so it must exist before the first of sixty SDK
> turns […] It is therefore inserted carrying `agent_invoked=false` and corrected by
> `update_eval_run_config` once the invocation has reported. Every failure between the two leaves the
> run claiming **less** than it did."

> "**`agent_invoked` is a conjunction, deliberately.** It means *the scored responses came from real
> agent turns* **and** *enough rows answered to be a measurement*."

> "**Failure is exclusion, not a zero.** […] `compute_correlation.py:485` learned this about a judge
> that errors; this is the same rule one layer earlier."

> "**The truncation, made explicit rather than fixed.** […] **recording** was chosen because the
> alternative changes `_run_sdk_turn` on the chat path."

> "**The canary is not re-rolled.** […] Passing `soul_override=None` would have served the agent's live
> `soul_*` columns while `eval_runs.prompt_version_id` still named the production version — BACKLOG
> 2.3's defect exactly."

> "**The branch is still unused, and that is not an oversight.** The turns run against the tenant's
> PRODUCTION connection string because `retrieve` has to see the corpus the customer is served. What
> stops them writing is **recorded mode**, not the branch."

> "**The PII firewall is NOT applied on the eval path**, and the run says so."

> "**Cost.** `AGENT_INVOCATION_CONCURRENCY = 1` […] and the loop **raises** if the constant moves
> without it. `AGENT_INVOCATION_MAX_CALLS_PER_RUN = 60`; worst case 60 × 90 s."

Deviations, self-reported:

> "1. **`eval_service.py` was modified.** The plan's scope names `eval.py`, `agent.py` (extraction
> only), `deployment_service.py` and a migration.
>  2. **No `alembic_tenant` revision.** `agent_invoked` lives inside the `config` jsonb column that
> migration 0013 already added, so P2 needs no schema change. If P3 wants a first-class column, that is
> P3's migration.
>  3. **The BACKLOG rows for 2.1/2.3 are deleted in the follow-on commit, not the one that landed the
> fix.**"

**Mutation proofs: `.dev/reference/p2-mutation-proofs.md`, 19 run (M19 folded into M20), verbatim:**

```
M1  side_effects="recorded" -> "live"
RED   : FAILED …::test_the_eval_path_never_asks_for_live_side_effects | FAILED …::test_the_turn_goes_through_the_seam_and_asks_for_recorded_side_effects | 2 failed, 21 passed in 20.47s
GREEN : 23 passed in 18.30s

M2  re-add "agent_response": row[3] to the fetched scenario dict
RED   : FAILED …::test_no_scenario_dict_in_the_eval_can_carry_the_label_as_the_prediction | 1 failed, 22 passed in 21.46s
GREEN : 23 passed in 18.72s

M3  "agent_response": response_text -> scenario["reference_answer"]
RED   : FAILED …::test_no_scenario_dict_in_the_eval_can_carry_the_label_as_the_prediction | FAILED …::test_a_scored_row_never_carries_the_reference_answer_as_its_response | FAILED …::test_the_task_hands_the_scorer_agent_responses_and_agent_contexts | 3 failed, 20 passed in 20.92s
GREEN : 23 passed in 19.48s

M4  "retrieved_contexts": contexts -> scenario["stored_retrieved_contexts"]
RED   : FAILED …::test_the_contexts_scored_are_the_ones_the_agent_retrieved | FAILED …::test_the_task_hands_the_scorer_agent_responses_and_agent_contexts | 2 failed, 21 passed in 21.23s
GREEN : 23 passed in 18.25s

M5  if response_text.strip(): -> if True:
RED   : FAILED …::test_a_turn_that_returns_no_text_is_counted_apart_from_one_that_raised | 1 failed, 22 passed in 20.64s
GREEN : 23 passed in 17.97s

M6  score a FAILED scenario instead of excluding it
RED   : FAILED …::test_a_failing_scenario_is_excluded_and_counted_never_scored_zero | FAILED …::test_a_run_below_the_response_rate_floor_reports_unknown | 2 failed, 21 passed in 20.16s
GREEN : 23 passed in 18.15s

M7  status is always 'measured'
RED   : FAILED …::test_a_run_below_the_response_rate_floor_reports_unknown | FAILED …::test_a_run_that_invoked_nothing_is_unknown_not_measured | 2 failed, 126 passed in 21.21s
GREEN : 128 passed in 18.01s

M8  agent_invoked drops the conjunction
RED   : FAILED …::test_a_run_below_the_response_rate_floor_reports_unknown | FAILED tests/unit/test_eval_service.py::TestBuildEvalRunConfig::test_a_run_below_the_floor_is_not_certified_even_though_it_invoked | 2 failed, 126 passed in 20.51s
GREEN : 128 passed in 18.00s

M9  finally: reset_side_effect_context() -> finally: pass
RED   : FAILED …::test_the_side_effect_mode_is_returned_to_live_when_the_loop_ends | 1 failed, 22 passed in 20.76s
GREEN : 23 passed in 20.59s

M10 per-run ceiling removed
RED   : FAILED …::test_the_per_run_ceiling_bounds_the_calls_and_says_what_it_skipped | 1 failed, 22 passed in 20.36s
GREEN : 23 passed in 18.84s

M11 provenance never written
RED   : FAILED …::test_the_run_records_that_the_agent_was_invoked | FAILED …::test_the_row_exists_before_the_first_turn_and_is_corrected_after_the_last | 2 failed, 21 passed in 20.85s
GREEN : 23 passed in 18.12s

M12 provenance patched AFTER scoring
RED   : FAILED …::test_the_row_exists_before_the_first_turn_and_is_corrected_after_the_last | 1 failed, 22 passed in 26.96s
GREEN : 23 passed in 26.50s

M13 a second copy of the retrieve cap  — FIRST ATTEMPT: THE GUARD DID NOT FAIL
RED   : 23 passed in 24.28s
GREEN : 23 passed in 25.06s
  … rewritten as test_the_turn_bounds_are_read_from_one_copy_of_the_number, re-run:
RED   : FAILED …::test_the_turn_bounds_are_read_from_one_copy_of_the_number[RETRIEVE_RESULT_CAPTURE_CHARS] | 1 failed, 23 passed in 27.13s
GREEN : 24 passed in 18.95s

M13b a second copy of the turn timeout
RED   : FAILED …::test_the_turn_bounds_are_read_from_one_copy_of_the_number[AGENT_TURN_TIMEOUT_S] | 1 failed, 23 passed in 19.93s
GREEN : 24 passed in 18.79s

M14 concurrency guard removed
RED   : FAILED …::test_the_loop_refuses_to_run_if_the_concurrency_bound_moves_without_it | 1 failed, 22 passed in 25.65s
GREEN : 23 passed in 19.73s

M15 stored column bound to the name the scorer reads
RED   : FAILED …::test_the_stored_context_column_is_not_named_what_the_scorer_reads | FAILED …::test_the_task_hands_the_scorer_agent_responses_and_agent_contexts | 2 failed, 22 passed in 19.70s
GREEN : 24 passed in 18.57s

M16 eval turn drops the prompt version's soul fields
RED   : FAILED …::test_the_turn_serves_the_prompt_version_the_run_is_attributed_to | 1 failed, 23 passed in 25.14s
GREEN : 24 passed in 18.39s

M17 eval turn handed a session-shaped event sink
RED   : FAILED …::test_the_eval_turn_writes_no_job_events | 1 failed, 23 passed in 19.97s
GREEN : 24 passed in 18.21s

M18 only the first scenario is invoked
RED   : 9 failed, 15 passed in 20.72s
GREEN : 24 passed in 17.67s

M20 recorded attempts read only on the success path
RED   : FAILED …::test_an_attempt_made_by_a_scenario_that_then_failed_is_still_recorded | 1 failed, 23 passed in 20.34s
GREEN : 24 passed in 17.62s

M21 a context exactly at the cap is not reported as truncated
RED   : FAILED …::test_the_bounds_the_run_ran_under_are_on_the_run | 1 failed, 23 passed in 20.17s
GREEN : 24 passed in 17.58s
```

Self-reported exclusion, quoted:

> "**Not mutated, and therefore not proven:** `M19` was folded into `M20` […]; the three
> `TestUpdateEvalRunConfig` cases in `test_eval_service.py` were written against a cursor double and
> their SQL has never executed against a database."

And the self-caught tautology, quoted from the trace:

> "`test_the_retrieve_cap_is_read_from_the_turn_path_not_copied` read `inspect.getsource(_run_sdk_turn)`
> and asserted the constant's NAME appeared and `[:1800]` did not. Mutating the slice back to a literal
> left it **green**: the name appears in the comment above the slice, and a reformatted literal is not
> the substring `[:1800]`. **Both halves were satisfied by prose.**"

### 3.6 P2 fix — 17 findings (`b62186f`, `075550d`)

Trace: `.dev/traces/260808-d1-p2-review-fixes.md`. Gate, verbatim:

```
before:  1795 passed, 11 skipped, 30 warnings in 370.91s (0:06:10)   # 7a7486e, the reviewer's own run
after:   1821 passed, 11 skipped, 30 warnings in 369.36s (0:06:09)
```
```
1820 passed, 11 skipped, 26 warnings in 371.38s (0:06:11)     # b62186f
1821 passed, 11 skipped, 30 warnings in 369.36s (0:06:09)     # final tree
```

The four that change what a run means, quoted:

> "**A below-floor run no longer produces a shippable signal.** 'Reports `unknown`, never `pass`' was
> true of `config["agent_invocation"]["status"]` and `config["agent_invoked"]`, and nothing outside
> `eval_service` reads either. **Everything a consumer does read reported a pass**: 2 surviving rows of
> a 2-of-40 run were scored, `write_eval_results` wrote them, `update_eval_run_status` marked the run
> `complete`, and `_fetch_eval_summary_sync` built a non-empty `pass_rates` and returned
> `EVAL_SIGNAL_MEASURED`."

> "**The judge was being shown a repr.** `tool_calls_log[*]["result"]` is `str(block.content)[:1800]` — a
> repr of `[{'type':'text','text': "<<<HEADER>>>\n[{'chunk_id': ...` cut mid-structure, handed to Ragas
> as ONE element. Three failures at once: dict-syntax noise the metric cannot distinguish from evidence;
> a cut below one full retrieval (5 chunks x up to 2000 chars against an 1800 cap); and a single-element
> context list, which leaves ContextPrecision nothing to rank."

> "**A run could not reach the bound it advertises.** Every run stamps `max_wall_clock_s = 5400`;
> `visibility_timeout` was 3600 and the idempotency window 600. A run consuming its stated ceiling was
> redelivered at 60 minutes and a second worker drove the same agent concurrently, with the guard in
> place and unable to fire. […] And a failure **after** the invocation no longer retries: `max_retries=2`
> meant one judge outage bought a second and third full set of live SDK turns."

> "**Zero is not a low score, one metric over.** A responded turn with zero retrieve calls was scored with
> `retrieved_contexts=[]`, so Faithfulness / ContextPrecision / ContextRecall were structurally 0 or NaN
> for an answer the agent gave correctly from its system prompt — and the gate's 'any pass_rate < 0.70'
> fires on it."

**Guards the review proved were not guards** — quoted in full, because this is the highest-value
paragraph on the branch:

> "- `stored_retrieved_contexts` is pinned on the **read**, not on the name. The reviewer ran the
>   mutation the implementer did not: `contexts or scenario["stored_retrieved_contexts"]` **left all 163
>   tests green**, because the name check inspects dicts carrying a `reference_answer` key and the scored
>   row builds its fields with `**scenario`, and because every dynamic test supplied a non-empty retrieve
>   result so the fallback never fired.
> - `eval.py` may not name `run_agent_turn` at all. The side-effects guard enumerates
>   `build_agent_options` call sites **inside eval.py** and is blind to any other route to a live turn.
> - `EVAL_INVOKES_AGENT` is pinned to a call site again, both directions. **It was flipped to `True` in
>   the same commit that deleted its only pin.**
> - The eval's **import** of each turn bound is asserted. The old guard read agent.py only; a local
>   `AGENT_TURN_TIMEOUT_S = 90` in eval.py left it green, and the provenance test compared 90 to 90.
> - `emit()` is **run** through `_EvalEventSink` rather than poked method by method.
> - The one-copy guard matches **bound-consuming syntax** rather than a bare integer."

**Mutation proofs: `.dev/reference/p2-review-mutation-proofs.md`, 23 guards, verbatim (name → RED → GREEN):**

```
stored-context-fallback                       RED 1 failed in 19.47s   GREEN 1 passed in 12.75s
eval-dispatches-the-chat-task                 RED 1 failed in 14.58s   GREEN 1 passed in 12.33s
eval-invokes-agent-constant-lies              RED 1 failed in 15.07s   GREEN 1 passed in 13.24s
no-retrieval-row-is-scored-anyway             RED 1 failed in 20.80s   GREEN 1 passed in 17.87s
unparsed-counted-as-no-retrieval              RED 1 failed in 20.07s   GREEN 1 passed in 17.77s
absolute-floor-removed                        RED 1 failed in 20.22s   GREEN 1 passed in 17.87s
coverage-divides-by-attempted                 RED 1 failed in 20.18s   GREEN 1 passed in 17.86s
below-floor-run-scores-anyway                 RED 1 failed in 20.20s   GREEN 1 passed in 17.88s
scored-source-from-attempted                  RED 1 failed in 20.88s   GREEN 1 passed in 20.07s
sink-not-emptied-per-iteration                RED 1 failed in 20.27s   GREEN 1 passed in 18.12s
eval-keeps-its-own-copy-of-the-timeout        RED 1 failed, 1 passed in 20.33s   GREEN 2 passed in 18.30s
visibility-timeout-below-the-ceiling          RED 1 failed in 20.23s   GREEN 1 passed in 17.68s
idempotency-window-back-to-ten-minutes        RED 1 failed in 15.27s   GREEN 1 passed in 17.74s
gate-reads-the-in-flight-run                  RED 1 failed in 2.66s    GREEN 1 passed in 2.02s
promotion-writes-the-agents-own-answer        RED 1 failed in 13.23s   GREEN 1 passed in 12.27s
blank-label-promoted                          RED 1 failed in 14.50s   GREEN 1 passed in 11.34s
second-orchestrator-accepts-the-tautology     RED 3 failed in 19.07s   GREEN 3 passed in 11.02s
chunks-flattened-back-into-one-blob           RED 1 failed in 20.28s   GREEN 1 passed in 17.45s
unreadable-payload-reported-as-empty          RED 1 failed in 20.06s   GREEN 1 passed in 17.71s
emit-gains-a-flush                            RED 1 failed in 20.11s   GREEN 1 passed in 17.56s
retry-re-buys-the-invocation                  RED 1 failed in 19.89s   GREEN 1 passed in 18.91s
second-copy-of-the-turn-timeout               RED 1 failed, 1 passed in 19.96s   GREEN 2 passed in 17.83s
at-cap-measured-against-the-audit-capture     RED 1 failed, 2 passed in 20.98s   GREEN 3 passed in 18.13s
```

**ONE DID NOT GO RED FIRST TIME**, self-reported:

> "`at-cap-measured-against-the-audit-capture` passed 2/2 against both cap tests as first written.
> Neither fixture separated the two caps: for a single short chunk the audit repr is short too, and for
> a 2000-char chunk both caps trip. `075550d` adds the production shape — three 700-char chunks, whose
> repr exceeds the 1800 audit cap while every chunk is whole — and the mutation goes red. **Recorded
> rather than quietly fixed: it is the same defect class as `7a7486e`'s self-caught tautology, twice on
> one branch.**"

### 3.7 P3 — the gate (`5011f97`)

Trace: `.dev/traces/260808-d1-p3-gate.md`, **written after the fact and saying so**:

> "**Written after the fact (2026-08-08).** P3 shipped without a trace; the tier-2 read of it filed that
> as a finding, correctly — CLAUDE.md's 'no task is done without its trace' was outstanding, and the
> mutation proofs, the two deviations and the BACKLOG transaction existed only in the commit message and
> in a session transcript that does not survive."

Gate, verbatim:

```
before:  1821 passed, 11 skipped   # 65eab9e — implementer's run, NOT reproduced here
after:   1839 passed, 11 skipped, 28 warnings in 400.25s   # tier-2's own run at 5011f97
         1839 passed, 11 skipped, 28 warnings in 384.06s (0:06:24)   # reproduced, this session
```

> "The `after` figure is observed twice, independently. The `before` figure would need a checkout of
> `65eab9e` and was not reproduced; it is corroborated only by the diff adding exactly 18 `def test_`
> lines and no skip markers."

Claims:

> "**`_fetch_eval_summary_sync` gained a fifth state.** […] returns `EVAL_SIGNAL_AGENT_NOT_INVOKED` when
> the claim is anything other than `True`, with `pass_rates` suppressed. Suppression matters more here
> than in the other four states because this is the only one whose scores EXIST."
> "**`apply_signal_evidence_gate` gained a fifth refusal**, `agent_invoked is not True`."
> "**Absent is refused exactly as false is** (settled by the owner, 2026-08-07)."
> "**Accepted cost, live from this commit:** every pre-D1 run, and every tenant DB older than
> `alembic_tenant` 0013 (no `config` column at all), fails closed until that DB is re-migrated and a
> fresh eval runs."
> "**No migration, and that is a finding rather than an omission.** […] `0015` remains the tenant head;
> `git diff 65eab9e HEAD -- apps/api/alembic_tenant/` is empty."

**Mutation proofs — P3's commit message claims SIX. Four were reproduced later; two were not, and
the two that were not are the two whose conclusion was rejected:**

| mutation | reproduced | observed |
|---|---|---|
| gate `is not True` → `is None` | yes | `5 failed, 7 passed` / green `12 passed` |
| gate `is not True` → `is False` | yes | `5 failed, 7 passed` / green `12 passed` |
| gate `is not True` → `is not None` | yes | `5 failed, 7 passed` / green `12 passed` |
| collector `is not True` → `is False` | yes, narrower selector | `1 failed` / green `1 passed` |
| both score-suppression layers together | **no** | **implementer self-report only** |
| ordering, `and pass_rates` | **no** | **implementer self-report only** |

> "**The two not reproduced are the two whose conclusion the tier-2 rejected**, and the rejection
> stands. P3's message says 'the scores are suppressed twice — structurally and by state' and cites a
> mutation that required breaking both. The same shape was executed exhaustively on the sibling state
> added by the review fixes: neither layer alone produces a red […] That is one falsifiable property
> with no single load-bearing layer, not two independent defences."

### 3.8 P3 fix — 11 findings, 6 unsupported claims (`8b124d4`, `9106412`)

Trace: `.dev/traces/260808-d1-p3-review-fixes.md`. Gate, verbatim:

```
before:  1839 passed, 11 skipped, 28 warnings in 384.06s (0:06:24)   # 5011f97, clean tree
after:   1873 passed, 11 skipped, 30 warnings in 369.98s (0:06:09)   # 8b124d4
final:   1873 passed, 11 skipped, 30 warnings in 358.80s (0:05:58)   # whole branch, final tree
```

The four that change behaviour, quoted:

> "**The gate never reached the artifact the approve route reads.** This is the whole of the high
> finding and it is the phase's own deliverable landing one layer short. `apply_signal_evidence_gate`
> has exactly one caller — the checklist Celery task — and `agent.is_deployed` has exactly one writer:
> `POST /approve-deployment` […] `recommendation` is FROZEN by whatever gate ran the day the row was
> written, so refusing an uninvoked eval at checklist time closed nothing for the runs that already
> exist: complete, 'ship', warnings inapplicable, envelope hash unmoved, `{"deployed": true}`."

> "**The owner-facing message narrated a cause it did not observe** — in the phase whose subject is
> exactly that. […] every owner in that state was told their check 'scored a set of pre-written model
> answers' and that the new numbers would be 'lower than the old ones', of which there are none. The
> console renders nothing else — a grep of `apps/admin` for `agent_invoked` or `eval_signal` returns
> nothing — so that sentence IS the owner-visible account."

> "**A run recorded as `failed` was still evidence, and P2's ordering makes that shape ordinary.** The
> invocation claim is patched into `eval_runs.config` BEFORE scoring (`eval.py:1082`) […]
> `summarise_run_validity` runs at `:1155`, one line AFTER `update_eval_run_status('complete')`, and
> anything raising from there lands in the `except` whose `_mark_failed_on_production` writes
> `status='failed'` over a row already carrying `agent_invoked=true` and a full set of high pass_rates.
> The collector returned `EVAL_SIGNAL_MEASURED` and the gate shipped it."

> "**The convergence mechanism did not fire for the population P3 creates.** Step 4b dispatched only on
> `EVAL_SIGNAL_NO_RUNS`, and no existing tenant is in that state."

**Three claims corrected rather than defended** — quoted:

> "- **'Two independent points, and both are needed.'** They are not, today. The collector is the only
>   producer of a 'measured' payload in the tree, and the reviewer reproduced that neutering the gate arm
>   alone leaves every collector test green.
> - **'The prompt was updated so the narration cannot contradict the verdict.'** Nothing in the repo
>   executes `run_orchestrator` (BACKLOG 3.10), so no test observes the model obeying any prose
>   condition. What constrains the narration is the suppression.
> - **'The scores are suppressed twice, structurally and by state.'** Executed exhaustively on the
>   sibling state: structural-only → `6 passed`, state-only → `6 passed`, both → `1 failed`."

> "**Test-double fidelity.** `_make_eval_conn` padded a 3-tuple to four elements before the double was
> built, so the pre-0013 test — whose whole subject is that the WIDE select raises and the NARROW
> three-column one answers — got a four-element row from `SELECT id, finished_at, status`. No database
> can do that."

**Mutation proofs: `.dev/reference/p3-review-mutation-proofs.md`, 11 guards, with `sha256` printed on
both sides of every restore. Verbatim:**

```
approve-route-does-not-read-the-stored-run   (the HIGH finding)
  RED:   5 failed, 2 passed in 48.95s
  GREEN: 7 passed in 25.77s
  sha256 before=70e1dd3c61d8986e after=70e1dd3c61d8986e identical=True

stored-run-helper-accepts-absent
  RED:   5 failed, 7 passed in 26.80s
  GREEN: 12 passed in 23.89s
  sha256 before=71443e905144322c after=71443e905144322c identical=True

failed-run-is-admissible
  RED:   4 failed, 5 passed in 3.09s
  GREEN: 9 passed in 2.03s
  sha256 before=71443e905144322c after=71443e905144322c identical=True

failed-run-check-is-a-deny-list
  RED:   1 failed, 5 passed in 2.74s
  GREEN: 6 passed in 2.07s

failed-run-yields-to-the-invocation-claim
  RED:   1 failed, 5 passed in 2.83s
  GREEN: 6 passed in 2.01s

failed-run-scores-do-not-travel — NO SINGLE MUTATION FALSIFIES THIS
  structural only — RED: 6 passed in 2.46s   (i.e. NO RED)
  state only      — RED: 6 passed in 2.03s   (i.e. NO RED)
  both            — RED: 1 failed, 5 passed in 2.80s
  GREEN after each restore: 6 passed in 2.01s / 6 passed in 2.05s / 6 passed in 1.97s

warning-does-not-branch-on-the-payload
  RED:   1 failed, 11 passed in 2.80s
  GREEN: 12 passed in 1.98s

collector-refuses-only-false
  RED:   1 failed in 2.70s
  GREEN: 1 passed in 2.02s

no-dispatch-for-the-historical-population
  RED:   2 failed, 11 passed in 15.01s
  GREEN: 13 passed in 14.08s
  sha256 before=80dac80367860715 after=80dac80367860715 identical=True

dispatch-fires-for-an-explicit-false-too
  RED:   1 failed, 12 passed in 14.91s
  GREEN: 13 passed in 14.05s

double-pads-the-narrow-row
  RED:   1 failed, 1 passed in 3.08s
  GREEN: 2 passed in 2.25s
  sha256 before=145ed874021021a0 after=145ed874021021a0 identical=True
```

> "**Recorded honestly:** the first run of this mutation reported `identical=False`, because an
> uncommitted docstring edit was live in the same file and the unconditional `git checkout HEAD --`
> correctly discarded it. That is the restore working, not failing."

### 3.9 Mutation-proof tally

| report | claimed | on disk verbatim | not red first time |
|---|---:|---:|---|
| P1 | 12 | **0** | not stated |
| P1 fix | 12 | **0** | not stated |
| P1b | 21 | 21 | **1** (M2) |
| P1b fix | 18 | **0** (2 in prose) | **1** (M8, incomplete) |
| P2 | 19 | 19 | **1** (M13) |
| P2 fix | 23 | 23 | **1** (at-cap) |
| P3 | 6 | 4 (reproduced later) | — |
| P3 fix | 11 | 11 (+sha256) | **1 property, not falsifiable by any single mutation** |
| **total** | **122** | **78** | **5 self-caught** |

**44 of 122 claimed mutation proofs have no surviving verbatim record.** All 44 are in P1, P1-fix and
P1b-fix. `.dev/BACKLOG.md` `3.9` already carries the general form of this debt for the previous
branch ("20 of the 72 guard demonstrations are implementer self-reports; tier-1 reproduced none").


---

## 3b. THE MONEY QUESTION

> **Can any eval path reach a live `ProviderAdapter`, the real mailer, or the tenant's metrics
> tables?**

**Answer, from the diff: NO to all three — with one deliberate exception and four residuals that are
writes but not the three named surfaces.** Stated plainly, then evidenced hunk by hunk.

The eval path is exactly one chain, and it has one entry point:

```
run_eval_suite                      eval.py
  → _invoke_agent_for_scenarios     eval.py       (per scenario, sequential, ≤60)
    → _run_one_eval_turn            eval.py
      → build_agent_options(side_effects="recorded")   agent.py   ← THE ONLY DOOR
      → _run_sdk_turn(...)                              agent.py
        → the 11 MCP tools                              agent_tools.py / transactional/tools.py
```

### 3b.1 A live `ProviderAdapter` — NO. Five doors, all four reachable ones closed.

`get_adapter_for_skill` is reached from exactly two places: `_execute_transactional_tool` step 6, and
`_execute_adapter_and_audit` called by `confirmation_resolution.execute_approved_confirmation`.

**Door 1 — the approve path (step 6).** Step 5.5 returns first:

```python
    if recorded:
        record_suppressed_side_effect("transactional.adapter", {...})
        await release_idempotency(agent_id, skill, idem_key)
        await write_audit_row(..., error=RECORDED_NOT_EXECUTED)
        log.info("transactional_tool.side_effect_recorded", ...)
        return _not_executed_result(skill)

    # -------------------------------------------------------- 6-7. Adapter + audit
```

**Door 2 — the step-3 idempotency `replay` arm, which returns BEFORE 5.5:**

```python
    if reservation.state == "replay":
        ...
        if recorded:
            # The stored result is a REAL provider result from a REAL earlier
            # call. Returning it here would hand the eval agent "Refund of
            # R45.00 issued" ... arriving through the one door step 5.5 sits
            # behind rather than in front of.
            record_suppressed_side_effect(RECORDED_DECLINED, _declined_detail(..., reason="idempotency.replay", ...))
            return _not_executed_result(skill)
        return reservation.result  # type: ignore[return-value]
```

Doubly closed, because the key is namespaced before the reservation is taken:

```python
    idem_key: str = (
        f"recorded:{validated.idempotency_key}" if recorded else validated.idempotency_key
    )
```

and a recorded execution **releases** rather than finalizes, so nothing is ever stored under a
`recorded:` key.

**Door 3 — the SLOW path, and the one step 5.5 structurally could not see.** The Actor's
`require_human` verdict wrote a `pending_confirmations` row that the owner's approval queue later
dispatches into a real adapter. This arm returns before 5.5:

```python
    elif decision == "require_human":
        await release_idempotency(agent_id, skill, idem_key)
        # D1/P1b: THE SECOND DOOR TO A LIVE ADAPTER ...
        # Approving it dispatches resolve_confirmation_task ->
        # execute_approved_confirmation -> _execute_adapter_and_audit ->
        # get_adapter_for_skill -> a real Stripe/Shopify/Woo/Calendly call. So a
        # nightly eval scenario that provokes a large refund silently queues a
        # real refund for the owner to approve, hours later ...
        if recorded:
            record_suppressed_side_effect(RECORDED_DECLINED, _declined_detail(..., reason="actor_require_human", ...))
            await write_audit_row(..., error=_recorded_error(recorded, "actor_require_human"))
            log.info("transactional_tool.require_human_not_queued", ...)
            return _not_executed_result(skill, "It requires human approval and no approval request was created.")

        now = datetime.now(timezone.utc)          # ← the row-writing code, now unreachable under recorded
```

**No row is written, so no approval can ever dispatch.** This is the highest-consequence hunk on the
branch: without it, approach (b) plus recorded-mode-at-5.5 still moved money, just asynchronously and
with the owner's own click on it.

**Door 4 — `confirm_action_tool`, which does not use the dispatcher at all:**

```python
    if recorded:
        record_suppressed_side_effect("transactional.confirm_action", {...})
        log.info("confirm_action.not_queued", ...)
        return _not_executed_result("confirm_action", ...)

    now = datetime.now(timezone.utc)
```

**Door 5 — `_execute_adapter_and_audit`, the shared helper — is deliberately NOT mode-aware**, and
that is argued rather than overlooked:

> "_execute_adapter_and_audit is shared with confirmation_resolution.execute_approved_confirmation,
> the human-approval resolver. That resolver runs hours later, in another task, with no per-turn
> context, and is forbidden from reading dispatcher ContextVars (OD-5,
> test_resolver_reads_no_dispatcher_contextvar)."

It is reachable only from an approved `pending_confirmations` row, and doors 3 and 4 mean recorded
mode writes none. **The helper is a live adapter call with no mode check, and its safety is entirely
inherited from the two upstream branches.** That is a real coupling for the judge to weigh: delete
door 3's `if recorded:` and money moves again, with `test_the_shared_adapter_helper_stays_free_of_the_mode`
still green by design.

**The eval's own request for the mode is mandatory and pinned twice** — once at the seam, which has no
default:

```python
def build_agent_options(
    *,
    agent, conn_str: str, conversation_id: str, job_id: str,
    side_effects: SideEffectMode,          # ← MANDATORY, no default
    ...
) -> "ClaudeAgentOptions":
    reset_side_effect_context()
    if side_effects not in SIDE_EFFECT_MODES:
        raise ValueError(
            f"build_agent_options: side_effects must be one of {SIDE_EFFECT_MODES}, "
            f"got {side_effects!r}. There is no third mode and no fallback: an "
            f"unrecognised value read as live is how an eval scenario issues a real "
            f"refund against the tenant's provider (BACKLOG 2.5)."
        )
```

— and once at the eval's call site:

```python
        options = build_agent_options(
            agent=agent, conn_str=conn_str, conversation_id=conversation_id,
            job_id=job_id,
            side_effects="recorded",
            verified_session_token="", soul_override=soul_override, resume=None,
        )
```

Note `build_tool_server`'s default is `"live"`, and that asymmetry is deliberate and argued (the
red-team probes must read real dispatcher verdict tags; a `recorded` default would silently stop
refunding real customers). Both directions are pinned (`test_build_tool_server_defaults_to_live`,
`test_the_seam_refuses_to_build_without_a_side_effects_mode`).

### 3b.2 The real mailer — NO.

`send_escalation_email` is bound into `notify_fn` only on the live arm of a conditional expression:

```python
    notify_fn = (
        (lambda reason, context: send_escalation_email(agent, reason, context))
        if side_effects == "live"
        else (
            lambda reason, context: record_suppressed_side_effect(
                "escalation.notify",
                {"agent_id": str(agent.id), "conversation_id": str(conversation_id),
                 "reason": reason, "context": context},
            )
        )
    )
```

**And the second half of the escalation edge — the tenant `conversations` UPDATE — is closed too**,
which the P1b guard could not see because it called the `notify_fn` closure directly and never entered
the tool:

```python
    if side_effects == "recorded":
        record_suppressed_side_effect("conversation.escalated_marker", {...})
        result: dict = {}
    else:
        # Write escalation marker to conversations table (idempotency guard inside).
        result = await loop.run_in_executor(
            None, lambda: _mark_conversation_escalated(conversation_id, agent_id, reason, context, conn_str),
        )
```

### 3b.3 The tenant's metrics tables — NO for `retrieval_metrics`, `turn_metrics`, `messages` and
`conversations`; **YES, deliberately, for `tool_calls_audit`.**

**`retrieval_metrics` — suppressed:**

```python
    if side_effects == "recorded":
        record_suppressed_side_effect(
            "retrieval_metrics.write",
            {"job_id": job_id, "conversation_id": conversation_id, "row": metrics_row},
        )
    else:
        await loop.run_in_executor(None, lambda: write_retrieval_metrics(conn_str, metrics_row))
```

**`turn_metrics`, `messages`, `conversations` — structurally unreachable, and this is the load-bearing
fact the diff does not spell out anywhere.** `_persist_messages` (`agent.py:1329`) and
`_write_turn_metrics` (`agent.py:1390`) are called from **`run_agent_turn`**, not from
`_run_sdk_turn`. The eval calls `_run_sdk_turn` directly and never enters `run_agent_turn`.
Independently verified by the collector at `HEAD`:

```
$ grep -n "conn_str" app/worker/tasks/runtime/agent.py | awk -F: '$1>870 && $1<1065'
876:    conn_str: str,
```

`conn_str` appears in `_run_sdk_turn`'s signature and **nowhere in its body** — the function opens no
connection. Its only outward calls are two `emit(job_id, ..., db, redis)`, and the eval hands both
slots `_EvalEventSink`, whose `publish`/`add`/`commit` are no-ops. **No `job_events` rows either.**

**`tool_calls_audit` — WRITTEN, ON PURPOSE, MARKED.** Every recorded outcome still writes exactly one
audit row, stamped:

```python
RECORDED_NOT_EXECUTED: str = "side_effects.recorded:not_executed"

def _recorded_error(recorded: bool, error: str) -> str:
    return f"{RECORDED_NOT_EXECUTED}|{error}" if recorded else error
```

and the decision eval refuses to score those rows as Actor decisions:

```python
_ERROR_DISPOSITIONS: tuple[tuple[str, str | None, str], ...] = (
    # FIRST, and it must stay first ...
    (RECORDED_NOT_EXECUTED, None, "recorded_not_executed"),
    ("actor_require_human", DISPOSITION_REQUIRE_HUMAN, "actor_require_human"),
```

**So the honest answer to "the tenant's metrics tables" is: the eval writes one marked row per
mutating attempt into `tool_calls_audit`, and nothing else in the tenant DB.** AUD-01 symmetry was
chosen over silence. Whether an eval's rows belong in an audit table at all is a judgement the judge
should weigh; `.dev/BACKLOG.md` `2.10` already records the consequence (the decision eval's
denominator may be starved by eval traffic).

### 3b.4 Four residual writes that are neither money, mail, nor metrics — stated for completeness

1. **`tool_idempotency_keys`, control DB.** `reserve_idempotency` INSERTs and `release_idempotency`
   DELETEs, under the `recorded:` namespace, once per mutating attempt. Transient and namespaced,
   but real rows in a real table.
2. **Redis `ratelimit:recorded:{agent}:{skill}:{window}`.** Namespaced rather than suppressed, and
   the reason is argued: *"Suppressing the INCR would make 'the agent kept refunding past its limit'
   unfalsifiable."* Real Redis writes, separate keyspace.
3. **`eval_runs` and `eval_results` on the PRODUCTION tenant DB.** By design — they are the run's own
   record, and putting them on production rather than the deleted branch was the previous branch's D2
   fix.
4. **The Actor gate is a live Haiku call per mutating attempt** (steps 1–5 run live by design). Real
   billed API spend, unbounded within a turn. `.dev/BACKLOG.md` `2.8` carries it, narrowed: P2 bounds
   the *turns* at 60 and does not bound the *attempts within a turn*.

### 3b.5 Two things the eval DOES reach that no claim on this branch addresses

**(a) The eval runs against the tenant's PRODUCTION connection string, and `lookup_structured` is an
allowlisted `SELECT *` over real tenant tables.** The diff and trace are explicit that production is
deliberate (*"`retrieve` has to see the corpus the customer is served"*) and that recorded mode, not
the Neon branch, is what stops writes. But the read side has a consequence nobody states: verified at
`HEAD`, `lookup_structured_tool` builds `SELECT * FROM {table} WHERE ... LIMIT 100` over
`ALLOWED_LOOKUP_TABLES` and returns `str(rows)` into the transcript. **That transcript is then sent
to the Ragas judge API**, and:

```python
        pii_firewall_applied=False,
```

with the eval path deliberately skipping `scan_response`. `.dev/BACKLOG.md` `2.11` frames the missing
firewall purely as a *scoring-fidelity* question ("a deflection is not an answer"). **The data-egress
reading of the same fact — sixty nightly turns that may read real customer rows and forward them to a
third-party judge with the output firewall off — is not addressed anywhere on this branch.** The
collector flags it; it is the judge's to weigh.

**(b) Sticky-mode leakage is closed by construction in three places, and the judge should check the
third is enough.** `build_agent_options` calls `reset_side_effect_context()` *before* anything that
can throw; `build_tool_server` publishes the mode *after* `create_sdk_mcp_server`; and the eval loop
resets per iteration and again in a `finally`. The safe default is `"live"`, so a leak strands the
*chat* path in the safe state and an *eval* turn in the dangerous one — and an eval turn that reached
the tools without passing `build_agent_options` is the only way that happens. `_run_one_eval_turn`
has no such route.

### 3b.6 The one hole in the structural argument

The repo-wide allowlist that makes approach (b) *structural* rather than intended lives in a test:

```python
MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS = {
    "app/worker/tasks/runtime/agent.py",
    "app/services/deployment_service.py",
    "app/services/red_team_probe.py",
    "app/services/red_team_service.py",
}
```

`eval.py` is correctly absent. But `red_team_probe.py` is on it and — per `.dev/BACKLOG.md` `2.9`,
opened by the P1b fixer — *"builds the CUSTOMER agent by hand, not through the seam […] with
`_PROBE_MODEL` and `_ALLOWED_TOOLS`, so the RTX victim turn is an agent with a different model and a
different tool list from the one production serves and the eval measures."* That is not an eval path
and does not bear on the money question, but it is a **second, unseamed construction of the customer
agent that this branch grandfathered rather than closed**, and the drift the seam exists to prevent
therefore still exists one module over.

---

## 4. Tier-1 findings, and whether the diff shows them fixed

**Read §0 first.** The reviewers' verbatim finding lists did not survive. Each row below is
reconstructed from the fixer's commit message and trace, then adjudicated by the collector **against
the diff**, not against the fixer's say-so. Counts claimed by the fixers: P1 **8 drift probes**, P1b
**12 findings**, P2 **17 findings + 7 unsupported claims**, P3 **11 findings (1 high, 3 medium, 4 low,
2 nit) + 6 unsupported claims** — 48 findings total, of which the surviving record names 31. **17 are
unaccounted for and this artifact cannot adjudicate them.**

### 4.1 P1 review → `d15be3a` (8 drift probes)

| # | Finding as reconstructed | Verdict from the diff |
|---|---|---|
| 1.1 | "Seven of eight realistic drift edits left all 12 guards green; the eighth went red only because the sentinel lacked the attribute." The guard recognised one spelling of one mutation. | **FIXED (structurally).** `test_agent_options_seam.py` is 1730 lines with an alias graph, an attribute-rebinding pin, a dynamic-dispatch pin, and a **VALUE guard** comparing every served kwarg against an independently-built reference. The value guard is the right shape: it pins the property, not the spelling. **Not independently verified** — the eight probes were not re-run and no verbatim red/green exists (§3.9). |
| 1.2 | Nothing made approach (b) structural — any module could construct options. | **FIXED.** `MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS`, repo-wide, `eval.py` absent. Caveat in §3b.6. |
| 1.3 | The seam hands every caller a live tool server that can call `issue_refund` for real. | **DEFERRED BY DESIGN, then FIXED in P1b.** `d15be3a` only documented it (agent.py comments) and opened BACKLOG 2.5. The code fix is `487ebbe`. |
| 1.4 | The canary write moved ahead of the options build, so a turn dying in options-building left the conversation permanently sticky. | **DEFERRED to P1b, then FIXED.** See 4.2/2.6. |
| 1.5–1.8 | Not named in any surviving artifact. | **UNKNOWN.** |

### 4.2 P1b review → `0580ea8`, `df0a0b7` (12 findings)

| # | Finding | Verdict from the diff |
|---|---|---|
| 2.1 | `require_human` returns before 5.5 and writes a `pending_confirmations` row the approval queue dispatches into a live adapter. | **FIXED.** The `if recorded:` block in the `require_human` arm returns before `now = datetime.now(...)`. Quoted in §3b.1 door 3. |
| 2.2 | The step-3 replay arm returns a stored REAL provider result. | **FIXED, twice.** Recorded branch returns `_not_executed_result`; the `recorded:` keyspace makes the arm unreachable anyway. |
| 2.3 | `escalate_to_human_tool` still committed `UPDATE conversations`. | **FIXED.** `if side_effects == "recorded": … result: dict = {}`. |
| 2.4 | Redis rate counter shared with production. | **FIXED.** `redis_key = f"ratelimit:{mode_prefix}{agent_id_str}:{skill}:{window_key}"` in `enforcement.py`. Namespaced, not suppressed — argued. |
| 2.5 | `confirm_action_tool` bypasses the dispatcher and writes a row per scenario. | **FIXED.** Recorded branch returns before the write. |
| 2.6 | `record_suppressed_side_effect` had three call sites, none on a refusal path — the *refused* column of the confusion matrix was empty. | **FIXED.** Nine `record_suppressed_side_effect(RECORDED_DECLINED, …)` sites now cover capability denial, IDV required / check-failed / invalid, replay, args_mismatch, in_progress, stranded reservation, rate denial, actor_block and require_human. |
| 2.7 | Only step 5.5's audit row carried the marker; a recorded `actor_block` row was byte-identical to a production one. | **FIXED.** `_recorded_error(recorded, …)` applied at every `write_audit_row` error site. |
| 2.8 | The recorded tool result told the agent it was being evaluated — every subsequent token came from an evaluation-aware agent. | **FIXED.** `_not_executed_result` carries no evaluation frame; `test_the_recorded_result_does_not_disclose_the_evaluation_frame` pins it. **Consequence accepted and stated:** a transcript alone can no longer distinguish a recorded call from a provider outage. |
| 2.9 | Sticky mode could leak `recorded` into a customer turn. | **FIXED, both halves.** `reset_side_effect_context()` first in `build_agent_options`; `_side_effects_var.set(...)` last in `build_tool_server`, after `create_sdk_mcp_server`. |
| 2.10 | Recorded rows would contaminate the Actor's supervised set. | **FIXED.** `(RECORDED_NOT_EXECUTED, None, "recorded_not_executed")` first in `_ERROR_DISPOSITIONS`. **Not in the finding list** — the fixer says an existing guard forced it. |
| 2.11 | `escalate_to_human`'s already-escalated return was a bare dict with no `content`. | **FIXED.** The return now carries a `content` list. |
| 2.12 | `red_team_probe` builds the customer agent by hand. | **NOT FIXED — deliberately deferred.** BACKLOG `2.9` opened; only the allowlist comment changed. The fixer records it as out of scope by the finding's own admission. See §3b.6. |

### 4.3 P2 review → `b62186f`, `075550d` (17 findings + 7 unsupported claims)

| # | Finding | Verdict from the diff |
|---|---|---|
| 3.1 | A below-floor run still wrote scores, marked itself `complete`, and reached the gate as `MEASURED`. | **FIXED.** `if invocation["status"] != AGENT_INVOCATION_MEASURED:` → no `run_ragas_eval`, no `write_eval_results`, empty `results`. |
| 3.2 | Ragas was handed a repr of the SDK block, cut at 1800 chars, as ONE element. | **FIXED.** `_tool_result_text` + `_retrieved_chunk_texts` + `RETRIEVE_CHUNKS_KEY`; `result` untouched so the Auditor and chat path are byte-for-byte. |
| 3.3 | `visibility_timeout` 3600 < `max_wall_clock_s` 5400; idempotency window 600. | **FIXED.** `BROKER_VISIBILITY_TIMEOUT_S = 7200`, pinned by a *relation* test; the window is derived (`MAX_CALLS × timeout + SLACK`). |
| 3.4 | A responded turn with no retrieve call was scored 0/NaN on three context metrics. | **FIXED.** `if record["responded"] and contexts:` — excluded and counted as `no_retrieval`, and explicitly not a failure. **Partial by the fixer's own admission:** AnswerRelevancy is well-defined without contexts and is lost with them. BACKLOG `2.15` carries it. |
| 3.5 | No absolute floor — a one-scenario run certified itself. | **FIXED.** `MIN_SCORED_OBSERVATIONS = 3`, applied to `scorable`. |
| 3.6 | `coverage_rate` diverged silently from `compute_correlation`'s shape. | **FIXED (reported), NOT GATED.** `coverage_rate = responded / valid` travels; nothing gates on it, deliberately. BACKLOG `2.16`. |
| 3.7 | The gate read an in-flight `running` run and blocked while a good completed run sat below. | **FIXED.** `AND status <> 'running'` on both the wide and narrow selects. |
| 3.8 | `promote_to_verified_qa` wrote `agent_response` while its trust gate inspected the label's provenance. | **FIXED.** `promotable_answer(scenario)` returns `reference_answer`; a blank label is refused. |
| 3.9 | `run_eval_for_agent` is a second door to `run_ragas_eval` that no P2 guard reaches. | **FIXED.** `if tautologies: raise ValueError(...)` before any judge call. |
| 3.10 | The side-effect sink was reset only inside `build_agent_options`; a scenario dying earlier re-read the previous scenario's sink. | **FIXED.** `reset_side_effect_context()` at the top of each loop iteration. |
| 3.11 | A failure after the invocation retried and re-bought sixty SDK turns. | **FIXED.** `if agent_was_invoked: … return {}` before `self.retry`. |
| 3.12 | `stored_retrieved_contexts` was pinned on the NAME; a one-token fallback passed all 163 tests. | **FIXED.** `test_the_stored_context_column_is_never_read_back_by_the_eval`, mutation `stored-context-fallback` observed red. |
| 3.13 | `EVAL_INVOKES_AGENT` was flipped to `True` in the same commit that deleted its only pin. | **FIXED.** `test_the_constant_that_claims_the_agent_is_invoked_is_pinned_to_the_code`. |
| 3.14 | The eval could keep its own copy of a turn bound. | **FIXED.** `_agent_turn_timeout_s()` imports lazily; `test_the_eval_imports_the_turn_bounds_rather_than_restating_them`. |
| 3.15 | The `emit()` no-op property was held by an undocumented coupling. | **FIXED.** `emit` is driven through `_EvalEventSink`; the `emit-gains-a-flush` mutation went red. |
| 3.16 | `retrieved_context_at_cap` was derived from the audit cap and was ~100% on every retrieving turn. | **FIXED.** Derived from `CHUNK_CONTENT_CHAR_LIMIT`; the fixture that separates the two caps was added in `075550d` after the mutation failed to go red. |
| 3.17 | An unreadable retrieve payload was reported as an empty retrieval. | **FIXED.** `_retrieved_chunk_texts` returns `None`, counted as `retrieved_context_unparsed`. |
| — | 7 unsupported claims | **NOT ENUMERATED ANYWHERE.** The trace's "Guards the review proved were not guards" section covers six of them by implication. **The judge cannot verify the seventh.** |

### 4.4 P3 review → `8b124d4`, `9106412` (11 findings + 6 unsupported claims)

| # | Finding | Verdict from the diff |
|---|---|---|
| 4.1 | **HIGH.** The gate never reached `POST /approve-deployment`, which validates against a frozen `recommendation`. Every pre-D1 checklist run stayed approvable. | **FIXED.** `stored_run_records_agent_invocation(run.report)` in `deployment.py`, `is True`, fail-closed on every unreadable shape, placed behind the three shipped validations and ahead of the envelope check. Mutation observed `5 failed, 2 passed` red. |
| 4.2 | The owner-facing warning narrated a cause it did not observe — four false claims in one sentence for a below-floor run. | **FIXED.** `_agent_not_invoked_warning` branches on `invoked is False` vs absent; the absent branch offers the tautology conditionally rather than asserting it. |
| 4.3 | A run whose own status is `failed` was still evidence, and P2's ordering makes that shape ordinary. | **FIXED.** `EVAL_SIGNAL_RUN_FAILED`, checked ahead of the invocation claim, as an **allow-list of one** so an unknown terminal status also fails closed. |
| 4.4 | Step 4b's convergence dispatch fired only on `NO_RUNS` — no existing tenant is in that state. | **FIXED, ASYMMETRICALLY AND ON PURPOSE.** Fires for `AGENT_NOT_INVOKED` **and** `agent_invoked is None` only. An explicit `false` and a `run_failed` recur, so firing on them would be a spend loop. Both directions mutation-proved. |
| 4.5 | `_dispatch_first_eval_run` / `first_eval_dispatched` are now misnamed. | **FIXED.** Renamed `_dispatch_eval_run` / `eval_dispatched`. |
| 4.6 | `_make_eval_conn` padded a 3-tuple to four columns — a shape no database can return. | **FIXED.** Row sliced at fetch time against the SQL actually executed; `TestNarrowRowWidth` pins both widths. |
| 4.7 | The migration the plan asked for does not exist. | **NOT FIXED — argued away.** No `alembic_tenant` revision on the branch. See §1.1. The argument (one home for the claim; a `false` backfill changes no outcome) is coherent, and it is still a contract deviation the plan did not sanction. |
| 4.8–4.11 | 4 low / 2 nit, not enumerated in any surviving artifact. | **UNKNOWN.** |
| — | "Two independent points, both load-bearing" | **CORRECTED, not defended.** Docstring rewritten: the collector is the enforcement, the `elif` is an invariant for a payload source that does not exist. |
| — | "The prompt was updated so narration cannot contradict the verdict" | **CORRECTED.** Nothing executes `run_orchestrator` (BACKLOG 3.10); the module comment now says the prompt tests are drift protection over a string. |
| — | "The scores are suppressed twice, structurally and by state" | **CORRECTED and the mutation re-run three ways:** structural-only `6 passed`, state-only `6 passed`, both `1 failed`. One property, no single load-bearing layer. |
| — | 3 further unsupported claims | **NOT ENUMERATED.** |

### 4.5 Adjudication summary

| | fixed | partially fixed | not fixed | unknown |
|---|---:|---:|---:|---:|
| P1 review (8) | 2 | 2 (deferred to P1b, then fixed there) | 0 | 4 |
| P1b review (12) | 11 | 0 | 1 (2.12, deferred to BACKLOG 2.9) | 0 |
| P2 review (17) | 15 | 2 (3.4 AnswerRelevancy; 3.6 reported-not-gated) | 0 | 0 |
| P3 review (11) | 6 | 0 | 1 (4.7, the migration) | 4 |
| **total (48)** | **34** | **4** | **2** | **8** |

Plus **13 unsupported claims** across P2 and P3, of which **6 are corrected in writing** and **7 are
named only as a count**.

---

## 5. What this branch does NOT prove

Every item here is a gate that skipped, a migration unapplied, or a path unexercised. None of it is
hypothetical: it follows from there being no PostgreSQL server on this machine and no live Claude
Agent SDK turn in the suite.

### 5.1 Every `-m integration` harness SKIPPED. 11 skips, all of them.

The collector's own run: `1873 passed, **11 skipped**`. `.dev/traces/260807-d1-p1b-recorded-mode.md`
states all 11 are `-m integration`. Per CLAUDE.md, **a skip is unobserved, never a pass.**
`CONTROL_DB_URL` points at live Neon production and is never a substitute (BACKLOG `0.2`).

`tests/integration/test_prompt_versions_e2e.py` was **modified on this branch** (+48/-20) for
`_resolve_turn_prompt_version`'s new three-tuple signature and **has never been executed**. It is
reviewable, not verified.

### 5.2 No migration was applied, and none was written.

`git diff main...HEAD --name-only -- '*alembic*'` is empty. `0015` remains the tenant head.
`.dev/BACKLOG.md` `3.5` already records that `0013`/`0014`/`0015` are verified by source-text
assertion only and **no `ALTER TABLE` on any recent branch has ever executed anywhere.**

### 5.3 The one write the whole gate depends on has never touched a database.

`update_eval_run_config`'s `config = COALESCE(config,'{}'::jsonb) || %(patch)s::jsonb` is the write
that turns `agent_invoked` from a default into an observation, and P3's gate reads exactly what it
writes. It is asserted **at the call site against a cursor double**. The P2 mutation-proof file says
so itself: *"the three `TestUpdateEvalRunConfig` cases … were written against a cursor double and
their SQL has never executed against a database."* BACKLOG `2.14`.

The same is true of `status <> 'running'` (asserted on SQL text) and of the pre-0013 `UndefinedColumn`
fallback.

### 5.4 No live SDK turn has ever run. Recorded mode has never faced a real ProviderAdapter.

Quoted from the P2 trace:

> "The Agent SDK subprocess is doubled at one boundary in every test. Nothing here proves the seam's
> options are accepted by the real SDK, that `recorded` mode holds against a real ProviderAdapter, or
> that a real retrieve result is shaped the way the context extraction assumes."

And from the P1b-fix trace:

> "no recorded-mode turn has ever run against a real Claude SDK. Recorded mode's effect on what the
> agent *says* after a `NOT EXECUTED` tool result is untested by construction — the fidelity argument
> for removing the evaluation-frame disclosure is a reasoned one, not a measured one."

**That the real `ProviderAdapter` is not reached is proven at the `get_adapter_for_skill` boundary by
unit test, not by watching a payment provider fail to receive a request.**

### 5.5 The new `agent.py` retrieve decode has never seen a real SDK `ToolResultBlock`.

It is exercised against the exact payload `agent_tools.retrieve_tool` constructs and against three
content shapes the SDK can hand back, but the SDK is not installed in a form these tests drive. A
chunk dict carrying a non-literal value makes `ast.literal_eval` refuse and the turn is counted
`retrieved_context_unparsed` — fail-closed and visible, but the failure mode has not been observed.

### 5.6 No end-to-end eval run. The metric has not been observed to move.

This is the phase's entire purpose and it is unobservable here. Faithfulness is expected to fall from
~1.0 to whatever is true; **no run has demonstrated that, and none can without BACKLOG `0.2`.** Nor
has the consequence the plan predicted — that the gate's absolute 0.70/0.85 thresholds will be wrong
for the first time in a visible way — been seen.

### 5.7 No end-to-end readiness check. The approve route's new 422 has never fired.

The HIGH finding's fix has never been exercised against a real `checklist_runs` row, and the new
`run_failed` state has never been derived from a real `eval_runs` row. The `failed`-with-scores shape
was **reasoned from `eval.py`'s ordering** and driven through the collector with a connection double.

### 5.8 `_dispatch_eval_run` was never observed dispatching anything.

Driven against a fake `celery.chain`, as in P2.

### 5.9 44 of 122 claimed mutation proofs have no verbatim record.

P1 (12), P1-fix (12) and P1b-fix (18). See §3.9. For P1 specifically this covers the phase the plan
calls *"the whole bet"* and whose guard it calls *"load-bearing, not hygiene"*.

### 5.10 Eight intermediate suite figures were relayed, not reproduced.

Only `af0f601`'s 1675/11/0 and `a021118`'s 1873/11/0 were independently observed (the second by the
collector, this session). `5011f97`'s 1839/11 is the one intermediate figure observed twice
independently. `65eab9e`'s 1821/11 was explicitly **not** reproduced and is corroborated only by a
`def test_` count.

### 5.11 `ruff` is not installed in `apps/api/.venv`.

Every "ruff clean" claim on this branch depends on a network `uvx ruff@latest` fetch that leaves no
artifact and was not re-run by the collector. `mypy app` was likewise not re-run.

### 5.12 `--cov-fail-under=80` has still never executed in this project's history.

BACKLOG `1.2`. Local measurement was 80.86% on the previous branch. This branch adds 7,374 diff lines of
tests and 4,212 of `app/`; **the coverage effect is unmeasured.**

### 5.13 CI has never run any of this.

BACKLOG `0.3`: the Actions wall-clock cap kills every job at ~15m. Unit and Integration have **never
executed on a runner** in this project's history.

### 5.14 Things the branch changes that no test on the branch observes end to end

- The owner-facing warning text is the **only** owner-visible account of the refusal — a grep of
  `apps/admin` for `agent_invoked` or `eval_signal` returns nothing. The console has not been updated
  and no frontend gate was run on this branch.
- `run_orchestrator` is never executed anywhere (BACKLOG `3.10`), so every claim about the system
  prompt's blocking conditions is drift protection over a string.
- The accepted cost — **every pre-D1 eval run and every pre-0013 tenant DB now fails closed at the
  deploy gate** — is live from `5011f97` and has never been observed against a real tenant.
