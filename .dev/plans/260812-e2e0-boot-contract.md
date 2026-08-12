# E2E-0 · fix the boot contract

**Row:** `1.21 · env-example-cannot-boot`. **Plan step:** `PRODUCTION-READINESS.md` §4, Phase A, E2E-0.
**Why first:** every later E2E step needs someone to be able to boot the app from a clean checkout.

## Goal

`.env.example` covers every `Settings` field that has no default, and a unit test fails when a new
no-default field is added without landing in the example. Closes §3.2.

## Established facts (2026-08-12, this session)

- `app/core/config.py` declares **63** fields. Counted from the file, the no-default set is:
  `NEON_API_KEY`, `NEON_ENCRYPTION_KEY`, `PLATFORM_CREDENTIAL_KEY`, `CONTROL_DB_URL`,
  `CONTROL_DB_SYNC_URL`, `ADMIN_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `JWT_SECRET`,
  `CLERK_WEBHOOK_SIGNING_SECRET` — **10**, matching `1.21`.
- **There are TWO example files**, which `1.21` does not say: root `.env.example` (2822 B) and
  `apps/api/.env.example` (1713 B), both tracked. Plus `apps/admin/.env.example` and
  `apps/admin/.env.local.example`.
- `_find_env_file()` walks up from `app/core/config.py`, so **`apps/api/.env` is what actually
  loads** (it exists); the root `.env` is never reached by `Settings`. That makes
  `apps/api/.env.example` the canonical API example and the root one a monorepo-level file.
- Read of `.env.*` is denied by the owner's global settings, so the current contents were obtained
  by owner paste rather than by tool read. Do not route around that rule.

## Approach

1. **Derive, do not hand-write.** The no-default set comes from `Settings.model_fields` at test
   time (`field.is_required()`), never from a copied list — a copied list is the same class of
   defect as `1.14`'s misnamed bindparam: it looks right on the page and drifts silently.
2. **The test is the deliverable, the file is its output.** `tests/unit/test_env_example_covers_required_settings.py`:
   - parse `apps/api/.env.example` for `KEY=` at line start (and `# KEY=` commented form → NOT a
     cover; a commented key cannot boot anything),
   - assert every required field appears uncommented,
   - name the missing fields in the failure message.
3. **Mutation proof, per the repo's negative-test rule.** Delete one covered key from the example,
   observe red naming exactly that key, restore from `HEAD` unconditionally, observe green. Record
   the verbatim output, not the intention.
4. Regenerate `apps/api/.env.example` with the 5 missing keys, keeping existing comments and
   placeholder values. Do **not** invent placeholder values that look like real credentials.
5. Decide the root `.env.example`'s status explicitly rather than silently: either it is the
   monorepo aggregate and gets the same 5 keys, or it is stale and says so at the top.

## Files

- `apps/api/tests/unit/test_env_example_covers_required_settings.py` (new)
- `apps/api/.env.example` (regenerate)
- `.env.example` (decide + update)
- `.dev/BACKLOG.md` (`1.21` row deleted in the same commit, per the maintenance rule)

## Risks

- **A test that reads a file the developer may not have.** `.env.example` is tracked, so it is
  always present in a checkout — but the test must locate it relative to the repo root, not the CWD,
  or it becomes a skip on some invocations, and a skip is unobserved.
- **The 63/10 count is READ from the file by eye.** The test derives it from `model_fields`, so if
  the eye-count is wrong the test is still right — but the plan's prose figure may not be. Re-derive
  before quoting it anywhere.
- Placeholder values must not be plausible secrets: `1.21`'s sibling defect is a `.env.example` that
  boots with a fake key and fails confusingly at the first API call.

## Tests

- New: the coverage test above, plus a mutation proof of it.
- Gate to re-run: `apps/api` unit suite (the CLAUDE.md command, docling modules excluded).
