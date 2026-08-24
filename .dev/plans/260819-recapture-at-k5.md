# The k=5 re-capture

**Goal:** twenty scenarios, five runs each, on a corpus nothing has contaminated. Run 0 of each is
the row the owner labels; runs 1-4 are what `reliable@k` reads. 100 live agent turns.

**Owner decisions taken 2026-08-19:** do the re-capture; label **all 45** (scenario, dimension) rows,
because "however much is best for production" and 45 is every row the 20 scenarios can produce.

## Why `--overwrite` and not top-up

`8.1` made the capture TOP UP a scenario short of k rather than skip it, which is right for a corpus
that was interrupted. It is wrong here. All twenty files on disk are the contaminated k=1 set
(`7.29` PII deflections in four of them, `tool_name: ""`, no `retrieved_chunks`), and topping up to
five would leave the contaminated response as **run 0** - the exact row the owner labels and the
only row the judge is calibrated against.

Delete all twenty, capture five fresh. Re-capture all twenty rather than the four deflected ones:
the missing chunks and unnamed tool calls affect every row.

## Environment, measured 2026-08-19 with `scripts/probe_environment.py`

| | |
|---|---|
| local `wchats_control` | UP, alembic 0019, 19 tables |
| local Redis | `+PONG` |
| Neon API key | sees `mute-dream-53534177`; connection URI retrievable |
| live control DB in `.env` | REFUSED (`7.32`) |
| `ANTHROPIC_API_KEY` | **not exported** |
| `ANTHROPIC_BASE_URL` | `api.deepseek.com` in `.env`, **not exported** |

**The provider is DeepSeek.** A worker started without `ANTHROPIC_BASE_URL` exported defaults to
`api.anthropic.com` on a DeepSeek key. That is `1.28`, it has cost four debugging cycles, and at
least one task reports success anyway. Export both, and prove it from inside the worker process
rather than from the shell.

## Steps

1. **Retrieve the live tenant connection URI** via the Neon API (`GET /projects/{id}/connection_uri`).
2. **Find the agent that owns the corpus.** Chunks are scoped by `agent_id` in the tenant DB, so the
   `AGENT_ID` the capture uses must be the one holding the 16 chunks. Query the tenant DB for it
   rather than trusting the id in HANDOFF.
3. **Seed the local control DB** with a tenant row and an agent row whose `neon_connection_string` is
   the Fernet-encrypted tenant URI. The plaintext `API_KEY` stops being a blocker here because
   whoever seeds the row chooses it.
4. **Drain the Redis `runtime` queue.** A stale task for an unrelated agent starts the moment a
   worker does; five were purged mid-M1 from killed runs.
5. **Pre-warm the tenant Neon endpoint** with a bad-credential probe until it fails FAST (about 2s,
   `password authentication failed` = warm). TCP connect lies: the proxy accepts while the compute
   wakes.
6. **Start the services** with the overlay: `uvicorn`, and `celery -Q runtime -P solo`.
7. **Delete the twenty response files**, then capture: `--runs 5 --overwrite`.
8. **Validate**: the capture exits with the validator's code, so a contaminated run says so while the
   services are still up.

## What to check in the run, and each is an absence

- **No `rerank.voyage_failed_falling_back`.** The Voyage credit moved the account to Tier 1; the
  fallback degrades retrieval quality with only a warning, and its absence is the only proof.
- **No PII deflection in any run**, and `validate_corpus.py` checks every run, not just run 0.
- **`retrieved_chunks` populated**, which needs `CONTROL_DB_SYNC_URL` exported. Unset means the run
  warns, records no chunks, and the validator reports BLIND.
- **`tool_name` non-empty** on every tool call.

## Risks

- **Cost is real and it is 100 turns.** A failure at scenario 18 wastes 17 scenarios of spend, so the
  capture tops up on re-run rather than starting over: if it dies, re-run WITHOUT `--overwrite`.
- **A 15-minute run outlives a 900s JWT.** `7.23` closed this by minting per run; if `401`s appear in
  a block, that regressed.
- **A crash mid-checklist blocks every later checklist for 60 minutes** (`1.31`). Reclaim with
  `UPDATE checklist_runs SET status='failed' WHERE status='running'`.
- **One adversarial turn measured 101 seconds.** `CAPTURE_TIMEOUT` defaults to 300 now. Do not lower it.
- The live project `mute-dream-53534177` is deliberately up. **Delete by id only, never by name pattern.**

## Then

The sheet grows from 10 rows to 45, spanning all four scenario categories deliberately: the 45 skew
hard (`escalation_accuracy` 20, `session_continuity` 2) and kappa needs both labels present. Then the
owner labels, re-labels blind via `--emit-second-pass`, and the gate finally runs against a real
judge and a real human for the first time.
