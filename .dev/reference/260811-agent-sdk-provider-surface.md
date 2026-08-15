# The Agent SDK's provider surface — settling the DeepSeek question

**2026-08-11.** HANDOFF recorded a conclusion ("the Agent SDK needs a translating proxy") and flagged it
**NOT VERIFIED**, naming this as the first thing to check. It is now verified against the Agent SDK's own
docs and against the installed package. **The conclusion holds, and the cost is higher than recorded.**

Sources: `code.claude.com/docs/en/llm-gateway-protocol`, `.../llm-gateway`, `.../third-party-integrations`,
`.../agent-sdk/overview`, `.../agent-sdk/claude-code-features`. Local: `apps/api/.venv/Lib/site-packages/claude_agent_sdk/`.

---

## 1. The knobs exist, and the SDK does honour them

The bundled binary was unreadable last session, so the env vars were found but their semantics were not.
Both halves are now settled.

**The SDK spawns the bundled CLI and passes the whole environment to it.** Verified in the installed
package, not inferred: `_internal/transport/subprocess_cli.py:430` builds
`inherited_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}`, then merges
`**self._options.env` over it (`:434`). `ClaudeAgentOptions.env` is a real field (`types.py:1721`).
`_bundled/claude.exe` is the process it feeds.

So **every provider variable documented for the Claude Code CLI applies to `claude_agent_sdk`** — the docs
for one are the docs for the other. Confirmed supported: `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`,
`CLAUDE_CODE_USE_FOUNDRY`, `CLAUDE_CODE_USE_ANTHROPIC_AWS`, and the matching `ANTHROPIC_*_BASE_URL` pointers.

## 2. Base URL is not wire format — now documented, not inferred

This was the load-bearing guess. It is correct. The gateway protocol reference gives **exactly three**
accepted formats, and the client's env config picks which one it speaks:

| Format | Selected by | Endpoints |
|---|---|---|
| Anthropic Messages | `ANTHROPIC_BASE_URL` | `/v1/messages`, `/v1/messages/count_tokens` (optional) |
| Amazon Bedrock InvokeModel | `ANTHROPIC_BEDROCK_BASE_URL` + `CLAUDE_CODE_USE_BEDROCK=1` | `/model/{model}/invoke`, `…/invoke-with-response-stream` |
| Google Agent Platform rawPredict | `ANTHROPIC_VERTEX_BASE_URL` + `CLAUDE_CODE_USE_VERTEX=1` | `:rawPredict`, `:streamRawPredict` |

Foundry and Claude Platform on AWS **implement the Anthropic Messages format** behind their own variables.
All three formats are Anthropic-shaped. **OpenAI Chat Completions is not among them**, so DeepSeek's
endpoint cannot be pointed at by any variable — exactly as the prior session reasoned.

## 3. The new fact: Anthropic explicitly does not support this

Not merely undocumented — ruled out in writing. From `docs/en/llm-gateway`:

> Any gateway that exposes a supported API format works. Anthropic doesn't endorse, maintain, or audit
> third-party gateway products, and **doesn't support routing Claude Code to non-Claude models through
> any gateway**.

The prior session did not have this. It converts "hard to build" into "unsupported even if built", which is
a different decision for the owner.

## 4. What the proxy would actually have to do

The prior session named tool-use translation as the hard part. It is one of six, and the protocol reference
documents the rest as hard requirements:

- **An open-ended forwarding contract, by design.** *"Treat the headers and body fields as open lists, not
  closed ones… A gateway pinned to an observed list strips the next capability's header or field and breaks
  it on the release that introduces it."* The proxy is not a one-time build; it is a component that must
  track Claude Code releases forever.
- **Header/body pairing.** Capabilities pair an `anthropic-beta` value with a body field, and the pair
  travels together. Break the pairing and you get hard `400`s — *"only when both halves are absent together
  does the feature turn off quietly."*
- **Streaming is mandatory.** *"Inference responses must stream… a gateway that buffers complete responses
  before relaying them stalls the client."* On `ANTHROPIC_BASE_URL` there is a byte-level watchdog that
  counts **every byte including SSE `ping` events and comment lines**, and aborts after 300s of silence. A
  translating proxy must synthesise keep-alives during DeepSeek's thinking pauses or the client kills the stream.
- **Errors must pass through unmodified.** Claude Code's automatic retry/degrade path *"matches on the
  upstream's error wording"* — a proxy that wraps DeepSeek's errors in its own envelope breaks recovery
  **even when it preserves the status code**.
- **Adaptive thinking is sent to unknown models.** Claude Code *"treats model names it doesn't recognize,
  such as gateway aliases, as current models that receive the field"* — so a DeepSeek alias gets
  `thinking: {"type": "adaptive"}` posted to it and must have that absorbed or translated.
- **`mcp__customer-tools__*` still rides on tool-use translation**, as recorded. That was right; it is just
  not the only thing.

Two smaller traps worth having written down:

- **Model discovery filters on the name.** `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` keeps an entry
  only when its `id` contains `claude` or `anthropic` (case-insensitive). A bare `deepseek-chat` id never
  appears in the picker; it would have to be aliased.
- **Two calls bypass the base URL entirely.** The fast-mode availability check and the WebFetch domain
  safety check call `api.anthropic.com` directly regardless of `ANTHROPIC_BASE_URL`.

## 5. What this means for the 7/11 split

The counts from last session stand (`claude_agent_sdk` in 7 source files, direct `anthropic` in 11). The
**scoring** of them changes:

- **The 7 Agent-SDK files are not a swap and not a config change.** They need a conformant translating
  proxy that Anthropic states it does not support, maintained against an intentionally-growing contract.
  Treat as a project, not a task.
- **The 11 direct-API files are unchanged in difficulty** — DeepSeek is OpenAI-compatible, so they remain a
  provider-adapter swap, and they still hold most of the call volume (judges, `classify_severity`, scenario
  generation, the Actor gate). Ragas is still easiest.

The split is therefore sharper than "two halves, one tractable": the tractable half is tractable, and the
other half is a standing maintenance commitment against an unsupported configuration.

## 6. Not established here

- Whether DeepSeek's tool-calling is expressive enough to carry the `mcp__customer-tools__*` envelope
  faithfully. Not investigated — it is downstream of a decision that has not been made.
- Whether any existing OSS proxy (LiteLLM et al.) meets the contract in §4. Not tested. Note that meeting it
  today is not the bar; tracking it across releases is.
- Cost/latency of DeepSeek on this workload. No measurement exists.

## 7. Addendum 2026-08-15: the premise changed, and the conclusion inverts

This note's "DeepSeek cannot follow" conclusion rested on one premise: DeepSeek exposed only an
OpenAI-shaped endpoint, and §2's three accepted wire formats are all Anthropic-shaped. **DeepSeek now
serves the Anthropic Messages format directly at `https://api.deepseek.com/anthropic`**, with model
auto-mapping (`claude-haiku/sonnet-*` → `deepseek-v4-flash`, `claude-opus-*` → `deepseek-v4-pro`).
That is the exact mechanism §2 documents as supported: `ANTHROPIC_BASE_URL` selects Anthropic
Messages, and the compatibility layer is DeepSeek's to maintain, not ours — §4's translating-proxy
burden never arises.

Still true and carried forward: §3's support posture (Anthropic does not support non-Claude routing
through any gateway) and all of §6, which is now the `7.7` verification list — the seam counts as
landed only when one SDK turn and one direct-API judge call have been observed through the endpoint.
Owner decision and mechanism: `.dev/MASTERPLAN.md` §Model provider; work row BACKLOG `7.7`.
