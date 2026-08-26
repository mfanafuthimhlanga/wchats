# 0008: One provider, and an owned loop

Status: accepted. Decided with the owner 2026-08-23 on issue #34, over three verification
passes (DPA pricing on `research/provider-price`, Mistral Small 4 benchmarks, GPT-5.6
family pricing). Carried into spec #37; the loop lands in #48, the Attacker and the
Orchestrator follow in #49.

OpenAI `gpt-5.6-luna` serves every model call: the Agent turn, all five Judges, and the
whole direct-API half. Verified 2026-08-23: $0.20/M in, $1.20/M out; Artificial Analysis
34 at low effort against gpt-5-mini's 26 at high; API terms with no training on API data,
30-day abuse logs, and an incorporated DPA. At the measured run shape the Agent turn
costs $0.76 per thousand turns against DeepSeek's $1.28 at peak, and peak (08:00 to 12:00
CAT weekdays) is when Customers talk. The Judge at `reasoning_effort: none` costs $0.62
per thousand against DeepSeek's $1.23. That floor holds only at effort none; any effort
increase is re-measured from the `model_calls` ledger, never assumed, which is why the
effort travels on the purpose route rather than at call sites. Mistral Small 4 lost on
measurement (AA 20 against V4 Flash's 52, absent from LMArena); gpt-5-mini is dominated
by Luna on both axes. DeepSeek serves the Agent turn only until the loop lands, then
retires.

The Agent turn leaves the `claude_agent_sdk` harness for an owned bounded tool loop on
the plain OpenAI SDK. No new framework. The SDK's `resume` stores session files on the
container filesystem, which Railway's ephemeral containers would break on every deploy;
the loop reads and writes session state in the existing `conversations` and `messages`
tables instead, so a turn resumes from the database on any container. The trade is the
SDK's harness features against portability and provider freedom, and portability won
because the harness's remaining value was session files this deployment model cannot
keep.

Judge self-preference cannot be blinded, because the bias is distribution-level and there
is no family label to hide. It is continuously measured instead: a random 20% of Judge
calls are duplicated on Gemini 3.5 Flash-Lite (EU endpoint, about $0.20 per thousand
turns), `EvalResult` carries the paired delta, and `rule_version` holds the threshold at
which a disagreeing audit flips a run to `ship_with_warnings`. Total shape $1.58 per
thousand turns under one DPA, against $1.90 for the DeepSeek-plus-Luna split.

Consequences a reader will meet. An agent product with no Agent SDK on its customer path:
the turn is assembled once in `build_agent_turn` and run by `run_agent_loop`, and the
eval drives the same two functions. `agent_turn` joins the factory's purpose routes, so
every loop iteration leaves a `model_calls` row and the turn's cost is derived from those
rows at read time; the SDK's `total_cost_usd`, priced from the wrong book, goes with the
harness. Retrieval reaches the model as framed JSON read back with `json.loads`; the repr
and `literal_eval` seam that #44 left standing dies here. `conversations.metadata` keeps
`sdk_session_id` on old rows as a dead key nothing reads. The eval-egress rule (#16, ADR
0006) is unchanged: an eval turn sends what a live turn sends, which after the cutover is
to OpenAI.
