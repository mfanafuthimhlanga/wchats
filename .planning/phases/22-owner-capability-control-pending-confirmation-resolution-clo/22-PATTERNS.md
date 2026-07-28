# Phase 22: Owner capability control + pending-confirmation resolution — Pattern Map

**Mapped:** 2026-07-28
**Files analyzed:** 10 (2 new backend routes/tasks modules, 1 schema, 1 execution helper, 2 modified backend files, 1 modified frontend file, 3 test files, 1 UAT doc)
**Analogs found:** 10 / 10 (all files have a direct or role-match analog in the current tree; two facts below were independently verified against source this session, not merely trusted from RESEARCH.md)

**Verification note:** RESEARCH.md's `§ ACT-07 Design`, `§ Code Examples`, and `§ Sources` were checked directly against the working tree this session. Every line-numbered claim below was re-read from the file itself; two things RESEARCH.md flagged as needing independent confirmation are now confirmed:

1. **`_get_owned_agent` has NOT drifted between `capability_envelopes.py` and `prompt_versions.py`.** Both copies are byte-identical: 404 on missing-agent, 404 on `agent.tenant_id != tenant.id` (no distinct 403), same two-line body, same "no existence leak" intent. Safe to copy from either file verbatim.
2. **`pending_confirmations`'s actual column list is `id, agent_id, skill, arguments, requested_at, expires_at, resolved_at, resolution`** (`apps/api/app/models/pending_confirmation.py:24-41`) — no `resolved_by`, no execution-outcome field. The current control-DB migration head is `0019` (`apps/api/alembic/versions/0019_blast_radius_capability_v2.py`; no `0020` file exists). The UI-SPEC's flagged gap ("approved and executed" vs "approved but denied at execution") is real: closing it requires either a new `0020` migration adding a column, or a read-time join against `tool_calls_audit` keyed by `(agent_id, skill, idempotency_key)` — no column exists today that answers it directly, so the planner cannot resolve this by reading a field that isn't there.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `apps/api/app/api/v1/pending_confirmations.py` (new) | route | request-response + CRUD (atomic claim) | `apps/api/app/api/v1/capability_envelopes.py` | exact |
| `apps/api/app/schemas/capability.py`-sibling: resolve-body schema (new, likely `apps/api/app/schemas/pending_confirmation.py`) | model/schema | request-response | `apps/api/app/schemas/capability.py` | exact |
| `apps/api/app/worker/tasks/runtime/confirmations.py` (new) | service (Celery task) | event-driven / batch (queued execution) | `apps/api/app/worker/tasks/runtime/agent.py` (`run_agent_turn`) | exact |
| `apps/api/app/services/transactional/confirmation_resolution.py` (new) | service (narrow dispatcher re-entry) | transform / CRUD | `_execute_transactional_tool` in `apps/api/app/services/transactional/tools.py` (steps 2/3/4/6/7 subset) | exact (same function, subset) |
| `apps/api/app/services/red_team_probe.py` (read-only reference, not modified) | service (out-of-band dispatcher entry) | event-driven | — (this IS the analog for ContextVar seeding) | exact |
| `apps/api/app/services/capability_service.py` (modified — `validate_tighten_only`'s `enabled` branch) | service (comparator) | transform | itself — the two adjacent boolean branches (`requires_confirmation`, `requires_identity_verification`) | exact (same function) |
| `apps/admin/app/agents/[id]/deploy/page.tsx` (modified) | component | request-response (staged-confirm PATCH) | itself — `CapabilityZone`'s existing `rate_limit`/`max_amount_cents` staged-confirm blocks | exact (same file, sibling pattern) |
| `apps/api/tests/unit/test_confirmation_resolution.py` (new) | test | request-response | `apps/api/tests/unit/test_capability_routes.py` | exact |
| `apps/api/tests/integration/test_act07_resolve_live.py` (new, gated) | test | batch (ephemeral DB lifecycle) | `apps/api/tests/integration/test_red_team_rtx.py` | exact |
| `apps/api/tests/unit/test_capability_routes.py` (extended) | test | request-response | itself | exact |
| `.planning/phases/22-.../22-UAT.md` (new) | doc | — | `.planning/phases/19-documentation-v1-1-verification/19-UAT.md` | exact |

---

## Pattern Assignments

### `apps/api/app/api/v1/pending_confirmations.py` (new route)

**Analog:** `apps/api/app/api/v1/capability_envelopes.py` (full file read this session, 262 lines)

**Imports pattern** (lines 24-47 of the analog):
```python
from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.tenant import Tenant
router = APIRouter(tags=["capability-envelopes"])
log = structlog.get_logger(__name__)
```
For the new module, additionally import `sqlalchemy.text as sa_text` (the atomic `UPDATE...RETURNING` is raw SQL, not ORM) and `PendingConfirmation` only if a typed read is needed elsewhere in the module.

**IDOR guard — copy verbatim, confirmed byte-identical in both existing copies** (`capability_envelopes.py:55-62` and `prompt_versions.py:66-73`):
```python
async def _get_owned_agent(agent_id: UUID, db: AsyncSession, tenant: Tenant) -> Agent:
    """Fetch agent and enforce IDOR (404, not 403, on mismatch — no existence leak)."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
```
No drift found between the two copies — safe to define a third copy (or import one) with zero adaptation. Must be called first in both new routes, before touching `confirmation_id` or the request body.

**Core pattern — GET list route shape** (`capability_envelopes.py:97-151`, `list_capability_envelopes`): `await _get_owned_agent(...)` first line, then a `select(...).where(...).order_by(...)`, structured logging on success (`log.info("list_capability_envelopes.ok", agent_id=..., tenant_id=..., row_count=...)`), returns a typed Pydantic response wrapping a list. Mirror this shape exactly for `GET /agents/{agent_id}/pending-confirmations`, substituting `PendingConfirmation` for `CapabilityEnvelope` and adding the unresolved-first / recent-resolved-24h ordering the UI-SPEC locks (`resolved_at IS NULL` sorted `expires_at ASC`, union recent-resolved sorted `resolved_at DESC`).

**Core pattern — atomic claim (verified against `22-RESEARCH.md § Code Examples`, this is the resolve route's actual body)**:
```python
# Source: pattern adapted from apps/api/app/services/transactional/idempotency.py's
# reserve_idempotency (INSERT...ON CONFLICT...RETURNING), applied as
# UPDATE...WHERE...RETURNING since the pending_confirmations row already exists.
result = await db.execute(
    sa_text(
        "UPDATE pending_confirmations "
        "SET resolved_at = now(), "
        "    resolution = CASE WHEN expires_at IS NOT NULL AND expires_at < now() "
        "                       THEN 'expired' ELSE :requested END "
        "WHERE id = :id AND agent_id = :agent_id AND resolved_at IS NULL "
        "RETURNING id, skill, arguments, resolution"
    ),
    {"id": str(confirmation_id), "agent_id": str(agent_id), "requested": body.resolution},
)
claimed = result.mappings().first()
if claimed is None:
    raise HTTPException(status_code=409, detail="Already resolved or not found")
if claimed["resolution"] == "approved":
    resolve_approved_confirmation.delay(str(confirmation_id))  # runtime queue, acks_late=True
await db.commit()
```
Note: `capability_envelopes.py`'s PATCH route commits via the AsyncSession ORM (`await db.commit()` after `setattr`); this route's write is raw SQL via `db.execute(sa_text(...))`, so the commit must still be called explicitly — `capability_envelopes.py:251` (`await db.commit()`) is the commit-placement precedent to copy (after the mutating statement, before the response is built).

**Error handling pattern:** `capability_envelopes.py` raises `HTTPException` directly at each guard point (404 unknown skill at line 190, 422 tighten-only rejection at line 232) rather than catching a service-layer exception — the new route follows the same "raise inline at the point of failure" shape, not a try/except wrapper. The 409-on-already-resolved is the one new HTTP status this route introduces; it belongs at the atomic-claim check, structured identically to the existing 404/422 raises.

**Logging pattern** (`capability_envelopes.py:254-260`): every route logs on the success path only, `log.info("<route>.ok", agent_id=str(agent_id), tenant_id=str(tenant.id), ...)`, never logging the request body itself for a route with security-relevant fields. Reuse this exact call shape and naming convention (`resolve_pending_confirmation.ok`, `list_pending_confirmations.ok`).

---

### Resolve-body schema (new, e.g. `apps/api/app/schemas/pending_confirmation.py`)

**Analog:** `apps/api/app/schemas/capability.py` (full file read this session, 111 lines)

**Shape-only-validation, `extra="forbid"` pattern** (lines 20-46 and class docstring 20-37):
```python
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict

class PendingConfirmationResolve(BaseModel):
    """POST body for /agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve.

    extra="forbid" — a typo'd or unknown field is a 422, not a silently
    ignored no-op, matching CapabilityEnvelopeUpdate's convention on this
    same authorization surface.
    """
    model_config = ConfigDict(extra="forbid")
    resolution: Literal["approved", "rejected"]
```
`capability.py`'s docstring explains *why* `extra="forbid"` matters here ("On an authorization surface, a silently dropped field is worse than a rejection") — carry that reasoning into the new schema's docstring, since the resolve body sits on the identical authorization surface. No direction/tighten-only validator is needed here (that concept does not apply to a resolve body), so the new schema is simpler than `CapabilityEnvelopeUpdate` — a single required `Literal` field, no optional fields, no `field_validator`s.

**Response schema pattern:** `CapabilityEnvelopeResponse` (lines 84-104) and `CapabilityEnvelopeListResponse` (107-111) are the shape to mirror for `PendingConfirmationResponse` / `PendingConfirmationListResponse` — one flat `BaseModel` per row, a `list[...]`-wrapping envelope model for the GET route.

---

### `apps/api/app/worker/tasks/runtime/confirmations.py` (new Celery task)

**Analog:** `apps/api/app/worker/tasks/runtime/agent.py`, `run_agent_turn` (lines 640-724+, read directly this session)

**Decorator form — copy exactly, only `name=` and `queue=` argument values differ:**
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="resolve_approved_confirmation",
)
def resolve_approved_confirmation(self, confirmation_id: str) -> dict:
    ...
```
This is the exact decorator form CLAUDE.md rule 5 requires (`acks_late=True` present) — confirmed at `agent.py:640-647`.

**`tenant_id`/`agent_id`-only task-arg discipline, conn_str decryption (CLAUDE.md rule 4), verified verbatim at `agent.py:721-724`:**
```python
# Decrypt connection string at runtime — NEVER in task args (CTL-08).
# conn_str is intentionally not logged.
from app.core.security import fernet_decrypt
conn_str = fernet_decrypt(agent.neon_connection_string)
```
The new task's sole argument is `confirmation_id: str` — not `agent_id`, not `conn_str` — matching `run_agent_turn`'s pattern of taking IDs and re-fetching everything else inside the task body via `get_sync_db()`. `agent_id` is derivable by re-reading the already-claimed `PendingConfirmation` row inside the task (the route's atomic claim already stamped `resolved_at`/`resolution` before enqueueing), so even `agent_id` need not be a task argument.

**Idempotency-on-redelivery pattern** (docstring, `agent.py:658-660,678-697`): the existing task achieves Celery-redelivery safety via a DB-side existence check before doing any work (`SELECT 1 FROM job_events WHERE job_id = ... AND event_type = 'agent.response'`) — returning early with a stable status dict if already done. The new task's equivalent guard is the **fresh `reserve_idempotency` call inside the narrow dispatcher subset** (see `confirmation_resolution.py` below), not a second, separate existence check — CLAUDE.md rule 5's "idempotency" half is satisfied by that reservation, exactly as RESEARCH.md's `§ Project Constraints` states.

**DB access pattern:** `with get_sync_db() as db:` (synchronous session, not async) — `agent.py`'s Celery tasks are synchronous throughout (Celery workers do not run an event loop by default); the new task must follow this same sync-session convention, even though `_execute_transactional_tool` itself is `async def` — expect an `asyncio.run(...)` boundary inside the task body for the narrow-helper call, matching how `run_agent_turn` bridges into `asyncio.run()` for the SDK turn (referenced in RESEARCH.md's `§ Critical Finding: No Out-of-Band Identity for Re-Entry`, `worker/tasks/runtime/agent.py:786`).

---

### `apps/api/app/services/transactional/confirmation_resolution.py` (new — the highest-risk file)

**Analog:** `_execute_transactional_tool` itself, `apps/api/app/services/transactional/tools.py:109-577` (full function read this session, all line numbers below are from this direct read, not from RESEARCH.md's summary).

**Exact step boundaries, confirmed by direct read:**

| Step | Lines | Reuse verbatim? |
|---|---|---|
| 1. IN-03 `agent_id` precondition | 162-175 | Trivial — the row supplies `agent_id` directly, no ContextVar read needed |
| 2. Capability check | 177-206 (`check_capability_access` call at 180, denial audit-write at 183-194) | **Yes — call the same function, against the LIVE envelope** |
| 2.5 IDV gate | 208-301 | **Skip entirely** — do not import `check_verified_session` |
| 3. Reserve idempotency | 303-365 (`reserve_idempotency` at 306, `replay`/`args_mismatch`/`in_progress`/`reserved` branches) | **Yes — fresh call**, using the stored `arguments` + `compute_args_hash` |
| 4. Rate + constraint checks | 369-399 (`apply_rate_and_constraint_checks` at 372) | **Yes — against the LIVE envelope, not `capability_snapshot`** |
| 5. Actor seam | 401-485 (`call_actor_gate` at 402, `require_human` branch at 429-485) | **Skip entirely** — the resolver's approval IS this verdict; never import `call_actor_gate` here (this is what T-22-ACT-05's `inspect.getsource` absence test checks) |
| 6. Adapter execute | 487-549 (`get_adapter_for_skill` at 492, try/except around `getattr(adapter, adapter_method)(...)` at 515-549) | **Yes — extract as part of the shared helper** |
| 7. Audit row + finalize | 551-577 (`write_audit_row` at 556, `finalize_idempotency` at 569) | **Yes — extract as part of the shared helper, with `actor_decision="approved_by_human"`** |

**Recommended refactor (per RESEARCH.md, confirmed feasible by this direct read):** extract lines 487-577 (steps 6-7) into a private helper, e.g. `_execute_adapter_and_audit(skill, validated, raw_args, adapter_method, agent_id, conn_str, conversation_id, snapshot, decision, rationale)`, called both by the unmodified dispatcher at its current call site and by the new narrow resolver function. This is a pure extraction — the existing dispatcher's behavior at lines 487-577 does not change, it simply calls the extracted function instead of inlining the same code.

**Denial-write pattern to copy exactly** (used at every early-return branch, e.g. lines 183-194, 326-337, 376-387): every denial path writes exactly one `write_audit_row(...)` call with `result=None`, `actor_decision=""`, `actor_rationale=""`, and an `error=f"capability.denial:{denial}"`-shaped string, before returning. The resolver's steps 2 and 4 deny-paths must reproduce this exact shape (same `error=` string prefix convention: `"capability.denial:{reason}"`), since the UI-SPEC's denial-reason translation table (`22-UI-SPEC.md` § Surface 2) keys directly off this `capability.denial:*` prefix.

**Do not reference:** `call_actor_gate` (import or call) anywhere in this new module — this is the literal assertion T-22-ACT-05 tests via `inspect.getsource` absence, mirroring the `18-05` `derive_blast_radius_warnings` regression-test precedent RESEARCH.md names.

---

### `apps/api/app/services/red_team_probe.py` (read-only — ContextVar-seeding reference)

**Confirmed exact seeding call, `red_team_probe.py:315-325`, `build_tool_server` imported at line 84:**
```python
tool_server = build_tool_server(
    conn_str=conn_str,
    agent_id=str(agent.id),
    agent_name=agent.name,
    strategy=strategy,
    conversation_id=conversation_id,
    notify_fn=lambda reason, context: None,
    tenant_id=tenant_id,
    verified_session_token="",  # deliberate: RTX-03's unverified posture
    job_id="",
)
```
The comment at line 299-301 ("`verified_session_token=""` is deliberate and load-bearing... conn_str is never logged") is the exact justification the new resolver's `confirmation_resolution.py` should cite for why it, too, passes `verified_session_token=""` (there is no session to supply, and the resolver's `confirmation_resolution.py` never reaches the IDV gate anyway since it re-implements only steps 2/3/4/6/7 — but if the resolver reuses `build_tool_server()` to seed the same ContextVars `_execute_transactional_tool` reads internally via `agent_tools.py`'s lazy import (`_agent_id_var`, `_conn_str_var`, `_conversation_id_var`, `_verified_session_token_var`, confirmed at `tools.py:150-160`), this is the exact seeding call to reuse). `conversation_id` for the resolver has no live turn to anchor it to — the resolver should generate a fresh `uuid4()` the same way `red_team_probe.py:306` does (`conversation_id = str(uuid4())`), since `write_audit_row`'s `conversation_id` field is informational, not a join key back to a real chat.

---

### `apps/api/app/services/capability_service.py` (modified — `validate_tighten_only`)

**Full verbatim current branch, read directly this session (`capability_service.py:307-313`):**
```python
    # --- enabled --------------------------------------------------------
    if "enabled" in proposed:
        current_enabled = bool(current.get("enabled", False))
        proposed_enabled = bool(proposed["enabled"])
        if proposed_enabled and not current_enabled:
            if not default_entry.get("enabled", False):
                return _reject("loosen_enabled", "enabled")
```

**The two adjacent boolean branches RESEARCH.md says are the correct behavioural model (`capability_service.py:349-364`, confirmed verbatim):**
```python
    # --- requires_confirmation ---------------------------------------------
    if "requires_confirmation" in proposed:
        current_rc = bool(current.get("requires_confirmation", False))
        proposed_rc = bool(proposed["requires_confirmation"])
        if current_rc and not proposed_rc:
            return _reject("loosen_requires_confirmation", "requires_confirmation")

    # --- requires_identity_verification -------------------------------------
    if "requires_identity_verification" in proposed:
        current_riv = bool(current.get("requires_identity_verification", False))
        proposed_riv = bool(proposed["requires_identity_verification"])
        if current_riv and not proposed_riv:
            return _reject(
                "loosen_requires_identity_verification",
                "requires_identity_verification",
            )
```
Confirmed: both are one-way safety switches, gated on **no** platform default at all — `default_entry` is never referenced in either branch, unlike `enabled`'s current branch which references `default_entry.get("enabled", False)`. The fix (per RESEARCH.md, and consistent with this direct read) removes the `default_entry` reference from the `enabled` branch entirely, making both directions unconditional — matching the shape of these two adjacent branches exactly (an `if <field> in proposed:` guard with no further gate), just for the opposite (loosening-permitted) direction since `enabled` is a capability switch, not a safety switch.

The five other branches (`rate_limit` 316-333, `constraints.max_amount_cents` 336-347, `requires_confirmation` 350-354, `requires_identity_verification` 357-364, `actor_mode` 367-383) are unchanged code — the plan's diff must touch only lines 307-313.

---

### `apps/admin/app/agents/[id]/deploy/page.tsx` (modified)

**Analog:** itself — `CapabilityZone`'s existing staged-confirm blocks for `rate_limit`/`max_amount_cents`.

**Checkbox block to edit, confirmed verbatim (`deploy/page.tsx:1126-1156`):**
```tsx
      <div className="field cap-row">
        <div className="cap-bool">
          <label htmlFor={`${envelope.skill}-enabled`}>Enabled</label>
          <input
            id={`${envelope.skill}-enabled`}
            type="checkbox"
            checked={envelope.enabled}
            disabled={enabledLocked}
            aria-disabled={isSaving || undefined}
            aria-label={`Enabled for ${skillLabel}`}
            onChange={(e) => {
              if (isSaving) return
              onSave(envelope.skill, { enabled: e.target.checked })
            }}
          />
        </div>
        <p className="help cap-caption">
          {enabledLocked
            ? 'Cannot re-enable - the platform default is off for this skill.'
            : envelope.enabled
              ? 'Enabled.'
              : 'Disabled.'}
        </p>
        {fieldErrors[`${envelope.skill}.enabled`] && (
          <p className="help cap-error">{fieldErrors[`${envelope.skill}.enabled`]}</p>
        )}
      </div>
```
`enabledLocked` is computed at line 898: `const enabledLocked = envelope.enabled === false && envelope.platform_default.enabled === false`. Per UI-SPEC (locked decision), this becomes conditional staged-confirm behavior gated on `agent.is_deployed`, not a straight deletion — `enabledLocked` itself must be deleted (no more permanent lock), but a **new** local staged/confirm state (mirroring `pendingRate`, below) replaces it for the `is_deployed === true` case only.

**Staged-confirm pattern to mirror exactly — `rate_limit`'s `requestRate`/`pendingRate`/`confirmRate` state machine, confirmed verbatim (`deploy/page.tsx:928,1036,1065-1067,1207-1240`):**
```tsx
const [pendingRate, setPendingRate] = useState<{ calls: number; unit: RateUnit } | null>(null)
...
const requestRate = () => { /* stages pendingRate from the dirty input, does not write */ }
...
const confirmRate = () => {
  if (pendingRate === null) return
  const next = `${pendingRate.calls}/${pendingRate.unit}`
  ...
}
...
{rateDirty && pendingRate === null && (
  <div className="cap-commit">
    <Btn variant="ghost" disabled={isSaving} onClick={requestRate}>Set rate limit</Btn>
  </div>
)}
{pendingRate !== null && (
  <div className="cap-confirm">
    ...
    <p className="cap-confirm-q" id={`${envelope.skill}-rate-confirm-q`}>
      {...`Change the rate limit from ${...} to ${...}?` : `Set the rate limit to ${...}?`}
    </p>
    <div className="cap-confirm-actions">
      <Btn autoFocus onClick={confirmRate}>Set {pendingRate.calls} per {pendingRate.unit}</Btn>
      ...
    </div>
  </div>
)}
```
UI-SPEC's locked decision: the checkbox skips the `cap-commit` "stage a button" intermediate step (a click is already discrete) and goes straight to a `cap-confirm` block on click, when `agent.is_deployed === true`. New state: `const [pendingEnabled, setPendingEnabled] = useState<boolean | null>(null)` (or equivalent), mounted only inside the `is_deployed` branch — the `Off → On` / `is_deployed === false` and every `On → Off` path stays the current unstaged `onSave` call unmodified.

**`aria-disabled`-during-save convention, confirmed** (`isSaving` prop, referenced at line 1138 and driven from `savingSkills[env.skill] === true` at line 2157) — the queue's per-row Approve/Reject must key off an equivalent per-row `savingConfirmations[row.id]`-shaped state, not a single shared flag, exactly matching this existing per-skill (not per-section) convention.

**`SKILL_LABELS`, confirmed verbatim (`deploy/page.tsx:247-255`):**
```tsx
const SKILL_LABELS: Record<string, string> = {
  place_order: 'Place order',
  cancel_order: 'Cancel order',
  issue_refund: 'Issue refund',
  update_subscription: 'Update subscription',
  book_slot: 'Book slot',
  update_customer_record: 'Update customer record',
  confirm_action: 'Confirm action',
}
```
Reused verbatim by the queue for its per-row skill label — no new map.

**`cap-${skill}-label` accessible-naming pattern, confirmed verbatim (`deploy/page.tsx:1115,1117`):**
```tsx
<Zone as="section" className="cap-zone" aria-labelledby={`cap-${envelope.skill}-label`}>
  <div className="section-head cap-head">
    <h3 className="label" id={`cap-${envelope.skill}-label`}>{skillLabel}</h3>
```
The queue's rows follow the equivalent `pending-${row.id}-label` shape per UI-SPEC (row accessible name is the full headline, not just the skill, since two rows can share a skill).

**`EmptyState` component signature, confirmed verbatim (`apps/admin/app/components/gotham/EmptyState.tsx:17-25`):**
```tsx
interface EmptyStateProps {
  heading: string
  body: string
  linkHref?: string
  linkLabel?: string
  className?: string
}
export default function EmptyState({ heading, body, linkHref, linkLabel, className }: EmptyStateProps) { ... }
```
Import as `import EmptyState from '../../../components/gotham/EmptyState'` (matches existing import at `deploy/page.tsx:9`), used exactly as at `deploy/page.tsx:2142-2145` with only `heading`/`body` supplied (no link props needed for the queue's empty state).

**Insertion point — confirmed real, not a guess** (`deploy/page.tsx:2130-2168`):
```tsx
          </section>

          {/* ═══ CAPABILITIES AND LIMITS ════════════════════════════════ */}
          <section className="section">
            <div className="section-head">
              <h2 className="label" id="capabilities-label">Capabilities and limits</h2>
            </div>
            {mutatingCapabilityEnvelopes.length === 0 ? (
              <EmptyState .../>
            ) : (
              <div className="cap-grid">
                {mutatingCapabilityEnvelopes.map((env) => ( <CapabilityZone .../> ))}
              </div>
            )}
          </section>
        </div>          {/* <-- new "Pending confirmations" <section> goes here, before this closing </div> */}

        <WidgetPreview mode={widgetConfig.appearance} />
      </div>
    </div>
```
The new `<section className="section">` (heading `Pending confirmations`, `h2.label` pattern, no subtitle) must be inserted immediately after the "Capabilities and limits" `</section>` closes at line 2164 and before the enclosing `</div>` at line 2165 (which itself precedes `<WidgetPreview>` at line 2167) — this is inside the same left-bench-column wrapping `<div>`, matching UI-SPEC's "last section in the left bench column" placement claim exactly.

---

## Shared Patterns

### IDOR guard (`_get_owned_agent`)
**Source:** `apps/api/app/api/v1/capability_envelopes.py:55-62` (identical to `apps/api/app/api/v1/prompt_versions.py:66-73` — confirmed no drift)
**Apply to:** Both new `pending_confirmations.py` routes (GET list, POST resolve) — must be the first statement after argument parsing, before any DB read of `confirmation_id`.
```python
async def _get_owned_agent(agent_id: UUID, db: AsyncSession, tenant: Tenant) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
```

### Atomic claim before dispatch
**Source:** `apps/api/app/services/transactional/idempotency.py`'s `reserve_idempotency` (`INSERT...ON CONFLICT...RETURNING` idiom); adapted to `UPDATE...WHERE...RETURNING` for the resolve route (§ above), and reused a second, independent time inside the Celery task's fresh `reserve_idempotency` call.
**Apply to:** The resolve route's claim, and the Celery task's execution-context reservation — two separate, stacked instances of the same idiom, not one shared call.

### `acks_late=True` + fresh idempotency reservation (CLAUDE.md rule 5)
**Source:** `apps/api/app/worker/tasks/runtime/agent.py:640-647` (decorator), `:306` in `tools.py` (`reserve_idempotency` call inside the dispatcher)
**Apply to:** `resolve_approved_confirmation` — decorator copied verbatim except `name=`/`queue=`; idempotency satisfied by the narrow helper's own fresh `reserve_idempotency` call, not a second bespoke check.

### `fernet_decrypt(agent.neon_connection_string)` (CLAUDE.md rule 4)
**Source:** `apps/api/app/worker/tasks/runtime/agent.py:724`
**Apply to:** `resolve_approved_confirmation` — the task's sole argument is `confirmation_id`; `conn_str` is decrypted at runtime after re-reading `agent_id` from the claimed row, never passed as a task arg.

### `build_tool_server()` out-of-band ContextVar seeding
**Source:** `apps/api/app/services/red_team_probe.py:315-325`
**Apply to:** `confirmation_resolution.py`'s narrow step-2/3/4/6/7 function, if it reuses `_execute_transactional_tool`'s internal ContextVar-sourced reads rather than taking `agent_id`/`conn_str` as explicit parameters. `verified_session_token=""` and a fresh `conversation_id = str(uuid4())` are the load-bearing choices to copy, both already justified in the source comment.

### Denial-write shape (`error=f"capability.denial:{reason}"`)
**Source:** `apps/api/app/services/transactional/tools.py:183-194` (and every other early-return branch, e.g. 326-337, 376-387)
**Apply to:** The narrow resolver's step-2 and step-4 deny paths — must write exactly one `write_audit_row(...)` call per deny, with the same `"capability.denial:{reason}"` string prefix the UI-SPEC's denial-translation table keys off.

---

## No Analog Found

None — every file in the Wave 0 list has a confirmed, directly-read analog in the current tree.

---

## Metadata

**Analog search scope:** `apps/api/app/api/v1/`, `apps/api/app/schemas/`, `apps/api/app/services/`, `apps/api/app/services/transactional/`, `apps/api/app/worker/tasks/runtime/`, `apps/api/app/models/`, `apps/api/alembic/versions/`, `apps/api/tests/unit/`, `apps/api/tests/integration/`, `apps/admin/app/agents/[id]/deploy/`, `apps/admin/app/components/gotham/`
**Files scanned/read directly this session:** `capability_envelopes.py` (full), `prompt_versions.py` (guard + 4 call sites), `capability.py` schema (full), `agent.py` worker task (decorator + conn_str block), `tools.py` (`_execute_transactional_tool` full body, 109-577), `red_team_probe.py` (ContextVar seeding block), `capability_service.py` (all six comparator branches, 280-389), `deploy/page.tsx` (checkbox block, SKILL_LABELS, insertion point, staged-confirm state machine), `EmptyState.tsx` (full), `pending_confirmation.py` model (full), alembic versions directory listing, `test_capability_routes.py` (parametrize + ASGITransport usage), `test_red_team_rtx.py` (gate + fixture pattern)
**Pattern extraction date:** 2026-07-28

---

## Confirmed findings beyond RESEARCH.md's original scope

1. **`_get_owned_agent` drift check: negative.** Both copies are identical; no reconciliation work is needed before Phase 22 adds a third copy or an import.
2. **`pending_confirmations` schema gap: confirmed real and unresolved by any existing column.** `id, agent_id, skill, arguments, requested_at, expires_at, resolved_at, resolution` is the complete column list. Closing the "approved and executed" vs "approved but denied" gap the UI-SPEC flags requires either a new `0020` migration (control-DB head is `0019`, confirmed by directory listing) or a read-time join against `tool_calls_audit` — there is no third option hiding in an unread column.
