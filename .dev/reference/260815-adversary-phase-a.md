# The adversary pass on Phase A: five guards that could not fail

An adversarial review read the Phase A work (`b89c9d9..HEAD`) after its gates were green. It ran
eleven mutations. **Five proved a guard written that same week could not fail on the defect it
names**, and one found a credential leak introduced by the fix for `5.1`.

Every finding is fixed under `1.33` and re-proved by re-running the adversary's own mutation. This
note records the *shapes*, because they recur.

## The credential leak, first

`deployment.py`'s new failure handler logged `error=str(exc)`. `conn_str` is the plaintext output of
`fernet_decrypt`, whose own docstring says never to log it, and psycopg2 embeds the DSN in its
message. Measured, verbatim:

```
INPUT: <a malformed connection string>
  ProgrammingError: invalid dsn: missing "=" after "<THE WHOLE DECRYPTED STRING>" in connection info string

INPUT: <a well-formed tenant DSN>
  OperationalError: could not translate host name "<TENANT HOST>" to address
```

The malformed-DSN path echoes the decrypted string verbatim. The reachable path echoes the tenant's
hostname. This fired on the ordinary failure of that call, on a route any tenant-key holder reaches.

**The bitter part:** the same commit added `error_type=type(exc).__name__` to *fix* a blank-diagnosis
bug (`1.30`). Adding `str(exc)` alongside it is what created the leak. A fix for one logging defect
introduced another, in the same line.

`error_type` alone carries the whole diagnostic value here. That is what it logs now.

## The five vacuous guards, by shape

### 1. Prose satisfied a test about code

`test_sdk_tools_are_registered.py` was **green on `1.32`**, the bug it was written for. Stripping
every wiring call while leaving comments intact produced `1 failed, 7 passed`. Three checks were
satisfied by the implementer's own writing:

- the "is this schema ever referenced again?" count was satisfied by a docstring sentence *stating*
  the schema had only ever been referenced once. **That sentence was the second reference.**
- the `create_sdk_mcp_server` marker survived on the `import` line and in that docstring.
- the `mcp__` allowlist check was satisfied by a comment explaining the `mcp__{server}__{tool}`
  rewrite.

**Rule: a substring scan cannot distinguish code from a sentence about code.** These modules carry
long explanatory comments, which makes every text-based pin weaker the more it is documented. The
rewrite walks `ast` and reads call nodes and keyword arguments only. The same mutation now fails 3 tests.

### 2. A single digit found itself in unrelated prose

The span-cap pin was `str(AUDITOR_MAX_CITATION_SPANS) in system_prompt`. Setting the cap to `2` and
deleting the sentence that states it left the test green: the `"2"` in `"under 25 words"` satisfied
it. It was non-vacuous only by the accident that the shipped value is `8`.

**Rule: pin the phrase, not the number.** `f"at most {N} citation spans"`.

### 3. The headline behaviour had no runnable coverage

Disabling guard 2b's refusal outright left `test_deployment_routes.py` at **21/21 green**. `5.1`'s
entire point had one test, in `tests/integration/`, which skips without `INTEGRATION_TESTS_ENABLED`
and a local Postgres. **A skipped test is unobserved, never a pass**, so the behaviour the fix
exists to deliver had no runnable proof.

What *was* covered was the fail-closed-on-unreadable-connection branch: the smaller half.

**Rule: when a fix's headline behaviour is only reachable in an environment-gated suite, it is
unproven in every environment that does not have it.**

### 4. Two identifiers of the same type, in the wrong order

Swapping `upload_key(agent_id, doc_id)` to `upload_key(doc_id, agent_id)` was invisible to 18 tests.
Both are UUID strings, so there is no type error and no exception; every fetch simply 404s. It
reproduces `1.26`'s exact symptom in a new spelling.

The checks asserted only that `storage_service.get_bytes` and `storage_service.upload_key` *appear*
in the file, and the task-level stub was `lambda key: b"..."`, it never looked at `key`.

**Rule: a stub that ignores its argument asserts nothing about the argument.** The pin now calls the
real `upload_key` from both the writer's and the reader's spelling and requires the same string, plus
an AST check of the positional order.

### 5. A constant declared but never enforced

`AUDITOR_MAX_CITATION_SPANS` is a sentence in a prompt. Nothing truncates or validates the returned
list, so a model returning 30 spans is accepted and re-breaches the token ceiling exactly as
described. The test named "the verdict cannot grow without bound" is a range check on the constant
(`1 <= N <= 20`), not a bound on anything.

This one is **recorded, not fixed**: enforcing it means truncating a judge's output, which changes
what a verdict means. It belongs with `5.16`.

## What this says about the week's own reference note

`.dev/reference/260815-the-never-executed-class.md` argues that Phase A's defects survived because
the tests could not see them, since they mocked the thing that was broken. The adversary then found
five instances of that same class **inside the fixes for it**, written by the person who wrote the
note.

The lesson is not "try harder". It is that this class is invisible from the inside, and the only
reliable detector is someone else mutating your product code and watching your test go green.

**Green gates are the entry condition, not the verdict.**

## Findings recorded rather than fixed

- **`5.17`**: guard 2b re-reads the *critical* rule but not the *high* rule, while
  `DEP_BLOCK_ON_HIGH_RED_TEAM` defaults `True`. Approve-time and checklist-time apply different
  predicates, which is the drift the fix claims to prevent.
- **`1.34`**: `test_embed_chunks_routes_to_bedrock` makes a real network call when
  `EMBEDDING_PROVIDER=voyage` is in the ambient environment.
- The production refusal on `S3_ENDPOINT_URL` keys on the exact string `"production"`, with no
  validator on `ENVIRONMENT`. `staging`, `PRODUCTION` and `prod` all bypass it, and staging holds
  real customer documents.
- The refusal is also first-call-only: it sits inside `if _s3 is None`, so a memoised client survives
  an environment change. Not exploitable while settings are frozen at import, and the test suite is
  structurally unable to observe it.
