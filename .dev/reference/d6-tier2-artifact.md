# D6 — the bounded artifact for the tier-2 judge

**Branch** `feat/d6-labelling-loop` @ `d0a3b4e`
**Base** `feat/d1-agent-invocation` @ `4179a5c` — **not `main`**. Nothing was rebased or merged.
**Assembled** 2026-08-09 by the session-model collector. **No code was written or run by the
collector**; every number below is quoted from a persisted document or read out of the diff.

**How to read this.** The judge's question is not "what is broken?" — tier 1 already asked that
against the code, three times, and its 47 findings are in §4. The question is **do the claims match
the evidence, and what is asserted but unproven?**

**Commits on the branch, oldest first** (18):

```
68ae317 docs(dev): plan D6 — the labelling loop, and what it actually is
c860780 feat(eval): give a LABEL its own trust tier, and wall it off from every model      <- P1
8bc6f38 test(eval): make the import-boundary detector prove each of its five arms
316ab9a test(migration): assert 0016's ADD COLUMNs by equality, not by banned substring
8c956f1 test(eval): parse the schema's source list the way test_eval_service does
aeb949b docs(dev): persist the D6 P1 findings, mutation proofs and the three weak tests
9e43d80 refactor(decision-eval): rename the fixture label constant off the column name     <- P1 fixes
e682106 fix(eval): R3 catches a capability, not a spelling; R4 fails closed
f23930e fix(migration): 0016 requires an answer for a human tier, and qualifies its schema
8e3d337 docs(dev): persist the D6 P1 review, its fixes and the trace P1 never wrote
4962ff5 feat(eval): a queue for the rows nothing could label, and the ordering it cannot have  <- P2
44f0ad5 docs(dev): persist the D6 P2 findings, both gate runs and all 14 mutation proofs
17a5774 fix(eval): the label queue's guards, demonstrated outside their own blind spots     <- P2 fixes
1c2b471 docs(dev): correct what P2's own report claimed, and persist the review
edb4fbb feat(eval): a label enters the eval, joins no golden set, reaches no customer       <- P3
fb065a2 docs(dev): persist the D6 P3 findings, both gate runs and all 12 mutation proofs
f78524e fix(eval): a justification that was never true, and the lock nobody named           <- P3 fixes
738c543 docs(dev): persist the D6 P3 review fixes, both gate runs and all 11 mutation proofs
d0a3b4e docs(dev): the review returned 14 findings, not 13
```

`738c543` and `d0a3b4e` touch only `.dev/` (verified with `git show --stat`), so **the last code
commit is `f78524e`** and the gate figures in §2 belong to it.

---

# 1. THE DIFF

`git diff --stat 4179a5c..feat/d6-labelling-loop` — 32 files, **12,412 insertions, 65 deletions**.

| group | files | diff lines | included here |
|---|---|---|---|
| `apps/api/app/` + `apps/api/alembic_tenant/` | 7 | **2,329** | **IN FULL, every hunk, below** |
| `apps/api/tests/` | 7 | 5,866 | **SUMMARISED — see §1.1. Every dropped line is named there.** |
| `.dev/` (plan, traces, reference reports, workflow, BACKLOG, HANDOFF) | 18 | 4,836 | **NOT reproduced as diff** — these are the six implementation reports and three reviews, and they are quoted verbatim in §3 and §4 instead. Named in §1.2. |

Nothing is silently truncated. Line counts are `git diff … -- <path> | wc -l` (raw diff lines,
including hunk headers).

## 1.1 The test diff, summarised — what was dropped and how much

**5,866 diff lines across 7 files are NOT reproduced below.** By file:

| file | diff lines dropped | +/− | what it is |
|---|---|---|---|
| `tests/unit/test_label_provenance.py` | 1,718 | +1712 / −0 | **new.** P1's guard suite: the tier vocabulary and its four fail-closed branches; R1 (no tier parameter); R2 (the import boundary, its five detector arms each with its own synthetic file, four evasive spellings, a negative control, and the no-worker-imports-`app.api` companion); R3 (the composed-SQL reconstruction, the name-level absence pin, 8 forgery fixtures, the documented blind spot of each scan); R4 (Celery-task arm, agent-ContextVar arm, both fail-closed-on-malfunction arms); `TestP1OpenedNoCustomerFacingDoor`; `TestTheWriteChangesNothingElse`. Collects **87**. |
| `tests/unit/test_eval_label_queue.py` | 1,877 | +1871 / −0 | **new.** P2's route suite: `TestQueueOrdering`, `TestQueueCounts`, `TestTheSelectorIsUntouched`, `TestTheLabelWrite`, `TestTenantIsolation`, `TestTheRouteShape`, `TestOnlyAHumansCredentialMayStampAHumanTier`. Collects **83** (was 54 before the P2 review fixes). |
| `tests/unit/test_label_downstream.py` | 1,247 | +1241 / −0 | **new.** P3's downstream suite: `TestNoLabelReachesACustomer`, `TestLabellingMakesARowEligibleNotPresent`, `TestGoldenMembershipIsNeverInherited`, `TestTheCountsStayHonest`, `TestTheLocksAreNotOneAssignmentAway`, `TestALabelChangesWhatTheDeployGateReads`. Collects **33** (was 22 before the P3 review fixes). |
| `tests/unit/test_migration_tenant_0016.py` | 764 | +758 / −0 | **new.** Source-level constraints on what 0016 is *allowed to contain*: additive-only, nullable-only, no DEFAULT, no backfill, the named CHECK, the non-empty-answer arm, schema qualification, revision identity/parentage/head, and one `-m integration` DB roundtrip **that skips**. Collects **33 + 1 skip**. |
| `tests/unit/test_eval_service.py` | 163 | +91 / −14 | pre-existing. Four `monkeypatch.setitem` call sites became `setattr` of a replacement mapping (forced by `MappingProxyType`); `+1` test — `TestBuildEvalRunConfig::test_the_whole_decision_reaches_the_run_record_not_just_the_flag`. |
| `tests/unit/test_decision_eval_service.py` | 57 | +29 / −3 | pre-existing. Renames for `FIXTURE_LABEL_PROVENANCE` / `label_provenance` / `fixture_label_provenance`; `+1` test — `TestFixtureDerivation::test_a_decision_fixture_does_not_read_as_a_labelled_eval_scenario`. |
| `tests/unit/test_migration_tenant_0015.py` | 40 | +13 / −4 | pre-existing. Head assertion relaxed `heads == {"0015"}` → `len(heads) == 1`, **inside the P1 feature commit `c860780`** (tier-1 P1 L6/#14). |

**The judge should note what a summary cannot carry:** whether any of those 5,866 lines contains an
assertion that is weaker than its name. Two of the branch's own findings are exactly that
(P1 §8.1/§8.2, P3 finding 7), and the ignored-new-files control is stated by the reviewers themselves
to be blind to it.

## 1.2 The `.dev/` documents, not reproduced as diff

Persisted per `BACKLOG 2.20`. All nine are quoted in §3/§4:

```
.dev/plans/260808-d6-labelling-loop.md            128 lines   the contract
.dev/reference/d6-p1-label-trust-tier.md          415         P1 implementation report
.dev/reference/d6-p1-adversarial-review.md        422         P1 tier-1 review
.dev/reference/d6-p1-review-fixes.md              319         P1 fix report
.dev/reference/d6-p2-labelling-queue.md           705         P2 implementation report
.dev/reference/d6-p2-adversarial-review.md        364         P2 tier-1 review
.dev/reference/d6-p2-review-fixes.md              298         P2 fix report
.dev/reference/d6-p3-label-downstream.md          382         P3 implementation report
.dev/reference/d6-p3-adversarial-review.md        390         P3 tier-1 review
.dev/reference/d6-p3-review-fixes.md              379         P3 fix report
.dev/traces/260808-d6-p1-label-trust-tier.md       54
.dev/traces/260809-d6-p1-review-fixes.md           51
.dev/traces/260809-d6-p2-labelling-queue.md       115
.dev/traces/260809-d6-p3-label-downstream.md       88
.dev/traces/260809-d6-p3-review-fixes.md           89
.dev/workflows/d6-labelling-loop.workflow.js      449
.dev/BACKLOG.md  (+13/-…)  .dev/HANDOFF.md  (+43)
```

**One document is UNTRACKED and therefore absent from the diff:**
`.dev/reference/d6-mining-yield.md` (399 lines, `?? ` in `git status`). Its conclusion is §7.

## 1.3 `apps/api/app/` and `apps/api/alembic_tenant/` — the full diff, every hunk

```diff
diff --git a/apps/api/alembic_tenant/versions/0016_eval_scenario_label_provenance.py b/apps/api/alembic_tenant/versions/0016_eval_scenario_label_provenance.py
new file mode 100644
index 0000000..accc80b
--- /dev/null
+++ b/apps/api/alembic_tenant/versions/0016_eval_scenario_label_provenance.py
@@ -0,0 +1,241 @@
+"""Tenant DB v16 migration — eval_scenarios label provenance (D6 P1).
+
+Revision ID: 0016
+Revises: 0015
+
+Context:
+    `eval_service.LABEL_TRUST_TIERS` has always declared five tiers, two of
+    which nothing in the system can produce: `human_verified` (2) and
+    `human_authored` (3). The only thing that resolved a tier was
+    `SCENARIO_SOURCE_TRUST_TIER`, and every source the schema allows maps to
+    `model_generated` or `customer_negative`. So the vocabulary anticipated a
+    human label and the schema had nowhere to put one.
+
+    THE TIER BELONGS TO THE LABEL, NOT TO THE ROW'S ORIGIN.
+        `eval_scenarios.source` says where the QUESTION came from. A mined
+        production failure whose answer the owner then writes by hand is
+        `source='mined'` — `customer_negative` in origin — and `human_authored`
+        in label, simultaneously and correctly. Storing the human tier by
+        widening `source` would fuse the two into one column, and a column that
+        means two things gets read as whichever one the reader had in mind:
+        that is exactly how a model-written string ends up admitted on a human
+        tier, the failure `eval_service.promotable_answer`'s docstring already
+        warns about. Hence a separate column, and hence THIS migration does NOT
+        touch 0011's `source` CHECK — see "What this migration deliberately does
+        not do" below.
+
+    The three columns (all nullable, no DEFAULT, no backfill):
+
+    label_trust_tier TEXT
+        NULL on every row that exists today and on every row any model-driven
+        producer writes. The CHECK below admits nothing but NULL and the two
+        human tiers, so **there is no value of this column that means "a model
+        wrote this"** — a model's label records no claim at all, which is what
+        NULL says. `eval_service.label_trust_tier()` resolves NULL to the row's
+        source-derived tier, which can never be a human tier (pinned by
+        test_no_schema_allowed_source_can_produce_a_human_label_tier).
+
+        WHAT THE CHECK DOES **NOT** SAY, corrected 2026-08-09. It constrains the
+        VALUE, never the AUTHOR. An earlier version of this docstring claimed
+        that "the column is set" and "a human wrote this" were the same
+        statement at the database level for any caller including one bypassing
+        the service layer. They are not: the constraint refuses
+        'model_generated' — the one value a forging writer would never choose —
+        and accepts 'human_authored' from anyone holding a tenant connection.
+        WHO may write a human value is enforced in Python by
+        `label_service`'s four restrictions, not here.
+
+    labelled_by TEXT
+        Who. NULL when label_trust_tier is NULL.
+
+    labelled_at TIMESTAMPTZ
+        When. NULL when label_trust_tier is NULL. NOT `NOW()` as a DEFAULT — a
+        default would stamp a labelling time on every unlabelled row ever
+        inserted, which is a claim about an event that did not happen.
+
+    The CHECK, and why this one is safe where 0005's was not:
+        0005 wrote `source TEXT NOT NULL CHECK (source IN (...))` inline and
+        unnamed, and 0011 then had to discover Postgres' auto-generated name at
+        apply time in order to widen it. 0014's docstring drew the lesson as
+        "do not repeat that". The lesson is about UNNAMED and about constraining
+        a column live INSERTs already write — not about CHECK constraints as
+        such. This one is:
+
+          - explicitly named (`eval_scenarios_label_trust_tier_check_v1`), so a
+            future widening is `DROP CONSTRAINT <that name>`, not an archaeology
+            expedition;
+          - on a column that is brand new, so no existing row can violate it and
+            no existing INSERT statement mentions it — it cannot break a live
+            tenant on apply;
+          - discovered rather than assumed on re-run: the DO block below
+            introspects pg_constraint/pg_attribute for whatever CHECK currently
+            governs `label_trust_tier` and drops only a DIFFERENTLY-named one,
+            the same technique 0011 used and for the same reason (never hardcode
+            a name you did not choose). On a second run ours is already present,
+            the introspection matches nothing, and the ADD is skipped — the
+            whole block is idempotent.
+
+        And it is load-bearing rather than decorative, in the narrow sense that
+        is actually true: a raw `UPDATE eval_scenarios SET label_trust_tier =
+        'model_generated'` is refused by the database itself, so the column
+        cannot come to hold a non-human vocabulary. A raw `... = 'human_authored'`
+        is NOT refused. The database bounds the vocabulary; it does not
+        authenticate the writer.
+
+        The second arm — a human tier requires a non-empty reference_answer —
+        closes the pairing the tier is a claim about. `label_trust_tier =
+        'human_authored'` beside `reference_answer = ''` asserts that a person
+        authored nothing, on a row the eval selector's `WHERE reference_answer
+        != ''` then never scores. `label_service.record_human_label` already
+        refuses to create it; this stops a direct write, a partial restore, or a
+        downgrade-and-re-upgrade from leaving one behind. It cannot break an
+        existing row: every row's tier is NULL, so every row satisfies the first
+        arm and the second is never evaluated.
+
+    What this migration deliberately does NOT do:
+        It does not widen 0011's `source` CHECK. Adding a human-flavoured source
+        value (e.g. 'owner_authored') would:
+          1. re-collapse origin into label, the precise defect this column
+             exists to separate; and
+          2. make `eval_service.is_promotable_to_verified_qa(source)` return
+             True for a schema-allowed source — opening the customer-facing
+             `verified_qa` write that `retrieval_service.verified_qa_lookup`
+             serves AHEAD of retrieval. The owner settled that question
+             eval-only on 2026-08-08 (`.dev/plans/260808-d6-labelling-loop.md`).
+        A human label is therefore recorded on the label columns and the row's
+        `source` is left saying exactly what it said before: where the question
+        came from.
+
+Cannot be applied on this machine:
+    There is no PostgreSQL server here — every `-m integration` harness skips,
+    and a skip is UNOBSERVED, never a pass. No ALTER TABLE in this file has been
+    executed against any database. The source-level assertions in
+    tests/unit/test_migration_tenant_0016.py are the only observed evidence that
+    exists for it, and they constrain what this migration is ALLOWED to contain:
+    additive columns only, nullable only, no DEFAULT, no backfill, no
+    pre-existing object touched, and a downgrade that drops only what upgrade
+    added.
+
+    Follows the established raw-SQL convention (mirrors 0009-0015) — no
+    SQLAlchemy ORM model, consistent with every other tenant-DB table.
+"""
+
+from typing import Sequence, Union
+
+from alembic import op
+
+# revision identifiers, used by Alembic.
+revision: str = "0016"
+down_revision: Union[str, None] = "0015"
+branch_labels: Union[str, Sequence[str], None] = None
+depends_on: Union[str, Sequence[str], None] = None
+
+# Explicit and stable. The v1 suffix is the affordance 0005 lacked: widening
+# this later is a DROP by name, not a pg_constraint lookup.
+_LABEL_TIER_CONSTRAINT_NAME = "eval_scenarios_label_trust_tier_check_v1"
+
+
+def upgrade() -> None:
+    # ------------------------------------------------------------------
+    # Step 1 — the three label-provenance columns. Nullable, no DEFAULT,
+    # no backfill: every row that exists predates human labelling and the
+    # honest value for "did a human label this?" on those rows is NULL.
+    # ------------------------------------------------------------------
+    op.execute("""
+        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS label_trust_tier TEXT
+    """)
+    op.execute("""
+        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS labelled_by TEXT
+    """)
+    op.execute("""
+        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS labelled_at TIMESTAMPTZ
+    """)
+
+    # ------------------------------------------------------------------
+    # Step 2 — constrain the new column to the two tiers that assert a
+    # human, mirroring 0011's technique: introspect pg_constraint /
+    # pg_attribute for whatever CHECK currently governs the column and drop
+    # only a differently-named one (never hardcode a name Postgres chose),
+    # then ADD ours under a stable explicit name if it is not already there.
+    # Both halves are guarded, so a re-run is a no-op.
+    #
+    # NULL passes: an unlabelled row makes no claim. Any non-human value is
+    # rejected by the database, so the column has no vocabulary for "a model
+    # wrote this" — which is a bound on the VALUE and not on the writer.
+    #
+    # SCHEMA-QUALIFIED, unlike 0011's copy of this block. 0011 filters on
+    # `rel.relname = 'eval_scenarios'` with no pg_namespace join and then
+    # EXECUTEs a DROP against an unqualified table name: if a tenant DB ever
+    # carried eval_scenarios in more than one schema, the name would be
+    # discovered from one table and the DROP applied to whichever the
+    # search_path resolves — dropping a constraint governing a different
+    # table's column. 0016 has never been applied anywhere, so fixing the
+    # inherited gap here is free; 0011's copy is deployed and is a separate
+    # decision.
+    # ------------------------------------------------------------------
+    op.execute(f"""
+        DO $$
+        DECLARE
+            con_name text;
+            con_schema text;
+        BEGIN
+            SELECT con.conname, nsp.nspname INTO con_name, con_schema
+            FROM pg_constraint con
+            JOIN pg_class rel ON rel.oid = con.conrelid
+            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
+            JOIN pg_attribute att
+                ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey)
+            WHERE rel.relname = 'eval_scenarios'
+              AND nsp.nspname = current_schema()
+              AND con.contype = 'c'
+              AND att.attname = 'label_trust_tier'
+              AND con.conname <> '{_LABEL_TIER_CONSTRAINT_NAME}'
+            LIMIT 1;
+
+            IF con_name IS NOT NULL THEN
+                EXECUTE format(
+                    'ALTER TABLE %I.%I DROP CONSTRAINT %I',
+                    con_schema, 'eval_scenarios', con_name
+                );
+            END IF;
+
+            IF NOT EXISTS (
+                SELECT 1
+                FROM pg_constraint con
+                JOIN pg_class rel ON rel.oid = con.conrelid
+                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
+                WHERE con.conname = '{_LABEL_TIER_CONSTRAINT_NAME}'
+                  AND rel.relname = 'eval_scenarios'
+                  AND nsp.nspname = current_schema()
+            ) THEN
+                ALTER TABLE eval_scenarios
+                    ADD CONSTRAINT {_LABEL_TIER_CONSTRAINT_NAME}
+                    CHECK (
+                        label_trust_tier IS NULL
+                        OR (
+                            label_trust_tier IN ('human_verified', 'human_authored')
+                            AND COALESCE(reference_answer, '') <> ''
+                        )
+                    );
+            END IF;
+        END $$;
+    """)
+
+
+def downgrade() -> None:
+    # Reverse order: the constraint, then the columns it constrained. IF
+    # EXISTS throughout so a downgrade against a DB that never received 0016
+    # is a no-op rather than an error.
+    #
+    # Rolling back LOSES every human label recorded since this revision. That
+    # is stated rather than mitigated: a downgrade that tried to preserve them
+    # would have to park them somewhere, and a human label parked outside the
+    # column that means "a human wrote this" is a label whose provenance the
+    # next reader has to guess.
+    op.execute(f"""
+        ALTER TABLE eval_scenarios
+        DROP CONSTRAINT IF EXISTS {_LABEL_TIER_CONSTRAINT_NAME}
+    """)
+    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS labelled_at")
+    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS labelled_by")
+    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS label_trust_tier")
diff --git a/apps/api/app/api/deps.py b/apps/api/app/api/deps.py
index 54f3b9e..05d9354 100644
--- a/apps/api/app/api/deps.py
+++ b/apps/api/app/api/deps.py
@@ -2,6 +2,7 @@
 FastAPI dependency functions for W Chats API authentication.
 
 get_current_tenant  — validates Clerk JWT (Bearer) first, falls back to X-API-Key; returns authenticated Tenant
+get_credential_kind — WHICH of those two paths authenticated this request
 get_admin           — validates X-Admin-Key header against settings.ADMIN_KEY
 get_async_redis     — yields an async Redis client for SSE pub/sub and health checks
 
@@ -19,7 +20,7 @@ import ssl
 
 import redis.asyncio as aioredis
 import structlog
-from fastapi import Depends, HTTPException, Security
+from fastapi import Depends, HTTPException, Request, Security
 from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
 from jwt import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
 from sqlalchemy import select
@@ -41,12 +42,39 @@ _admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=True)
 _bearer_scheme = HTTPBearer(auto_error=False)
 
 
+# ---------------------------------------------------------------------------
+# Which credential authenticated the request
+# ---------------------------------------------------------------------------
+# WHY THIS EXISTS. `get_current_tenant` resolves BOTH a Clerk JWT — behind which
+# there is one specific signed-in human — and an `X-API-Key`, which is a machine
+# credential a script, a scheduler or a model-driven pipeline can hold. It
+# returns the same `Tenant` either way and used to report nothing about which
+# path ran, so a route could not tell a person from an automation.
+#
+# For almost every route that is fine: they authorise an ACCOUNT to act on its
+# own data. It is not fine for exactly one route. `POST .../label` stamps
+# `eval_scenarios.label_trust_tier = 'human_authored'`, a claim about WHO WROTE a
+# string, and `VERIFIED_QA_MIN_TRUST_TIER` is defined over that hierarchy. If a
+# machine credential can produce that tier, then `human_authored` means "whoever
+# holds an API key said so", and label_service's four restrictions — which bind
+# in-process Celery and ContextVar state — cannot see an out-of-process caller at
+# all. The credential is the only evidence about the caller that survives the
+# process boundary, so it is the only place that check can live.
+CREDENTIAL_CLERK_JWT = "clerk_jwt"
+CREDENTIAL_API_KEY = "api_key"
+# Nothing recorded a credential. Reached only when `get_current_tenant` is
+# overridden (a test) or replaced; a route that cares must treat it as "cannot
+# tell" and fail CLOSED, never as "probably a human".
+CREDENTIAL_UNKNOWN = "unknown"
+
+
 # ---------------------------------------------------------------------------
 # get_current_tenant
 # ---------------------------------------------------------------------------
 
 
 async def get_current_tenant(
+    request: Request,
     bearer: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
     api_key: str | None = Security(_api_key_header),
     db: AsyncSession = Depends(get_async_db),
@@ -64,6 +92,12 @@ async def get_current_tenant(
     Raises HTTP 401 if neither credential is present or valid.
     Raises HTTP 503 if the JWKS endpoint is unreachable (network error, not an auth failure).
     Never logs credentials (T-04-02).
+
+    Records WHICH path succeeded on `request.state.credential_kind` before every
+    successful return, for `get_credential_kind` below. It is set on the way out
+    rather than returned, so no existing caller's type changes; the kind itself
+    is never logged and never leaves the process — it is a fact about the
+    credential's SHAPE, not the credential.
     """
     # --- Path 1: Clerk JWT ---
     if bearer is not None:
@@ -78,6 +112,7 @@ async def get_current_tenant(
             )
             tenant = result.scalars().first()
             if tenant:
+                request.state.credential_kind = CREDENTIAL_CLERK_JWT
                 return tenant
             # JWT valid but no tenant provisioned yet (webhook may not have fired)
             raise HTTPException(
@@ -140,6 +175,7 @@ async def get_current_tenant(
         )
         tenant = result.scalars().first()
         if tenant and verify_api_key(tenant.api_key_hash, api_key):
+            request.state.credential_kind = CREDENTIAL_API_KEY
             return tenant
 
         # Fallback path: scan rows where prefix is NULL (legacy rows without prefix)
@@ -152,12 +188,33 @@ async def get_current_tenant(
         for tenant in result.scalars():
             # verify_api_key always returns bool; never raises on mismatch (01-02 decision)
             if verify_api_key(tenant.api_key_hash, api_key):
+                request.state.credential_kind = CREDENTIAL_API_KEY
                 return tenant
 
     # T-04-03: detail string contains no key fragment or DB error
     raise HTTPException(status_code=401, detail="Authentication required")
 
 
+async def get_credential_kind(
+    request: Request,
+    tenant: Tenant = Depends(get_current_tenant),
+) -> str:
+    """CREDENTIAL_CLERK_JWT, CREDENTIAL_API_KEY or CREDENTIAL_UNKNOWN.
+
+    Depends on `get_current_tenant` rather than merely running beside it, so the
+    ordering is a property of the dependency graph and not of the parameter order
+    in whichever handler declares both. FastAPI caches the sub-dependency, so the
+    tenant is still resolved exactly once per request.
+
+    Returns CREDENTIAL_UNKNOWN rather than raising when nothing was recorded: the
+    honest answer to "which credential was this?" when no credential resolver ran
+    is "cannot tell", and the decision about what to do with that belongs to the
+    route that cares. The only route that cares — the human-label write — treats
+    it as a refusal.
+    """
+    return getattr(request.state, "credential_kind", CREDENTIAL_UNKNOWN)
+
+
 # ---------------------------------------------------------------------------
 # get_admin
 # ---------------------------------------------------------------------------
diff --git a/apps/api/app/api/v1/evals.py b/apps/api/app/api/v1/evals.py
index 37adad8..8d2f4db 100644
--- a/apps/api/app/api/v1/evals.py
+++ b/apps/api/app/api/v1/evals.py
@@ -8,6 +8,8 @@ Routes:
     GET  /agents/{agent_id}/eval-runs                   — list runs with aggregate scores (EVL-06)
     GET  /agents/{agent_id}/eval-runs/{run_id}/results  — per-scenario results (EVL-07)
     POST /agents/{agent_id}/eval-runs/trigger            — dispatch run_eval_suite manually (EVL-04)
+    GET  /agents/{agent_id}/eval-scenarios/unlabelled    — the labelling queue (D6 P2)
+    POST /agents/{agent_id}/eval-scenarios/{scenario_id}/label — record one human label (D6 P2)
 
 Architecture:
     - eval_runs and eval_results live in the TENANT DB (per-Neon-project), not the control DB.
@@ -56,19 +58,65 @@ So every metric now travels with the fact of its own measurement:
         the same rule eval_service.summarise_run_validity applies to the same
         rows. The two used to disagree about them, giving one run two
         denominators.
+
+The labelling queue (D6 P2)
+---------------------------
+`eval_scenarios` rows written with `reference_answer = ''` — every mined
+production failure, every owner-filed failing trace, every contained red-team
+finding — are inert to the nightly selector by construction, because
+`run_eval_suite` selects `WHERE reference_answer != ''`. That exclusion is
+correct and this module does not touch it. What was missing is any path by
+which a row LEAVES that state. These two routes are that path:
+
+    GET  .../eval-scenarios/unlabelled          the queue, plus the counts
+    POST .../eval-scenarios/{id}/label          one human-authored answer
+
+A labelled row becomes eligible to the existing selector with NO change to the
+selector: the write sets `reference_answer`, and `reference_answer != ''` is the
+selector's only label predicate. `counts.eligible` reports that number under the
+name the eval uses; what HOLDS the identity is the cross-module test that reads
+the predicate out of `run_eval_suite`'s source, not the payload — see
+`_queue_counts_sync`.
+
+THE WRITE REACHES ONLY WHAT THE QUEUE OFFERED. `label_service._LABEL_SQL` is
+scoped by the negation of that same predicate, so an already-answered scenario is
+a 409 rather than a silent overwrite of somebody's — possibly the golden set's —
+existing reference answer.
+
+THE AUTHOR IS DERIVED, NEVER SUBMITTED, AND ONLY A HUMAN'S CREDENTIAL MAY WRITE.
+`labelled_by` is computed from the authenticated principal inside the handler and
+the request model forbids extra fields, so a body naming an author is a 422 and
+not a field quietly ignored. That is P1's settled decision, and it is the same
+argument as `label_service`'s absent tier parameter one level up: a caller able
+to name the human is a caller able to name any human. Beyond that, the route
+refuses any credential but a Clerk JWT: `X-API-Key` authenticates an account, not
+a person, and `label_service`'s in-process guards cannot see an out-of-process
+automation holding one.
+
+THIS ORDERING IS NOT AN UNCERTAINTY ORDERING. See QUEUE_ORDERING below — the
+judge-confidence signal is not joinable to a scenario, and the response says so
+in the payload rather than leaving a reader to assume the queue is smarter than
+it is.
 """
 
 from __future__ import annotations
 
 import asyncio
+import copy
 from uuid import UUID
 
 import psycopg2
 import structlog
-from fastapi import APIRouter, Depends, HTTPException
+from fastapi import APIRouter, Depends, HTTPException, Query
+from pydantic import BaseModel, ConfigDict, Field, field_validator
+from sqlalchemy import select
 from sqlalchemy.ext.asyncio import AsyncSession
 
-from app.api.deps import get_current_tenant
+from app.api.deps import (
+    CREDENTIAL_CLERK_JWT,
+    get_credential_kind,
+    get_current_tenant,
+)
 from app.core.config import settings
 from app.core.database import get_async_db
 from app.core.security import fernet_decrypt
@@ -78,8 +126,21 @@ from app.services.eval_service import (
     DATASET_EXPLORATORY,
     DATASET_GOLDEN,
     EVAL_DATASETS,
+    HUMAN_LABEL_TIERS,
+    LABEL_TIER_COLUMN,
+    SCENARIO_SOURCE_TRUST_TIER,
+    SELECTOR_ELIGIBILITY_PREDICATE,
+    scenario_trust_tier,
+    trust_tier_rank,
 )
 from app.services.eval_service import METRIC_KEYS as EVAL_METRIC_KEYS
+from app.services.label_service import (
+    HumanLabelRefused,
+    LabelRejected,
+    assert_human_context,
+    record_human_label,
+    visible_answer,
+)
 from app.worker.tasks.runtime.eval import run_eval_suite
 
 router = APIRouter(tags=["evals"])
@@ -629,3 +690,707 @@ async def trigger_eval_run(
         "task_id": task.id,
         "agent_id": str(agent_id),
     }
+
+
+# ---------------------------------------------------------------------------
+# D6 P2 — the labelling queue
+# ---------------------------------------------------------------------------
+# WHY THIS QUEUE IS NOT ORDERED BY JUDGE UNCERTAINTY, WHICH IS WHAT IT SHOULD
+# BE ORDERED BY.
+#
+# The plan asks for uncertainty ordering, and it is right to: the rows the
+# judges were least sure about are worth several times more per owner label than
+# the newest rows. The signal exists — `validators.py` puts `confidence` on
+# every `gatekeeper.complete` and `auditor.complete` payload. It is not joinable
+# to a scenario, for three independent reasons, and this comment states them
+# rather than substituting a proxy and calling it uncertainty:
+#
+#   1. DIFFERENT DATABASES. `emit()` is called with the session from
+#      `get_sync_db()`, so `job_events` is a CONTROL-DB table, while
+#      `eval_scenarios` lives in the tenant's own Neon project (one project per
+#      tenant, so that eval branching works). There is no SQL join across them.
+#
+#   2. NO JOIN KEY, so application-side correlation does not rescue it either.
+#      `scenario_service.store_scenarios` inserts
+#      (id, source, question, reference_answer, retrieved_contexts,
+#      scenario_category, created_at) — no job_id, no conversation_id, not even
+#      the `origin_trace_id` column that 0011 added and that
+#      `insert_provenance_scenario` populates for the promote and red-team
+#      paths. And `mine_production_scenarios` selects `je.job_id` and
+#      `je.payload->>'verdict'` only: it discards `payload->>'confidence'` at
+#      the source, so the number is dropped before the row is even built.
+#
+#   3. THE ONE TENANT-SIDE CONFIDENCE COLUMN IS THE WRONG POPULATION.
+#      `verified_qa_candidates.auditor_confidence` (migration 0004) is written
+#      by `run_auditor` only when the verdict is `grounded` AND the confidence
+#      clears the threshold — precisely the complement of the
+#      fail/ungrounded/partial turns this queue is built out of. The confidence
+#      attached to a FAILED judgement is never persisted tenant-side at all.
+#
+# Making it joinable is a schema change (a key carried onto the scenario row at
+# mining time) plus a change to the miner, and it would still be retroactively
+# empty for every row already mined. That is `BACKLOG 6.4`'s real cost and it is
+# not P2's to spend.
+#
+# WHAT THE ORDERING ACTUALLY IS, and why each key earns its place:
+#
+#   origin trust tier, best first — a mined production failure, an owner-filed
+#       failing trace and a contained red-team finding are all
+#       `customer_negative`: a question a real customer asked that the agent got
+#       wrong. A `generated` row with an empty answer is `model_generated`: an
+#       artefact of a generation that came out without an answer. The first is
+#       worth more of the owner's time than the second. The rank comes from
+#       `eval_service`'s own tables, never restated here.
+#       IN SQL THIS IS `array_position(<priority array>, source) ASC`, not a
+#       `DESC` on a tier column: there is no tier column on eval_scenarios, so
+#       the ranking is carried in as a bound array whose ORDER already runs
+#       best-first, and ASC follows that array. Saying "tier DESC" — which
+#       QUEUE_ORDERING's key list used to claim — describes a query that does
+#       not exist. NULLS LAST because array_position returns NULL for a source
+#       missing from the array, and an unclassified origin must sort last.
+#   created_at, ASCENDING — oldest first, which is the opposite of recency, not
+#       a dressed-up version of it. The oldest unlabelled row is the one that
+#       has been unmeasurable the longest, and newest-first starves the tail of
+#       the queue permanently.
+#   id — the tiebreak that makes this a TOTAL order. Without it two rows sharing
+#       a source and a created_at have no defined relative position, and
+#       LIMIT/OFFSET pagination can then show one row twice and skip another.
+
+
+def _source_priority_order() -> list[str]:
+    """Scenario sources, most-worth-labelling first.
+
+    Derived from `eval_service`'s tier tables rather than restated. A source
+    added to the schema without a tier resolves to 'unknown', ranks below
+    'model_generated' by `trust_tier_rank`, and therefore sorts to the end of
+    this list — and a source added to the schema without being added to
+    `SCENARIO_SOURCE_TRUST_TIER` at all is absent from the list entirely, where
+    `array_position` returns NULL and `NULLS LAST` sorts it last. Both
+    directions fail in the same direction: an origin nobody has classified is
+    never promoted to the front of the owner's queue.
+
+    The secondary key is the source name, so the order is deterministic between
+    two sources that share a tier rather than depending on dict iteration order.
+    """
+    return sorted(
+        SCENARIO_SOURCE_TRUST_TIER,
+        key=lambda source: (-trust_tier_rank(scenario_trust_tier(source)), source),
+    )
+
+
+# The nightly selector's label predicate. IMPORTED, not spelled here: it now
+# lives in `eval_service` because `label_service`'s UPDATE needs the same string
+# and a service may not import `app.api` (R2). `run_eval_suite` filters on
+# exactly this text in all three of its scenario queries and
+# test_the_queue_selects_exactly_what_the_eval_selector_excludes reads it back
+# out of that task's source, so `unlabelled` here and "will never be scored"
+# there cannot drift apart without a test going red. THIS MODULE DOES NOT CHANGE
+# THE SELECTOR; the whole point of P2 is that a labelled row becomes eligible
+# without the selector being touched.
+#
+# The name is re-exported at module scope by the import above.
+
+# The queue itself. `dataset` (0014) and the label columns (0016) are
+# deliberately NOT selected: this route needs neither, and every column it does
+# not name is a tenant-DB migration state it cannot break on. It requires 0011,
+# which _LEDGER_SQL above already requires unconditionally.
+_UNLABELLED_QUEUE_SQL = f"""
+    SELECT
+        id,
+        source,
+        question,
+        scenario_category,
+        retrieved_contexts,
+        provenance,
+        origin_trace_id,
+        created_at
+    FROM eval_scenarios
+    WHERE NOT ({SELECTOR_ELIGIBILITY_PREDICATE})
+    ORDER BY
+        array_position(%(source_priority)s::text[], source) ASC NULLS LAST,
+        created_at ASC,
+        id ASC
+    LIMIT %(limit)s OFFSET %(offset)s
+"""
+
+
+def _order_by_keys(sql: str) -> list[str]:
+    """The ORDER BY keys of *sql*, verbatim and in order, one per line.
+
+    ONE PARSE, USED BOTH BY THE PAYLOAD AND BY THE TESTS. `QUEUE_ORDERING["keys"]`
+    used to be a hand-written list — `["origin_trust_tier DESC", ...]` — naming a
+    column that is not in the schema and a direction the statement does not use,
+    and nothing connected it to the query. The 2026-08-09 adversarial review
+    reversed the statement's own sort direction (`ASC NULLS LAST` ->
+    `DESC NULLS LAST`), which inverts the queue so `generated` is offered first
+    and `mined` last — the exact opposite of everything this module claims — and
+    all 54 tests passed while the payload went on reporting the old list.
+
+    Deriving the list from the statement closes both halves at once: the response
+    can no longer describe an ordering the database is not performing, and a test
+    asserting the expected key list is now asserting the SQL.
+    """
+    clause = sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
+    return [
+        line.strip().rstrip(",") for line in clause.strip().splitlines() if line.strip()
+    ]
+
+
+# Reported verbatim on every queue response, so a console cannot mistake this
+# for an uncertainty ranking and neither can a reader of the payload.
+# DEEP-copied at each use site: `dict(QUEUE_ORDERING)` is shallow and "keys" is a
+# list, so the copy shared the constant's list and a caller appending to the
+# returned dict poisoned it for every later request in the process. (Not
+# reachable over HTTP, where FastAPI serialises the dict — but the comparison
+# this used to draw to eval_service.VERIFIED_QA_PROMOTION_DECISION did not hold:
+# that constant is all scalars and has no nested mutable to share.)
+QUEUE_ORDERING: dict = {
+    "by_uncertainty": False,
+    "keys": _order_by_keys(_UNLABELLED_QUEUE_SQL),
+    "reason": (
+        "Judge confidence is emitted onto job_events, which is a control-DB "
+        "table, while eval_scenarios lives in the tenant's own Neon project — "
+        "no SQL join spans them. Application-side correlation has no key "
+        "either: store_scenarios writes no job_id, conversation_id or "
+        "origin_trace_id for a mined row, and mine_production_scenarios "
+        "discards payload->>'confidence' at the point it reads the event. The "
+        "one tenant-side confidence column, verified_qa_candidates."
+        "auditor_confidence, is written only for grounded turns above "
+        "threshold — the complement of the failed turns this queue is built "
+        "from. So this ordering is origin trust tier first, then oldest first; "
+        "it is not an uncertainty ordering and is not offered as a proxy for "
+        "one. BACKLOG 6.4."
+    ),
+}
+
+# The counts, in one round trip, every one of them a count out of `total`.
+# `total` is not decoration: a rate must not be constructible from this
+# response without its denominator being in the reader's hand at the same
+# moment, which is the same house rule summarise_run_validity follows.
+#
+# `unlabelled` is the NEGATION of the selector's own predicate rather than a
+# separately-written `= ''`, so `unlabelled + labelled == total` is an identity
+# of the SQL and not a coincidence of two hand-written conditions agreeing.
+# (`reference_answer` is NOT NULL since migration 0005, so neither FILTER can
+# drop a row into a third bucket.)
+_QUEUE_COUNTS_SQL = f"""
+    SELECT
+        COUNT(*)                                                        AS total,
+        COUNT(*) FILTER (WHERE NOT ({SELECTOR_ELIGIBILITY_PREDICATE}))  AS unlabelled,
+        COUNT(*) FILTER (WHERE {SELECTOR_ELIGIBILITY_PREDICATE})        AS labelled,
+        COUNT(*) FILTER (
+            WHERE {LABEL_TIER_COLUMN} = ANY(%(human_tiers)s::text[])
+        )                                                               AS human_labelled
+    FROM eval_scenarios
+"""
+
+# The same counts for a tenant DB that predates migration 0016. NOT a
+# convenience: 0016 has never been applied to any database, so this is the path
+# every tenant is on today. `human_labelled` is then reported as null with
+# `label_provenance_available: false` beside it — "no way to tell" and "none"
+# are different claims, and a metric over zero valid observations is unknown,
+# never zero.
+_QUEUE_COUNTS_PRE_0016_SQL = f"""
+    SELECT
+        COUNT(*)                                                        AS total,
+        COUNT(*) FILTER (WHERE NOT ({SELECTOR_ELIGIBILITY_PREDICATE}))  AS unlabelled,
+        COUNT(*) FILTER (WHERE {SELECTOR_ELIGIBILITY_PREDICATE})        AS labelled
+    FROM eval_scenarios
+"""
+
+
+def _queue_counts_sync(conn_str: str) -> dict:
+    """(total, unlabelled, labelled, eligible, human_labelled) for one tenant DB.
+
+    Blocking psycopg2; called through asyncio.to_thread like every other tenant
+    query in this module.
+
+    `eligible` is `labelled` — the SAME PYTHON VALUE, bound to two keys — and
+    that identity IS the P2 claim: the nightly selector's only label-related
+    predicate is SELECTOR_ELIGIBILITY_PREDICATE, so writing an answer is the
+    whole of what makes a row eligible and the selector needs no change.
+
+    WHAT THE PAYLOAD THEREFORE DOES NOT DO, corrected 2026-08-09. This used to
+    say that reporting both names "lets a reader check that from the payload
+    instead of taking it on trust". It does not: `eligible == labelled`
+    unconditionally, whatever `run_eval_suite` filters on, so a reader who
+    checked it from the payload would be reassured by a tautology. What actually
+    holds the identity is the cross-module pin,
+    test_the_queue_selects_exactly_what_the_eval_selector_excludes, which reads
+    the predicate back out of `inspect.getsource(run_eval_suite)` — a real proof
+    (replacing `!=` with the semantically identical `<>` still turns it red).
+    `eligible` is reported because a console needs the number under the name the
+    eval uses, not because it is independent evidence.
+
+    Eligible is not "will be scored tonight" — the exploratory half of the run
+    is a sample of at most EXPLORATORY_SAMPLE_SIZE rows — it is "the selector
+    will consider it".
+    """
+    try:
+        rows = _query_tenant_db_sync(
+            conn_str,
+            _QUEUE_COUNTS_SQL,
+            {"human_tiers": list(HUMAN_LABEL_TIERS)},
+        )
+        total, unlabelled, labelled, human_labelled = rows[0] if rows else (0, 0, 0, 0)
+        label_provenance_available = True
+    except psycopg2.errors.UndefinedColumn:
+        rows = _query_tenant_db_sync(conn_str, _QUEUE_COUNTS_PRE_0016_SQL, {})
+        total, unlabelled, labelled = rows[0] if rows else (0, 0, 0)
+        human_labelled = None
+        label_provenance_available = False
+
+    labelled_count = int(labelled or 0)
+    return {
+        "total": int(total or 0),
+        "unlabelled": int(unlabelled or 0),
+        "labelled": labelled_count,
+        "eligible": labelled_count,
+        "human_labelled": (
+            int(human_labelled or 0) if label_provenance_available else None
+        ),
+        "label_provenance_available": label_provenance_available,
+    }
+
+
+def _unlabelled_page_sync(conn_str: str, limit: int, offset: int) -> list[tuple]:
+    """One page of the unlabelled queue, in the order QUEUE_ORDERING describes."""
+    return _query_tenant_db_sync(
+        conn_str,
+        _UNLABELLED_QUEUE_SQL,
+        {
+            "source_priority": _source_priority_order(),
+            "limit": limit,
+            "offset": offset,
+        },
+    )
+
+
+async def _resolve_agent_tenant_db(
+    agent_id: UUID, db: AsyncSession, tenant: Tenant
+) -> str:
+    """The decrypted tenant connection string for *agent_id*, or an HTTPException.
+
+    THE TENANT-ISOLATION MECHANISM FOR BOTH QUEUE ROUTES, factored out so the
+    two cannot drift into two different checks. Byte-for-byte the sequence the
+    three routes above already use, and it is structural rather than advisory:
+
+      - the agent is fetched from the CONTROL db and 404s unless
+        `agent.tenant_id == tenant.id`, so a foreign agent_id is
+        indistinguishable from a nonexistent one and tenant enumeration gets
+        nothing;
+      - the only database a queue route ever opens is the one reached through
+        THAT agent's own encrypted connection string. A scenario_id belonging to
+        another tenant is not a row in this connection's eval_scenarios, so the
+        label write matches nothing and the route 404s on the row count.
+
+    404 rather than 403 on the ownership mismatch, matching the routes above:
+    403 would confirm the agent exists.
+
+    A SOFT-DELETED AGENT IS NOT AN AGENT HERE. `agents.py:226` states the
+    invariant — "all read routes already filter on deleted_at IS NULL, so a
+    soft-deleted agent disappears from the API surface" — and a `db.get()` does
+    not filter, so DELETE /agents/{id} followed by a label POST would have
+    decrypted a deleted agent's connection string and written a `human_authored`
+    row into its tenant database. The three older routes in this module share the
+    read-side gap and fixing them is a separate decision; extending it to a WRITE
+    is not one worth taking. Matches documents.py:122 and query.py:80.
+    """
+    result = await db.execute(
+        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
+    )
+    agent = result.scalar_one_or_none()
+    if agent is None:
+        raise HTTPException(status_code=404, detail="Agent not found")
+
+    if agent.tenant_id != tenant.id:
+        raise HTTPException(status_code=404, detail="Agent not found")
+
+    if not agent.neon_connection_string:
+        raise HTTPException(status_code=404, detail="Agent database not provisioned")
+
+    # Decrypted at runtime — never stored, never logged (T-02-01)
+    return fernet_decrypt(agent.neon_connection_string)
+
+
+@router.get("/agents/{agent_id}/eval-scenarios/unlabelled")
+async def list_unlabelled_scenarios(
+    agent_id: UUID,
+    limit: int = Query(20, ge=1, le=100),
+    offset: int = Query(0, ge=0),
+    db: AsyncSession = Depends(get_async_db),
+    tenant: Tenant = Depends(get_current_tenant),
+) -> dict:
+    """One page of scenarios awaiting a human answer, plus the queue's counts.
+
+    Security:
+        Same ownership check as every other route here — see
+        `_resolve_agent_tenant_db`.
+
+    Response shape:
+        {"scenarios": [...], "counts": {...}, "ordering": {...}, "page": {...}}
+
+    `counts` carries `total` as the denominator of every other figure in it, and
+    `human_labelled` is null (not 0) with `label_provenance_available: false`
+    beside it on a tenant DB that predates migration 0016 — which is every
+    tenant DB today, because 0016 has not been applied anywhere.
+
+    `ordering` states in the payload that this is NOT an uncertainty ordering
+    and why the signal is unavailable. See QUEUE_ORDERING.
+    """
+    conn_str = await _resolve_agent_tenant_db(agent_id, db, tenant)
+
+    rows = await asyncio.to_thread(_unlabelled_page_sync, conn_str, limit, offset)
+    counts = await asyncio.to_thread(_queue_counts_sync, conn_str)
+
+    scenarios = []
+    for (
+        scenario_id,
+        source,
+        question,
+        scenario_category,
+        retrieved_contexts,
+        provenance,
+        origin_trace_id,
+        created_at,
+    ) in rows:
+        scenarios.append(
+            {
+                "id": str(scenario_id),
+                "source": source,
+                # The tier the row's ORIGIN earns, which is NOT the tier a label
+                # would carry — that distinction is the whole of D6 P1, and this
+                # response keeps the two named apart on the wire too.
+                "origin_trust_tier": scenario_trust_tier(source),
+                "question": question or "",
+                "scenario_category": scenario_category,
+                # What the owner needs in order to write a grounded answer.
+                # Empty for every mined row (mine_production_scenarios stores
+                # []), which is itself worth seeing.
+                "retrieved_contexts": retrieved_contexts or [],
+                "provenance": provenance,
+                "origin_trace_id": origin_trace_id,
+                "created_at": created_at.isoformat() if created_at else None,
+            }
+        )
+
+    log.info(
+        "list_unlabelled_scenarios.ok",
+        agent_id=str(agent_id),
+        tenant_id=str(tenant.id),
+        returned=len(scenarios),
+        unlabelled=counts["unlabelled"],
+        total=counts["total"],
+        label_provenance_available=counts["label_provenance_available"],
+    )
+    return {
+        "scenarios": scenarios,
+        "counts": counts,
+        "ordering": copy.deepcopy(QUEUE_ORDERING),
+        "page": {"limit": limit, "offset": offset, "returned": len(scenarios)},
+    }
+
+
+# An upper bound on a stored reference answer.
+#
+# NOT ordinary input hygiene, which is why it is here and not left to the default
+# of "unbounded TEXT column". This is the one field in app/api/v1 whose stored
+# value is interpolated into a PAID MODEL'S PROMPT REPEATEDLY: run_eval_suite
+# feeds `reference_answer` to Ragas' judge on every nightly run for as long as
+# the row lives, so an oversized label costs per token per night, not once at
+# write time. 8000 characters is several pages of prose — generous for an answer
+# a support agent is meant to give — and the refusal is a 422 the caller sees
+# rather than a bill nobody attributes.
+MAX_REFERENCE_ANSWER_CHARS = 8000
+
+
+class ScenarioLabelRequest(BaseModel):
+    """The entire body of a labelling request: the answer, and nothing else.
+
+    `extra="forbid"` is load-bearing, not tidiness. It is the structural half of
+    the decision P1 settled and left to P2 to enforce: the label's author is
+    DERIVED from the authenticated principal and is never read from the request.
+    With `extra` at its default an unrecognised `labelled_by` would be dropped
+    silently, the request would succeed, and the caller would have every reason
+    to believe it had named the author. Forbidding it makes that a 422 the
+    caller can see.
+
+    There is no tier field either, and there must never be one — same argument
+    as `record_human_label`'s absent tier parameter, one level up the stack. The
+    tier is what this route asserts, not what its caller asks for.
+    """
+
+    model_config = ConfigDict(extra="forbid")
+
+    reference_answer: str = Field(
+        min_length=1,
+        max_length=MAX_REFERENCE_ANSWER_CHARS,
+        description="The answer the authenticated human wrote for this question.",
+    )
+
+    @field_validator("reference_answer")
+    @classmethod
+    def _must_carry_something_visible(cls, value: str) -> str:
+        """Strip, and refuse an answer with nothing a reader could see.
+
+        THIS BELONGS AT THE BOUNDARY, not only in the writer. `record_human_label`
+        has always refused a visibly-empty answer, but by the time it ran,
+        `_record_label_sync` had already opened a tenant connection — so the
+        property the route advertises for a refused CONTEXT ("never reaches the
+        database") was not the property it had for refused CONTENT. A whitespace
+        body decrypted a connection string and connected to Postgres before being
+        rejected. Validating here makes the refusal a 422 from Pydantic with no
+        tenant work at all, which is what the test of that name always claimed.
+
+        `min_length=1` above does not cover this: it passes `"   "`, and it
+        passes `"\\u200b"`, which no amount of `str.strip()` removes either. See
+        `label_service.visible_answer` — this calls it rather than reimplementing
+        it, so the boundary and the writer cannot disagree about the same string.
+        """
+        answer = visible_answer(value)
+        if not answer:
+            raise ValueError(
+                "reference_answer carries no visible character — an unlabelled "
+                "row is already the state this write exists to leave"
+            )
+        return answer
+
+
+def _label_principal(tenant: Tenant) -> str:
+    """The `labelled_by` value, derived from the authenticated principal.
+
+    IT NAMES AN ACCOUNT, NOT A PERSON, AND THE PREFIX SAYS SO.
+    `get_current_tenant` resolves to a `Tenant`, by either of two credential
+    paths: a Clerk JWT, behind which there is one specific human, or an
+    `X-API-Key`, which is a machine credential with no human behind it at all.
+
+    THE CREDENTIAL PATH IS NOW KNOWN — `get_credential_kind` reports it, and
+    `label_eval_scenario` refuses anything that is not a Clerk JWT — SO THE
+    REASON THIS STILL NAMES AN ACCOUNT HAS CHANGED, and it is worth stating
+    rather than leaving the reader to assume the old one still applies. Knowing
+    that a JWT authenticated the request is not the same as knowing the tenant
+    row's `clerk_user_id` is the person who sent it: the tenant is looked up BY
+    that claim on the JWT path, so today they coincide, but nothing in the schema
+    forbids a second user against one tenant and the moment one exists
+    `tenant.clerk_user_id` would name the wrong human. Attributing a write to a
+    specific person needs the principal carried out of the dependency, not
+    re-derived from the tenant row. Recording the account remains the strongest
+    claim this function can make on its own; the credential gate is what makes it
+    a claim about a human at all. `BACKLOG 4.7`'s residue is the person, not the
+    machine.
+
+    Matches `deployment.py`'s `run.approved_by = str(tenant.id)` in substance;
+    the `tenant:` prefix is added because this value is stored next to a human
+    trust tier, where a bare UUID would read as a user id.
+    """
+    return f"tenant:{tenant.id}"
+
+
+def _record_label_sync(
+    conn_str: str,
+    scenario_id: str,
+    reference_answer: str,
+    labelled_by: str,
+) -> dict:
+    """Open a tenant connection, record one human label, commit, close.
+
+    `record_human_label` neither commits nor closes — the caller owns the
+    transaction — so this function is that owner, and the tenant connection
+    string never leaves this frame.
+    """
+    conn = psycopg2.connect(conn_str, connect_timeout=10)
+    try:
+        try:
+            result = record_human_label(
+                conn,
+                scenario_id=scenario_id,
+                reference_answer=reference_answer,
+                labelled_by=labelled_by,
+            )
+        except Exception:
+            conn.rollback()
+            raise
+        conn.commit()
+        return result
+    finally:
+        conn.close()
+
+
+@router.post("/agents/{agent_id}/eval-scenarios/{scenario_id}/label")
+async def label_eval_scenario(
+    agent_id: UUID,
+    scenario_id: UUID,
+    body: ScenarioLabelRequest,
+    db: AsyncSession = Depends(get_async_db),
+    tenant: Tenant = Depends(get_current_tenant),
+    credential_kind: str = Depends(get_credential_kind),
+) -> dict:
+    """Record one human-authored reference answer on one unlabelled scenario.
+
+    Security:
+        Ownership check per `_resolve_agent_tenant_db`. The write lands in the
+        tenant's own database and nowhere else, so a `scenario_id` from another
+        tenant matches no row and returns 404.
+
+        A CLERK JWT IS THE ONLY CREDENTIAL THAT MAY PRODUCE THIS TIER, and that
+        is the phase's central claim finally being enforced rather than assumed.
+        `label_service`'s R1-R4 bind the call SITE — no tier parameter, one
+        importing module, no model-driven writer, and a runtime guard over
+        in-process Celery and ContextVar state. None of the four can see a caller
+        in a DIFFERENT PROCESS, so before 2026-08-09 any script, scheduler or
+        model-driven pipeline holding a tenant `X-API-Key` could POST model prose
+        here and have it land as `label_trust_tier='human_authored'` — the tier
+        `VERIFIED_QA_MIN_TRUST_TIER` is defined over. The hierarchy was then worth
+        the secrecy of an API key rather than any human-in-the-loop property.
+        `get_credential_kind` is the only evidence about the caller that survives
+        the process boundary, and anything that is not a Clerk JWT is refused with
+        a 403. CREDENTIAL_UNKNOWN refuses too: "cannot tell" is not "human".
+
+    The tier is not a parameter of this route and there is no field for it:
+    `record_human_label` stamps `human_authored` and the caller cannot ask for
+    anything else. The row's `source` is untouched — it still says where the
+    QUESTION came from.
+
+    Nothing here reaches a customer. `verified_qa` promotion is off by the
+    owner's settled decision of 2026-08-08 and
+    `eval_service.VERIFIED_QA_PROMOTION_DECISION` records the disablement with
+    its reason on every run.
+
+    Returns 200 with the recorded provenance and the queue's counts recomputed
+    AFTER the write, so the labelled -> eligible transition is observable in the
+    same response that caused it. An already-answered scenario is a 409, not a
+    silent overwrite — see `label_service._LABEL_SQL`.
+    """
+    # The runtime context guard, before any tenant work and before a connection
+    # could be opened. `record_human_label` re-asserts this as its own first
+    # statement; running it here as well is what keeps P1's property — a refused
+    # context never reaches the database — true across the thread hop, since
+    # asyncio.to_thread copies this context into the worker thread but
+    # _record_label_sync opens its connection before the writer can refuse.
+    try:
+        assert_human_context()
+    except HumanLabelRefused as exc:
+        log.error(
+            "label_eval_scenario.refused_context",
+            agent_id=str(agent_id),
+            tenant_id=str(tenant.id),
+            reason=str(exc),
+        )
+        raise HTTPException(
+            status_code=500,
+            detail="A human trust tier cannot be recorded from this context.",
+        )
+
+    # The credential guard, in the same place and for the same reason: it must
+    # refuse before anything is decrypted. A machine credential is not a fault of
+    # the server, so this is a 403 and not the 500 above — and the detail says
+    # which credential is required, because an operator hitting this with a
+    # service-account key needs to know the route is not simply broken.
+    if credential_kind != CREDENTIAL_CLERK_JWT:
+        log.warning(
+            "label_eval_scenario.refused_credential",
+            agent_id=str(agent_id),
+            tenant_id=str(tenant.id),
+            credential_kind=credential_kind,
+        )
+        raise HTTPException(
+            status_code=403,
+            detail=(
+                "A human-authored label requires a signed-in user session. "
+                "An API key authenticates an account, not a person, so it "
+                "cannot record a human trust tier."
+            ),
+        )
+
+    conn_str = await _resolve_agent_tenant_db(agent_id, db, tenant)
+
+    try:
+        result = await asyncio.to_thread(
+            _record_label_sync,
+            conn_str,
+            str(scenario_id),
+            body.reference_answer,
+            _label_principal(tenant),
+        )
+    except LabelRejected as exc:
+        raise HTTPException(status_code=422, detail=str(exc))
+    except HumanLabelRefused as exc:
+        log.error(
+            "label_eval_scenario.refused_context",
+            agent_id=str(agent_id),
+            tenant_id=str(tenant.id),
+            reason=str(exc),
+        )
+        raise HTTPException(
+            status_code=500,
+            detail="A human trust tier cannot be recorded from this context.",
+        )
+    except psycopg2.errors.UndefinedColumn:
+        # 0016 has not been applied to this tenant database, which is the state
+        # every tenant is in today. A provisioning gap, not a bad request, and
+        # the detail names the migration so the operator does not have to guess.
+        log.error(
+            "label_eval_scenario.label_columns_absent",
+            agent_id=str(agent_id),
+            tenant_id=str(tenant.id),
+        )
+        raise HTTPException(
+            status_code=503,
+            detail=(
+                "This tenant database has no label provenance columns — "
+                "alembic_tenant migration 0016 has not been applied to it."
+            ),
+        )
+
+    if result["rows_updated"] == 0 and result["already_labelled"]:
+        # The row is here and already carries an answer. The UPDATE is scoped to
+        # the queue's own population, so this is a refusal rather than the silent
+        # overwrite it used to be — which could replace a curated GOLDEN-set
+        # reference answer and break the paired per-item comparison the golden
+        # set exists to make, with no record of what had been there.
+        log.info(
+            "label_eval_scenario.already_labelled",
+            agent_id=str(agent_id),
+            scenario_id=str(scenario_id),
+            tenant_id=str(tenant.id),
+        )
+        raise HTTPException(
+            status_code=409,
+            detail=(
+                "This scenario already has a reference answer. Relabelling is "
+                "not part of the labelling queue: it would replace an existing "
+                "answer with no record of what it was."
+            ),
+        )
+
+    if result["rows_updated"] == 0:
+        # No row with that id in THIS tenant's database. Same 404 as a foreign
+        # agent_id, and for the same reason: the two must not be distinguishable.
+        log.info(
+            "label_eval_scenario.no_such_scenario",
+            agent_id=str(agent_id),
+            scenario_id=str(scenario_id),
+            tenant_id=str(tenant.id),
+        )
+        raise HTTPException(status_code=404, detail="Scenario not found")
+
+    counts = await asyncio.to_thread(_queue_counts_sync, conn_str)
+
+    log.info(
+        "label_eval_scenario.recorded",
+        agent_id=str(agent_id),
+        scenario_id=str(scenario_id),
+        tenant_id=str(tenant.id),
+        label_trust_tier=result["label_trust_tier"],
+        labelled_by=result["labelled_by"],
+        unlabelled=counts["unlabelled"],
+        total=counts["total"],
+        # The answer text is never logged — it is customer-domain content, and
+        # this line's job is provenance.
+    )
+    return {
+        "scenario_id": result["scenario_id"],
+        "label_trust_tier": result["label_trust_tier"],
+        "labelled_by": result["labelled_by"],
+        "counts": counts,
+    }
diff --git a/apps/api/app/services/decision_eval_service.py b/apps/api/app/services/decision_eval_service.py
index 9a71267..c0d2b4b 100644
--- a/apps/api/app/services/decision_eval_service.py
+++ b/apps/api/app/services/decision_eval_service.py
@@ -198,11 +198,22 @@ Label trust
 -----------
 Every label here is human-authored: it follows from the shipped envelope, which
 is the owner's own policy statement, or from an enforcement order documented in
-`transactional/enforcement.py`. None is model-generated. `FIXTURE_LABEL_TRUST_TIER`
+`transactional/enforcement.py`. None is model-generated. `FIXTURE_LABEL_PROVENANCE`
 records that in the vocabulary `eval_service.LABEL_TRUST_TIERS` already defines,
 because a decision eval is exactly the kind of instrument that would one day gate
 a deploy, and a model-generated label may never do that.
 
+    NOT named `label_trust_tier`, and renamed away from it on 2026-08-09.
+    `eval_scenarios.label_trust_tier` is now a real database column added by
+    alembic_tenant 0016, and `eval_service.label_trust_tier(scenario)` reads
+    that key off any mapping handed to it. While this module used the same
+    spelling, every `DecisionFixture` and the `score_decision_run` report
+    resolved through that function as `is_human_labelled() is True` — objects
+    that are not eval scenarios, carry no `reference_answer`, and whose
+    "human_authored" means something else entirely ("these fixtures were
+    hand-written"). No caller did that, and the collision was one import away
+    from a decision-eval artefact being counted as a labelled eval scenario.
+
 What this does NOT measure — stated, not hidden
 -----------------------------------------------
 1. NOTHING SHIPPED EXECUTES THESE FIXTURES. There is no scheduled runner and no
@@ -281,12 +292,16 @@ OUTCOMES: tuple[str, ...] = (
 DECISION_SIGNAL_MEASURED = "measured"
 DECISION_SIGNAL_NO_OBSERVATIONS = "no_observations"
 
-# The trust tier of every label in this fixture set, in eval_service's
+# The provenance of every label in this fixture set, in eval_service's
 # vocabulary. Not imported from there: eval_service pulls ragas, instructor and
 # anthropic at module scope, and a read-only scorer should not carry that import
 # cost. A unit test asserts the literal is a key of LABEL_TRUST_TIERS, so the two
 # cannot drift apart.
-FIXTURE_LABEL_TRUST_TIER = "human_authored"
+#
+# The NAME is deliberately not `label_trust_tier` — see the module docstring's
+# "Label trust" section. That spelling now belongs to an eval_scenarios column
+# and to the resolver that reads it off any mapping.
+FIXTURE_LABEL_PROVENANCE = "human_authored"
 
 # Is there anything in the shipped system that runs these fixtures? No. See the
 # module docstring, § What this does NOT measure. This travels on the report so a
@@ -524,7 +539,7 @@ class DecisionFixture:
     label_basis: str
     rationale: str
     label_fields: tuple[str, ...]
-    label_trust_tier: str = FIXTURE_LABEL_TRUST_TIER
+    label_provenance: str = FIXTURE_LABEL_PROVENANCE
 
     def session_precondition_is_material(self) -> bool:
         """Does the identity gate actually run for this case?
@@ -1516,7 +1531,7 @@ def score_decision_run(
           signal
               'measured' or 'no_observations'.
 
-          fixture_drift / has_driver / label_trust_tier
+          fixture_drift / has_driver / fixture_label_provenance
               the run's own attribution: what the fixture set no longer matches,
               whether anything shipped executes it, and whose labels these are.
     """
@@ -1769,7 +1784,10 @@ def score_decision_run(
         ),
         "fixture_drift": fixture_drift(),
         "has_driver": DECISION_EVAL_HAS_A_DRIVER,
-        "label_trust_tier": FIXTURE_LABEL_TRUST_TIER,
+        # Key renamed from "label_trust_tier" on 2026-08-09: that spelling is an
+        # eval_scenarios column, and eval_service.label_trust_tier() reads it off
+        # any mapping — so this report read as a human-labelled eval scenario.
+        "fixture_label_provenance": FIXTURE_LABEL_PROVENANCE,
     }
 
 
diff --git a/apps/api/app/services/eval_service.py b/apps/api/app/services/eval_service.py
index d6da723..7612270 100644
--- a/apps/api/app/services/eval_service.py
+++ b/apps/api/app/services/eval_service.py
@@ -40,8 +40,16 @@ row whose answer is the one a human FLAGGED AS FAILING. Only the branch write
 Promotion is therefore gated on the label trust hierarchy below and is
 UNREACHABLE for every scenario source the shipped schema allows. That is a
 deliberate disablement recorded on each run in `eval_runs.config`, not an
-oversight: re-enabling it is a decision that needs human-verified labels behind
-it, not a side effect of repairing persistence.
+oversight.
+
+THE SECOND HALF OF THAT PARAGRAPH USED TO SAY "re-enabling it is a decision that
+needs human-verified labels behind it", AND D6 MADE IT STALE. The system can now
+produce a `human_authored` label — rank 3, which clears the minimum — through
+one Clerk-authenticated labelling route. What holds promotion shut is no longer
+an absent producer: it is three things, named strongest first above
+LABEL_TRUST_TIERS (no caller, the resolver, the decision flag). A reader who
+takes the old sentence at face value will conclude the door is held by an
+absence and that opening it is safe once labels exist.
 
 Design notes:
 - All Ragas imports use the 0.4.x path (ragas.metrics.collections) — CLAUDE.md constraint.
@@ -58,6 +66,8 @@ from __future__ import annotations
 import hashlib
 import json
 import uuid
+from collections.abc import Mapping
+from types import MappingProxyType
 
 import anthropic
 import instructor
@@ -205,11 +215,49 @@ CONNECT_TIMEOUT_S = 5
 #   human_verified    (2) — a human read a candidate answer and confirmed it.
 #   human_authored    (3) — a human wrote the answer.
 #
-# Nothing in the shipped system produces tier >= human_verified yet: there is no
-# correction UI. That is precisely why VERIFIED_QA_MIN_TRUST_TIER is set there —
-# promotion is unreachable BY CONSTRUCTION rather than by an `if False`, and it
-# becomes reachable the moment a genuinely human-verified source exists, without
-# anyone having to remember to remove a flag.
+# THIS PARAGRAPH USED TO READ "nothing in the shipped system produces tier >=
+# human_verified yet: there is no correction UI", AND D6 MADE IT FALSE.
+# `label_service.record_human_label` — reachable from the labelling route in
+# app/api/v1/evals.py, and only behind a Clerk session — stamps `human_authored`,
+# rank 3, which CLEARS VERIFIED_QA_MIN_TRUST_TIER (rank 2) outright.
+#
+# The old argument was that promotion is "unreachable BY CONSTRUCTION rather than
+# by an `if False`, and becomes reachable the moment a genuinely human-verified
+# source exists, without anyone having to remember to remove a flag". That
+# property has inverted from a feature into a hazard: the owner settled on
+# 2026-08-08 that the labelling loop is EVAL-ONLY, so "it turns itself on the
+# moment a human tier exists" is precisely what must not happen. The flag that
+# paragraph was proud of not having is VERIFIED_QA_PROMOTION_DECISION["enabled"]
+# below, and select_promotion_candidates consults it.
+#
+# THREE INDEPENDENT LOCKS, because any one alone is one edit away from being
+# wrong. Listed strongest first, which is NOT the order they were written in —
+# the D6 P3 review's finding 4 is that the narrative named two of them and
+# omitted the one actually carrying the load today:
+#   LOCK ZERO, NO CALLER — `promote_to_verified_qa` is invoked from nowhere in
+#       `app/`. `run_eval_suite` returns a hardcoded `promoted: 0` and never
+#       calls it. This is the load-bearing lock today: with it in place the
+#       other two are defence in depth. Two absence pins cover the two doors
+#       that ever held the call —
+#       test_eval_task.py::TestPromotionIsUnreachableFromTheTask::
+#       test_module_does_not_import_or_call_promote_to_verified_qa and
+#       test_eval_service.py::test_run_eval_for_agent_does_not_promote — and
+#       BOTH ARE MODULE-SCOPED, not tree-wide: a THIRD module introducing the
+#       call would trip neither. Say that rather than claim a pin that does not
+#       exist.
+#   the RESOLVER — the promotion gate reads `source`, the QUESTION's origin,
+#       which labelling never changes (the label write does not touch it), so no
+#       labelled row clears it. NOTE the hazard here is LATENT, not live: see
+#       select_promotion_candidates' gate 1, where the swap the comment warns
+#       about is inert today because no selector projects `label_trust_tier`.
+#   the DECISION — `enabled: False`, consulted LAST, so a row that cleared every
+#       other gate is refused and COUNTED rather than promoted.
+#
+# All three are process-local: none is recorded in any database, and a second
+# process running a different build carries its own copy. The two constants
+# below are `MappingProxyType` so that `X["enabled"] = True` raises rather than
+# lifting a lock for the life of a process; rebinding the module attribute still
+# works, and is pinned absent by test_label_downstream.py's mutation scan.
 LABEL_TRUST_TIERS: dict[str, int] = {
     "unknown": -1,
     "model_generated": 0,
@@ -222,7 +270,14 @@ LABEL_TRUST_TIERS: dict[str, int] = {
 # widened CHECK constraint in alembic_tenant 0011
 # (source IN ('generated', 'mined', 'production', 'red_team')); a new source
 # value that lands without a tier here resolves to 'unknown' and is refused.
-SCENARIO_SOURCE_TRUST_TIER: dict[str, str] = {
+#
+# READ-ONLY AT RUNTIME (D6 P3 review, finding 4). This mapping is one of the two
+# things holding the customer-facing promotion write shut, and until this commit
+# it was a plain dict that any module in the process could open with a single
+# `SCENARIO_SOURCE_TRUST_TIER["mined"] = "human_authored"` — a strictly more
+# dangerous surface than the label writer, which carries four independent
+# restrictions. `MappingProxyType` makes that assignment raise `TypeError`.
+SCENARIO_SOURCE_TRUST_TIER: Mapping[str, str] = MappingProxyType({
     # scenario_service.generate_eval_suite_for_agent — Haiku wrote the answer.
     "generated": "model_generated",
     # scenario_service.mine_production_scenarios — a production failure, stored
@@ -233,27 +288,104 @@ SCENARIO_SOURCE_TRUST_TIER: dict[str, str] = {
     "production": "customer_negative",
     # red_team.py finding containment — an attack that succeeded.
     "red_team": "customer_negative",
-}
+})
 
 # The minimum tier a scenario must carry before its answer may be written into
 # verified_qa, which retrieval_service serves to customers ahead of retrieval.
 VERIFIED_QA_MIN_TRUST_TIER = "human_verified"
 
+# The refusal string `select_promotion_candidates` counts a row under when the
+# DECISION, rather than any property of the row, is what held it back. Named
+# separately from the reason prose because it is the key a reader greps for in a
+# refusals dict.
+#
+# IT IS NOT THE MEASUREMENT THIS COMMENT USED TO CLAIM (D6 P3 review, finding
+# 1). The claim was that `refusals[PROMOTION_DISABLED_REFUSAL]` is "how many rows
+# would have been written into verified_qa if the owner flipped the decision".
+# Probed against the shipped configuration — all four schema sources, every row
+# carrying a human-authored label tier, a non-empty answer and 1.0/1.0 scores —
+# that count is 0, and it is structurally 0, for three separate reasons:
+#
+#   1. Gate 1 (the trust tier, on `source`) runs FIRST and refuses every source
+#      the shipped schema allows, so no row reaches gate 3 to be counted by it.
+#      What the number really answers is "what would flipping the decision
+#      promote GIVEN the resolver gate is lifted too" — two edits, not one.
+#   2. Nothing under `app/` calls select_promotion_candidates or
+#      promote_to_verified_qa, so the number is never computed at all.
+#   3. `run_eval_suite`'s return dict does not carry `refusals`, so even once
+#      computed there is nowhere an owner could read it from.
+#
+# An owner shown 0 would conclude that flipping the decision promotes nothing,
+# when 0 means "the resolver gate refused them all first". THE GATE ORDERING IS
+# KEPT ANYWAY, on the merit it actually has: a refused row keeps its MOST
+# SPECIFIC reason. A row held by its origin reports `trust_tier:customer_negative`
+# — which names what would have to change — instead of the decision's reason,
+# which names something that was never reached. The `promoted + refused ==
+# scored` invariant holds under either ordering, so a promotion rate still
+# cannot be constructed without its denominator.
+PROMOTION_DISABLED_REFUSAL = "promotion_disabled:eval_only"
+
 # Recorded verbatim on every run in eval_runs.config so the disablement is a
 # statement in the run record with a reason attached, rather than an absence a
 # future reader has to infer. Copied (never handed out by reference) at every
 # use site so a caller mutating the returned dict cannot poison the constant.
-VERIFIED_QA_PROMOTION_DECISION: dict = {
+#
+# KEPT FLAT ON PURPOSE. `build_eval_run_config` copies it with `dict(...)`, which
+# is shallow, so a nested dict or list here would be handed out by reference and
+# a caller mutating it would poison the constant for the process —
+# test_promotion_decision_is_copied_not_shared only observes the top level.
+#
+# THE REASON CHANGED IN D6 P3, AND THE OLD ONE WOULD NOW MISLEAD. It said "no row
+# is promotable until a correction UI produces human-verified answers". D6 P1/P2
+# built that correction UI. A run stamping the old text would be telling a later
+# reader that the door is held by an absent producer, when the producer now
+# exists and the door is held by a decision and a resolver choice. An absence a
+# reader has to infer is bad; a stale statement a reader will believe is worse.
+#
+# READ-ONLY AT RUNTIME, for the same reason SCENARIO_SOURCE_TRUST_TIER is: this
+# was a plain dict, so `VERIFIED_QA_PROMOTION_DECISION["enabled"] = True` from
+# any module in the process opened the customer-facing write for the life of
+# that process, with no pin anywhere watching for it. `dict(...)` on a
+# MappingProxyType still yields a fresh plain dict, so build_eval_run_config's
+# copy semantics are unchanged.
+VERIFIED_QA_PROMOTION_DECISION: Mapping[str, object] = MappingProxyType({
     "enabled": False,
     "min_trust_tier": VERIFIED_QA_MIN_TRUST_TIER,
+    # Eval-only: a label improves what the eval can measure and reaches no
+    # customer. Owner, 2026-08-08 (.dev/plans/260808-d6-labelling-loop.md).
+    "scope": "eval_only",
+    "decided_on": "2026-08-08",
+    # The tier the shipped human-label writer stamps. Spelled as a literal here
+    # because that writer imports THIS module, so the dependency cannot run the
+    # other way; pinned equal across the boundary by a test rather than by hope.
+    #
+    # AND THE PROSE BELOW MAY NOT NAME THAT MODULE EITHER. R2's import-boundary
+    # scan in test_label_provenance.py reads every non-docstring string constant
+    # in this tree and refuses any that MENTIONS the writer's module or function
+    # name — because `import_module("app.services." + "...")` is how the
+    # full-dotted-path version of that scan was evaded. A prose mention would be
+    # indistinguishable from that, so the reason describes the writer instead of
+    # naming it. The scan fired on the first draft of this constant, which is
+    # the only evidence worth having that it is still doing its job.
+    "producible_label_tier": "human_authored",
+    "refusal_reason": PROMOTION_DISABLED_REFUSAL,
     "reason": (
-        "verified_qa is served to customers ahead of retrieval, so only a "
-        "human-verified or human-authored answer may enter it. Every scenario "
-        "source the schema currently allows is model-generated or labels a "
-        "negative, so no row is promotable until a correction UI produces "
-        "human-verified answers."
+        "verified_qa is served to customers by retrieval_service."
+        "verified_qa_lookup AHEAD of retrieval, so one mistyped label would be "
+        "answered to a real customer with no eval between the typo and them. "
+        "Since D6 the owner CAN produce a human_authored label, through the "
+        "one labelling route a Clerk session may drive, and that tier outranks "
+        "min_trust_tier — so the disablement is no longer the absence of a "
+        "producer, it is the owner's settled decision of 2026-08-08 that the "
+        "labelling loop is eval-only. THREE things hold it shut, strongest "
+        "first: promote_to_verified_qa has no caller anywhere under app/, so "
+        "the gates below are defence in depth rather than the thing doing the "
+        "work; select_promotion_candidates gates on eval_scenarios.source, "
+        "which labelling never changes; and it refuses outright while enabled "
+        "is false. Turning promotion on is a decision plus a code change, and "
+        "never a migration."
     ),
-}
+})
 
 
 def scenario_trust_tier(source: str | None) -> str:
@@ -295,6 +427,138 @@ def is_promotable_to_verified_qa(source: str | None) -> bool:
     )
 
 
+# ---------------------------------------------------------------------------
+# The tier a LABEL carries — which is not the tier its QUESTION's origin earns
+# ---------------------------------------------------------------------------
+# SCENARIO_SOURCE_TRUST_TIER above answers "where did this QUESTION come from?".
+# It is the only tier resolver that existed, and it is why LABEL_TRUST_TIERS
+# declared human_verified and human_authored that nothing could produce: there
+# was no source value a human could occupy without also claiming to be the
+# question's origin.
+#
+# A mined production failure whose answer the owner then writes by hand is
+# `customer_negative` in ORIGIN and `human_authored` in LABEL, at the same time,
+# and both statements are true. Collapsing them into one column is how a
+# model_generated string ends up admitted on a human tier — the failure
+# promotable_answer's docstring already warns about. So the label carries its
+# own tier, in alembic_tenant 0016's `label_trust_tier` column, and the row's
+# `source` keeps meaning exactly what it meant before.
+#
+# THE TWO TIERS THAT ASSERT A HUMAN. Kept in step with 0016's CHECK constraint
+# by test_the_human_tiers_match_the_migrations_check_constraint, which parses the
+# migration rather than restating it.
+HUMAN_LABEL_TIERS: tuple[str, ...] = ("human_verified", "human_authored")
+
+# The eval_scenarios column 0016 adds. Named here so the read path and the write
+# path (label_service) agree on one spelling.
+LABEL_TIER_COLUMN = "label_trust_tier"
+
+# The nightly selector's ONLY label predicate, spelled once for the whole system.
+# `run_eval_suite` filters on exactly this text in all three of its scenario
+# queries; `evals.py`'s labelling queue is its negation; and `label_service`'s
+# UPDATE is scoped by that same negation so the write cannot reach a row the
+# queue never offered.
+#
+# It lives HERE rather than in either consumer because the two consumers are on
+# opposite sides of an import wall: `label_service` may not import `app.api`
+# (R2), and `app/api/v1/evals.py` is not something a service may depend on. This
+# module is the one both already import, so one spelling can serve all three
+# without inverting a dependency.
+#
+# Kept honest across the module boundary by
+# test_the_queue_selects_exactly_what_the_eval_selector_excludes, which reads
+# this constant back out of `inspect.getsource(run_eval_suite)`: if the task ever
+# stops filtering on it, "unlabelled" and "will never be scored" have come apart
+# and that test is what makes it audible.
+SELECTOR_ELIGIBILITY_PREDICATE = "reference_answer != ''"
+
+# Sentinel distinguishing "the row has no reference_answer key" (a narrow
+# projection) from "the row has an empty one" (a human claim about a string that
+# is not there). `None` cannot do that job: it is a legitimate column value.
+_NO_REFERENCE_ANSWER_KEY = object()
+
+
+def is_human_label_tier(tier: str | None) -> bool:
+    """True iff *tier* is one of the two tiers that assert a human wrote it."""
+    return tier in HUMAN_LABEL_TIERS
+
+
+def _is_an_eval_scenario(scenario: dict) -> bool:
+    """Does this mapping look like an `eval_scenarios` row at all?
+
+    A row selected from that table always carries `source` (NOT NULL since 0005)
+    or `reference_answer` (NOT NULL since 0005) — usually both. A mapping with
+    neither is not a scenario, whatever `label_trust_tier` key it happens to
+    hold.
+
+    This exists because of a real collision, not a hypothetical one:
+    `decision_eval_service` used to publish `label_trust_tier: 'human_authored'`
+    on every `DecisionFixture` and on its run report, meaning "these fixtures
+    were hand-written". Handed to the function below, all 23 of them resolved as
+    `is_human_labelled() is True` — a human-authorship claim about a
+    `reference_answer` those objects do not have. That constant is now named
+    `FIXTURE_LABEL_PROVENANCE`; this check is the half that does not depend on
+    every other module in the tree choosing a different spelling.
+    """
+    return "source" in scenario or "reference_answer" in scenario
+
+
+def label_trust_tier(scenario: dict) -> str:
+    """The trust tier of the scenario's LABEL (its reference_answer).
+
+    Four cases, and the direction of each is the whole point:
+
+      the column is set to a human tier  -> that tier. The label outranks the
+          origin, which is the case the column exists for: an owner-written
+          answer on a mined question.
+      the column is NULL / absent        -> the origin's tier, via
+          scenario_trust_tier(source). This is a DOWNGRADE path only: no source
+          the schema allows resolves to a human tier (pinned by
+          test_no_schema_allowed_source_can_produce_a_human_label_tier), so the
+          fallback can never manufacture a human claim out of a row's origin.
+      a human tier on an EMPTY reference_answer -> 'unknown'. The claim is about
+          a string that is not there. `record_human_label` refuses to create
+          that row and 0016's CHECK refuses to store it, so a row in that state
+          arrived by bypassing both — which is the same situation as the branch
+          below, and gets the same answer. Note the shape: the check applies
+          only when the key is PRESENT, so a narrow projection that did not
+          select `reference_answer` is not silently downgraded.
+      the column holds anything else     -> 'unknown', which ranks BELOW
+          model_generated. 0016's CHECK permits only NULL or a human tier there,
+          so any other value means the column was written by something that
+          bypassed both the service layer and the database constraint, and a
+          provenance nobody can account for is worth less than one that has been
+          accounted for and found untrustworthy.
+
+    Takes the whole scenario dict rather than two strings so that a caller
+    cannot pass the source where the label tier belongs, or reach past this to
+    read `scenario["label_trust_tier"]` raw and skip the fail-closed branch.
+    Taking the whole dict is also what makes `_is_an_eval_scenario` possible:
+    the function can tell a scenario from something else that merely has the
+    key, which two loose strings could not.
+    """
+    raw = scenario.get(LABEL_TIER_COLUMN)
+    if raw is None or raw == "":
+        return scenario_trust_tier(scenario.get("source"))
+    if not _is_an_eval_scenario(scenario):
+        return "unknown"
+    if is_human_label_tier(raw):
+        answer = scenario.get("reference_answer", _NO_REFERENCE_ANSWER_KEY)
+        if answer is not _NO_REFERENCE_ANSWER_KEY and not str(answer or "").strip():
+            return "unknown"
+        return str(raw)
+    return "unknown"
+
+
+def is_human_labelled(scenario: dict) -> bool:
+    """True iff a human authored or verified this scenario's reference_answer.
+
+    False for every row that predates alembic_tenant 0016 and for every row any
+    model-driven producer writes, because those carry no label tier at all.
+    """
+    return is_human_label_tier(label_trust_tier(scenario))
+
+
 # ---------------------------------------------------------------------------
 # The golden set, and the denominators every measurement travels with
 # ---------------------------------------------------------------------------
@@ -1661,7 +1925,7 @@ def select_promotion_candidates(
 ) -> tuple[list[tuple[dict, dict]], dict[str, int]]:
     """Decide which scored scenarios may enter verified_qa. Pure — no I/O.
 
-    Two independent gates, applied in this order:
+    Three independent gates, applied in this order:
 
     1. TRUST TIER — is this scenario's answer allowed to be served to a
        customer at all? Checked FIRST and it is not a tiebreak: a high score on
@@ -1669,8 +1933,38 @@ def select_promotion_candidates(
        not about the answer's truth, so no score may buy a source out of its
        tier. Checking it first also means an unpromotable row never reaches the
        embedding call below.
+
+       IT READS `source`, THE QUESTION'S ORIGIN, AND THAT IS DELIBERATE RATHER
+       THAN LEFT OVER. `label_trust_tier(scenario)` is the resolver that answers
+       "who wrote this ANSWER", and since D6 it can return `human_authored` for
+       an owner-labelled row, so swapping this gate to it reads like a bug fix.
+
+       THE HAZARD IS LATENT, NOT LIVE, AND THE EARLIER WORDING OVERSTATED IT
+       (D6 P3 review, finding 13). The swap would change nothing today: none of
+       `run_eval_suite`'s three selectors projects `label_trust_tier`, and
+       `label_trust_tier()` / `is_human_labelled()` have no production caller,
+       so every production scenario dict reaching this function falls through to
+       the source-based tier regardless of which resolver is named here. Only a
+       hand-built dict (a unit test's) carries the column at all. `BACKLOG 4.12`
+       — projecting `label_trust_tier` into the selectors — is the change that
+       ACTIVATES this hazard, so 4.12 and this gate must be re-argued together.
     2. SCORE THRESHOLD — D-21's 0.90/0.90 quality bar, applied only to answers
        that cleared the tier gate.
+    3. THE DECISION — VERIFIED_QA_PROMOTION_DECISION["enabled"]. Not a property
+       of the row: a policy the owner set. LAST so that a refused row keeps its
+       MOST SPECIFIC reason: a row held by its origin reports
+       `trust_tier:customer_negative`, which names what would have to change,
+       rather than the decision's reason, which names a gate it never reached.
+
+       IT IS NOT A MEASUREMENT, WHICH IS WHAT THIS PARAGRAPH USED TO SAY (D6 P3
+       review, finding 1). `refusals[PROMOTION_DISABLED_REFUSAL]` was described
+       as "what turning promotion on would actually promote". Gate 1 refuses
+       every source the shipped schema allows before gate 3 is ever reached, so
+       that count is 0 and structurally always 0; nothing under `app/` calls
+       this function; and `run_eval_suite` does not return `refusals`. See
+       PROMOTION_DISABLED_REFUSAL's own comment for the full statement. An early
+       `return []` would report the same zero — the argument against it is the
+       specificity of the reasons above, not a number nobody can read.
 
     A score whose scenario cannot be found is refused ('scenario_not_found'),
     not skipped silently: promoting an answer we cannot attribute to a question
@@ -1714,6 +2008,18 @@ def select_promotion_candidates(
             _refuse("below_score_threshold")
             continue
 
+        # THE LAST GATE IS A DECISION, NOT A PROPERTY OF THE ROW. Everything
+        # above asked something about this scenario; this asks whether the owner
+        # has turned the customer-facing write on at all, and the answer is no
+        # (eval-only, 2026-08-08). Last so that the refusals above keep their
+        # more specific reasons — NOT, as this comment used to say, because the
+        # count here is readable as "would have been promoted". It is not: gate
+        # 1 refuses every schema-allowed source first, so this count is 0 and
+        # structurally always 0. See PROMOTION_DISABLED_REFUSAL's comment.
+        if not VERIFIED_QA_PROMOTION_DECISION["enabled"]:
+            _refuse(PROMOTION_DISABLED_REFUSAL)
+            continue
+
         candidates.append((scenario, score))
 
     return candidates, refusals
@@ -1730,13 +2036,34 @@ def promote_to_verified_qa(
     retrieval_service.verified_qa_lookup BEFORE hybrid search, at 0.93 cosine
     similarity — so this function's output goes straight to end users. Its gate
     is therefore the label trust hierarchy first (select_promotion_candidates),
-    the D-21 score thresholds second. No scenario source the shipped schema
-    allows clears the trust gate today, so this function performs zero writes
-    and does not open a connection at all.
+    the D-21 score thresholds second, and the owner's decision last.
+
+    WHY IT IS UNREACHABLE HAS CHANGED, AND THE OLD ANSWER IS NO LONGER THE WHOLE
+    ANSWER. It used to be "no scenario source the shipped schema allows clears
+    the trust gate", full stop. That is still true — labelling does not touch
+    `source` — but D6 gave the system a producer of `human_authored` labels, so
+    "unreachable" rests on THREE things and a reader must be told all of them,
+    STRONGEST FIRST (D6 P3 review, finding 4 — the earlier text said two and
+    omitted the one carrying the load):
+
+      0. NO CALLER. This function is invoked from nowhere under `app/`.
+         `run_eval_suite` returns a hardcoded `promoted: 0`. While that holds,
+         locks 1 and 2 are defence in depth and this is the whole of the
+         guarantee. It is pinned in the two modules that ever held the call —
+         and in only those two, so a third module adding it trips nothing.
+      1. THE RESOLVER. The gate reads the question's origin rather than the
+         label's tier. Latent rather than live: see select_promotion_candidates,
+         gate 1 — the "obvious fix" swap is inert until `BACKLOG 4.12` projects
+         `label_trust_tier` into the selectors.
+      2. THE DECISION. VERIFIED_QA_PROMOTION_DECISION["enabled"] is False by the
+         owner's settled eval-only decision of 2026-08-08.
+
+    This function still performs zero writes and does not open a connection at
+    all.
 
     It is retained rather than deleted for two reasons: the promotion machinery
     (D-22 provenance, D-23 question_vector, the SELECT-first idempotency check)
-    is correct and will be needed once human-verified labels exist, and a
+    is correct and will be needed if the decision is ever flipped, and a
     surviving second lock on the door means a future caller that reintroduces
     the call still cannot serve a model-written answer to a customer.
 
diff --git a/apps/api/app/services/label_service.py b/apps/api/app/services/label_service.py
new file mode 100644
index 0000000..e80d852
--- /dev/null
+++ b/apps/api/app/services/label_service.py
@@ -0,0 +1,471 @@
+"""The one write path that may stamp a human trust tier on an eval label.
+
+`eval_service.LABEL_TRUST_TIERS` has declared `human_verified` (2) and
+`human_authored` (3) since D5 and nothing could produce either. This module
+produces one of them — and the interesting part is not the UPDATE, it is the set
+of restrictions that make the UPDATE unreachable from anything a model drives.
+
+WHY THAT MATTERS MORE THAN THE WRITE ITSELF
+    A trust tier is a claim about WHO WROTE a string. The claim is worth exactly
+    as much as the difficulty of forging it. If a Celery task, an agent tool, a
+    judge or a test fixture can call something that stamps `human_authored`, then
+    `human_authored` means "some code said so", the hierarchy collapses to one
+    tier, and `VERIFIED_QA_MIN_TRUST_TIER` — the gate standing between a model's
+    prose and a real customer, via `retrieval_service.verified_qa_lookup` — is
+    guarding a door with no wall attached.
+
+THE FOUR RESTRICTIONS, EACH INDEPENDENTLY MUTABLE AND EACH SEPARATELY PINNED
+    1. There is no tier parameter.
+       `record_human_label()` does not accept a tier. It stamps
+       HUMAN_AUTHORED_TIER, a module constant. A caller cannot ask for a tier
+       because there is nowhere to put the request.
+       Pinned by test_the_writer_has_no_tier_parameter.
+
+    2. The import boundary.
+       Only `app/api/v1/evals.py` may reference this module. Nothing under
+       `app/worker/` (every Celery task), nothing else under `app/services/`
+       (every agent tool, judge, scenario producer and eval service), nothing
+       under `scripts/` or `_runlogs/`, and no test module but the one that
+       tests it.
+       Pinned by test_only_the_one_named_api_module_may_reference_the_writer
+       and by test_no_worker_or_service_module_imports_the_api_layer, which
+       closes the transitive route through a re-export.
+
+       WHAT THIS IS NOT. It used to say "reachable from an authenticated HTTP
+       request and from nowhere else in the tree", with `app/api/` as the
+       permitted region. `app/api/` is not an authentication property: it also
+       holds `widget.py`, whose own header records `/widget/{agent_id}/config`
+       and `/widget/jobs/{job_id}/events` as no-auth, and whose chat routes run
+       behind a JWT issued to an anonymous website visitor. The test asserts a
+       module path, so the claim is a module path.
+
+    3. The model-driven writers do not write the label columns — and do not
+       name them.
+       `scenario_service.store_scenarios` and
+       `scenario_service.insert_provenance_scenario` are the only INSERT paths
+       into `eval_scenarios` that go through a service, and between them they
+       carry every model-driven producer: generated suites, mined production
+       failures, promoted traces, contained red-team findings. Neither
+       statement names a label-provenance column, so the failure mode of that
+       route is a NULL tier, which reads as "no human labelled this".
+
+       THIS SAID "PHYSICALLY CANNOT" UNTIL 2026-08-09, AND THAT WAS FALSE. The
+       P1 adversarial review appended a function to a real Celery task module
+       that issued an f-string `UPDATE eval_scenarios SET ...
+       label_trust_tier = 'human_authored'` — importing nothing, calling
+       nothing — and every test in test_label_provenance.py stayed green,
+       because R3 was a substring scan over single string constants. R3 is now
+       two scans with different blind spots: a composed-SQL reconstruction
+       (f-strings, `+`, `%`, `.format`, `.join`, `public.` and quoted
+       identifiers) and a name-level absence pin over `app/worker/`, the rest
+       of `app/services/`, `scripts/` and `_runlogs/`. What is true is that no
+       forgery shape anyone has yet devised passes unnoticed; what is NOT true
+       is that raw SQL cannot reach the column.
+       Pinned by test_only_the_label_writer_writes_the_label_columns,
+       test_no_model_driven_module_names_a_label_column_at_all, and the eight
+       forgery fixtures in
+       test_the_write_scan_sees_a_forged_label_write_however_it_is_spelled.
+
+    4. The runtime context guard.
+       Belt to the import boundary's braces, and the one that survives a caller
+       who reaches this module by a route the static checks did not model
+       (importlib, a monkeypatched attribute, a future refactor that moves a
+       module across the boundary). `record_human_label` refuses outright when
+       it finds itself executing inside a Celery task or inside an agent tool
+       call.
+       Pinned by test_a_celery_task_context_refuses_the_human_label and
+       test_an_agent_tool_context_refuses_the_human_label.
+
+    Restriction 4 is thread-local: Celery's current-task stack lives in
+    `celery.utils.threads._LocalStack`, so a bare thread spawned from inside a
+    task would not see the task, and `agent_tools`' ContextVars do not propagate
+    into `run_in_executor` threads either (agent_tools.py:161). That hole is
+    stated rather than papered over, and it is the reason restriction 4 is the
+    last line rather than the only one: restrictions 2 and 3 do not depend on
+    which thread is asking.
+
+WHAT THE FOUR RESTRICTIONS DO NOT COVER, AND WHAT P2 OWES
+    They authenticate the CALL SITE. They say nothing about the CONTENT of
+    `reference_answer` or about the identity in `labelled_by`, both of which
+    this function takes on the caller's word — which is, at the human, exactly
+    the defect restriction 1 forbids at the tier. An `app/api/` route that asks
+    a model to draft an answer and forwards it as
+    `record_human_label(reference_answer=<model prose>,
+    labelled_by='owner@example.com')` produces a `human_authored` row of model
+    output and trips none of R1-R4: no Celery task, no agent ContextVar, no
+    import violation, no SQL scan hit.
+
+    THE DECISION, TAKEN NOW SO THAT P2 INHERITS IT RATHER THAN INVENTING IT:
+
+      - `labelled_by` is DERIVED FROM THE AUTHENTICATED PRINCIPAL inside the
+        handler. It is never read from the request body, and no route may
+        accept it as a field. Same argument as restriction 1: a caller able to
+        name the human is a caller able to name any human.
+      - `reference_answer` must arrive ON the authenticated request, as text
+        the principal submitted. A server-side composition step between a model
+        and this call is what makes `human_authored` mean "some code said so".
+      - If a machine-drafted candidate is ever offered for a human to approve,
+        that is `human_verified`, not `human_authored`, and it needs its own
+        writer recording who approved what — which is why 0016's CHECK admits
+        `human_verified` although nothing produces it yet.
+
+    Nothing here can pin those today: the route does not exist, and a test
+    asserting a property of a module that has not been written is a test that
+    passes vacuously. It is written down, and it is a BACKLOG row against P2.
+
+    AND ONE MORE THING THE FOUR DO NOT COVER, WHICH P2 CLOSED ON 2026-08-09.
+    R1-R4 are all IN-PROCESS facts: a parameter list, an import graph, a Celery
+    thread-local, a ContextVar. An automation in a DIFFERENT process trips none
+    of them. `app/api/deps.get_current_tenant` accepts `X-API-Key`, a machine
+    credential, so any script or scheduler holding a tenant key could POST model
+    prose to the labelling route and have it stored as `human_authored` — making
+    the hierarchy worth the secrecy of an API key rather than any
+    human-in-the-loop property. The credential is the only evidence about the
+    caller that survives a process boundary, so the check has to live at the auth
+    layer: `get_credential_kind` reports which credential resolved, and
+    `label_eval_scenario` refuses anything but a Clerk JWT with a 403. That is a
+    restriction on the ROUTE, not on this module, and it is recorded here because
+    this is where a reader comes to find out what "human_authored" is worth.
+
+WHAT THIS MODULE DOES NOT DO
+    It does not promote anything into `verified_qa`. A human label improves what
+    the eval can measure; it reaches no customer. That is the owner's settled
+    decision of 2026-08-08 (`.dev/plans/260808-d6-labelling-loop.md`), and
+    `eval_service.VERIFIED_QA_PROMOTION_DECISION` carries the disablement and
+    its reason onto every run. `is_promotable_to_verified_qa` still gates on
+    `source`, still returns False for every source the schema allows, and this
+    module does not change a row's `source`.
+
+    It opens no connection and holds no connection string. The caller passes an
+    already-open psycopg2 connection and owns the transaction, matching
+    `scenario_service.insert_provenance_scenario` — which also keeps the
+    "connection strings never leave the control DB fetch" rule trivially true
+    here, because this module never sees one.
+"""
+
+from __future__ import annotations
+
+import unicodedata
+
+import structlog
+
+from app.services.eval_service import (
+    HUMAN_LABEL_TIERS,
+    SELECTOR_ELIGIBILITY_PREDICATE,
+    is_human_label_tier,
+)
+
+log = structlog.get_logger(__name__)
+
+# The tier this module stamps. `human_verified` (a human confirming a candidate
+# someone else drafted) is a different act with no producer yet; 0016's CHECK
+# admits it so that adding one later is a code change and not a migration.
+HUMAN_AUTHORED_TIER = "human_authored"
+
+
+class HumanLabelRefused(RuntimeError):
+    """A human-tier write was attempted from a context a model drives.
+
+    Deliberately not a subclass of ValueError: this is never a bad-input
+    problem the caller can fix by passing something else. It means the call
+    happened somewhere it must not happen.
+    """
+
+
+class LabelRejected(ValueError):
+    """The label itself is unusable — empty answer, or no author named."""
+
+
+def _current_celery_task():
+    """The Celery task executing in this thread, or None.
+
+    `celery._state.get_current_task()` is the plain function behind the
+    `celery.current_task` proxy; it returns None outside a task. Imported
+    lazily so that this module stays importable in an API process that has no
+    Celery wiring at all — a guard that raises on import is a guard that gets
+    deleted.
+
+    THE TWO FAILURE CASES ARE NOT THE SAME, and treating them as one made the
+    one function whose entire job is to fail closed the only place in this
+    module that failed open:
+
+      ImportError — Celery is not installed in this process. There is then no
+          Celery task to be inside, so `None` is the true answer and the guard
+          stays silent. This is what the lazy import is for.
+      anything else — the detector itself malfunctioned. A detector that
+          malfunctioned cannot certify that no model is driving this call, and
+          "I could not tell" must never resolve to "go ahead and stamp
+          `human_authored`". It refuses.
+    """
+    try:
+        from celery import _state  # noqa: PLC0415
+    except ImportError:
+        return None
+    try:
+        return _state.get_current_task()
+    except Exception as exc:
+        raise HumanLabelRefused(
+            "could not determine whether a Celery task is driving this call "
+            f"({type(exc).__name__}: {exc}); a human trust tier is never "
+            "stamped on an unverified context"
+        ) from exc
+
+
+def _current_agent_id() -> str:
+    """The agent id of the tool-server context in scope, or ''.
+
+    Set by `agent_tools.build_tool_server()` for the duration of an agent turn,
+    so a non-empty value means a model is driving this call stack.
+
+    Same split as `_current_celery_task`: a missing `agent_tools` means there is
+    no agent context in this process (`''`); any other failure means the
+    detector could not answer, and an unanswerable question about who is driving
+    the call refuses rather than proceeds.
+    """
+    try:
+        from app.services.agent_tools import _agent_id_var  # noqa: PLC0415
+    except ImportError:
+        return ""
+    try:
+        return str(_agent_id_var.get() or "")
+    except Exception as exc:
+        raise HumanLabelRefused(
+            "could not determine whether an agent tool context is driving this "
+            f"call ({type(exc).__name__}: {exc}); a human trust tier is never "
+            "stamped on an unverified context"
+        ) from exc
+
+
+# Unicode general categories that render as nothing a reader can see:
+#   Cc  control characters (\n, \t, \r)
+#   Cf  format characters — U+200B ZERO WIDTH SPACE, U+FEFF BOM,
+#       U+200C/U+200D zero-width non-joiner/joiner, the bidi overrides
+#   Zs  space separators, including U+00A0 NBSP and U+2007 FIGURE SPACE
+#   Zl / Zp  line and paragraph separators
+#
+# WHY THIS EXISTS RATHER THAN str.strip() ALONE. `str.strip()` removes Cc and Zs
+# but NOT Cf, so `reference_answer = "\u200b"` survived it, was stamped
+# `human_authored`, and satisfied BOTH `run_eval_suite`'s
+# `WHERE reference_answer != ''` and 0016's `COALESCE(reference_answer,'') <> ''`
+# CHECK. The row was then simultaneously marked "a human wrote this" and
+# effectively still unlabelled — the exact state the emptiness guard exists to
+# prevent, reached by a stray zero-width space from a rich-text paste rather than
+# by an attacker. Observed through the real route on 2026-08-09: U+200B, U+FEFF
+# and U+200C each returned 200 and bound tier='human_authored'.
+_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})
+
+
+def visible_answer(reference_answer: str | None) -> str:
+    """*reference_answer* stripped, or `''` when it carries nothing visible.
+
+    The single definition of "this answer is empty", used by `record_human_label`
+    below AND by the route's request model, so the boundary rejection and the
+    writer's own guard cannot come to different conclusions about the same
+    string. Returns the stripped text unchanged when it holds at least one
+    character a reader could see — normalising the CONTENT is not this
+    function's business, only deciding whether there is any.
+    """
+    answer = (reference_answer or "").strip()
+    if not any(
+        unicodedata.category(char) not in _INVISIBLE_CATEGORIES for char in answer
+    ):
+        return ""
+    return answer
+
+
+def assert_human_context() -> None:
+    """Refuse if a model is driving this call stack.
+
+    Raises HumanLabelRefused inside a Celery task or an agent tool context.
+    Split out from record_human_label so that the restriction is one named
+    thing a reader can find, and so a future second human-tier write cannot
+    reimplement a subtly weaker version of it.
+    """
+    task = _current_celery_task()
+    if task is not None:
+        raise HumanLabelRefused(
+            "a human trust tier may not be stamped from inside a Celery task "
+            f"(task={getattr(task, 'name', type(task).__name__)!r}); "
+            "eval_scenarios.label_trust_tier means a human wrote this answer"
+        )
+
+    agent_id = _current_agent_id()
+    if agent_id:
+        raise HumanLabelRefused(
+            "a human trust tier may not be stamped from inside an agent tool "
+            f"context (agent_id={agent_id!r}); eval_scenarios.label_trust_tier "
+            "means a human wrote this answer"
+        )
+
+
+# The UPDATE.
+#
+# THE SECOND PREDICATE IS THE POINT, AND IT WAS MISSING UNTIL 2026-08-09. The
+# WHERE was `id = %(scenario_id)s::uuid` alone, which meant this write reached
+# ANY row in the agent's database rather than only a row the labelling queue had
+# offered. One POST with the id of an already-answered scenario silently replaced
+# its `reference_answer` and re-stamped its provenance, with no record of what
+# had been there. The blast radius was worst on a `dataset='golden'` row:
+# `eval.py`'s golden half runs in full every night precisely so consecutive runs
+# are a PAIRED per-item comparison, and changing one item's reference answer
+# breaks that comparison while the run report has no way to say so.
+#
+# `NOT (SELECTOR_ELIGIBILITY_PREDICATE)` is the queue's own population, spelled
+# with the queue's own constant rather than a hand-written `= ''`. So the write's
+# reach is now exactly the set of rows the GET can return, and the two cannot
+# drift: the same string defines both.
+#
+# RELABELLING IS THEREFORE REFUSED, NOT SILENTLY PERFORMED — see
+# `record_human_label`'s `already_labelled`. If a correction path is wanted later
+# it is an explicit second act (which answer is being superseded, by whom, and
+# whether a golden row may move at all), not a side effect of the queue's write.
+#
+# Idempotent by construction in the direction that matters for a retry: the first
+# application labels the row, and a retry of the same request now matches zero
+# rows and reports `already_labelled` instead of moving `labelled_at` again.
+_LABEL_SQL = f"""
+    UPDATE eval_scenarios
+    SET reference_answer = %(reference_answer)s,
+        label_trust_tier = %(tier)s,
+        labelled_by = %(labelled_by)s,
+        labelled_at = NOW()
+    WHERE id = %(scenario_id)s::uuid
+      AND NOT ({SELECTOR_ELIGIBILITY_PREDICATE})
+"""
+
+# Run ONLY when the UPDATE matched nothing, to tell the two reasons apart: the
+# row is not in this database at all (404 — also the cross-tenant outcome, and
+# the two must stay indistinguishable), or it is here and already carries an
+# answer (409). Without it both collapse into 404 and a caller told "not found"
+# about a row it can see in its own queue history has been told something false.
+#
+# Deliberately projects no column: existence is the whole question, and `SELECT
+# 1` needs neither 0016's columns nor 0011's, so this probe cannot itself become
+# a migration-state failure on the error path.
+_SCENARIO_EXISTS_SQL = """
+    SELECT 1
+    FROM eval_scenarios
+    WHERE id = %(scenario_id)s::uuid
+"""
+
+
+def record_human_label(
+    conn,
+    *,
+    scenario_id: str,
+    reference_answer: str,
+    labelled_by: str,
+) -> dict:
+    """Record a human-authored reference answer on one eval scenario.
+
+    NOTE THE ABSENT PARAMETER. There is no `tier` argument and there must never
+    be one: the tier is what this function asserts, not what its caller asks
+    for. A caller able to name the tier is a caller able to name
+    `human_authored` from anywhere, which is the whole thing the hierarchy is
+    defending against.
+
+    The row's `source` is not touched. `source` says where the QUESTION came
+    from and stays true after the answer is written by someone else; a mined
+    failure that the owner answers stays `source='mined'` and becomes
+    `label_trust_tier='human_authored'`. Fusing the two is the defect
+    `eval_service.label_trust_tier()` exists to prevent.
+
+    Args:
+        conn: An open psycopg2 connection. This function does NOT commit or
+            close it — the caller owns the transaction, matching
+            scenario_service.insert_provenance_scenario.
+        scenario_id: UUID string of the eval_scenarios row to label.
+        reference_answer: The answer the human wrote. Must carry at least one
+            VISIBLE character — see `visible_answer`. An empty label is what the
+            row already has, and writing a human tier over one would claim a
+            human authored nothing while making the row eligible to a selector
+            that filters on `reference_answer != ''`. A zero-width string
+            satisfies that selector and every CHECK the schema has, so
+            "non-empty" is decided on Unicode category, not on `str.strip()`.
+        labelled_by: Identifier of the human. Must be non-empty — a label with
+            no author is a tier with nothing behind it. NON-EMPTY IS ALL THIS
+            FUNCTION CAN CHECK: it is caller-asserted free text, and nothing
+            here binds it to an authenticated principal. The caller must derive
+            it from the request's principal and must never read it from a
+            request body — see "WHAT THE FOUR RESTRICTIONS DO NOT COVER" in the
+            module docstring.
+
+    Returns:
+        {"scenario_id": str, "label_trust_tier": str, "labelled_by": str,
+         "rows_updated": int, "already_labelled": bool}. `rows_updated` is 0
+        when the UPDATE matched nothing, and `already_labelled` says WHICH of
+        the two reasons applies: the row is absent from this database
+        (False — the caller's 404) or it is present and already answered
+        (True — the caller's 409). Both are reported, never raised, so the
+        caller counts outcomes rather than catching them. `already_labelled` is
+        False whenever `rows_updated` is 1: the probe is not run on a successful
+        write.
+
+    Raises:
+        HumanLabelRefused: called from inside a Celery task or an agent tool.
+        LabelRejected: visibly-empty reference_answer, or empty labelled_by.
+    """
+    # First statement in the body, before validation and before a cursor is
+    # opened: a refused context must not be able to reach the database at all.
+    assert_human_context()
+
+    answer = visible_answer(reference_answer)
+    if not answer:
+        raise LabelRejected(
+            "reference_answer carries no visible character — an unlabelled row "
+            "is already the state this write exists to leave, and a zero-width "
+            "answer would leave it there while claiming a human wrote it"
+        )
+
+    author = (labelled_by or "").strip()
+    if not author:
+        raise LabelRejected(
+            "labelled_by is empty — a human tier with no human named behind it "
+            "is an unsourced claim"
+        )
+
+    # Belt for the constant: if HUMAN_AUTHORED_TIER is ever edited to something
+    # 0016's CHECK does not admit, fail here rather than at the database, where
+    # the error would arrive as a CheckViolation inside the caller's
+    # transaction and take the rest of the request's writes down with it.
+    if not is_human_label_tier(HUMAN_AUTHORED_TIER):
+        raise LabelRejected(
+            f"{HUMAN_AUTHORED_TIER!r} is not one of {HUMAN_LABEL_TIERS!r}"
+        )
+
+    already_labelled = False
+    with conn.cursor() as cur:
+        cur.execute(
+            _LABEL_SQL,
+            {
+                "reference_answer": answer,
+                "tier": HUMAN_AUTHORED_TIER,
+                "labelled_by": author,
+                "scenario_id": str(scenario_id),
+            },
+        )
+        rows_updated = cur.rowcount
+
+        if rows_updated == 0:
+            # The UPDATE's two predicates failed as one. Ask which.
+            cur.execute(_SCENARIO_EXISTS_SQL, {"scenario_id": str(scenario_id)})
+            already_labelled = bool(cur.fetchall())
+
+    log.info(
+        "label_service.human_label_recorded",
+        scenario_id=str(scenario_id),
+        label_trust_tier=HUMAN_AUTHORED_TIER,
+        labelled_by=author,
+        rows_updated=rows_updated,
+        already_labelled=already_labelled,
+        # The answer text itself is never logged — it is customer-domain
+        # content, and the log line's job is provenance, not content.
+    )
+
+    return {
+        "scenario_id": str(scenario_id),
+        "label_trust_tier": HUMAN_AUTHORED_TIER,
+        "labelled_by": author,
+        "rows_updated": rows_updated,
+        "already_labelled": already_labelled,
+    }
diff --git a/apps/api/app/worker/tasks/runtime/eval.py b/apps/api/app/worker/tasks/runtime/eval.py
index cfa3690..d040c5a 100644
--- a/apps/api/app/worker/tasks/runtime/eval.py
+++ b/apps/api/app/worker/tasks/runtime/eval.py
@@ -32,10 +32,26 @@ tenant data. What changed is that its ABSENCE is no longer fatal while
 eval_service.EVAL_SCORING_REQUIRES_BRANCH is False: a degraded Neon endpoint
 used to abandon a whole night's measurement over a resource nothing reads.
 
-verified_qa promotion is not performed by this task at all. It is disabled
-behind eval_service's label trust hierarchy, and the decision — with its reason
-— is recorded on the run in `eval_runs.config` so the disablement is a statement
-in the record rather than an absence a later reader has to infer.
+verified_qa promotion is not performed by this task at all.
+
+WHAT HOLDS IT SHUT IS THREE THINGS, AND THIS PARAGRAPH USED TO NAME ONE (D6 P3
+review, finding 6). It said "disabled behind eval_service's label trust
+hierarchy", which was the whole answer before D6 and is now the weakest third of
+it: D6 gave the system a producer of `human_authored` labels, rank 3, which
+CLEARS `VERIFIED_QA_MIN_TRUST_TIER`. Strongest first —
+
+    0. NO CALLER. `promote_to_verified_qa` is invoked from nowhere under `app/`;
+       the `promoted: 0` this task returns is a literal, not a result. Pinned by
+       TestPromotionIsUnreachableFromTheTask below.
+    1. THE RESOLVER. `select_promotion_candidates` gates on `eval_scenarios.
+       source` — where the QUESTION came from — which labelling never writes.
+    2. THE DECISION. `VERIFIED_QA_PROMOTION_DECISION["enabled"]` is False, the
+       owner's settled eval-only decision of 2026-08-08.
+
+The decision — with its reason — is recorded on the run in `eval_runs.config`
+and returned as `promotion_enabled` / `promotion_disabled_reason`, so the
+disablement is a statement in the record rather than an absence a later reader
+has to infer.
 
 Which rows a run covers
 -----------------------
@@ -645,10 +661,57 @@ def run_eval_suite(self, agent_id: str) -> dict:
            finally: delete the Neon branch if one was created (D-10 — always
                 runs, even on exception).
 
-    No verified_qa promotion happens here. See the module docstring and
-    eval_service.VERIFIED_QA_PROMOTION_DECISION: promotion is gated on the label
-    trust hierarchy and unreachable for every scenario source the schema allows,
-    and the decision is recorded on the run in eval_runs.config.
+    No verified_qa promotion happens here. See the module docstring for the
+    three locks and eval_service.VERIFIED_QA_PROMOTION_DECISION for the
+    recorded reason.
+
+    A HUMAN-LABELLED ROW CHANGES NOTHING ABOUT THAT (D6 P3). Labelling makes a
+    row ELIGIBLE TO THE SELECTOR above — it acquires a reference_answer, which
+    is the one thing `WHERE reference_answer != ''` was excluding it for. It
+    does not touch `dataset`, so it joins the exploratory half and never the
+    golden one (membership of the golden set is asserted, never inherited); and
+    it does not touch `source`, so it stays unpromotable, on top of the decision
+    flag now returned as `promotion_enabled`.
+
+    ELIGIBLE IS NOT PRESENT, AND THE FIRST VERSION OF THIS PARAGRAPH CONFLATED
+    THE TWO (D6 P3 review, finding 3). It said a labelled row "is fetched,
+    counted in `valid`, put to the agent and scored", unconditionally. That holds
+    only while the eligible exploratory pool is SMALLER than
+    EXPLORATORY_SAMPLE_SIZE. `_EXPLORATORY_SQL` is `ORDER BY RANDOM() LIMIT 30`:
+    at 200 eligible rows a new label does not raise `attempted` at all — it
+    changes WHICH rows are drawn, and the labelled row has a 30/200 chance of
+    being drawn on any given night. Nothing in the run report tells the owner
+    their label was not exercised, and no run preferentially draws a fresh label
+    (`BACKLOG 4.14`), so the feedback latency of the labelling loop is unbounded
+    above the sample size. The golden half is unsampled, but labelling cannot
+    reach it.
+
+    A LABEL CHANGES WHAT THE DEPLOY GATE READS (D6 P3 review, finding 5). This
+    is the live downstream consumer, and the earlier analysis stopped at
+    verified_qa, which has no caller. The chain:
+
+        this task -> write_eval_results -> eval_results on PRODUCTION
+        deployment_service._fetch_eval_summary_sync: AVG(score) GROUP BY metric,
+            filtered on eval_run_id and NOTHING else -> `pass_rates`
+        run_deployment_checklist puts eval_summary on the orchestrator payload
+        the orchestrator applies "all eval metrics >= 0.85" (ship) and
+            "[0.70, 0.85)" (warn) — prose in _DEPLOYMENT_SYSTEM_PROMPT
+
+    `apply_signal_evidence_gate` does NOT read the rates: it is a one-way floor
+    on the signal's PRESENCE (measured, agent_invoked) and on red-team severity,
+    so it can only make a recommendation more conservative and can never rescue
+    a rate that labelling depressed.
+
+    And labelling depresses rates by design: the queue is populated with mined
+    production FAILURES, so answering them adds hard negatives to the scored
+    population — an owner can lower their own pass rates by doing the work, with
+    nothing connecting the refused deploy back to their labelling. The inverse is
+    equally live: an owner who pastes the agent's own answer back in as the
+    reference inflates faithfulness. And an owner-authored answer is not grounded
+    in the retrieved corpus by construction, so context_recall over labelled rows
+    measures something different from context_recall over Haiku-written
+    references — averaged into one dataset mean, because no selector projects
+    `label_trust_tier` (`BACKLOG 4.12`).
 
     Args:
         agent_id: UUID string of the agent to evaluate.
@@ -656,7 +719,7 @@ def run_eval_suite(self, agent_id: str) -> dict:
     Returns:
         {"run_id", "scenario_count", "attempted", "valid", "scored", "datasets",
          "dataset_column_available", "golden_set_present", "promoted",
-         "config_recorded", "promotion_disabled_reason",
+         "config_recorded", "promotion_enabled", "promotion_disabled_reason",
          "branch_isolation", "agent_invoked", "agent_invocation",
          "invocation_recorded"}                                  on success.
         {"status": "already_running"}                            on idempotent skip.
@@ -1196,11 +1259,22 @@ def run_eval_suite(self, agent_id: str) -> dict:
             # predates migration 0014 — not that it has no golden rows.
             "dataset_column_available": dataset_column_available,
             "golden_set_present": composition["golden_set_present"],
-            # Always 0 — promotion is disabled behind the trust gate, and the
-            # key is kept so a caller reading it sees the zero rather than a
-            # missing key it might treat as "not measured".
+            # Always 0 — a literal, not a result: this task never calls
+            # promote_to_verified_qa (lock zero), and behind that the resolver
+            # gate and the decision flag. The key is kept so a caller reading it
+            # sees the zero rather than a missing key it might treat as "not
+            # measured". `promotion_enabled` below is what distinguishes this
+            # zero from an ENABLED run that promoted nothing.
             "promoted": 0,
             "config_recorded": config_recorded,
+            # THE FLAG TRAVELS WITH THE PROSE. `promoted: 0` and a reason string
+            # are what a run reported before D6, and neither is machine-readable
+            # as a policy: 0 is also what an ENABLED run that promoted nothing
+            # reports, and a reader cannot tell "promotion is off" from "nothing
+            # qualified" without parsing English. Since D6 the two are genuinely
+            # different — the system can now produce a label that would qualify —
+            # so the boolean is stated beside the count it explains.
+            "promotion_enabled": VERIFIED_QA_PROMOTION_DECISION["enabled"],
             "promotion_disabled_reason": VERIFIED_QA_PROMOTION_DECISION["reason"],
             # 'provisioned_unused' — a branch exists and no statement ran
             # against it; 'unavailable' — Neon could not give us one and the
```

---

# 2. THE GATE, AND THE IGNORED-NEW-FILES CONTROL PER PHASE

The gate command, identical everywhere, run from `apps/api`:

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py
```

## 2.1 The verbatim final line of the last gate run

Last code commit `f78524e`; `738c543` and `d0a3b4e` are docs-only.

```
2112 passed, 12 skipped, 30 warnings in 360.40s (0:06:00)
```

**Versus the brief's stated baseline `1873 passed / 11 skipped / 0 failed` at `4179a5c`:**
`+239 passed, +1 skipped, 0 failed`. The +1 skip is 0016's `-m integration` DB roundtrip, which
**skips** for want of a PostgreSQL server and is therefore unobserved, never a pass.

**No gate run exists at the branch tip `d0a3b4e` itself.** The two trailing commits are docs-only and
nobody re-ran the suite after them. That is a stated gap, not an inference.

## 2.2 The ignored-new-files control (`BACKLOG 2.26`), per phase

Six phases, six controls. **Every phase produced one.** Read the "reference" column carefully — only
P1's two controls are stated against `1873/11`; from P2 onward each control is stated against **the
previous phase's full-gate number**, because P1 and P2 had already added ~204 tests to the branch.

| # | phase | commit | full gate (verbatim) | control (verbatim) | extra `--ignore` / `--deselect` | control's reference | matches? |
|---|---|---|---|---|---|---|---|
| 1 | P1 impl | `8c956f1` | `1962 passed, 12 skipped, 28 warnings in 394.57s (0:06:34)` | `1873 passed, 11 skipped, 30 warnings in 433.60s (0:07:13)` | `--ignore test_label_provenance.py` `--ignore test_migration_tenant_0016.py` | `4179a5c` = 1873/11 | **exact** |
| 2 | P1 fixes | `f23930e` | `1994 passed, 12 skipped, 30 warnings in 362.10s (0:06:02)` | `1874 passed, 11 skipped, 30 warnings in 354.47s (0:05:54)` → **plus** `--deselect test_decision_eval_service.py::TestFixtureDerivation::test_a_decision_fixture_does_not_read_as_a_labelled_eval_scenario` → `1873 passed, 11 skipped, 1 deselected, 30 warnings in 362.74s (0:06:02)` | as above + the deselect | `4179a5c` = 1873/11 | **exact only after the deselect.** The raw control reads **1874**, and the report says so in its own §1: "Read the middle number honestly." |
| 3 | P2 impl | `4962ff5` | `2048 passed, 12 skipped, 30 warnings in 363.89s (0:06:03)` | `1994 passed, 12 skipped, 28 warnings in 371.40s (0:06:11)` | `--ignore test_eval_label_queue.py` | `8e3d337` = 1994/12 (P1's HEAD) | **exact** |
| 4 | P2 fixes | `17a5774` | `2077 passed, 12 skipped, 28 warnings in 369.61s (0:06:09)` | `1994 passed, 12 skipped, 28 warnings in 387.82s (0:06:27)` | `--ignore test_eval_label_queue.py` | control at `44f0ad5` = 1994/12 | **exact** — and load-bearing, because these fixes changed `app/api/deps.py`, which every authenticated route resolves |
| 5 | P3 impl | `edb4fbb`/`fb065a2` | `2101 passed, 12 skipped, 30 warnings in 370.48s (0:06:10)` | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 365.63s (0:06:05)` | `--ignore test_label_downstream.py` + 2 `--deselect` | `17a5774` = 2077/12 | **exact** |
| 6 | P3 fixes | `f78524e` | `2112 passed, 12 skipped, 30 warnings in 360.40s (0:06:00)` | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 363.04s (0:06:03)` | `--ignore test_label_downstream.py`, `--deselect test_label_provenance.py::TestTheWriteChangesNothingElse::test_the_tier_the_writer_stamps_is_the_tier_the_run_record_names`, `--deselect test_eval_service.py::TestBuildEvalRunConfig::test_the_whole_decision_reaches_the_run_record_not_just_the_flag` | `17a5774` = 2077/12 | **exact** |

**The chain is unbroken and it reaches 1873.** 1873 (`4179a5c`) → P1 control 1873 → P1 full 1994 →
P2 control 1994 → P2 full 2077 → P3 control 2077 → P3 full 2112. Each phase's control reproduces the
previous phase's full gate pass-for-pass and skip-for-skip.

**Independently re-run by the tier-1 reviewers, not only by the implementers:**

- P1 review §5: `1873 passed, 11 skipped, 30 warnings in 456.72s (0:07:36)` — the control, reproduced.
  Gate at HEAD also reproduced: `1962 passed, 12 skipped, 28 warnings in 506.11s (0:08:26)`.
- P2 review §0: gate `2048 passed, 12 skipped, 30 warnings in 406.79s (0:06:46)`; control
  `1994 passed, 12 skipped, 28 warnings in 377.35s (0:06:17)`. **Caveat the reviewer stated:** "I did
  not check out `8e3d337` to re-observe the pre-P2 baseline myself … the baseline itself is taken on
  the implementer's word."
- P3 review: gate `2101 passed, 12 skipped, 28 warnings in 415.69s (0:06:55)`; control
  `2077 passed, 12 skipped, 2 deselected, 28 warnings in 381.57s (0:06:21)`. Reviewer: "I did not
  re-measure the 2077/12 baseline by stashing; I corroborated it from `1c2b471` being docs-only plus
  HANDOFF's record at `17a5774`."

**One failing gate run is on the record, and it is not hidden.** P1's first control run:

```
1 failed, 1872 passed, 11 skipped, 30 warnings in 491.78s (0:08:11)
FAILED tests/unit/test_services.py::TestWaitForNeonReady::test_wait_for_neon_ready_retries_then_succeeds
```

Attributed to `BACKLOG 1.3` (a known flake, "failed 1 in 11 identical runs"). Evidence given: the run
that *included* the new files passed it, it passes in isolation (`3 passed in 0.66s`), it did not
recur. **The implementer states plainly: "I did not capture a traceback", so `BACKLOG 1.3`'s stated
next step is still open.**

**And one failing full-suite run inside P2, reported as its own most useful finding:**

```
11 failed, 2036 passed, 12 skipped, 30 warnings in 383.20s (0:06:23)
```

Cause: `agent_tools.build_tool_server()` leaks `_agent_id_var` for the rest of the pytest process
(`BACKLOG 4.6`), so R4 refused. Reproduced at minimum size before fixing:
`11 failed, 43 passed in 28.03s` → `55 passed in 24.05s`. **The direction is fail-closed** — 500s, not
forged rows.

## 2.3 The control's own stated blind spot

Both the P1 reviewer and the P3 implementer state it: the control proves **no pre-existing test
changed status**; it **cannot see a pre-existing assertion getting weaker inside a test that still
passes.** That blind spot intersects this branch in exactly one place — `c860780` weakened
`test_migration_tenant_0015.py`'s head assertion inside the feature commit. The P1 report originally
cited the control as evidence the weakening was safe; that sentence has been deleted (§4, P1 #14).

---

# 3. EVERY IMPLEMENTER CLAIM AND EVERY MUTATION PROOF, VERBATIM

Six implementation reports. **56 mutation proofs in total** (10 + 12 + 14 + 14 + 12 + 11 by the
implementers) plus **18 run by the tier-1 reviewers** (§4). Every entry below carries the selector it
was run under, quoted from its report.

## 3.1 P1 — `.dev/reference/d6-p1-label-trust-tier.md`

### Claims

1. **The tier is carried by the LABEL, not inferred from the question's origin.** Three new nullable
   columns on `eval_scenarios`; `source` keeps meaning what it meant.
2. **What the column's presence does and does not claim** — *corrected 2026-08-09*: "This section
   used to say that `label_trust_tier IS NOT NULL` and 'a human wrote this answer' were the same
   statement, *at the database level, for any caller including one that bypasses the service layer
   entirely.* **That is false.** The CHECK constrains the VALUE, never the AUTHOR."
3. **Four restrictions, each separately pinned.** R1 no tier parameter · R2 only
   `app/api/v1/evals.py` may reference `label_service` (*corrected from `app/api/`*) · R3
   *"~~physically cannot~~ **FALSE, refuted by observation** … The true claim is *no forgery shape
   anyone has devised passes unnoticed*"* · R4 refuses at runtime inside a Celery task or agent tool.
4. **R4's known hole, stated rather than papered over:** thread-local; a bare thread spawned inside a
   task sees neither. "That is why R4 is the last line and not the only one."
5. **0011's `source` CHECK was deliberately NOT widened** — a stated deviation from the literal
   instruction, on two grounds: re-collapsing origin into label, and opening
   `is_promotable_to_verified_qa`.
6. **Three of its own tests were weaker than they read** (§8.1 detector arms nobody had seen fire;
   §8.2 line-boundary blind spot; §8.3 a parser that invented a fifth scenario source), each fixed in
   its own commit.
7. **§8.4, not fixed, needs a BACKLOG row:** `agent_tools` ContextVar leaks across the whole pytest
   process.

### Mutation proofs — 10. Protocol stated: mutate, run, observe red, `git checkout HEAD -- <path>` unconditionally, run, observe green.

```
#1  R1, the writer has no tier parameter
    SELECTOR  tests/unit/test_label_provenance.py::TestR1NoTierParameter
    MUTATION  added `tier: str = HUMAN_AUTHORED_TIER` to record_human_label's signature
    RED    FAILED ...::TestR1NoTierParameter::test_the_writer_has_no_tier_parameter
           1 failed, 2 passed in 31.70s
    GREEN  3 passed in 22.15s

#2  R2, the import boundary
    SELECTOR  tests/unit/test_label_provenance.py::TestR2ImportBoundary
    MUTATION  `from app.services.label_service import record_human_label` added to
              app/worker/tasks/runtime/eval.py
    RED    FAILED ...::test_no_model_driven_module_may_import_the_human_label_writer
           1 failed, 3 passed in 4.37s
    GREEN  4 passed in 3.36s

#3  R2's detector is not vacuous   *** THIS ONE FAILED FIRST ***
    SELECTOR  tests/unit/test_label_provenance.py::TestR2ImportBoundary
    MUTATION  misspelled the detector's watched-name set (labell_service, recordd_human_label)
    FIRST ATTEMPT, before the test was strengthened:
           4 passed in 3.79s          <-- NO RED. The wall's detector had four arms nobody
                                          had ever seen fire.
    AFTER 8bc6f38:
    RED    FAILED ...::test_the_boundary_detector_sees_every_route_to_the_writer[from-import of the module-...]
           FAILED ...::test_the_boundary_detector_sees_every_route_to_the_writer[attribute call with no import in the file-...]
           2 failed, 8 passed in 5.56s
    GREEN  10 passed in 5.23s

#4  R3, the model-driven writers cannot write the label columns
    SELECTOR  tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite
    MUTATION  store_scenarios' INSERT extended with label_trust_tier / 'human_authored'
    RED    FAILED ...::test_only_the_label_writer_writes_the_label_columns
           FAILED ...::test_the_scenario_service_insert_paths_name_no_label_column
           2 failed, 2 passed in 40.83s
    GREEN  4 passed in 24.58s
    [The P1 reviewer's verdict on this one: "The red was real; it demonstrated the guard inside
     the complement of its own blind spot."]

#5  R4, the Celery-task arm
    SELECTOR  tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard
    MUTATION  `if task is not None:` -> `if False:`
    RED    ---------------------------- Captured stdout call -----------------------------
           2026-08-08 23:08:53 [info     ] label_service.human_label_recorded
             label_trust_tier=human_authored labelled_by=owner@example.com rows_updated=1
             scenario_id=11111111-1111-1111-1111-111111111111
           FAILED ...::test_a_celery_task_context_refuses_the_human_label
           FAILED ...::test_a_task_context_refuses_even_with_a_perfectly_valid_label
           2 failed, 3 passed in 22.97s
    GREEN  5 passed in 19.26s

#6  R4, the agent-tool arm
    SELECTOR  tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard
    MUTATION  `if agent_id:` -> `if False:`
    RED    E       AssertionError: the guard let the call reach the database before refusing
           FAILED ...::test_an_agent_tool_context_refuses_the_human_label
           1 failed, 4 passed in 20.15s
    GREEN  5 passed in 18.35s

#7  a CHECK-forbidden tier value fails closed
    SELECTOR  tests/unit/test_label_provenance.py::TestLabelTierVocabulary
    MUTATION  `return "unknown"` -> `return scenario_trust_tier(scenario.get("source"))`
    RED    FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[model_generated]
           FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[customer_negative]
           FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[unknown]
           FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[HUMAN_AUTHORED]
           FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[human]
           FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[7]
           6 failed, 10 passed in 20.19s
    GREEN  16 passed in 32.35s

#8  the migration's columns are nullable with no DEFAULT   *** THIS ONE FAILED FIRST ***
    SELECTOR  tests/unit/test_migration_tenant_0016.py
    MUTATION  `label_trust_tier TEXT` + newline + `NOT NULL DEFAULT 'human_authored'`
    FIRST ATTEMPT, before the test was strengthened:
    RED    FAILED ...::test_upgrade_is_strictly_additive_and_nullable[DEFAULT]
           1 failed, 29 passed, 1 skipped in 17.96s
           <-- only the blanket DEFAULT ban fired. The per-column nullability test had a
               line-boundary blind spot and would have missed a bare NOT NULL entirely.
    AFTER 316ab9a:
    RED    FAILED ...::test_every_added_column_is_the_bare_alter_and_nothing_else
           FAILED ...::test_upgrade_is_strictly_additive_and_nullable[DEFAULT]
           2 failed, 28 passed, 1 skipped in 28.17s
    GREEN  30 passed, 1 skipped in 23.19s

#9  0011's `source` CHECK is not touched
    SELECTOR  tests/unit/test_migration_tenant_0016.py
    MUTATION  0016 given a DROP CONSTRAINT eval_scenarios_source_check_v2 + a v3 CHECK adding
              'owner_authored'
    RED    FAILED ...::test_the_only_check_is_on_the_new_column
           FAILED ...::test_the_constraint_name_is_discovered_not_assumed
           FAILED ...::test_the_source_check_is_not_touched
           FAILED ...::test_the_new_source_values_are_not_snuck_in_as_scenario_sources
           4 failed, 26 passed, 1 skipped in 18.99s
    GREEN  30 passed, 1 skipped in 15.14s

#10 P1 opened no customer-facing door
    SELECTOR  tests/unit/test_label_provenance.py::TestP1OpenedNoCustomerFacingDoor
    MUTATION  is_promotable_to_verified_qa -> `return True`
    RED    FAILED ...::test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa
           FAILED ...::test_no_source_became_promotable[generated]
           FAILED ...::test_no_source_became_promotable[mined]
           FAILED ...::test_no_source_became_promotable[production]
           FAILED ...::test_no_source_became_promotable[red_team]
           5 failed, 2 passed in 36.65s
    GREEN  7 passed in 21.36s
```

## 3.2 P1 review fixes — `.dev/reference/d6-p1-review-fixes.md`

### Claims

1. **"R3 caught a spelling, not a capability, and it was the only one of the four restrictions
   standing between a Celery task and the `label_trust_tier` column."** Fix is two scans with
   different blind spots (composed-SQL reconstruction; name-level absence pin over `app/worker/`, the
   rest of `app/services/`, `scripts/`, `_runlogs/`), plus 8 permanent forgery fixtures.
2. **"The claim is now what is true."** Not "physically cannot" but *"no forgery shape anyone has yet
   devised passes unnoticed."* The residual — composing `"label" + "_trust_tier"` inside the
   allowlisted `eval_service.py` — "is undetectable statically, is written down" (`BACKLOG 4.8`).
3. **Finding 3 is decided but not pinnable today** (§4): `labelled_by` derived from the principal,
   never from a body; a machine-drafted candidate a human approves is `human_verified`. "The route
   does not exist. A test asserting a property of a module nobody has written passes vacuously."
4. **§5, stated so nobody infers it:** "`HUMAN_LABEL_TIERS`, `LABEL_TIER_COLUMN`,
   `is_human_label_tier()`, `label_trust_tier()` and `is_human_labelled()` **have no callers outside
   `eval_service` and the tests.** … **So a Haiku-written answer and an owner-written answer are
   indistinguishable to every consumer that exists today.** P1 built the vocabulary and the wall; it
   did not close the 'model prose reaches the eval' gap."
5. **Finding 14 partly fixed only:** "The commit itself cannot be repackaged — rewriting `c860780`
   means rewriting history on a branch, and the task forbids rebasing."

### Mutation proofs — 11 (numbered #1–#12, no #12 duplication; the report labels 12 rows and calls it "11 mutation proofs" in its heading; the table below is what it contains). Restore verified: "`git status --short` and `git diff HEAD --stat` were both empty afterwards".

```
#1  R3 vs. the exact forgery the review used
    SELECTOR  tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite
    MUTATION  the review's f-string UPDATE appended to app/worker/tasks/runtime/eval.py
    RED    E  AssertionError: a module that may not label a row names a label-provenance column:
              {'app\\worker\\tasks\\runtime\\eval.py': ['label_trust_tier (Constant)',
                                                        'labelled_at (Constant)',
                                                        'labelled_by (Constant)']}
           FAILED ...::test_only_the_label_writer_writes_the_label_columns
           FAILED ...::test_no_model_driven_module_names_a_label_column_at_all
           2 failed, 14 passed in 20.11s
    GREEN  16 passed in 17.63s
    [report: "the identical mutation was green before this work."]

#2  R2 sees a composed importlib path
    SELECTOR  tests/unit/test_label_provenance.py::TestR2ImportBoundary
    MUTATION  importlib.import_module('app.services.' + 'label_service') in a Celery task module
    RED    E  AssertionError: ... {'app\\worker\\tasks\\runtime\\eval.py':
              ["string containing 'label_service'"]}
           FAILED ...::test_only_the_one_named_api_module_may_reference_the_writer
           1 failed, 17 passed in 8.21s
    GREEN  18 passed in 5.88s

#3  R2's region is one module, not all of app/api/
    SELECTOR  tests/unit/test_label_provenance.py::TestR2ImportBoundary
    MUTATION  from app.services.label_service import record_human_label in app/api/v1/agents.py
    RED    E  {'app\\api\\v1\\agents.py': ['from app.services.label_service import ...',
                                           'from ... import record_human_label',
                                           'name record_human_label']}
           FAILED ...::test_only_the_one_named_api_module_may_reference_the_writer
           1 failed, 17 passed in 6.22s
    GREEN  18 passed in 5.47s

#4  no worker/service module imports the API layer
    SELECTOR  ...::TestR2ImportBoundary::test_no_worker_or_service_module_imports_the_api_layer
    MUTATION  from app.api.v1 import evals inside a function in app/worker/tasks/runtime/eval.py
    RED    E  {'app\\worker\\tasks\\runtime\\eval.py': ['app.api.v1']}
           1 failed in 2.87s
    GREEN  1 passed in 2.19s

#5  the fixture ban covers every test module
    SELECTOR  ...::TestR2ImportBoundary::test_no_test_module_outside_this_one_may_reference_the_writer
    MUTATION  a helper importing record_human_label appended to tests/unit/test_eval_service.py
    RED    E  {'tests\\unit\\test_eval_service.py': ['from app.services.label_service import ...',
                                                     'from ... import record_human_label',
                                                     'name record_human_label']}
           1 failed in 4.69s
    GREEN  1 passed in 3.85s

#6  the resolver refuses a mapping that is not a scenario
    SELECTOR  tests/unit/test_label_provenance.py::TestLabelTierVocabulary
    MUTATION  `if not _is_an_eval_scenario(scenario): return "unknown"` deleted
    RED    E  AssertionError: assert 'human_authored' == 'unknown'
           FAILED ...::test_a_mapping_that_is_not_a_scenario_never_reads_as_human_labelled
           1 failed, 19 passed in 22.28s
    GREEN  20 passed in 18.96s

#7  a human tier over an empty answer fails closed
    SELECTOR  tests/unit/test_label_provenance.py::TestLabelTierVocabulary
    MUTATION  the empty-answer downgrade deleted
    RED    E  AssertionError: assert 'human_authored' == 'unknown'
           FAILED ...::test_a_human_tier_over_an_empty_answer_fails_closed
           1 failed, 19 passed in 18.95s
    GREEN  20 passed in 17.19s

#8  R4's Celery detector refuses on malfunction
    SELECTOR  tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard
    MUTATION  the `raise HumanLabelRefused` restored to `except Exception: return None`
    RED    E  Failed: DID NOT RAISE HumanLabelRefused
           ---------------------------- Captured stdout call ------------------------------
           2026-08-09 01:18:41 [info  ] label_service.human_label_recorded
             label_trust_tier=human_authored labelled_by=owner@example.com rows_updated=1
             scenario_id=11111111-1111-1111-1111-111111111111
           FAILED ...::test_a_broken_celery_detector_refuses_rather_than_proceeding
           1 failed, 8 passed in 14.52s
    GREEN  9 passed in 13.15s

#9  R4's agent detector refuses on malfunction
    SELECTOR  tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard
    MUTATION  the same, on _current_agent_id
    RED    E  Failed: DID NOT RAISE HumanLabelRefused
           ---------------------------- Captured stdout call ------------------------------
           2026-08-09 01:19:32 [info  ] label_service.human_label_recorded
             label_trust_tier=human_authored labelled_by=owner@example.com rows_updated=1
           FAILED ...::test_a_broken_agent_detector_refuses_rather_than_proceeding
           1 failed, 8 passed in 14.71s
    GREEN  9 passed in 12.57s

#10 0016's CHECK requires a non-empty answer
    SELECTOR  tests/unit/test_migration_tenant_0016.py
    MUTATION  `AND COALESCE(reference_answer, '') <> ''` removed from the CHECK
    RED    E  AssertionError: 0016's CHECK must require a non-empty reference_answer whenever a
              human tier is present
           FAILED ...::test_the_check_refuses_a_human_tier_on_an_empty_answer
           1 failed, 32 passed, 1 skipped in 12.02s
    GREEN  33 passed, 1 skipped in 11.21s

#11 0016's introspection and DROP are schema-qualified
    SELECTOR  tests/unit/test_migration_tenant_0016.py
    MUTATION  pg_namespace join, current_schema() filter and %I.%I reverted to 0011's shape
    RED    E  AssertionError: 0016's catalog lookup must join pg_namespace so it discovers a
              constraint on the table it is about to alter
           FAILED ...::test_the_catalog_lookup_and_the_drop_are_schema_qualified
           1 failed, 32 passed, 1 skipped in 11.90s
    GREEN  33 passed, 1 skipped in 10.95s

#12 the tenant head is 0016
    SELECTOR  tests/unit/test_migration_tenant_0016.py
    MUTATION  revision: str = "0016" -> "0016b"
    RED    FAILED ...::test_migration_revision
           FAILED ...::test_0016_is_the_sole_child_of_0015_and_the_tree_is_unforked
           FAILED ...::test_0016_is_the_tenant_head
           3 failed, 30 passed, 1 skipped in 11.96s
    GREEN  33 passed, 1 skipped in 11.29s
```

**Discrepancy the judge should note:** the section heading says *"Mutation proofs — 11, every one
run"* and the table contains **12** numbered rows (#1–#12). The commit message for the docs commit
(`8e3d337`) also says "12 mutation proofs". Nothing else about the rows is inconsistent.

## 3.3 P2 — `.dev/reference/d6-p2-labelling-queue.md`

### Claims

1. **Headline, as CORRECTED:** the heading "judge confidence is not joinable to a scenario"
   *"overstates its own evidence … What the three legs establish is **not implementable without a
   tenant migration and a change to the miner** … The distinction is between *impossible* and *not
   P2's to spend*, and only the second is proven."*
2. **Three legs of unjoinability**, each traced to a line: different databases; no join key
   (`store_scenarios` writes no `job_id`/`conversation_id`/`origin_trace_id`, and
   `mine_production_scenarios` discards `payload->>'confidence'` at read); the one tenant-side
   confidence column is the wrong population. **The P2 tier-1 reviewer independently confirmed all
   three, line by line.**
3. **The ordering is origin trust tier ASC via `array_position`, then `created_at ASC`, then `id
   ASC`**, and the payload declares `by_uncertainty: false`. *CORRECTED:* the key list was
   hand-written and named a column that does not exist; it is now parsed from the SQL.
4. **The counts travel with their denominator**; `human_labelled` is `null`, never `0`, when 0016 is
   absent — "which is the state of every tenant DB today".
5. *CORRECTED:* **"`counts.eligible == counts.labelled` … lets a reader check it from the payload"
   was false, "and it is the more dangerous kind of false: it invites a reader to verify something by
   looking at a tautology."**
6. *CORRECTED:* **"THE WRITE'S REACH WAS WIDER THAN THE FEATURE IT SERVES, AND THIS DOCUMENT DID NOT
   MENTION IT."** The UPDATE reached any scenario in the agent's DB, including a `dataset='golden'`
   row. Now scoped by `AND NOT (SELECTOR_ELIGIBILITY_PREDICATE)`; relabel is a 409.
7. *CORRECTED:* **the empty-answer guard was `str.strip()`, which does not remove Cf** — a zero-width
   answer was accepted and stamped `human_authored`.
8. *CORRECTED:* **an API key could stamp a human tier.** "the value of the whole hierarchy was
   bounded by the secrecy of an API key rather than by any human-in-the-loop property." Fixed at the
   auth layer with `credential_kind` + a 403.
9. *CORRECTED:* **a soft-deleted agent was still labellable.**
10. **"Fourteen of fourteen went red"** *— CORRECTED to:* **"a statement about the mutations that
    were CHOSEN, never about coverage."** Four mutations the reviewer chose instead survived.

### Mutation proofs — 14 (M1–M14), original set. Selector, identical for every row, run from `apps/api`:

```
.venv/Scripts/python.exe -m pytest tests/unit/test_eval_label_queue.py -q
```

Restore: "unconditional (a `finally:` around the red run) … `git diff --stat HEAD -- apps/api/app/api/v1/evals.py` is empty."

| # | guard | mutation | RED | GREEN |
|---|---|---|---|---|
| M1 | `ScenarioLabelRequest` forbids extra fields | `extra="forbid"` → `extra="ignore"` | `6 failed, 48 passed in 35.77s` | `54 passed in 26.29s` |
| M2 | `_resolve_agent_tenant_db` 404s cross-tenant | delete the `tenant_id` comparison | `3 failed, 51 passed in 29.56s` | `54 passed in 24.36s` |
| M3 | `human_labelled` is null, not 0 | `… else None` → `… else 0` | `1 failed, 53 passed in 26.23s` | `54 passed in 23.98s` |
| M4 | oldest-first, not recency | `created_at ASC,` → `created_at DESC,` | `1 failed, 53 passed in 26.30s` | `54 passed in 23.72s` |
| M5 | a write matching no row is a 404 | `== 0:` → `< 0:` | `1 failed, 53 passed in 26.29s` | `54 passed in 27.04s` |
| M6 | the context guard runs before a connection | `assert_human_context()` → `pass` | `3 failed, 51 passed in 31.75s` | `54 passed in 24.63s` |
| M7 | `labelled_by` derived from the principal | `f"tenant:{tenant.id}"` → `"owner"` | `2 failed, 52 passed in 27.19s` | `54 passed in 26.81s` |
| M8 | a tenant DB without 0016 gets a 503 | `UndefinedColumn:` → `UndefinedTable:` | `1 failed, 53 passed in 31.23s` | `54 passed in 50.89s` |
| M9 | the queue's WHERE is the eval selector's own predicate, read cross-module | `"reference_answer != ''"` → `"reference_answer <> ''"` | `1 failed, 53 passed in 44.84s` | `54 passed in 30.62s` |
| M10 | an unclassified source sorts LAST | `ASC NULLS LAST` → `ASC NULLS FIRST` | `1 failed, 53 passed in 27.32s` | `54 passed in 24.41s` |
| M11 | the priority order covers every schema source | drop `red_team` from `_source_priority_order()` | `3 failed, 51 passed in 26.29s` | `54 passed in 23.43s` |
| M12 | the route module issues no `eval_scenarios` write | append `_ADVERSARIAL = "UPDATE eval_scenarios SET reference_answer = %(a)s"` | `1 failed, 53 passed in 26.37s` | `54 passed in 25.05s` |
| M13 | the ordering is total — `id` is the final key | delete `id ASC` | `1 failed, 53 passed in 25.81s` | `54 passed in 24.37s` |
| M14 | every count travels with its denominator | delete `"total": int(total or 0),` | `14 failed, 40 passed in 33.74s` | `54 passed in 24.22s` |

M1/M2/M3 re-run with failing identities captured:

```
### M1   RED: 6 failed, 48 passed in 27.78s
    FAILED ...::TestTheLabelWrite::test_the_body_may_not_name_the_author
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[label_trust_tier]
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[labelled_at]
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[tier]
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[source]
    FAILED ...::TestTheRouteShape::test_the_request_model_forbids_extra_fields
         GREEN: 54 passed in 24.43s

### M2   RED: 3 failed, 51 passed in 26.94s
    FAILED ...::TestTenantIsolation::test_a_cross_tenant_request_is_404_and_opens_no_database[GET]
    FAILED ...::TestTenantIsolation::test_a_cross_tenant_request_is_404_and_opens_no_database[POST]
    FAILED ...::TestTheRouteShape::test_the_ownership_check_still_compares_the_tenant
         GREEN: 54 passed in 23.74s

### M3   RED: 1 failed, 53 passed in 25.72s
    FAILED ...::TestQueueCounts::test_human_labelled_is_unknown_not_zero_before_migration_0016
         GREEN: 54 passed in 23.70s
```

**M8 could not be replayed as recorded** — self-reported: `except psycopg2.errors.UndefinedColumn`
occurs three times in `evals.py`, and the row recorded only `1 failed`. Re-run as M8b below.

## 3.4 P2 review fixes — `.dev/reference/d6-p2-review-fixes.md` (+ `d6-p2-labelling-queue.md` §7.6)

### Claims

1. **F1's suggested fix was extended, not just applied** — "verified, not assumed": reusing
   `_scenario_write_statements` alone returns `[]` against a composed table name, so a second
   verb-level scan was added and the first scan's blind spot is its own test.
2. **F3 was fixed by scoping, not by making relabelling explicit** — the other option "is a feature
   with product questions … and no owner decision behind it".
3. **F7 fixed structurally rather than by documentation** — `credential_kind` on `request.state`,
   `get_credential_kind` as a dependency **of** `get_current_tenant`, 403 on anything but a Clerk JWT,
   `CREDENTIAL_UNKNOWN` included "because 'cannot tell' must never resolve to 'human'".
4. **"a mutation ledger measures the ledger's author's imagination, not the suite's coverage."**
5. **`SELECTOR_ELIGIBILITY_PREDICATE` moved** from `evals.py` to `eval_service.py` — required by F3,
   because `label_service` may not import `app.api` (R2).

### Mutation proofs — 14 (M15–M27, M8b). Two selectors, run from `apps/api`:

```
A: .venv/Scripts/python.exe -m pytest tests/unit/test_eval_label_queue.py -q
B: .venv/Scripts/python.exe -m pytest tests/unit/test_eval_label_queue.py tests/unit/test_label_provenance.py -q
```

Baseline for A is `83 passed`; for B, `170 passed`. Harness `scratchpad/mutate.py` "refuses an anchor
that matches other than exactly once, and it refuses a mutation that changes nothing".

| # | guard | mutation | sel | RED | GREEN |
|---|---|---|---|---|---|
| M15 | the priority key sorts the best origin FIRST | `array_position(...) ASC NULLS LAST,` → `DESC NULLS LAST,` | A | `3 failed, 80 passed in 30.31s` | `83 passed in 24.84s` |
| M16 | limit binds to LIMIT and offset to OFFSET | params swapped | A | `1 failed, 82 passed in 26.87s` | `83 passed in 25.01s` |
| M17 | the `labelled` FILTER is the selector predicate | → `FILTER (WHERE question != '')` | A | `1 failed, 82 passed in 26.58s` | `83 passed in 24.17s` |
| M18 | no second write path — **composed** spelling | `_ADV_TBL = "eval_" + "scenarios"` / f-string UPDATE | B | `1 failed, 169 passed in 32.30s` | `170 passed in 29.83s` |
| M19 | no second write path — **schema-qualified** spelling | `"UPDATE public.eval_scenarios SET reference_answer = %(a)s"` | B | `2 failed, 168 passed in 34.69s` | `170 passed in 30.57s` |
| M20 | the label UPDATE is scoped to an unlabelled row | delete `AND NOT ({SELECTOR_ELIGIBILITY_PREDICATE})` | B | `2 failed, 168 passed in 32.91s` | `170 passed in 29.93s` |
| M21 | only a Clerk JWT may stamp a human tier | `!= CREDENTIAL_CLERK_JWT` → `== 'never-this'` | A | `3 failed, 80 passed in 26.67s` | `83 passed in 25.81s` |
| M22 | emptiness is decided on Unicode category | drop `"Cf"` from `_INVISIBLE_CATEGORIES` | B | `4 failed, 166 passed in 33.15s` | `170 passed in 30.93s` |
| M23 | the emptiness check is at the BOUNDARY | `visible_answer(value)` → `value` in the field validator | A | `7 failed, 76 passed in 27.21s` | `83 passed in 24.20s` |
| M24 | a soft-deleted agent is not resolvable | delete `Agent.deleted_at.is_(None)` | A | `2 failed, 81 passed in 27.21s` | `83 passed in 26.94s` |
| M25 | the reference answer is bounded | delete `max_length=MAX_REFERENCE_ANSWER_CHARS,` | A | `1 failed, 82 passed in 30.12s` | `83 passed in 25.59s` |
| M26 | QUEUE_ORDERING is deep-copied | `copy.deepcopy` → `dict(...)` | A | `1 failed, 82 passed in 28.03s` | `83 passed in 24.82s` |
| M27 | the probe distinguishes a relabel from a missing row | `already_labelled = bool(cur.fetchall())` → `= False` | A | `1 failed, 82 passed in 27.30s` | `83 passed in 25.16s` |
| M8b | the 503, **`label_eval_scenario` occurrence** | that occurrence's `UndefinedColumn:` → `UndefinedTable:` | A | `1 failed, 82 passed in 27.88s` | `83 passed in 24.83s` |

FAILED identities, verbatim:

```
### M15   3 failed
    FAILED ...::TestQueueOrdering::test_the_ordering_is_exactly_these_keys_in_this_direction
    FAILED ...::TestQueueOrdering::test_the_priority_key_sorts_the_best_origin_first_not_last
    FAILED ...::TestQueueOrdering::test_the_response_states_that_this_is_not_an_uncertainty_ordering
### M16   1 failed
    FAILED ...::TestQueueOrdering::test_the_page_takes_its_limit_and_offset_in_that_order
### M17   1 failed
    FAILED ...::TestTheSelectorIsUntouched::test_the_two_count_filters_are_the_predicate_and_its_exact_negation
### M18   1 failed          <- THE ONE THAT MATTERS MOST
    FAILED ...::TestTheSelectorIsUntouched::test_this_module_issues_no_write_statement_of_any_kind
### M19   2 failed
    FAILED ...::TestTheSelectorIsUntouched::test_this_module_issues_no_write_of_its_own_to_eval_scenarios
    FAILED ...::TestTheSelectorIsUntouched::test_this_module_issues_no_write_statement_of_any_kind
### M20   2 failed
    FAILED ...::TestTheLabelWrite::test_a_scenario_that_already_has_an_answer_is_a_409_not_an_overwrite
    FAILED ...::TestTheLabelWrite::test_the_label_write_is_scoped_to_an_unlabelled_row
### M21   3 failed
    FAILED ...::TestOnlyAHumansCredentialMayStampAHumanTier::test_an_api_key_may_not_record_a_human_label
    FAILED ...::TestOnlyAHumansCredentialMayStampAHumanTier::test_an_unrecorded_credential_is_refused_too
    FAILED ...::TestOnlyAHumansCredentialMayStampAHumanTier::test_the_route_declares_the_credential_dependency
### M22   4 failed
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[​]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[﻿]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[‌]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[​‌﻿]
### M23   7 failed   (all eight parametrisations except "", which min_length=1 still catches)
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[   ]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\n\t ]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\xa0]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[​]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[﻿]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[‌]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[​‌﻿]
### M24   2 failed
    FAILED ...::TestTenantIsolation::test_a_soft_deleted_agent_is_gone_from_both_routes[GET]
    FAILED ...::TestTenantIsolation::test_a_soft_deleted_agent_is_gone_from_both_routes[POST]
### M25   1 failed
    FAILED ...::TestTheLabelWrite::test_an_oversized_answer_is_rejected_at_the_boundary
### M26   1 failed
    FAILED ...::TestQueueOrdering::test_the_ordering_record_cannot_be_mutated_through_the_response
### M27   1 failed
    FAILED ...::TestTheLabelWrite::test_a_scenario_that_already_has_an_answer_is_a_409_not_an_overwrite
### M8b   1 failed          <- the identity the original M8 row could not supply
    FAILED ...::TestTheLabelWrite::test_a_tenant_database_without_0016_says_which_migration_is_missing
```

## 3.5 P3 — `.dev/reference/d6-p3-label-downstream.md`

### Claims

1. **The finding:** `VERIFIED_QA_PROMOTION_DECISION["reason"]` was stamped verbatim into
   `eval_runs.config` on **every** run and said promotion waits "until a correction UI produces
   human-verified answers". **"D6 P1 and P2 built that correction UI."**
2. **"That property has INVERTED."** `human_authored` is rank 3; `VERIFIED_QA_MIN_TRUST_TIER` is
   `human_verified`, rank 2. "The tier the shipped writer stamps *clears the minimum*."
3. **THREE locks, not two** *(corrected by the review)*: **LOCK ZERO — `promote_to_verified_qa` has
   no caller** ("This is the guarantee today"); the RESOLVER (gates on `source`); the DECISION
   (`enabled is False`). "Naming two locks while omitting the strongest makes a reader mis-rank the
   risks."
4. **Both locks were mutable module dicts with no absence pin.** Now `MappingProxyType` +
   an AST scan for the subscript/rebind/`.update()` shapes.
5. **The decision-gate justification P3 itself wrote was never true** — kept struck-in-place: *"P3
   removed a stale justification and replaced it with a new one that was never true."*
6. **"A labelled row becomes ELIGIBLE to the eval — which is not the same as PRESENT in a run."**
   `_EXPLORATORY_SQL` is `ORDER BY RANDOM() LIMIT 30`; at 200 eligible rows the owner's row has a
   30/200 chance per night. "Every original test ran with a **three-row** pool."
7. **It joins no golden set.** **Nothing an owner labels reaches a customer.** **The counts stay
   honest.**
8. **A label changes what the DEPLOY GATE reads** *(added by the review)* — and one correction to the
   review: `apply_signal_evidence_gate` never reads `pass_rates`; the 0.85 bar is prose in
   `_DEPLOYMENT_SYSTEM_PROMPT`. "The consequence is the same and slightly worse."
9. **`labelled_by` records the ACCOUNT, not the Clerk user.**

### Mutation proofs — 12 (M1–M12). Protocol: mutate → run the exact selector → red → `git checkout HEAD -- <path>` (unconditional; `HEAD` is `edb4fbb`) → green. "`git status --short` was empty after the last restore."

| # | guard | mutation | selector | RED | GREEN |
|---|---|---|---|---|---|
| M1 | the DECISION gate | deleted the `if not …["enabled"]` block | `test_lock_two_the_decision_refuses_a_row_that_clears_every_other_gate`, `test_the_decision_refusal_is_counted_not_swallowed` | `2 failed in 17.70s` | `2 passed in 12.12s` |
| M2 | the RESOLVER gate | swapped the gate to `label_trust_tier(scenario)` | `test_lock_one_the_gate_reads_the_questions_origin` | `1 failed in 14.23s` — `assert {'promotion_disabled:eval_only': 1} == {'trust_tier:customer_negative': 1}` | `1 passed in 12.19s` |
| M3 | `dataset` absent from the label UPDATE | added `dataset = 'golden',` to the SET | ~~P3's own test~~ **deleted (finding 2); the live guard is P2's `test_eval_label_queue.py::TestTheLabelWrite::test_a_label_is_recorded_at_the_human_authored_tier`** | `1 failed in 14.15s` — `Extra items in the left set: 'dataset'` | `1 passed in 12.53s` |
| M4 | golden selector has no label clause | `AND (dataset = %(golden)s OR label_trust_tier = 'human_authored')` | `test_the_golden_selector_cannot_be_reached_by_labelling` | `1 failed in 14.37s` | `1 passed in 11.98s` |
| M5 | golden membership asserted, not inherited | `dataset_of` returns golden for NULL | `TestGoldenMembershipIsNeverInherited` | `2 failed, 1 passed in 18.81s` — `assert 4 == 2` | `3 passed in 16.83s` |
| M6 | the denominator is the FETCHED set | `summarise_run_validity(scored_scenarios, …)` | `TestTheCountsStayHonest` | `1 failed, 2 passed in 18.91s` — `assert 4 == 5` | `3 passed in 16.47s` |
| M7 | the recorded reason is current | restored the pre-D6 reason text | `test_the_recorded_decision_names_the_decision_not_an_absent_producer` | `1 failed in 18.86s` — `assert '2026-08-08' in '…until a correction UI produces human-verified answers.'` | `1 passed in 11.89s` |
| M8 | the run states the flag | removed `promotion_enabled` from the return | `test_the_run_reports_the_flag_beside_the_zero` | `1 failed in 19.01s` — `KeyError: 'promotion_enabled'` | `1 passed in 21.81s` |
| M9 | the run record stays flat | added `"supersedes": [...]` to the decision | `test_the_recorded_decision_stays_flat_because_the_copy_is_shallow` | `1 failed in 14.78s` — `Extra items in the left set: 'supersedes'` | `1 passed in 15.30s` |
| M10 | the empty-label exclusion | `WHERE reference_answer != ''` → `WHERE TRUE` in `_GOLDEN_SQL` | `test_the_selector_is_the_only_thing_standing_between_the_two_states` | `1 failed in 14.97s` | `1 passed in 12.03s` |
| M11 | the owner's text survives the row builder | `"reference_answer": row[3]` → `row[2]` | `test_the_owners_answer_is_the_reference_and_never_the_prediction` | `1 failed in 19.23s` — `+ Do you refund?` | `1 passed in 20.77s` |
| M12 | the row is really fetched | `rows.extend(_cur.fetchall())` → `rows.extend([])` | `test_labelling_makes_a_row_the_selector_returns`, `test_the_labelled_row_is_put_to_the_agent` | `2 failed in 20.19s` | `4 passed in 17.02s` |

**Self-correction after its own proof:** "M11 established that the first assertion … is real. Its
**docstring** additionally claimed the second assertion pinned audit D1's return. **It does not.**"

## 3.6 P3 review fixes — `.dev/reference/d6-p3-review-fixes.md`

### Claims

1. **"The shape of the branch's defect, stated once":** *"P3's job was to remove a **stale**
   justification … Then it wrote a **new** justification, stamped it in four places … and the new one
   was never true. That is a worse failure than the one it fixed."*
2. **"The other thirteen findings are variations on the same theme: a claim that reads as verified …
   where the assertion is narrower than the sentence."**
3. **One correction to the tier-1 finding itself:** F5 said `apply_signal_evidence_gate` "blocks a
   deploy on `pass_rates`". "It does not — verified by reading the function body."
4. **Lock zero's pins are module-scoped, stated not closed:** "A third module introducing the call
   trips neither. That gap is written down, not closed."
5. **`f78524e`'s commit message says 13 findings. It miscounted** — corrected in `d0a3b4e`.

### Mutation proofs — 11 (A–K). Protocol: mutate → selector → red → `git checkout HEAD -- <path>` (unconditional; `HEAD` is `f78524e`) → green. "`git status --short` was **empty** after the last restore."

| # | guard | mutation | selector | RED | GREEN |
|---|---|---|---|---|---|
| A | the decision gate is never reached (F1) | moved the `enabled` block ABOVE the trust-tier gate | `test_label_downstream.py::TestNoLabelReachesACustomer::test_the_decision_gate_is_never_reached_by_a_schema_allowed_source` | `1 failed in 18.34s` — `assert 4 == 0`, `{'promotion_disabled:eval_only': 4}` | `1 passed in 13.62s` |
| B | the exploratory draw is capped in the SQL (F3) | deleted `LIMIT %(limit)s` from `_EXPLORATORY_SQL` | `test_label_downstream.py::TestLabellingMakesARowEligibleNotPresent` | `1 failed, 2 passed in 19.30s` — `assert 'LIMIT %(limit)s' in "… ORDER BY RANDOM()"` | `3 passed in 17.02s` |
| B2 | the task draws no more than the cap (F3) | `"limit": EXPLORATORY_SAMPLE_SIZE` → `* 10` | same | `2 failed, 1 passed in 19.05s` — `assert 203 == (2 + 30)` | `3 passed in 16.86s` |
| C | the score reaches PRODUCTION (F5) | `write_eval_results(…, conn_str)` → `branch_conn_str or conn_str` | `…::TestALabelChangesWhatTheDeployGateReads::test_the_labelled_rows_score_is_written_to_eval_results` | `1 failed in 19.16s` — `'postgresql://neon-branch/tenant' != 'postgresql://production/tenant'` | `1 passed in 16.88s` |
| D | the pass-rate aggregation has no provenance filter (F5) | added `AND label_trust_tier IS NULL` to `_fetch_eval_summary_sync` | `…::test_the_pass_rate_query_cannot_exclude_a_labelled_row` | `1 failed in 15.47s` — `assert 'WHERE eval_run_id = %s GROUP BY metric' in …` | `1 passed in 13.45s` |
| E | the ship bar is prompt prose, not gate code (F5) | added a `pass_rates` check to `apply_signal_evidence_gate` | `…::test_the_ship_bar_is_prose_in_the_prompt_not_code_in_the_gate` | `1 failed in 15.41s` — `pass_rates` found in the gate body | `1 passed in 13.57s` |
| F | the locks are read-only mappings (F4a) | `MappingProxyType({` → `dict({` | `test_label_downstream.py::TestTheLocksAreNotOneAssignmentAway` | `1 failed, 2 passed in 16.10s` — `SCENARIO_SOURCE_TRUST_TIER is a dict` | `3 passed in 16.22s` |
| G | no module under `app/` writes to a lock (F4a) | added `_es.SCENARIO_SOURCE_TRUST_TIER = {...}` to `eval.py` | `…::test_no_module_under_app_writes_to_either_lock` | `1 failed in 19.86s` — `{'eval.py': ['line 159: rebinds .SCENARIO_SOURCE_TRUST_TIER']}` | `3 passed in 16.22s` |
| H | the whole decision reaches the run record (F10) | `dict(VERIFIED_QA_PROMOTION_DECISION)` → `{"enabled": …}` | `test_eval_service.py -k "the_whole_decision_reaches_the_run_record or promotion_decision_is_copied"` | `1 failed, 1 passed in 15.69s` — missing `'scope'`, `'min_trust_tier'`, … | `2 passed in 13.45s` |
| I | the refusal names the ORIGIN's tier (F7) | deleted the resolver gate | `test_label_provenance.py::TestP1OpenedNoCustomerFacingDoor::test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa` | `1 failed in 14.37s` — `{'promotion_disabled:eval_only': 1}` vs `{'trust_tier:customer_negative': 1}` | `1 passed in 12.12s` |
| J | the observation count is the SCORED count (F9) | `"observations": len(values)` → `bucket["valid"]` | `…::TestTheCountsStayHonest::test_the_unscored_labelled_row_does_not_move_a_metric` | `1 failed in 19.38s` — `faithfulness claims 3 observations … assert 3 == 2` | `1 passed in 16.63s` |
| K | a hard negative reaches the rate (F5) | filtered `results["scores"]` to `>= 0.85` before `write_eval_results` | `…::test_a_hard_negative_label_lowers_the_rate_the_orchestrator_reads` | `1 failed in 19.00s` — `assert 0.9 < 0.9` | `1 passed in 16.86s` |

**H is singled out by the report:** "under it the **pre-existing**
`test_promotion_decision_is_copied_not_shared` stayed **green** while the new test went red."

**One mutation was discarded and re-run, and the report says so:** "The first attempt at J used
`len(rows)`, which is not in scope … the run raised `NameError` and the test failed with a `KeyError`
… That is a crash, not a guard firing, so it proves nothing. Restored and re-mutated."

---

# 4. EVERY TIER-1 FINDING, AND WHETHER THE DIFF SHOWS IT FIXED

**47 findings across three reviews** (P1: 15 + 8 unsupported claims · P2: 18 + 7 · P3: 14 + 7).
Persisted to:

- `C:/Users/Bantu/mzansi-agentive/wchats/.dev/reference/d6-p1-adversarial-review.md`
- `C:/Users/Bantu/mzansi-agentive/wchats/.dev/reference/d6-p2-adversarial-review.md`
- `C:/Users/Bantu/mzansi-agentive/wchats/.dev/reference/d6-p3-adversarial-review.md`

**Status column is determined from the diff, not from the fixer's say-so.** Three verdicts are used:

- **FIXED (in diff)** — the change is visible in §1.3, in `app/` or `alembic_tenant/`.
- **FIXED (test-side, not in this diff)** — the fix lives in a test file summarised in §1.1. **The
  judge cannot verify these from the artifact.** The evidence offered is a mutation proof (named).
- **NOT FIXED / PARTIAL / DOCUMENTED** — stated explicitly, with what the diff shows.

## 4.1 P1 review — 15 findings

| # | sev | finding | claimed | **status from the diff** |
|---|---|---|---|---|
| 1 | critical | R3 catches a spelling, not a capability: an f-string `UPDATE` in a real Celery task module stamped `human_authored` with all 59 tests green | fixed | **FIXED (claim in diff; detector test-side).** §1.3 shows `label_service.py`'s restriction 3 rewritten to *"THIS SAID 'PHYSICALLY CANNOT' UNTIL 2026-08-09, AND THAT WAS FALSE"* and the true claim substituted. The two scans themselves are in `test_label_provenance.py` — **not reproducible from this artifact.** Evidence: P1-fixes mutation #1, both arms red on the reviewer's exact forgery. |
| 2 | high | 0016's CHECK constrains the VALUE, not the AUTHOR; three files claim otherwise | fixed | **FIXED (in diff).** 0016 docstring: *"WHAT THE CHECK DOES **NOT** SAY, corrected 2026-08-09 … The database bounds the vocabulary; it does not authenticate the writer."* Same in `label_service.py`. |
| 3 | high | `labelled_by` is caller-asserted free text; a route could forward model prose | decided, not pinnable then | **FIXED STRUCTURALLY IN P2 (in diff), with a named residue.** `_label_principal(tenant)` returns `f"tenant:{tenant.id}"`; `ScenarioLabelRequest` is `extra="forbid"` with one field. Residue in the diff's own words: *"IT NAMES AN ACCOUNT, NOT A PERSON"* — `BACKLOG 4.7`. |
| 4 | medium | R2's region is `app/api/`, which includes the anonymous widget surface | fixed | **FIXED (claim in diff; test-side scan).** `label_service.py`: *"Only `app/api/v1/evals.py` may reference this module … The test asserts a module path, so the claim is a module path."* Evidence: P1-fixes mutations #3, #4. |
| 5 | medium | nothing reads `label_trust_tier`; the vocabulary is write-only | documented | **NOT FIXED — BY DESIGN, AND STILL TRUE AT HEAD.** The diff confirms it: all three of `run_eval_suite`'s selectors project `id, source, question, reference_answer, retrieved_contexts, dataset` and no `label_trust_tier`. `eval_service.py`'s gate-1 comment now says so out loud: *"none of `run_eval_suite`'s three selectors projects `label_trust_tier`."* Carried as `BACKLOG 4.12`. |
| 6 | medium | `decision_eval_service.FIXTURE_LABEL_TRUST_TIER` collides with the new column | fixed twice | **FIXED (in diff), twice.** Rename to `FIXTURE_LABEL_PROVENANCE` / `label_provenance` / `fixture_label_provenance`, **and** `eval_service._is_an_eval_scenario()` refuses a mapping carrying neither `source` nor `reference_answer`. |
| 7 | medium | R4's two detectors fail OPEN behind `# pragma: no cover` | fixed | **FIXED (in diff).** `_current_celery_task` / `_current_agent_id` now split `ImportError` (silent, true answer) from any other exception (`raise HumanLabelRefused`). |
| 8 | medium | no `.dev/traces/` entry for P1; BACKLOG not transacted | fixed | **FIXED.** `.dev/traces/260808-d6-p1-label-trust-tier.md` added; `.dev/BACKLOG.md` +13/−… in the file list. |
| 9 | low | the fixture ban covers 2 files of 159 | fixed | **FIXED (test-side, not in this diff).** Evidence: P1-fixes mutation #5. |
| 10 | low | R2's detector is blind to composed paths; its self-test is all honest spellings | fixed | **FIXED (test-side, not in this diff).** Evidence: P1-fixes mutation #2. The remaining fragment-assembly blind spot is asserted as a documented limit. |
| 11 | low | R2/R3 scan `app/` only; `scripts/` and `_runlogs/` are outside every restriction | fixed | **FIXED (claim in diff; scan test-side).** `label_service.py`: *"nothing under `scripts/` or `_runlogs/`."* `_runlogs/` really exists and holds `run_eval_prod.py`. |
| 12 | low | `is_human_labelled()` is True for a human tier over an empty answer | fixed twice | **FIXED (in diff), twice.** 0016's CHECK gains `AND COALESCE(reference_answer, '') <> ''`; `label_trust_tier()` downgrades a PRESENT-and-empty answer to `unknown` while leaving a narrow projection alone. |
| 13 | low | 0016's catalog introspection and DROP are not schema-qualified | fixed | **FIXED (in diff).** `JOIN pg_namespace`, `AND nsp.nspname = current_schema()`, `format('ALTER TABLE %I.%I DROP CONSTRAINT %I', con_schema, …)`, and the existence guard qualified too. 0011's deployed copy left alone, stated as a separate decision. |
| 14 | low | the 0015 head assertion was weakened inside the feature commit; the control cannot see that | **partly fixed** | **PARTIAL — CONFIRMED BY THE DIFF.** `tests/unit/test_migration_tenant_0015.py` still carries +13/−4; the relaxation stands and it still lives in `c860780`. Head identity is now pinned once, in `test_migration_tenant_0016.py`. The report: *"The commit itself cannot be repackaged — rewriting `c860780` means rewriting history on a branch, and the task forbids rebasing."* |
| 15 | nit | `"source" not in joined` is a raw substring over the UPDATE SQL | fixed | **FIXED (test-side, not in this diff).** |

**P1's six refuted/overstated claims**, all reworded per the fixes doc: "four independent structural
restrictions" (refuted for the direct-SQL route) · R3 "physically cannot" (refuted) ·
"`label_trust_tier IS NOT NULL` and 'a human wrote this' are the same statement at the database
level" (refuted) · "reachable from an authenticated HTTP request and from nowhere else in the tree"
(overstated) · "no conftest fixture may" (narrower than it reads) · R2's detector "sees every route"
(overstated).

## 4.2 P2 review — 18 findings

| # | sev | finding | claimed | **status from the diff** |
|---|---|---|---|---|
| F1 | high | the "no second write path" guard demonstrated only inside its own blind spot; 3 of 4 forgery spellings invisible to 141 tests | fixed + extended | **FIXED (test-side, not in this diff).** Two scans with opposite blind spots; the division of labour pinned by `test_the_table_aware_scan_has_this_exact_blind_spot`. Evidence: M18 (composed → verb scan only), M19 (schema-qualified → both). |
| F2 | high | the queue's headline ordering property is unpinned in the SQL; `ASC`→`DESC` inverts the queue and 54 tests pass | fixed | **FIXED (in diff).** `_order_by_keys(sql)` added, and `QUEUE_ORDERING["keys"]` is now `_order_by_keys(_UNLABELLED_QUEUE_SQL)` — parsed, not hand-written. Evidence: M15, 3 tests red. |
| F3 | med-high | the write reaches any scenario in the agent's DB, including a `dataset='golden'` row | fixed by scoping | **FIXED (in diff).** `_LABEL_SQL` now carries `AND NOT ({SELECTOR_ELIGIBILITY_PREDICATE})`; `_SCENARIO_EXISTS_SQL` probe runs only on the zero-row path; `already_labelled` → 409 in the route. Relabelling refused rather than half-designed (stated deviation). |
| F4 | medium | `labelled` FILTER not pinned to the selector predicate; the counts identity is unguarded | fixed | **FIXED (test-side, not in this diff).** The diff shows both FILTERs built from `SELECTOR_ELIGIBILITY_PREDICATE`; the *counting* test that makes that binding audible is in `test_eval_label_queue.py`. Evidence: M17. |
| F5 | medium | LIMIT and OFFSET can be swapped without a test noticing | fixed | **FIXED (test-side, not in this diff).** Evidence: M16. |
| F6 | medium | a zero-width answer defeats the empty-label guard and enters the eval at a human tier — **observed through the real ASGI route**, 3 codepoints, status=200, `tier='human_authored'` | fixed | **FIXED (in diff).** `_INVISIBLE_CATEGORIES = frozenset({"Cc","Cf","Zl","Zp","Zs"})` and `visible_answer()`, used by both the writer and the request model. |
| F7 | medium | `human_authored` is stamped on an `X-API-Key` request — R1–R4 are all in-process facts | **fixed structurally**, not by documentation | **FIXED (in diff).** `deps.py` gains `CREDENTIAL_CLERK_JWT/API_KEY/UNKNOWN`, sets `request.state.credential_kind` on all three success returns, and `get_credential_kind` depends on `get_current_tenant`. `label_eval_scenario` 403s anything else, `CREDENTIAL_UNKNOWN` included. |
| F8 | low | `counts.eligible` is `counts.labelled` by assignment, so the payload proves nothing | documented, field kept | **NOT A CODE CHANGE — DELIBERATE, AND THE DIFF CARRIES THE CORRECTION.** `_queue_counts_sync`'s docstring: *"a reader who checked it from the payload would be reassured by a tautology."* |
| F9 | low | `ordering.keys` describes a query that does not exist | fixed | **FIXED (in diff)** — same mechanism as F2. |
| F10 | low | `dict(QUEUE_ORDERING)` is a shallow copy over a nested list | fixed | **FIXED (in diff).** `copy.deepcopy(QUEUE_ORDERING)` at the use site. Evidence: M26. |
| F11 | low | `test_an_empty_answer_is_rejected_without_touching_the_database` does touch the database | fixed | **FIXED (in diff).** `field_validator("reference_answer")` → `_must_carry_something_visible`, so the refusal is a Pydantic 422 with no tenant work. Evidence: M23, 7 of 8 parametrisations red. |
| F12 | low | a soft-deleted agent is still labellable | fixed for the write only | **FIXED (in diff), PARTIALLY BY DESIGN.** `_resolve_agent_tenant_db` uses `select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))`. **The three older read routes in `evals.py` still use the unfiltered form — the diff does not touch them, and both the fix report and the code comment say so explicitly.** |
| F13 | low | **the GET has no fallback for a pre-0011 tenant DB** — `_UNLABELLED_QUEUE_SQL` projects `provenance`/`origin_trace_id` with no `UndefinedColumn` handler, so a pre-0011 tenant gets a 500 | — | **NOT FIXED, AND ABSENT FROM THE FIX REPORT'S DISPOSITION LIST.** The diff confirms: `_unlabelled_page_sync` calls `_query_tenant_db_sync` with no `try/except`, while `_queue_counts_sync` (0016) and `_LIST_EVAL_RUN_DATASETS_SQL` (0014) both have fallbacks. The code comment acknowledges the dependency (*"It requires 0011, which `_LEDGER_SQL` above already requires unconditionally"*) but does not fall back. **`d6-p2-review-fixes.md` renumbers its own F-list (its F13 is the review's F14), so this finding has no disposition row anywhere.** |
| F14 | low | M8's record cannot be replayed | fixed | **FIXED.** Re-run as M8b with the failing identity captured. |
| F15 | nit | the trace's test count is off by one (55 vs 54) | fixed | **FIXED (docs).** |
| F16 | nit | `.dev/HANDOFF.md` was not updated for D6 | fixed | **FIXED.** `.dev/HANDOFF.md` +43 in the file list. |
| F17 | nit | no upper bound on `reference_answer` | fixed | **FIXED (in diff).** `MAX_REFERENCE_ANSWER_CHARS = 8000`, `max_length=` on the field, with the per-night judge-cost argument in the comment. Evidence: M25. |
| F18 | nit | line references off by 1–2 and omit the module path | fixed | **FIXED (docs).** Substance of all three legs confirmed correct by the reviewer. |

## 4.3 P3 review — 14 findings

| # | sev | finding | claimed | **status from the diff** |
|---|---|---|---|---|
| F1 | **HIGH** | the decision gate's stated purpose is unachievable — `refusals[PROMOTION_DISABLED_REFUSAL]` is structurally always 0, for three independent reasons, and the claim is stamped in four code sites | fixed, option (a) + part of (c) | **FIXED (in diff).** All four sites rewritten: `PROMOTION_DISABLED_REFUSAL`'s comment (*"IT IS NOT THE MEASUREMENT THIS COMMENT USED TO CLAIM"*), `select_promotion_candidates`' gate-3 docstring, the inline comment at the gate, and the test docstring. The ordering is kept on its real merit. The reviewer's probe is now a test. |
| F2 | medium | the new label-write test already existed (P2's), and the deviation it forced was avoidable | fixed | **FIXED (test-side, not in this diff).** P3's copy deleted; `test_label_provenance.py` net **+1** test instead of +2. **Note: the control still carries two `--deselect` flags at `f78524e`** — one moved from the deleted duplicate to the new `test_eval_service.py` test. Composition changed, count did not. |
| F3 | medium | "a labelled row enters the eval" holds only for a pool smaller than the sample size (`ORDER BY RANDOM() LIMIT 30`) | fixed | **FIXED (in diff).** `run_eval_suite`'s docstring now carries *"ELIGIBLE IS NOT PRESENT, AND THE FIRST VERSION OF THIS PARAGRAPH CONFLATED THE TWO"* with the 30/200 arithmetic and `BACKLOG 4.14`. Three new tests; the cursor double now honours `LIMIT`. |
| F4 | medium | both "independent locks" are mutable module state with no absence pin — **and the load-bearing lock was never named** | fixed, both halves | **FIXED (in diff).** `SCENARIO_SOURCE_TRUST_TIER` and `VERIFIED_QA_PROMOTION_DECISION` are `MappingProxyType`. Lock zero named strongest-first in the `LABEL_TRUST_TIERS` block comment, `promote_to_verified_qa`'s docstring, `eval.py`'s module docstring and the recorded `reason`. **Residue stated in the diff itself:** the two lock-zero pins are module-scoped, *"so a third module adding it trips nothing."* |
| F5 | medium | the downstream analysis omits the deploy gate | fixed, with one correction to the finding | **FIXED (in diff).** `run_eval_suite`'s docstring carries the full chain and the correction that `apply_signal_evidence_gate` does **not** read `pass_rates` — the 0.85 bar is prose in `_DEPLOYMENT_SYSTEM_PROMPT`. Four new tests. |
| F6 | low | stale pre-D6 prose in a file P3 edited (`eval.py` module docstring, and the `promoted: 0` comment) | fixed, + an undeclared sibling | **FIXED (in diff).** Both rewritten to name three locks. `eval_service.py`'s own module docstring carried the identical defect and was fixed in the same pass — **declared as a deviation, not cited in the finding.** |
| F7 | low | a P1 absence pin is satisfied by the wrong lock — observed green with the resolver gate deleted | fixed | **FIXED (test-side, not in this diff).** `assert refusals == {"trust_tier:customer_negative": 1}`. Evidence: mutation I, which reproduces the reviewer's own M-B. |
| F8 | low | citation drift: `eval_service.py:1591` points at unrelated code | fixed | **FIXED (docs).** Cited by symbol now, *"and both line numbers are already wrong again after this commit, which is the point."* |
| F9 | low | a new test pins `measured is True` over 2 observations, contradicting `MIN_SCORED_OBSERVATIONS = 3` | fixed | **FIXED (test-side, not in this diff).** The observation count is now the assertion. Evidence: mutation J. |
| F10 | low | half of "the disablement is recorded with its reason" is unpinned | fixed | **FIXED (test-side, not in this diff).** `+1` test in `test_eval_service.py`. Evidence: mutation H — the pre-existing sibling stayed green under the narrowing. |
| F11 | low | `human_authored` names an account, not a human | **not fixed — out of scope**, documented | **NOT FIXED, DOCUMENTED (in diff).** `_label_principal`'s docstring: *"Attributing a write to a specific person needs the principal carried out of the dependency, not re-derived from the tenant row."* `BACKLOG 4.7`. |
| F12 | nit | the lock-two tests aliased a real schema source (`monkeypatch.setitem(…, "mined", …)`) | fixed | **FIXED (test-side, not in this diff).** Both now use `"owner_written"`. |
| F13 | nit | the stated hazard for lock one does not exist in the tree | fixed | **FIXED (in diff).** Gate 1's comment now reads *"THE HAZARD IS LATENT, NOT LIVE, AND THE EARLIER WORDING OVERSTATED IT"* and names `BACKLOG 4.12` as the change that arms it. |
| F14 | nit | `BACKLOG` section 4 ran 4.9, 4.10, 4.11, 4.12, 4.13, 4.8 | fixed | **FIXED (docs).** And `f78524e`'s commit message dropped this finding entirely — corrected by `d0a3b4e`. |

## 4.4 The shape of what the diff CANNOT settle

**16 of the 47 findings** are fixed in test files that §1.1 summarises rather than reproduces:
P1 #1 (partly), #9, #10, #15; P2 F1, F4, F5; P3 F2, F7, F9, F10, F12 — plus the test-side halves of
P1 #4 and #11. For each, the only evidence in this artifact is a mutation proof written by the same
agent that wrote the fix. **The reviewers re-ran 18 mutations of their own (§3, P1 review §1/§5;
P2 review §4 MX1–MX7; P3 review M-A–M-D) and reproduced the implementers' numbers**, but they
reviewed the *pre-fix* tree; nobody has adversarially re-probed the post-fix detectors.

---

# 5. THE PHASE-DECIDING QUESTION, ANSWERED FROM THE DIFF

## 5.1 Can anything that is not a human write a `reference_answer` at a human trust tier?

**Answer: not through any path this branch built, and one path it did not close is disclosed rather
than shut. But the strongest true statement is narrower than "no", and the branch says so itself.**

### What the diff shows holding the door

**(a) The tier is not a parameter — it is a module constant the writer stamps.**

```python
+HUMAN_AUTHORED_TIER = "human_authored"
...
+def record_human_label(
+    conn,
+    *,
+    scenario_id: str,
+    reference_answer: str,
+    labelled_by: str,
+) -> dict:
```

```python
+    NOTE THE ABSENT PARAMETER. There is no `tier` argument and there must never
+    be one: the tier is what this function asserts, not what its caller asks
+    for. A caller able to name the tier is a caller able to name
+    `human_authored` from anywhere, which is the whole thing the hierarchy is
+    defending against.
```

**(b) The route refuses a machine credential outright.** This is the hunk that decides the question
for out-of-process callers:

```python
+    if credential_kind != CREDENTIAL_CLERK_JWT:
+        log.warning(
+            "label_eval_scenario.refused_credential",
+            ...
+        )
+        raise HTTPException(
+            status_code=403,
+            detail=(
+                "A human-authored label requires a signed-in user session. "
+                "An API key authenticates an account, not a person, so it "
+                "cannot record a human trust tier."
+            ),
+        )
```

and, in `deps.py`, what makes `credential_kind` mean anything:

```python
+CREDENTIAL_CLERK_JWT = "clerk_jwt"
+CREDENTIAL_API_KEY = "api_key"
+# Nothing recorded a credential. Reached only when `get_current_tenant` is
+# overridden (a test) or replaced; a route that cares must treat it as "cannot
+# tell" and fail CLOSED, never as "probably a human".
+CREDENTIAL_UNKNOWN = "unknown"
```

**(c) The in-process guard runs before anything is decrypted**, as the handler's first statement:

```python
+    try:
+        assert_human_context()
+    except HumanLabelRefused as exc:
```

and `assert_human_context` refuses inside a Celery task or an agent-tool context, with **both
detectors failing closed on malfunction**:

```python
+    try:
+        return _state.get_current_task()
+    except Exception as exc:
+        raise HumanLabelRefused(
+            "could not determine whether a Celery task is driving this call "
+            f"({type(exc).__name__}: {exc}); a human trust tier is never "
+            "stamped on an unverified context"
+        ) from exc
```

**(d) The body cannot name the author or the tier:**

```python
+    model_config = ConfigDict(extra="forbid")
+
+    reference_answer: str = Field(
+        min_length=1,
+        max_length=MAX_REFERENCE_ANSWER_CHARS,
```

**(e) The database has no vocabulary for a model's label** — but only that:

```sql
+                    CHECK (
+                        label_trust_tier IS NULL
+                        OR (
+                            label_trust_tier IN ('human_verified', 'human_authored')
+                            AND COALESCE(reference_answer, '') <> ''
+                        )
+                    );
```

### What the diff shows NOT holding the door — quoted from the branch's own code

**The CHECK does not authenticate anybody.** 0016's docstring, in the diff:

```
+        And it is load-bearing rather than decorative, in the narrow sense that
+        is actually true: a raw `UPDATE eval_scenarios SET label_trust_tier =
+        'model_generated'` is refused by the database itself... A raw `... = 'human_authored'`
+        is NOT refused. The database bounds the vocabulary; it does not
+        authenticate the writer.
```

**Raw SQL from a worker module is caught by a scan, not by a mechanism.** `label_service.py`, in the
diff:

```
+       THIS SAID "PHYSICALLY CANNOT" UNTIL 2026-08-09, AND THAT WAS FALSE. The
+       P1 adversarial review appended a function to a real Celery task module
+       that issued an f-string `UPDATE eval_scenarios SET ...
+       label_trust_tier = 'human_authored'` — importing nothing, calling
+       nothing — and every test in test_label_provenance.py stayed green...
+       What is true is that no
+       forgery shape anyone has yet devised passes unnoticed; what is NOT true
+       is that raw SQL cannot reach the column.
```

**Content is still taken on the caller's word.** `label_service.py`, in the diff:

```
+    They authenticate the CALL SITE. They say nothing about the CONTENT of
+    `reference_answer` or about the identity in `labelled_by`... An `app/api/` route that asks
+    a model to draft an answer and forwards it as
+    `record_human_label(reference_answer=<model prose>,
+    labelled_by='owner@example.com')` produces a `human_authored` row of model
+    output and trips none of R1-R4
```

No such route exists in this diff — the one route that exists takes
`body.reference_answer` straight from a Clerk-authenticated request and passes
`_label_principal(tenant)` as the author. But the guard against one being written is a decision
written in a docstring, not a mechanism.

**And `labelled_by` names an account, not a person** (diff, `_label_principal`): `f"tenant:{tenant.id}"`.

### The honest answer

**No model, agent, task or judge can reach the human-tier write through any code path in this diff,
and an out-of-process automation holding an API key is refused with a 403.** What is *not* true is
that it is impossible: raw SQL in a worker module reaches the column and is caught by two AST scans
whose residual blind spot (`"label" + "_trust_tier"` composed inside the allowlisted
`eval_service.py`) is written down as `BACKLOG 4.8`; the database refuses the wrong *value*, never
the wrong *writer*; and the *content* of the answer is whatever the authenticated caller sends.

**A fact that dominates all of the above:** `alembic_tenant` 0016 has been applied to **no database**.
On every tenant today the label write raises `psycopg2.errors.UndefinedColumn` and the route returns
a **503**. So the currently observable answer to "can anything write a human tier?" is *nothing can,
human or otherwise, because the column does not exist anywhere.*

## 5.2 Can a labelled scenario reach `verified_qa`?

**Answer: no. Three locks, and the diff shows all three. The strongest is that the function has no
caller.**

**LOCK ZERO — no caller.** From the diff (`eval.py` module docstring):

```
+    0. NO CALLER. `promote_to_verified_qa` is invoked from nowhere under `app/`;
+       the `promoted: 0` this task returns is a literal, not a result. Pinned by
+       TestPromotionIsUnreachableFromTheTask below.
```

and the return value itself:

```python
+            # Always 0 — a literal, not a result: this task never calls
+            # promote_to_verified_qa (lock zero), and behind that the resolver
+            # gate and the decision flag.
             "promoted": 0,
```

**Collector's independent check:** `grep -rn "promote_to_verified_qa" app/` returns its own
definition (`eval_service.py:2028`), its two internal log lines, and prose. **No call site.**

**LOCK ONE — the resolver reads `source`, which labelling never writes.** The label UPDATE, from the
diff, assigns exactly four columns and `source` is not one of them:

```sql
+_LABEL_SQL = f"""
+    UPDATE eval_scenarios
+    SET reference_answer = %(reference_answer)s,
+        label_trust_tier = %(tier)s,
+        labelled_by = %(labelled_by)s,
+        labelled_at = NOW()
+    WHERE id = %(scenario_id)s::uuid
+      AND NOT ({SELECTOR_ELIGIBILITY_PREDICATE})
+"""
```

And the gate, unchanged in what it reads, with the swap-hazard now correctly described as latent:

```
+       THE HAZARD IS LATENT, NOT LIVE, AND THE EARLIER WORDING OVERSTATED IT
+       (D6 P3 review, finding 13). The swap would change nothing today: none of
+       `run_eval_suite`'s three selectors projects `label_trust_tier`...
```

**Collector's independent check:** all three selectors in `eval.py` project
`id, source, question, reference_answer, retrieved_contexts, dataset`. **`label_trust_tier` appears
nowhere in `eval.py` except one prose reference to `BACKLOG 4.12`.** Confirmed.

**LOCK TWO — the decision, now consulted and now immutable.** The new gate:

```python
+        if not VERIFIED_QA_PROMOTION_DECISION["enabled"]:
+            _refuse(PROMOTION_DISABLED_REFUSAL)
+            continue
```

on a constant that can no longer be lifted by subscript assignment:

```python
+VERIFIED_QA_PROMOTION_DECISION: Mapping[str, object] = MappingProxyType({
     "enabled": False,
```

**Two caveats the diff itself states.** (1) *"All three are process-local: none is recorded in any
database."* (2) Lock zero's absence pins are module-scoped — *"a THIRD module introducing the call
would trip neither. Say that rather than claim a pin that does not exist."* A tree-wide caller scan
was declared out of the bounded pass.

**Also from the diff, and it is the reason this question needed asking at all:** the tier the writer
stamps *outranks* the promotion minimum.

```
+# THIS PARAGRAPH USED TO READ "nothing in the shipped system produces tier >=
+# human_verified yet: there is no correction UI", AND D6 MADE IT FALSE.
+# `label_service.record_human_label` ... stamps `human_authored`,
+# rank 3, which CLEARS VERIFIED_QA_MIN_TRUST_TIER (rank 2) outright.
```

So the answer is "no" **because of three deliberate locks**, not because the vocabulary forbids it.
Remove any two and the third still holds; remove lock zero and a labelled row is still refused by the
resolver *and* the decision.

**Finally, the owner's decision is now recorded on every run rather than inferred**, and the run
carries the boolean beside the count:

```python
+            "promotion_enabled": VERIFIED_QA_PROMOTION_DECISION["enabled"],
             "promotion_disabled_reason": VERIFIED_QA_PROMOTION_DECISION["reason"],
```

---

# 6. WHAT THIS BRANCH DOES NOT PROVE

Assembled from all six reports' "what is not proven" sections plus the collector's own reading of the
diff. Nothing here is softened.

## 6.1 The migration was never applied, and could not be

- **`alembic_tenant/versions/0016_eval_scenario_label_provenance.py` has been applied to no
  database.** There is no PostgreSQL server on this machine. **No `ALTER TABLE` in that file has
  executed anywhere.** `CONTROL_DB_URL` points at live Neon production and was not used.
- **The CHECK constraint has never rejected anything** — neither the `model_generated` refusal nor the
  new `COALESCE(reference_answer,'') <> ''` arm. The test that would exercise both,
  `test_migration_tenant_0016_db_roundtrip`, is written out in full and **skips**.
- **The schema qualification (`pg_namespace` / `current_schema()` / `%I.%I`) has never been planned or
  executed.** It is asserted at the source level only.
- **The downgrade has never run.** Its stated consequence — losing every human label — is untested.
- Consequence for the whole feature: **on every tenant database today, the label route returns 503**,
  and the queue's `human_labelled` count returns `null` with `label_provenance_available: false`. The
  200 path of the write has never touched a real `label_trust_tier` column.

## 6.2 Every gate that skipped

- **12 skips in every gate run from P1 onward** (11 at the `4179a5c` baseline). The +1 is 0016's
  integration roundtrip. **Every `-m integration` harness skips**, and all six reports state the same
  rule: *a skip is unobserved, never a pass.*
- **No frontend or widget gate was run.** P4 (the console labelling queue) is unstarted by the owner's
  "backend only this run", and no frontend code is touched. `npx tsc --noEmit`,
  `check:no-dusk-tokens`, `check:ops-room-wiring`, `test:unit`, `test:e2e` and the widget size check
  were **not run** — correctly, since nothing in the diff touches those workspaces, but they are
  therefore unobserved for this branch.
- **No gate run exists at the branch tip `d0a3b4e`.** The last run is at `f78524e`.

## 6.3 Every path unexercised for want of a database

- **No SQL in this branch has been executed by Postgres.** Every statement — `_UNLABELLED_QUEUE_SQL`,
  `_QUEUE_COUNTS_SQL`, `_QUEUE_COUNTS_PRE_0016_SQL`, `_LABEL_SQL`, `_SCENARIO_EXISTS_SQL`,
  0016's `DO $$` block — is asserted at the **string level** and against a `_RecordingCursor`.
- **`array_position(%(source_priority)s::text[], source) ASC NULLS LAST` has never been planned or
  executed.** The row order Postgres would actually produce is unobserved. There is also **no index
  supporting the queue's `WHERE` + `ORDER BY`** (`BACKLOG 4.9`).
- **The scoped UPDATE has never been planned or executed**, and the write-then-probe pair is **not
  `FOR UPDATE`**. The 409-under-concurrency argument is *"reasoning about the manual, not an
  observation"* (`BACKLOG 4.11`).
- **`counts` has never been computed by Postgres.** `FILTER (WHERE …)`, `= ANY(...::text[])` and the
  `unlabelled + labelled == total` identity are asserted against canned tuples.
- **`AVG(score) GROUP BY metric` was never executed.** The deploy-gate arithmetic test computes the
  mean in Python over the rows the run handed to `write_eval_results`.
- **No real `eval_scenarios` row has ever been labelled.** `record_human_label` has only ever run
  against a recording cursor.
- **No mined row has ever been seen.** See §7 — the miner cannot run at all.

## 6.4 Paths unexercised for reasons other than the database

- **R4 has never run in a real Celery worker** — only against Celery's real `_state` stack in-process.
- **`request.state.credential_kind` has never been read in a real ASGI process.** Exercised via
  `get_current_tenant` called directly with a hand-built `Request`, and via dependency overrides. *"A
  genuine Clerk JWT arriving over HTTP needs a live JWKS."*
- **`ORDER BY RANDOM()` is not emulated** by the test double; it truncates.
- **The orchestrator's 0.85 ship bar is applied by a model, not by code.** The tests assert the
  sentence is in `_DEPLOYMENT_SYSTEM_PROMPT` and that `apply_signal_evidence_gate` does not read the
  rates. *"Whether a Sonnet orchestrator actually applies it is unobserved here and always has been."*
- **Lock zero's absence pins are module-scoped.** A third module introducing a
  `promote_to_verified_qa` call trips nothing.
- **The R3 residual is undetectable statically** — a column name composed from fragments inside the
  allowlisted `eval_service.py` (`BACKLOG 4.8`).
- **`MAX_REFERENCE_ANSWER_CHARS = 8000` is a judgement, not a measurement.**
- **The 403 changes the contract for any API-key caller of this route.** There is none today, so
  nothing was broken — an argument from absence, not from measurement.
- **P2 review finding F13 is unfixed and undisposed** (§4.2): a pre-0011 tenant DB gets a 500 from
  the GET rather than the degradation shape the module argues for elsewhere.
- **The `agent_tools` ContextVar leak (`BACKLOG 4.6`) is not fixed** — it has now cost two identical
  autouse fixtures in two test modules, and every module touching the human-label path will pay it
  again.

## 6.5 Structural facts about the branch itself

- **Stacked on unmerged `feat/d1-agent-invocation`.** The plan's own risk: *"If D1 changes in review,
  this rebases."* D1's two owner decisions (`BACKLOG 0.4`, `0.5`) are recorded as still open.
- **`c860780` cannot be repackaged.** The `test_migration_tenant_0015.py` weakening rides inside the
  P1 feature commit because rebasing is forbidden.
- **The tier-1 reviewers reviewed the pre-fix trees.** No adversarial pass has probed the post-fix
  detectors, the 403, the scoped UPDATE or the three locks.
- **`.dev/reference/d6-mining-yield.md` is untracked** and will not survive a clean checkout of the
  branch. It is the document that answers `BACKLOG 4.10`.

---

# 7. THE MINING-YIELD REPORT'S CONCLUSION

`.dev/reference/d6-mining-yield.md` (untracked, 399 lines, written 2026-08-09 at `d0a3b4e`). Its own
first statement about method: **"I ran nothing. No gate run, no test run, no database touched … Every
claim below is a static read of the code and the migrations."**

## The conclusion, verbatim in substance

```
queue_depth = 0 + F + G

  mined (source='mined')       — exactly 0, provably, from the schema
  production (filed traces)    — F, unknown, plausibly 0. THIS PATH WORKS.
  red_team                     — 0: it writes a NON-EMPTY answer, so those rows are
                                 already labelled and never enter the queue
  generated                    — G, only when Haiku returns an empty reference_answer

"So: plausibly zero overall, and the mined component is not 'plausibly' anything — it is zero."
```

**And the mechanism is not the one the plan and `BACKLOG 4.10` name.** The plan attributes the zero
to `mine_production_scenarios` `continue`-ing past jobs without a `conversation_id`. The report:

> "That `continue` (`scenario_service.py:444-446`) **is unreachable.** The statement two steps before
> it names a column that does not exist in the control DB, so the miner **raises** on the first
> flagged row and `run_eval_suite` swallows the exception as a warning. The plan, the docstring and
> `BACKLOG 4.10` all describe a graceful skip. What actually happens is an aborted function."

`jobs` has nine columns (`alembic/versions/0001_control_db_initial.py:66-77`) and **no
`conversation_id`**; `grep -rn "ALTER TABLE jobs" alembic/versions/` returns no matches; every
`Job(...)` construction passes only `tenant_id, agent_id, kind, status`. The report notes this was
**already recorded in Phase 21** (`21-RESEARCH.md:274-275`, `21-05-PLAN.md:63`) and deliberately
routed around for the sibling `bench_service`, while the miner was left as it was.

**Why it survived:** no unit test covers `mine_production_scenarios`; every test that would reach it
stubs it out (`test_eval_task.py:164`, `test_eval_agent_invocation.py:1551`,
`test_label_downstream.py:247`); no integration test exists and it would skip if it did.

**Two further defects it records in passing**, both bearing on any future yield estimate:

1. `ON CONFLICT DO NOTHING` **can never fire** — the PK is a fresh `uuid4()` and there is no other
   unique constraint, so `scenario_service.py:130`'s "Idempotent via ON CONFLICT DO NOTHING" is false
   for this path. If C3 were fixed, a 168-hour lookback on a nightly beat would re-mine the same
   failure for **seven consecutive nights**.
2. `needs_clarification` is absent from the miner's verdict `IN` list — an entire gatekeeper failure
   mode excluded by omission, with no comment saying it was a choice.

## The recommendation

> **"Do not build [P4] yet."** Three reasons, ranked: (1) *"A console built now would tell the owner
> something false … an empty list reads as 'there are no failures to label.' There ARE failures —
> they are sitting in `job_events`, flagged, with their `conversation_id` recoverable from the
> `agent.response` payload. The miner just cannot reach them."* (2) The one working producer already
> has a console — the ops-room bench. (3) The miner repair is ~30 lines plus one tenant migration
> **which cannot be applied on this machine, so it ships unapplied like 0016**.

Suggested order: (a) two guard tests from M1, no database needed; (b) one control-DB `COUNT(DISTINCT
job_id)` over flagged `job_events`, **run by the owner** — *"I did not run it. The only control DB
reachable from here is live production"*; (c) the miner repair as its own branch if (b) is non-zero;
(d) revisit P4 with a real number.

> "If (b) comes back zero, the finding is that **the labelling loop is waiting on production traffic,
> not on a queue UI** — and that is worth knowing before a console is built for it."

## Corrections it makes to records this artifact also carries

| record | says | actually |
|---|---|---|
| the D6 plan's Risks | mining `continue`s past jobs without `conversation_id`; empty is "a plausible outcome" | the `continue` is unreachable; the miner **raises**. Empty is certain, not plausible |
| `BACKLOG 4.10` | "zero is a plausible reading" | zero is established; the row can be closed as measured |
| the D6 plan §"What D6 actually is" | contained red-team findings "are all stored and never scored" | red-team rows carry a non-empty reference answer and **are** scored |
| `d6-p2-labelling-queue.md:74` | the miner "recovers the question from tenant `messages` via `jobs.conversation_id`" | it cannot — the column does not exist; the statement raises |
| `scenario_service.py:130` docstring | "Idempotent via ON CONFLICT DO NOTHING" | not idempotent on this path |

**What this means for the branch under judgement:** D6 built a queue, a write path and a downstream
contract for rows that **its nominal producer cannot produce**. That is not a defect in D6 — the
report is explicit that it is a finding about the miner — but it is the fact that decides whether any
of the 12,412 lines has yet moved a single row out of the unlabelled state. **None has, and none
could, because 0016 is applied nowhere and the miner raises before it inserts.**
