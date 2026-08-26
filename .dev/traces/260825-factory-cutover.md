# Every direct-API site through the factory; Judges on Luna (#47)

Ticket #47, decisions #34 and #22 on map #4. Four commits on `feat/factory-cutover`,
stacked on `feat/model-call-ledger` (#46, PR #75): the OpenAI provider (`baefcc1`),
the ten-site migration (`07e4811`), judge identity and the run proof (`611cd43`), the
review round (`3aabc3d`).

## What holds now

- The factory speaks both providers; the hook parses both body shapes, with OpenAI's
  fresh input computed as prompt_tokens minus cached, clamped at zero, field names
  read from the installed SDK's own types.
- The purpose routing table is immutable data, fourteen rows to `gpt-5.6-luna`; the
  five Judge purposes carry `reasoning_effort: none` on the instructor seam, asserted
  on the wire sync and async, and the raw client path refuses judge purposes so no
  judge call can lose its effort silently.
- A forbidden import contract makes `model_client` the one legal importer of
  `anthropic`, `openai` and `instructor`; both import shapes observed BROKEN then
  KEPT, written into the pyproject comment. The SDK modules are private aliases, so
  the re-export bypass is gone.
- Luna prices flat at the verified $0.20 and $1.20 per million; cache reads price at
  the full input rate, a deliberate overcount named in a comment until a cache tariff
  is verified.
- All five judges stamp a frozen `JudgeIdentity(model, reasoning_effort,
  prompt_version)` where calibration will read it: four in `eval_results.detail`,
  the fifth in `retrieval_metrics.judge_identity` (tenant 0020, round-tripped on the
  probe). The model is the configured one, deliberately; served drift is the ledger's
  and the shadow audit's job. `JUDGE_PROMPT_VERSION` reads the ragas distribution
  version, because that package authors every judge prompt and a typed literal goes
  stale at the next resolve.
- The eval task's judge path is proven end to end: the pinned arithmetic of judge
  calls lands the same count of ledger rows with the Luna served model, per-row
  purpose-to-dimension pairing, every insert asserted, agent and tenant ids real.

## The line the ticket did not cross

The nine messages-shaped sites (gatekeeper, auditor, strategist, actor gate, scenario
generation, metadata enrichment, red-team severity and probe, query expansion) now
construct through the factory, carry purposes and land ledger rows, but still speak
`messages.create` on their previous endpoint; `openai.OpenAI` has no `.messages`.
Their Luna rewrite is issue #76 and is named in `make_client`'s docstring. The
ticket's letter required the Judges on Luna, which holds.

## Sharp edges recorded, no machinery

- A raw `httpx` call to a provider API imports nothing forbidden; the contract fences
  SDK imports, review owns the network.
- ragas could construct its own client through `llm_factory`; no caller does today.
- instructor defaults fill absent kwargs only, so a call site passing
  `reasoning_effort` would win; the seam docstring states the rule and the call
  sites are ours.
- These sites read the api key from Settings now, which retires the exported-key
  footgun (`1.28`) for the direct-API half; the SDK Agent path keeps it until #48.
- Per-call httpx clients; pooling per tenant and purpose is a follow-up noted on #76.

## Evidence, observed

- Red-first throughout, including the fifth judge's missing identity, the unknown
  purpose raise, the raw-path refusal, and the async fail-open proven by deleting the
  guard and watching its test red.
- 25 functions re-tightened and eight stale pins lowered during the migration;
  nothing raised, three more brought back under their pins in the review round.
- `full gates passed in 603.1s.` at the migration, `730.1s.` at slice C, `730.9s.`
  at the review round; suite 2850 passed; collection 2897; inserted dashes 0.
- Also fixed on the way: a fake `claude_agent_sdk` module installed at collection
  lacked `UserMessage` and broke later modules; it fills its gaps from the installed
  package now.
- `eval_runs.config`'s two-field partial identity is commented onto #51 for the
  rebuild.
