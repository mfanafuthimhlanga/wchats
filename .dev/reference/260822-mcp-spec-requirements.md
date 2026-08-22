# What the MCP 2026-07-28 spec requires of a thin stateless server

**Date:** 2026-08-22 · **Ticket:** #9 (map #4) · **Feeds:** the M7 MCP provisioning surface

What the current MCP specification demands of a server that wraps the existing REST API, how
Claude Code reaches such a server, and where `get_current_tenant` falls short of it. Every
normative word below is quoted from the page listed under Sources.

## The revision that counts

- The current protocol version is **2026-07-28**. Earlier revisions still published: 2025-06-18
  and 2025-11-25. The draft changelog is empty, so nothing newer is pending.
- 2026-07-28 makes the protocol stateless by design. The `initialize` handshake, the
  `Mcp-Session-Id` header, the HTTP GET stream and SSE resumability are all removed. A "thin
  stateless server" is the only shape the current spec describes.
- Claude Code v2.1.232 and later asks HTTP servers whether they speak 2026-07-28 and falls back
  to the legacy handshake when they do not. Python SDK 2.x serves both eras from one app. Build
  to 2026-07-28 and let the SDK carry legacy clients.

## Transport: Streamable HTTP

- The server "MUST provide a single HTTP endpoint path" that "supports POST". GET and DELETE
  "SHOULD" get `405 Method Not Allowed`.
- Clients send `Accept: application/json, text/event-stream`. The server answers each POST with
  either `Content-Type: application/json` (one JSON object) or `text/event-stream`. Plain JSON
  replies are fully conforming; SSE is per request and optional.
- A notification POST "MUST return HTTP status code 202 Accepted with no body".
- Required request headers, checked against the body: `MCP-Protocol-Version` (mismatch is 400
  plus `HeaderMismatch` error `-32020`), `Mcp-Method` on every request, `Mcp-Name` on
  `tools/call`, `resources/read` and `prompts/get`.
- Unknown method: "MUST respond with 404 Not Found" and JSON-RPC `-32601`.
- "Servers MUST validate the Origin header"; present and invalid is 403.
- A closed response stream "MUST be treated by the server as cancellation". No `Last-Event-ID`.
- Server-initiated requests are gone. A tool that needs more input returns
  `resultType: "input_required"` with `inputRequests`; the client retries the original call with
  `inputResponses` (MRTR). This is how the approve journey's acknowledge-each-warning step maps.

## Sessions and lifecycle

- "Servers MUST NOT rely on prior requests over the same connection to establish context."
- Every request carries `_meta["io.modelcontextprotocol/protocolVersion"]` and
  `_meta["io.modelcontextprotocol/clientCapabilities"]`; a missing field is `-32602` and HTTP 400.
- "Servers MUST implement `server/discover`" (versions, capabilities, identity).
- An incoming `Mcp-Session-Id` is ignored; the server mints and echoes none.
- Legacy clients (2025-xx) send `initialize` first; a server "MAY assign a session ID" and a
  stateless one simply never does. Python SDK 2.x handles the legacy path when the request is an
  `initialize`; its `stateless_http` flag now applies to legacy requests only.

## Tool schemas

- Tool fields: `name`, `title` (optional), `description`, `icons` (optional), `inputSchema`,
  `outputSchema` (optional), `annotations` (optional). `inputSchema` "MUST be a valid JSON
  Schema object"; a no-argument tool uses `{"type": "object", "additionalProperties": false}`.
- Default dialect is JSON Schema 2020-12, any keyword allowed, `$ref` resolution required.
- Names "SHOULD" be 1 to 128 characters, case-sensitive, only `A-Z a-z 0-9 _ - .`, unique per
  server.
- With an `outputSchema`, "Servers MUST provide structured results that conform to this schema"
  in `structuredContent`, and "SHOULD also return the serialized JSON in a TextContent block".
- Input validation failures are `isError: true` tool results, not JSON-RPC errors.
- Every result carries `resultType: "complete"`. `tools/list` results carry `ttlMs` and
  `cacheScope` (`"public"` or `"private"`) and "SHOULD" list tools in a deterministic order.
- The tool set "MUST NOT vary per-connection" but "MAY vary by the authorization presented on
  the request". Annotations are hints a client "MUST consider ... untrusted".

## Authorization

- "Authorization is OPTIONAL for MCP implementations." Over HTTP, implementations "SHOULD
  conform" to the OAuth section. A static bearer key is outside the OAuth section, and the spec
  neither forbids nor specifies it.
- When conforming, "A protected MCP server acts as an OAuth 2.1 resource server". It "MUST
  implement OAuth 2.0 Protected Resource Metadata (RFC9728)" with at least one
  `authorization_servers` entry, served at `/.well-known/oauth-protected-resource[/path]` or
  named in `WWW-Authenticate: Bearer resource_metadata="..."` on a 401. Clients try the header
  first. "MCP servers SHOULD include a `scope` parameter" in that header.
- The authorization server "MUST provide" RFC 8414 metadata or OIDC Discovery and "MUST
  implement OAuth 2.1" (PKCE). Client registration priority: pre-registered credentials, then
  Client ID Metadata Documents ("SHOULD" support), then Dynamic Client Registration (now
  deprecated, "MAY").
- Clients "MUST" send the RFC 8707 `resource` parameter equal to the server's canonical URI,
  for example `https://api.example.com/mcp` with no trailing slash.
- Token use: `Authorization: Bearer <access-token>` on every request; "Access tokens MUST NOT be
  included in the URI query string".
- Validation: "MCP servers MUST validate that access tokens were issued specifically for them as
  the intended audience". "Invalid or expired tokens MUST receive a HTTP 401 response."
  "MCP servers MUST NOT accept or transit any other tokens." "MCP Servers MUST NOT use sessions
  for authentication."
- Status table: 401 "Authorization required or token invalid"; 403 "Invalid scopes or
  insufficient permissions"; 400 "Malformed authorization request".

## How Claude Code connects

- `claude mcp add --transport http wchats https://<host>/mcp --header "Authorization: Bearer <token>"`.
- `.mcp.json`: `{"mcpServers": {"wchats": {"type": "http", "url": "...", "headers":
  {"Authorization": "Bearer ${WCHATS_KEY}"}}}}`. `${VAR}` and `${VAR:-default}` expand in `url`
  and `headers`. `type` is mandatory; `streamable-http` is an alias for `http`. Any header name
  works, so `X-API-Key` is as valid as `Authorization`.
- Static header wins over OAuth: "If you configured `headers.Authorization` for the server and
  the server rejects that header, Claude Code reports the connection as failed instead of falling
  back to OAuth." `headersHelper` runs a command at connect time for minted tokens.
- OAuth path: a 401 or 403 triggers `/mcp` or `claude mcp login <name>`. Discovery "first checks
  RFC 9728 Protected Resource Metadata at `/.well-known/oauth-protected-resource`, then falls back
  to RFC 8414". Supports DCR, CIMD (auto-discovered) and pre-provided `--client-id` /
  `--client-secret` / `oauth.authServerMetadataUrl`. `claude -p` cannot run the browser flow.

## Python SDK shape (2.x, `pip install mcp`)

- `from mcp.server import MCPServer` (the class formerly named `FastMCP`).
  `app = mcp.streamable_http_app(transport_security=...)`, mounted at `/mcp`, run by uvicorn.
  Without an allowed-hosts setting, non-localhost requests get 421.
- Auth: `MCPServer(..., token_verifier=<TokenVerifier>, auth=AuthSettings(issuer_url=...,
  resource_server_url=..., required_scopes=[...]))`. Both or `ValueError`. The SDK then serves
  `/.well-known/oauth-protected-resource/mcp` and answers a missing or rejected token with 401
  plus `WWW-Authenticate`. Handlers read the caller via `get_access_token()`.
- `TokenVerifier.verify_token(token) -> AccessToken | None` is the whole seam: a verifier that
  looks up a tenant API key hash fits it as well as a JWT verifier does.

## What the repo's credential model cannot satisfy

`get_current_tenant` (`apps/api/app/api/deps.py`) accepts two credentials and records which one
ran on `request.state.credential_kind`:

| Path | Header | What it is | MCP fit |
|---|---|---|---|
| Clerk JWT | `Authorization: Bearer <session token>` | Clerk browser session, RS256 via JWKS, `verify_aud: False` (`app/core/clerk_jwt.py`) | fails the OAuth section |
| Tenant key | `X-API-Key: vrd_live_...` | argon2-hashed machine credential | works as a static header, outside the OAuth section |

1. **The Clerk session JWT cannot be the MCP access token.** The spec's audience MUST needs a
   token "issued specifically for" the MCP server; Clerk session tokens carry no `aud` and the
   verifier disables the check. Claude Code also has no way to mint one: the token comes from
   Clerk's in-browser `getToken()`, not from an OAuth authorization-code flow discovered through
   protected resource metadata. Making Clerk the authorization server means a new token type
   (Clerk as OAuth provider, audience-bound, aud verified), not the existing session-token path.
2. **The tenant key conforms to nothing in the OAuth section.** No protected resource metadata,
   no authorization server, no audience. It is allowed because that section is SHOULD, and it is
   the path Claude Code's static `headers` supports first-class. Its cost is the next row.
3. **Any tool that needs a human cannot be exposed over the tenant key.**
   `POST /agents/{id}/eval-scenarios/{id}/label` (`apps/api/app/api/v1/evals.py:1289`) returns
   403 unless `credential_kind == CREDENTIAL_CLERK_JWT`, because `human_authored` is a claim
   about who wrote a string. An MCP server presenting a machine credential inherits that refusal.
   The M7 op list (create, soul, ingest, eval, red-team, checklist, approve, embed, audit) does
   not include the label write, so the tenant-key path covers M7. Labelling stays in the admin UI
   until an audience-bound OAuth token exists.
4. **The 401 has no `WWW-Authenticate`.** `get_current_tenant` raises
   `HTTPException(401, "Authentication required")` with no headers, and no
   `/.well-known/oauth-protected-resource` route exists. Both are required the day OAuth is
   adopted; neither matters on the static-key path. A valid JWT with no tenant row returns 404,
   which is outside the spec's 401/403/400 table.
5. **A separate-process thin server forwarding the caller's credential to the REST API is the
   passthrough the spec forbids** ("MUST NOT accept or transit any other tokens"). Mounting the
   MCP app inside the FastAPI process and calling services directly keeps one audience and no
   transit. Under the tenant key the rule's letter is about OAuth tokens, but the same design
   avoids a second place that holds the key.

## Sources

- https://modelcontextprotocol.io/specification/versioning
- https://modelcontextprotocol.io/specification/2026-07-28/changelog
- https://modelcontextprotocol.io/specification/2026-07-28/basic/index
- https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations
- https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- https://code.claude.com/docs/en/mcp
- https://github.com/modelcontextprotocol/python-sdk
- https://py.sdk.modelcontextprotocol.io/migration/
- https://py.sdk.modelcontextprotocol.io/run/deploy/
- https://py.sdk.modelcontextprotocol.io/run/authorization/
