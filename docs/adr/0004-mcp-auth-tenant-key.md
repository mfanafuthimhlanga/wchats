# 0004: MCP authenticates with the tenant API key, not OAuth

Status: accepted. Decided with the owner 2026-08-23 on issue #10, built in #56.

## What was decided

The MCP server is one stateless POST endpoint at `/mcp`, inside the FastAPI app
(`app/api/mcp.py`), speaking protocol revision 2026-07-28. It authenticates every request
by the tenant API key, sent as a static bearer header:

```
claude mcp add --transport http wchats https://<host>/mcp --header "Authorization: Bearer vrd_live_..."
```

The endpoint resolves the key through the same argon2 lookup `get_current_tenant` uses,
then serves each tool by replaying it against the tool's own REST route in-process, with
the key forwarded as `X-API-Key`. The key never leaves the process and no second
credential type exists. No OAuth: no protected-resource metadata, no `WWW-Authenticate`
challenge, no audience-bound tokens.

## Why

- Every lifecycle route already accepts the tenant key. The MCP surface is a map over
  routes that exist; a credential that already authorises each of them adds no new trust
  decision.
- Claude Code supports a static `headers.Authorization` first-class and prefers it over
  OAuth discovery. The one consumer the finish line needs works with one flag.
- The 2026-07-28 spec makes authorization optional. Its OAuth section is a SHOULD, and a
  static bearer key sits outside that section rather than in violation of it (spec
  requirements: `.dev/reference/260822-mcp-spec-requirements.md` on `research/mcp-spec`).
- OAuth buys per-user identity. The finish line has one operator holding one tenant key;
  the resource-server implementation (RFC 9728 metadata, RFC 8414 discovery, an
  audience-bound token Clerk does not currently mint) would be built for no caller.

## What this costs

- **Hard to reverse once Tenants configure clients.** Every configured client carries the
  key in its MCP config; moving to OAuth later invalidates all of them at once.
- **No per-user identity.** The key authenticates an account, not a person. Any tool
  whose write asserts a human (the `human_authored` label tier) stays off this surface;
  golden registration over MCP therefore records authorship as provenance on the row and
  leaves `label_trust_tier` NULL.
- **A reader who knows the MCP spec expects OAuth.** This record is the answer: the
  omission is a decision, not a gap.

## The shape that follows

The tenant key means the tool list is account-scoped and static: fourteen lifecycle
wrappers plus golden registration, identical for every caller. Trigger tools return a
job id and the caller polls `get_job`; nothing on this surface waits, so nothing needs a
session, which is what lets the endpoint meet the stateless revision exactly.
