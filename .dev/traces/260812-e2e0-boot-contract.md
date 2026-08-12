# TRACE — E2E-0 · fix the boot contract (2026-08-12)

**Plan:** `.dev/plans/260812-e2e0-boot-contract.md`. **Row closed:** `1.21 · env-example-cannot-boot`.
**Commit:** `2482283`. **Branch:** `chore/local-postgres`.

## What changed

| File | What |
|---|---|
| `apps/api/tests/unit/test_env_example_covers_required_settings.py` | new — 4 tests, derives the required set from `Settings.model_fields` |
| `apps/api/.env.example` | regenerated — 10 required uncommented + the full 63-field optional surface with real defaults |
| `.env.example` (root) | regenerated — 10 required + common optionals + pointers to the per-app examples |
| `.dev/BACKLOG.md` | `1.21` struck through with the correction |
| `.dev/PRODUCTION-READINESS.md` | §3.2 closed with the correction; E2E-0 marked done in §4 |

## The finding: `1.21` was counting one of two files

The row said "`.env.example` omits 5 of the 10". **There are two tracked examples**, and they failed
differently:

```
root      .env.example   missing 5   ANTHROPIC_API_KEY VOYAGE_API_KEY PLATFORM_CREDENTIAL_KEY
                                     JWT_SECRET CLERK_WEBHOOK_SIGNING_SECRET
apps/api/ .env.example   missing 3   PLATFORM_CREDENTIAL_KEY JWT_SECRET CLERK_WEBHOOK_SIGNING_SECRET
                                     -- and TWO of those three were COMMENTED OUT, not absent
```

**A commented key is worse than an absent one.** `# JWT_SECRET=change-me-in-production` puts the name
in front of the reader while dotenv ignores the line, so the `ValidationError` naming a field that is
visibly "in" the example reads as an application bug rather than as a line nobody wrote. M2 in
`.dev/reference/260812-e2e0-mutation-proofs.md` is the proof that the test discriminates; a substring
scan would have passed over the real repo state.

**Which file loads is positional, not configured.** `_find_env_file()` walks up from
`app/core/config.py` and stops at the first `.env`. On a fresh clone that is the **root** file; once
`apps/api/.env` exists it wins permanently. So both examples had to be complete — the root one is not
decoration, it is what a new developer's first run actually reads.

## Decisions

- **Derive, never list.** The required set comes from `Settings.model_fields[...].is_required()` at
  test time. A hand-maintained list is `1.14`'s failure mode exactly: correct on the page, silently
  wrong the first time a field is added.
- **Commented ≠ covered**, and the failure message says which of the two a key is.
- **The absent-file case is an `AssertionError`, not a skip.** A skipped test is unobserved, which is
  the whole `1.13` lesson.
- **Placeholders are deliberately not credential-shaped** (`<generate-with-the-command-above>`), so a
  copied example fails loudly at first use rather than looking configured.
- **`apps/api/.env.example` carries the whole optional surface with real defaults**; the root file
  carries the 10 plus pointers. Two competing full copies would drift.

## Deviations from the plan

- The plan assumed one example file and a 5-key gap. Both were wrong; corrected above and in the row.
- The plan proposed "either the root file is the aggregate or it says it is stale". Neither: it is
  **load-bearing on a fresh clone**, which the plan had not established. It got the full required set.
- **The plan's step 3 said one mutation proof; four were run.** M2 (commented-out) and M4
  (guard-of-guard) were not in the plan and are the two that carry information.

## Environment note, recorded because it will recur

The owner's global settings denied `Read(.env.*)`, which also caught the committed, secret-free
`.env.example` files. The owner confirmed the rule was aimed at `.env` / `.env.local` with real
secrets. **The deny was narrowed to the conventional secret-bearing names** (`.env`, `.env.local`,
`.env.*.local`, `.env.dev|development|prod|production|staging|test`) rather than worked around with
`cat`. `.env.example`, `.env.sample`, `.env.template` and `apps/admin/.env.local.example` are now
readable; real env files are still denied.

## Gates

- New module: `4 passed in 0.45s`. Four mutation proofs, red then green, restore from `HEAD`, tree
  clean — `.dev/reference/260812-e2e0-mutation-proofs.md`.
- Full backend unit gate: see below.

## What this does NOT establish

**Nothing here has booted the app.** The test asserts key *names* are present and uncommented. No API
process, worker or alembic command has been started from an example-derived environment, and the four
generation commands in the examples were not run. E2E-1 is the first step that would show any of
that, and it is the next move.
