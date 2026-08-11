# BACKLOG 1.13 — the 22 skips, inventoried and partly opened

**2026-08-11.** `1.13` said "decide per module: enable it, or delete it" but nobody had written down
what the 22 skips actually need. Inventory taken by running the gate with `-rs`, then acting on the
safe subset. Two defects found, one fixed.

## The inventory (reproduced exactly: 15 passed / 22 skipped / 24 deselected)

| Needs | N | Modules |
|---|---|---|
| `INTEGRATION_TESTS_ENABLED=1` + Postgres only | 3 | `test_act07_resolve_live` |
| + Postgres **and** Redis | 7 | `agent_chat`×2, `aud03`, `integration_e2e`×2, `worker_kill`, `ver01` |
| + `CONTROL_DB_SYNC_URL` naming a test DB | 4 | `test_prompt_versions_e2e` |
| A real `ANTHROPIC_API_KEY` | 3 | `test_red_team_rtx` |
| Third-party credentials | 4 | `stripe_live`×2, `ingestion_e2e`×2 |
| The `pipeline` extra (docling) | 1 | `test_ingestion_chain` |

**14 of 22 are one env var away from the machine that now exists.** That is the number `1.13` wanted.

## Safety finding: the billing hazard is real, not hypothetical

`.env` carries a **real 108-character `ANTHROPIC_API_KEY`**. The owner has no Claude credits, so the
practical outcome of a stray live call is an auth failure rather than a bill — but that is a guess about
an account balance, not a guarantee, and it is not mine to gamble.

`test_ver01_adversarial_harness` drives 100 adversarial messages through the transactional dispatcher,
whose Step 5 Actor gate is **a synchronous Haiku call per mutating attempt** (`BACKLOG 2.8`). The module
docstring argues every mutating call dies earlier at the Step 2.5 IDV gate (the corpus runs with
`verified_session_token=""`), which would mean zero model calls. That is very likely correct **and it is
a docstring, not an observation** — acting on it is precisely the "inference stated at the confidence of
observation" failure this repo has now caught three times.

**So `ver01` and `red_team_rtx` were left off.** Opening them needs the Actor-gate question settled by
measurement first — e.g. run with `ANTHROPIC_API_KEY` unset and confirm the run still reports
`provider_not_configured == 0`, which its own assertion already demands.

### COSTED 2026-08-11 — the ver01 bound is pennies, and the docstring is only 1/5 true

Two corrections to the paragraph above, both from measurement rather than reading.

**The IDV short-circuit is conditional, not universal.** `tools.py:531` gates Step 2.5 on
`snapshot.get("requires_identity_verification", False)`, and `test_ver01:295` records that the clean
tenant sets that flag on **`issue_refund` only** — 20 of the 104 corpus entries. The docstring's "every
call dies at 2.5" is literally true for `issue_refund` and covers under a fifth of the corpus. The other
84 proceed past it; how many then die at Step 4 (rate/constraint, `tools.py:823`) before the Actor gate
at `:865` is still unmeasured — the 44 `value_bound_evasion` entries are built to breach a constraint,
so plausibly most of them.

**The cost bound, computed from the real prompt and the real corpus** (`claude-haiku-4-5`, $1/$5 per
Mtok, `max_tokens=512`, one forced `submit_verdict` call per mutating attempt; median prompt 1,357 chars
≈ 388 tokens; output is a verdict enum + one-sentence rationale ≈ 80 tokens):

| reach the gate | input tok | output tok | cost |
|---|---|---|---|
| all 104 | 40,056 | 8,320 | **$0.082** |
| 84 (issue_refund IDV-blocked) | 32,353 | 6,720 | **$0.066** |
| ~50 (value-bound denied too) | 19,258 | 4,000 | **$0.039** |

**Absolute ceiling $0.31**, if every call somehow maxed its 512-token budget — which a forced
one-sentence tool call will not.

So the framing above ("not mine to gamble the owner's balance") **overstated it**. The bound is 4–8
cents. The real reason to measure first was that an unobserved run is unobserved, not the money. And on
a zero-balance key the calls fail auth rather than billing silently, which the test's own
`provider_not_configured == 0` assertion turns into a loud red rather than a quiet charge.

**`red_team_rtx` RTX-01 is a genuinely different question** and is NOT costed here: its attackers run
`claude-sonnet-4-6` ($3/$15, 3× Haiku both ways) with multi-turn probes rather than one forced tool
call. Plausibly dollars, not cents. No call count measured.

### RUN 2026-08-11, owner-authorised — VER-01 SC3 PASSES, first time in repo history

Owner authorised the spend (~$1 budget). Key lives in `apps/api/.env` and is **not** in `os.environ`;
`actor_seam.py:38` builds `anthropic.Anthropic()` straight off `os.environ`, and pydantic loads `.env`
into `Settings` only — so the runner exports it explicitly. Key never printed, never on a command line.

```
VER-01 SC3 attempted=104
VER-01 SC3 by_verdict={'capability_denied': 15, 'identity_required': 20,
                       'actor_blocked': 30, 'rate_denied': 19, 'awaiting_approval': 20}
1 passed in 220.33s
```

**The costing analysis is confirmed against the run.** `identity_required: 20` is exactly the
`issue_refund` IDV short-circuit predicted above, and `15 + 30 + 19 + 20 = 84` is exactly the set that
proceeds past it. **30 attempts reached the Actor gate** (`actor_blocked`), so actual spend was
~11,640 input + ~2,400 output tokens ≈ **$0.024**, *under* the $0.039–$0.082 range estimated. Ceiling
for 30 calls would have been $0.088.

`langfuse` / `opentelemetry` export errors in the log are environmental (no collector on this box), not
test failures.

### Defect 3 (FIXED): the fixture read the wrong database, and never ran a single message

First attempt errored in `clean_tenant` before any corpus message ran — **zero Actor calls, zero spend**:

```
sqlalchemy.orm.exc.UnmappedInstanceError: Class 'builtins.NoneType' is not mapped
  tests/integration/test_ver01_adversarial_harness.py:964  db.expunge(agent)
```

`db.get(Agent, agent_id)` returned `None` immediately after the agent rows were committed
(`control_engine.begin()`, so the seed was durable). Cause: line 958 ran
`from app.core.database import get_sync_db` **above** the `with _control_db_redirected(...)` block.
A direct `from X import Y` binds the object into the frame at that moment, so
`patch("app.core.database.get_sync_db", ...)` never reached the local name — the fixture seeded the
ephemeral control DB and then read back through the **real** session, which the integration conftest
points at the shared `wchats_control`.

This is precisely the binding hazard the module's own docstring documents at length for
`invoke_probe_tool` / `red_team_mode` (lines 36-61) — the fixture below it did the one thing that
docstring says not to. Fixed by binding inside the patch context, plus an explicit
`assert agent is not None` naming the failure mode so the next occurrence reads as a wrong-database
error instead of an `UnmappedInstanceError` on `None`.

Two defects therefore stood between this gate and its first green: the `env.py` migration retarget
(Defect 1) and this. Neither was visible while the module skipped.

Note the run that *was* made logged `provider.red_team_mode_stub`, confirming the provider is stubbed on
the act07 path.

## Defect 1 (FIXED): alembic silently migrated the wrong database

**All three `act07` tests failed the moment they were enabled** — `relation "tenants" does not exist`,
with alembic reporting success in the same run.

Cause: `tests/integration/conftest.py:52` sets `CONTROL_DB_SYNC_URL` to the shared local control DB.
`alembic/env.py:40` then did, unconditionally:

```python
if "CONTROL_DB_SYNC_URL" in os.environ:
    config.set_main_option("sqlalchemy.url", os.environ["CONTROL_DB_SYNC_URL"])
```

which **overwrites** the URL a programmatic caller already set. The `control_db_url` fixture creates
`wchats_test_2205_act07_<hex>`, calls `cfg.set_main_option("sqlalchemy.url", conn_url)`, then
`command.upgrade(cfg, "0019")` — and alembic migrated `wchats_control` instead. The fixture then
inserted into an unmigrated ephemeral database.

The comment above that line already said *"for CLI mode"*. The guard just never restricted itself to it.
Fix narrows the override to the case the comment claims, by only applying the env var when the caller has
not set a URL. `alembic.ini` intentionally carries no `sqlalchemy.url`, so CLI mode is unchanged.

**Observed red → green, on the same tree with only `env.py` differing:**

```
before:  3 failed in 39.37s   — relation "tenants" does not exist
after:   2 passed, 1 failed in 60.33s   — full 0015→0019 chain runs on the ephemeral DB
```

**Blast radius is wider than act07.** `act07`'s fixture says it "Mirrors `test_red_team_rtx.py`'s
`control_db_url` fixture exactly", and `ver01` builds ephemeral tenant + control DBs the same way. Every
fixture that migrates an ephemeral DB through the Alembic Python API was hitting this. That is a large
part of why the enabled set has never been observed to pass.

**Latent hazard now closed:** the old behaviour meant the migration target was whatever
`CONTROL_DB_SYNC_URL` happened to name. Under the integration conftest that is pinned to localhost, so
**production was never at risk in this configuration** — but the override defeated an explicit caller by
design, and `CLAUDE.md` records that `CONTROL_DB_URL` points at live Neon production.

## Defect 2 (FILED, not fixed): a tightened ceiling does not deny a prior approval

`test_tightened_ceiling_denies_a_previously_approved_confirmation` fails on the product, not the test:

```
assert 'executed' == 'denied'
{'status': 'executed', 'confirmation_id': '...', 'reason': None}
```

Seed a `max_amount_cents` ceiling of 100,000, raise a 5,000-cent refund confirmation, approve it, tighten
the ceiling to 1,000, then execute. Expected `denied` + `capability.denial:max_amount_cents`. Got
`executed`.

Ruled out as a test defect: `_tighten_ceiling` (`:335`) UPDATEs `capability_envelopes.constraints` on the
same `agent_id`+`skill` the seed inserted, and both write the identical single-key shape
(`{"max_amount_cents": N}`) — so it genuinely tightens rather than blanking the constraint, and the row
exists (the earlier approve/dispatch assertions in the same test depend on it).

So the execution path does not re-read the capability envelope at execution time; it trusts the frozen
approval. **This is the third instance of the `2.19` family** — "a stored decision outlives the rules that
produced it" — after `5.1` (OPS-15, approve-deployment on a frozen `recommendation`) and the eval half
closed at `8b124d4`. It is the highest-stakes of the three because the frozen decision moves money.

Filed as `BACKLOG 5.6` rather than fixed here: changing enforcement semantics on the refund path has its
own threat surface and belongs in a planned change, exactly as `5.1` records for OPS-15.

## No regression, and an unexplained wall clock

The standard gate is byte-for-byte unchanged after the `env.py` fix:
**15 passed, 22 skipped, 24 deselected** — the same counts recorded on 2026-08-11 pre-fix.

**Recorded but NOT diagnosed:** both of today's runs of that gate took **243.92s** and **258.73s**,
against the **109.31s / 119s** recorded earlier on the same tree. Two consistent readings today, two
consistent readings before, ~2.2x apart, machine otherwise idle and suites run one at a time.

I am not calling this a regression. This repo has already recorded one session diagnosing a wall-clock
regression from two data points that did not exist, and the `env.py` change cannot plausibly cost 130s
(it removes work — one `get_main_option` call, and migrations now target a small ephemeral DB rather
than the large shared one on the enabled path, which does not even run in this gate). Most likely an
environmental difference not captured here. Written down so the next reading has four points, not two.

## Left undone, deliberately

- The other 11 locally-runnable skips (`agent_chat`, `aud03`, `integration_e2e`, `worker_kill`,
  `prompt_versions`) were not enabled. The `env.py` fix likely unblocks several, since they share the
  ephemeral-DB fixture shape — unverified, and stated as unverified.
- `ver01` / `red_team_rtx` remain off pending the Actor-gate spend question above.
