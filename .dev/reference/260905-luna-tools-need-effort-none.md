# gpt-5.6-luna refuses a function tool without `reasoning_effort: "none"`

Observed 2026-09-05 on staging and reproduced from this box. Read this before
touching `PURPOSE_ROUTES`, the raw client seam, or any comment about what the
provider does by default.

## The rule

On `/v1/chat/completions`, `gpt-5.6-luna` accepts a request that carries
function tools only when it also carries `reasoning_effort: "none"`. Four
calls from this box, openai 2.45.0:

| tools | effort field | answer |
|---|---|---|
| yes | `none` | 200 |
| yes | absent | 400, `set reasoning_effort to 'none'` |
| yes | `low` | 400, the same message |
| yes | `minimal` | 400, `Supported values are: 'none', 'low', 'medium', 'high', and 'xhigh'` |
| no | `none` | 200 |

The 400 text names the alternative, `/v1/responses`, which nothing here uses.

## What it broke

The first #57 checklist on staging blocked in 25 seconds. Every raw call that
attached a tool was refused, so `generate_eval_suite` produced no scenarios and
the eval recorded `agent_not_invoked`, the red team's attacker loop raised on
all twelve attempts across four vectors, and the Orchestrator's narration fell
back. The Verdict read as three reasons and had one.

## How the effort reaches the wire now

`app/core/model_client.py` puts the route's effort on the wire on every seam.

- `make_instructor_client` stores it as an instructor default.
- `agent_loop._request_kwargs` writes it on each body the owned loop builds.
- `make_client` and `make_async_client` install it through
  `_carry_route_effort`, a default on the cached `chat.completions` resource,
  covering `create` and `parse`. A call site that names an effort wins.

`_LUNA` names `none`, so all nineteen purposes run at the one effort the
provider accepts beside a tool, and the raw path no longer refuses an
effort-bearing route. `calibration_judge` therefore reports a real
`JudgeIdentity`.

## The class

FM-026 in `.dev/failure-modes.jsonl`. A comment stated what the provider does
with a request shape no test ever sent. The detector: for every sentence
describing provider behaviour, find the recorded answer to that exact request
shape, tools included, or send it once and write `OBSERVED <date>` beside it.
