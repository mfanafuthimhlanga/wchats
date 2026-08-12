# E2E-0 mutation proofs — `test_env_example_covers_required_settings.py`

**Date:** 2026-08-12. **Branch:** `chore/local-postgres`. **Commit under test:** `2482283`.
**Rule being satisfied:** CLAUDE.md — *"A negative test never observed to fail is indistinguishable
from a tautology. Mutate the guard, observe red, restore from `HEAD` unconditionally, observe green.
Record the observed output, not the intention."*

All four were run against the committed tree, so every restore is `git checkout HEAD -- <file>`
rather than a hand-undo. Tree verified clean (`git status --short` empty) after M4.

Baseline, before every mutation and after every restore:

```
....                                                                     [100%]
4 passed in 0.45s
```

---

## M1 — a required key is deleted from `apps/api/.env.example`

The ordinary drift case: someone adds a `Settings` field, or trims the example.

**Mutation:** remove the `PLATFORM_CREDENTIAL_KEY=` line (asserted to be exactly one line).

**RED:**

```
E         Missing (no default in Settings, absent from the example - a fresh
E           - PLATFORM_CREDENTIAL_KEY
E       assert not {'PLATFORM_CREDENTIAL_KEY'}
1 failed, 3 passed in 1.03s
```

**Restored → `4 passed in 0.45s`.**

---

## M2 — a required key is COMMENTED OUT rather than deleted

**This is the one that matters**, because it is the defect that was actually there: two of the three
keys missing from `apps/api/.env.example` were present as `# JWT_SECRET=` /
`# CLERK_WEBHOOK_SIGNING_SECRET=`. A test that scanned for the substring `JWT_SECRET` would have
passed over the real repo state and been worthless.

**Mutation:** `\nJWT_SECRET=` → `\n# JWT_SECRET=`. The name is still visibly in the file.

**RED:**

```
E         Missing (no default in Settings, absent from the example - a fresh
E           - JWT_SECRET
E         Present but COMMENTED OUT, which is not coverage - dotenv will not
E         load these and the app still fails at import:
E           - JWT_SECRET
E       assert not {'JWT_SECRET'}
1 failed, 3 passed in 1.08s
```

Note the second block fired: the failure message distinguishes *absent* from *commented*, so the
reader is not sent hunting for a key that is on screen in front of them.

**Restored → `4 passed in 0.42s`.**

---

## M3 — the repo-root example's assertion is separate and live

The two examples are checked by two different tests. A single test covering only `apps/api/` would
leave the file a fresh clone actually reads (see `_find_env_file()`) unguarded.

**Mutation:** remove `ANTHROPIC_API_KEY=` from the **repo-root** `.env.example`; leave
`apps/api/.env.example` untouched.

**RED — and it names the root path, not the api one:**

```
E       AssertionError: C:\Users\Bantu\mzansi-agentive\wchats\.env.example does not cover every required setting.
E           - ANTHROPIC_API_KEY
E       assert not {'ANTHROPIC_API_KEY'}
1 failed, 3 passed in 0.94s
```

**Restored → `4 passed in 0.49s`.**

---

## M4 — the guard-of-guard, and it earned its place

The coverage tests assert `required - live == set()`. Their failure mode is **silence**: if
`required` is ever empty, both pass while checking nothing. That is not hypothetical — it is what
happens if `is_required()` changes meaning across a pydantic release, or if every field acquires a
default.

**Mutation:** `required_settings_fields()` body replaced with `return set()`.

**RED:**

```
E       AssertionError: Settings now declares no required fields at all. Either that is a real and
        deliberate change - in which case delete this module - or `is_required()` no longer means
        what this test assumes.
FAILED tests/unit/test_env_example_covers_required_settings.py::test_settings_still_has_required_fields
1 failed, 3 passed in 1.05s
```

**Read the `3 passed` in that line.** Both coverage tests passed — vacuously, over an empty required
set, against examples that could have been blank. Without this guard the module would have reported
`4 passed` on a mutation that removed its entire subject. That is the observation, not the intention.

**Restored → `4 passed in 0.47s`.** `git status --short` empty.

---

## What is NOT proven here

- **That a filled-in `.env.example` boots the app.** Every proof above is about which key *names*
  appear in a file. Nothing has started the API, the worker or an alembic command from an
  example-derived environment. That is E2E-1, and until it runs, "the boot contract is fixed" means
  "the contract is now written down and pinned", not "a fresh environment has been observed to
  start".
- **That the placeholder values are usable.** They are deliberately not credential-shaped
  (`<generate-with-the-command-above>`), so an environment copied from the example fails at the
  first real API call rather than at import. That is the intended trade — a loud failure with a name
  attached — but it has not been exercised.
- **That the four generation commands in the examples produce values the app accepts.** They are
  transcribed from the existing root example and from `NEON_ENCRYPTION_KEY`'s documented convention;
  none was run.
