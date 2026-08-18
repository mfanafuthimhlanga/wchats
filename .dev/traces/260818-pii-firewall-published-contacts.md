# Trace: the PII firewall stops deleting the tenant's own published address (7.29)

Plan: `.dev/plans/260818-pii-firewall-published-contacts.md`.

## What changed

| File | Change |
|---|---|
| `app/utils/pii_firewall.py` | `detect_pii` and `scan_response` take a keyword-only `published_context`. Email only, exact membership against addresses extracted from that context |
| `app/worker/tasks/runtime/agent.py` | `_published_context` builds the list from this turn's retrieve captures; the call site passes it and logs `pii_firewall.published_contact_allowed` when an address passes only because it was published |
| `tests/unit/test_pii_firewall.py` | the exemption, and the signature property restated: one POSITIONAL parameter, the new one keyword-only |
| `tests/unit/test_pii_firewall_published_context.py` | new. Cases driven from a real `ToolResultBlock` through the production capture, except the two that pin unreachable guards and say so |
| `tests/evals/capture_responses.py` | reads `tool_name`, the key the emitter sends |
| `tests/evals/calibration/compute_correlation.py` | `--check` names deflected responses and excludes them from the scorable count |

## Two corrections to what was recorded

**Four responses were deflected, not three:** S-002 business hours, S-003 product availability,
S-005 contact info, S-010 clarify-then-retrieve. All four byte-identical to `PII_DEFLECTION`.

**S-010 is an edge scenario, not a golden one.** The finding was filed as a golden-path defect. It
was wider than that: any question whose best chunk is the contact section, whatever its category.

## Mutation proofs

Five guards, `git checkout --` between each, observed output recorded rather than intended.

| Mutation | First run | After |
|---|---|---|
| M1 let a card consult the allowlist | **RED**, 2 failed | proven first time |
| M2 drop the errored-retrieve skip | **RED**, 1 failed | proven first time |
| M3 drop the unparsed skip | **GREEN** | tautology. Pinned with a hand-built fixture, then **RED** |
| M4 drop the tool_name check | **GREEN** twice | tautology, then a harness bug. Pinned and retargeted, then **RED** |
| M5 substring instead of extraction | **RED**, 1 failed | proven first time |

**M3 and M4 were tautologies and the mutation is the only reason anyone knows.** Both tests passed
whether or not the guard they named existed, because `_attach_retrieve_capture` writes chunks only
onto a `retrieve` entry and writes `[]` for a payload it could not decode. No shape the production
capture emits can reach either guard. They are kept as defence against the capture changing, and
they are now pinned by fixtures built directly in the hostile shape, labelled in the test file as
shapes production does not emit.

**M4's second GREEN was the harness, not the product.** The mutation anchor
`if tc.get("tool_name") != "retrieve" or "result" not in tc:` appears twice in `agent.py`, and
`_judge_retrieved_context` at line 522 sorts first, so `replace(..., 1)` mutated the judge's builder
and left the firewall's untouched. A mutation proof that mutates the wrong function reports the
guard unproven and looks identical to a tautology. Anchor on something unique to the function under
proof, and assert the anchor count is 1.

## A test that had been red for a day

`test_the_shipped_tree_is_honest_about_being_unready` asserted `readiness()["blocking"]` was
non-empty. That held only while `responses/` had never been captured. The E2E-6 capture on
2026-08-17 filled it, every machine-fixable input became present, `blocking` correctly emptied, and
the assertion stayed pinned to a state the project had left.

**It went unnoticed because the last full battery ran 2026-08-16 and `fast` is collect-only.** A
day of green `fast` runs said nothing about it. Rewritten to assert the report names whatever is
missing, and to assert the printed text says so, rather than pinning one spelling of missing.

Confirmed pre-existing by stashing the change and re-running: identical failure at the pre-change
code.

## Not closed

**The live re-capture.** S-002, S-003, S-005 and S-010 still hold the deflection on disk, so the
corpus still understates what the agent can do. The capture skips scenarios whose response file
already exists, so deleting those four files and running without `--overwrite` re-captures exactly
them, at a cost of four agent turns. It needs `AGENT_ID` and the **plaintext** tenant `API_KEY`
(only the hash is stored), and it needs `7.32` lifted first.

**`7.32`, found while trying to run that proof against the live corpus.** The `.env` control DB
credential is rejected by Neon: `password authentication failed for user 'neondb_owner'`. Ruled out
in order, so the finding is the credential and not the plumbing:

| Suspected | Ruled out by |
|---|---|
| dotenv mangling a special character | raw line and parsed value byte-identical, password 16 characters both ways |
| URI percent-encoding | `unquote(password) == password`, nothing to decode |
| psycopg2's URI parser | host, user, password and dbname passed as separate parameters fail identically |
| endpoint gone or misrouted | the server answered with an auth error, so TLS and routing are fine |

`NEON_API_KEY` lists one project, the tenant one, so the control project sits outside that key's
organisation and cannot be inspected from here.

**When it broke is RECORD, not OBSERVED.** `7.29`'s row says its cause was proven by a query against
the live tenant DB earlier on 2026-08-18, and reaching that DB means decrypting its connection
string from the control DB. If that is what happened, the credential stopped working within the same
day. Nobody watched it break, so treat the window as read from a note rather than measured.

**What this cost, and what it did not.** The live-corpus proof was going to confirm that the real
chunk text yields the plain `local@domain.tld` shape the exemption matches on, rather than something
obfuscated that the regex would not extract. That confirmation is still owed. It does not affect the
unit evidence, which drives real SDK blocks through the production capture.

**A follow-up turn that does not retrieve.** The exemption is built from THIS turn's retrieval, so
"what is your support email?" is answered and a bare "sorry, say that again?" is deflected if the
second turn answers from conversation memory instead of retrieving. The agent is instructed to
retrieve before any factual answer, which makes it unlikely rather than impossible. Closing it means
carrying the turn's published addresses on the conversation and unioning across turns, which is a
design change rather than a tightening, so it is named here rather than guessed at.

## Accepted, and now filed rather than implicit

`7.30` — an address that reaches the corpus inside an ingested third-party document is repeatable.
The control belongs at ingest: an egress regex over generated text misses the paraphrase anyway. The
identical exposure already exists for phone numbers by OD-4's own decision, so this widens a known
accepted risk rather than opening a class.

`7.31` — the corpus checks that accepted E2E-6 as clean looked for empties, length and provider-error
text. A deflection is a well-formed sentence of ordinary length. `--check` now names them; nothing
yet catches the other well-formed-and-worthless shapes, a refusal or an escalation where an answer
was expected.

## What the full battery found, none of it from this change

The battery had not run since 2026-08-16. It ran here at **826.7s, 2 failed, 2361 passed, 13
skipped**, and both failures predate this session: `test_patch_targets_resolve.py` reads
`eval_service.py`, `test_eval_e2e.py` and its own pin list, and this session's commits touch none of
the three.

`tests/integration/test_eval_e2e.py` patches `app.services.eval_service.evaluate` at two sites.
`evaluate` is the Ragas 0.3 entry point; `7.18` moved scoring to `ascore` and the name left with it.
The integration suite has no PostgreSQL, so the sites never execute and never raised. Both are now
pinned in `_KNOWN_BROKEN` with their count and reason, which makes the battery green and keeps the
pin able to catch the next rename. The sites are still wrong: `7.33`.

**The battery being red for two days is the finding, not the pin.** The gate that ran at the end of
every session was whole-suite `--collect-only`, which imports every module and asserts nothing.

## The Stop hook's gate was over its own ceiling

`.dev/gates.json` declared `fast` as whole-suite `--collect-only`, measured at 78.8s on 2026-08-15
with a comment in that file warning that a heavy dependency would push it past the harness's 170s
clamp, and that being killed reports nothing at all. It did, and it was: 142.5s standalone on
2026-08-18, killed at 170s from the hook.

Raising the timeout is not available (the clamp is the harness's). So the split moved to where the
cost actually is, which is **import cost, not test count**:

| Mode | Steps | Measured |
|---|---|---|
| `static` | ruff, import contracts, lizard | **8.4s**, and nothing here imports app code |
| `fast` | static + whole-suite collection | 142.5s and growing with the dependency tree |
| `full` | fast + the unit suite | 826.7s |

The hook now runs `static`. Its headroom cannot erode by adding a dependency, because nothing in it
imports one.

## Durable note

`.dev/reference/260818-green-for-the-wrong-reason.md` — four checks that were green while the thing
they guard was broken, each with the cheap test that separates them. Two of the four are this
session's own tests.

## The battery, green, on the final tree

`gates.py full` at `a36e21f`: **693.4s total, suite 547.4s, 2364 passed, 13 skipped, exit 0.** The
count moves from the red run's 2361 passed by the two failures that are now pinned and the one test
added for the deflection dedupe.

Widget and admin were not re-run: this change is backend only and touches no file either reads.

## The corpus is worse than the deflections, and that changes the advice

Asked whether the sheet could now be scored, I said yes for eight rows. That was wrong, and reading
the rubric is what showed it.

`grounding_fidelity` requires a claim to be traceable to a retrieved chunk **provided in the
tool_calls log**. Every captured entry carries `"result": {}`, because the capture drives the widget
SSE and SSE does not carry tool results. **PASS is unreachable and every grounding verdict must FAIL
whatever the answer says.** The unnamed tool calls forecloses the rubric's other FAIL condition the
same way, and `run_evals.py` counts escalate and clarify calls by that same name, so those come back
zero.

So the corpus is not eight-scorable-rows-with-a-caveat. It has to be re-captured in full, and
scoring it first would produce a real number about nothing.

`validate_corpus.py` now says all of this at capture time: **FATAL 15, BLIND 14, exit 1** against the
current corpus. `capture_responses.py` runs it before exiting and exits with its code, so a
contaminated run is known while the services are still up rather than a day later.

**The property that took two attempts.** The blind check first keyed on `tool_name == "retrieve"`,
which no unnamed call matches, so the unnamed-tool defect masked the missing-chunk defect and fixing
the first would have revealed the second on the next capture. A validator that reveals one finding
per run schedules the re-runs it exists to prevent. `_looks_like_retrieve` now also matches an
unnamed call carrying `input.query`, and the mutation proof is recorded: reverting it to the
name-only form turns `test_both_defect_classes_are_reported_in_one_run` red, 1 failed of 12,
restored green.

`CAPTURE_TIMEOUT` also defaulted to 30s against a measured 101s turn. Now 300.

**What is still open is a decision, not a patch (`7.34`).** The untruncated chunks exist only on the
worker's in-process `tool_calls_log`; `retrieved_context_json` is a Celery task argument and a
char-count log line, and nothing durable holds it. Widening the SSE payload is not available because
that stream is the customer's. HANDOFF carries the three options and the recommendation.

## 7.34: the corpus now carries its own evidence

Owner decision 2026-08-18, from three options that differed in what the corpus would MEAN rather
than in cost: keep it on the served path, so it measures what a customer receives with the PII
firewall applied, rather than the eval path that no customer uses.

| Half | State |
|---|---|
| migration `0017`, `tool_calls.retrieved_chunks` | written, **never run**. No PostgreSQL here |
| `_persist_messages` writes the judge rendering | landed, 8 tests, mutation-proven |
| `capture_responses.py` reads the row back | landed, 6 tests |

**Both halves apply the same rule, and a test asserts they agree.** NULL means the call retrieves
nothing or its capture could not be decoded; `[]` means a retrieve ran and matched nothing.
`validate_corpus` keys BLIND on falsiness, so collapsing them would let a corpus miss read as absent
evidence, which is `5.16` one level down.

**Mutation proof.** Swapping `RETRIEVE_JUDGE_CHUNKS_KEY` for `RETRIEVE_CHUNKS_KEY` in the write
turns 4 of 8 red, including the provenance test. `5.18` is the reason the judge rendering is the one
stored: a claim naming a document cannot be supported by a context that contains neither.

**The unverified half is named rather than implied.** `0017` exists in a file and in no database.
The tests cover the two functions that decide what a row stores and what the corpus records; the
INSERT is unobserved and stays that way until a tenant DB is migrated, which is behind `7.32`. A
capture against an unmigrated tenant fails on the INSERT rather than silently recording nothing,
which is the right failure but is still a failure someone has to expect.
