# BACKLOG — the single ordered list

**Every open item, with its source.** Written 2026-08-06 because outstanding work had accumulated
across five files and one temp-directory journal, and "what is next?" could only be answered by
reading all of them. If an item is not here, it is not tracked.

**Maintenance rule:** a phase that closes an item deletes its row here in the same commit that lands
the fix. A phase that discovers work adds a row. `.dev/traces/` records what happened; this file
records what has not.

Legend — **[owner]** needs a human, **[blocked]** has an external precondition, **[code]** is ordinary work.

---

## 0. Blocked on you, and nothing above them is trustworthy

| # | Item | Source |
|---|---|---|
| 0.1 | **[blocked, then owner]** Score the 10 rows in `apps/api/tests/evals/calibration/human_scores.csv`. Until then every LLM judge in the system is uncalibrated — Gatekeeper, Auditor, Strategist, `classify_severity`, and the **Actor gate that runs before money moves**. The harness exists and gates at Spearman ≥ 0.75; no agent may fill that column. **Corrected 2026-08-07: this is not yet owner work — it is behind `0.2`.** `--check` exits 3 and names one blocker: `responses/` has never been captured (0 on disk, 20 scenarios). `capture_responses.py` needs `AGENT_E2E_ENABLED=1`, a live API, a provisioned *and ingested* agent, and its plaintext key. There is nothing to score yet, by a human or anyone else. | audit D7 |
| 0.2 | **[blocked]** Install a local PostgreSQL server. One precondition unblocks `VER-01`, `AUD-03`, `CAP-03`, the 6 blocked `23-UAT.md` checkpoints, the 4 `human_needed` items in `23-VERIFICATION.md`, and the 3 migration roundtrips below. Nothing listens on 5432-5435; `CONTROL_DB_URL` is live Neon production and is never a substitute. | HANDOFF |
| 0.3 | **[owner]** Actions minutes / spending limit at `github.com/settings/billing`. Two runs cancelled at 15m03s and 15m02s with every job killed — Lint included, which takes 11s. Until this lifts CI reports nothing and the gate is unreadable. | HANDOFF |

## 1. CI — finish what the cap interrupted

> **PAUSED by the owner, 2026-08-07.** The wall-clock cap is the binding constraint and it is a
> billing question, not a code one. `1.1` and `1.2` cannot execute until `0.3` lifts — they need a
> runner. `1.3` (the flake) and `1.4` (frontend gates absent from `ci.yml`) are local work and stay
> available. Do not spend runner minutes probing the cap.

| # | Item | Source |
|---|---|---|
| 1.1 | Confirm Unit + Integration actually pass on a runner. Causes were found and fixed (no Redis service; `tenants(api_key)` → `api_key_hash` per migration `0006`) but have **never executed remotely**. | HANDOFF |
| 1.2 | **`--cov-fail-under=80` has never run in CI history.** Local measurement is **80.86%** — a 0.86-point margin, and it *fell* from 81.17% because clearing F401 deleted covered import lines. Report the real number; do not lower the threshold. | ci-green log |
| 1.3 | **The 9% flake.** `test_services.py::TestWaitForNeonReady::test_wait_for_neon_ready_retries_then_succeeds` failed 1 in 11 identical runs. Diagnosis is written up: it patches the shared `time` module attribute while five Langfuse daemon threads run. Next step is a `--tb=long` loop to confirm which assertion fails; if it is `assert_called_once_with(1)`, the fix is `assert_any_call(1)`. | ci-green log |
| 1.4 | Frontend gates (`tsc`, `check:no-dusk-tokens`, `check:ops-room-wiring`, playwright) are **not in `ci.yml` at all**. | ci-green non-goals |
| 1.5 | `nightly.yml` E2E also failing, pre-existing, never diagnosed. | ci-green non-goals |

## 2. D1 — the measurement still measures nothing

**This is the headline.** Everything the eval-foundation branch built is scaffolding around a metric
that cannot move.

> **Status 2026-08-07: P1 + P1b.** `feat/d1-agent-invocation` carries the seam (`ec5f445`), its
> hardened guard (`d15be3a`) and P1b — recorded mode plus the canary write reorder, which closed the
> two rows that blocked P2. **P2 and P3 have still not executed**, so `2.1`–`2.4` remain open: the
> eval still sets `agent_response = reference_answer`.
>
> **No tier-2 judge has read this branch.** `d15be3a`'s message and the former rows `2.5`/`2.6`
> originally credited "tier-2"; they were **tier-1** findings from the P1 adversarial reviewer. The
> distinction is load-bearing — tier-2 is a Fable judge reading a bounded artifact and asking whether
> the claims match the evidence, and that question has not been asked about P1 or P1b.

Numbering note: `2.5` and `2.6` were recorded mode and the canary write order. Both were closed by
P1b and their rows deleted per the maintenance rule. The numbers are **not reused** — `agent.py`,
`transactional/tools.py` and the plan all cite "BACKLOG 2.5"/"2.6" for those decisions, and a reader
following one of those comments must not land on an unrelated row. New work discovered by P1b is
`2.7`/`2.8`.

| # | Item | Source |
|---|---|---|
| 2.1 | `eval.py` still sets `agent_response = reference_answer`. The agent is never invoked; the label is the prediction. Faithfulness and AnswerRelevancy approach 1.0 by construction. | audit D1 |
| 2.2 | The deploy gate fail-closes on an **absent** eval signal while shipping on a **present** one that measures nothing. No gate test reads `config.agent_invoked`. | tier-2 #1 |
| 2.3 | The config tuple now stamps `prompt_version_id` / `model_id` onto that tautology, which makes it *look* credible. | tier-2 #1 |
| 2.4 | Mined scenarios are inert by construction — written with `reference_answer=''`, selected by `WHERE reference_answer != ''`. EVL-03 produces write-only data. | audit D6 |
| 2.7 | **P2 must decide what `conversation_id` it hands the seam, and the escalation path is the tell.** Recorded mode suppresses the escalation *mail*, which is what the owner settled — it does not suppress `_mark_conversation_escalated`, which UPDATEs the tenant `conversations` row. Approach (b) exists so the eval writes no `conversations` rows, so that UPDATE will match zero rows, and `escalate_to_human_tool` returns `{"already_escalated": True}` **without calling notify_fn at all**. An eval scenario about escalation would then read "already escalated" where production reads "I've flagged this conversation", and the recorded notification would never fire. Not a bug in P1b; a decision P2 owns and must make on purpose. | D1/P1b |
| 2.8 | **Recorded mode does not bound the eval's Actor-gate spend.** Steps 1-5 of the transactional dispatcher run live by design — the envelope, IDV gate, rate ceiling and Actor seam are what the eval measures. The Actor gate is a synchronous Haiku call per mutating attempt, so a scenario set that provokes many attempts bills per attempt on top of the per-turn SDK call. Belongs with the plan's existing "cost and latency, unbounded by default" risk and its per-run ceiling. | D1/P1b |

## 3. Verification debt from the eval branch

| # | Item | Source |
|---|---|---|
| 3.1 | **Pre-P4 red-team runs remain shippable evidence.** A run stored while 5 of 7 attackers had no tools still reads `signal='measured'` with clean findings; unrecorded coverage only warns and substitutes the current build's 7/7. Fence or invalidate them. | tier-2 #7 |
| 3.2 | **`write_eval_results`' column names are pinned by no test.** Tier-1 rewrote the INSERT to the D3 names and the whole suite stayed green — on a branch whose D3 *was* a column-name mistake. | tier-2 #2 |
| 3.3 | The `human_scores.csv` write-ban misses `with open(path, 'w')`, and its guard was demonstrated only inside the complement of its own blind spot. | tier-2 #8 |
| 3.4 | `test_eval_e2e.py` exercises `run_eval_for_agent`, which has **zero production callers**. Passing e2e asserting the wrong surface. | tier-2 #3 |
| 3.5 | Migrations `0013`/`0014`/`0015` verified by source-text assertions only. **No `ALTER TABLE` on this branch has ever executed anywhere.** Run the roundtrips before the next tenant is provisioned. | tier-2 #4 |
| 3.6 | The decision eval (P3, 1865 lines) has **scored zero real audit rows** — no driver exists, every run reports `valid=0`. It is a scorer, not yet an eval. | tier-2 #10 |
| 3.7 | SDK attacker wiring is proven only against a fake harness that structurally cannot detect the wiring being removed. Nothing pins the three-way name agreement (`create_sdk_mcp_server` ↔ `mcp_servers` key ↔ `mcp__{name}__` prefix). | tier-2 #5 |
| 3.8 | Three deterministic red-team vectors still `except Exception: return []` — clean over an unobserved run — while coverage asserts all seven valid. Also `identity_bypass` vs `identity_verification_bypass` vocabulary split breaks the coverage↔findings join. | tier-2 #6 |
| 3.9 | 20 of the 72 guard demonstrations (P2, P3) are implementer self-reports; tier-1 reproduced none of them. Spot-reproduce 2–3, highest value first: the D3 column-name revert. | tier-2 #11 |
| 3.10 | `_run_orchestrator_loop` reports "was never awaited" — `run_orchestrator` is never executed anywhere, so every claim about the prompt's prose blocking conditions is untested. Four phases and tier-1 read past it. | tier-2 #16 · retro A.5 |
| 3.11 | Remaining tier-2 items not itemised here: **#12–#15, #17** and evidence mismatches **#1–#8**. Full text in `.dev/reference/tier2-judge-eval-foundation.md`. | — |

## 4. Test-suite integrity

| # | Item | Source |
|---|---|---|
| 4.1 | **5 `patch()` sites name symbols that do not exist** — 4× `HybridChunker` in `test_ingestion_chain.py` (580/720/865/955), 1× `get_adapter` in `test_actor_latency.py:221`. Pinned in `test_patch_targets_resolve.py::_KNOWN_BROKEN`; re-measure with that module's `__main__`. | ci-green log |
| 4.2 | `test_integration_e2e.py` has **zero T-16-01 credential-leak coverage** — the block built two strings, looped six patterns, and had `pass` as the body. Dead code removed; the real assertion still needs writing. | ci-green log |
| 4.3 | `test_parse_task.py`'s `mock_parse.assert_not_called()` is vacuously true — `parse.py` only ever calls `parse_document_from_bytes`. | ci-green log |
| 4.4 | The 10 docling-gated tests have **never run in repo history**; no job installs the `pipeline` extra. | ci-green log |
| 4.5 | `pyproject.toml:42` declares `PyJWT[cryptography]==2.12.1`; no such extra exists, so uv warns and it is silently ignored. | ci-green log |

## 5. Milestone v1.2 closure

| # | Item | Source |
|---|---|---|
| 5.1 | **OPS-15 server gap.** `POST /approve-deployment` gates on the frozen `run.recommendation`, never live `open_findings`. Console can no longer deploy over a critical finding; any script still can. Fails closed, so not a hole — but the milestone audit calls it a blocker. | v1.2 audit |
| 5.2 | `REQUIREMENTS.md`: `WIRE-01..05` has **zero rows**; the v1.2 rollup sentence is stale; the `OPS-01..06` collision is live (lines 274-279 hold them Phase 10 `Pending` while line 415 ticks them complete via Phase 21). | v1.2 audit |
| 5.3 | Nyquist `status: draft` on `20/21/23-VALIDATION.md`; Phase 20 has no `20-SECURITY.md`. | v1.2 audit |
| 5.4 | Console renders unknown as `0 critical · 0 high` with a **Pass chip** (`deploy/page.tsx:2428`). Family B closed in the gate, alive at the surface. | trace · tier-2 #5 mismatch |
| 5.5 | `eval/page.tsx` unguarded `res.scores.faithfulness` — 5 call sites, grep-confirmed. | Phase 23 |

## 6. The ladder beyond D1 — not yet planned anywhere

From the data-science framing. Each depends on everything above it.

| # | Item |
|---|---|
| 6.1 | **Golden-set refresh policy.** A fixed set gets overfit; the rotating exploratory set and a promotion/retirement rule are what stop that. |
| 6.2 | **Per-tenant baselines.** Absolute thresholds (0.70/0.85) are the enemy of autonomy — 0.85 means different things on a 40-page and a 4000-page corpus. Needs trailing median + variance over persisted runs, and a **cold-start policy** for the tenant with no history, which is every new signup. |
| 6.3 | **Regression detection over threshold checking** — "2σ below this tenant's own trailing baseline on the same items" survives across tenants; "below 0.85" does not. |
| 6.4 | **Label efficiency.** `validators.py:220` already emits judge `confidence` into `job_events` and it is discarded for ranking. Uncertainty sampling on the bench is worth 5–10× per owner label. |
| 6.5 | **Wire `message_feedback` into the dataset.** Shipped Phase 23, feeds a CSAT tile and nothing else — the only *direct customer label* in the system. |
| 6.6 | **Automated prompt evolution (SkillOpt).** Two constraints first: the optimizer may never modify a capability envelope (structurally, not by instruction), and the fitness function must include refusal correctness or it learns to refuse less. Pointing it at today's scores optimizes a tautology. |

---

## Suggested order

**Revised 2026-08-07.** The earlier order said `0.1` and `0.2` were cheap and could run in parallel
with anything. `0.1` is not parallel to `0.2` — it is **behind** it, because the responses a human
would score cannot be captured without a database. Corrected above.

`0.2` (a local PostgreSQL) is now the single highest-leverage owner action: it unblocks `0.1`, the
three migration roundtrips, `VER-01`/`AUD-03`/`CAP-03`, the 6 UAT checkpoints, the 4 `human_needed`
items — and the end-to-end proof of §2. **§1 is paused** on the billing cap, which is not a code
problem. So the working order is **§2 (D1)** — unblocked for implementation and unit proof, blocked
only for its end-to-end observation — then §3's verification debt, much of which dissolves once the
metric moves, then §5 to close the milestone. §6 is the product ambition and is gated on all of it.
