# Where every credential lives

Every credential W Chats needs, the dashboard that issues it, and each place a copy of it
sits. Written for whoever rotates one, adds one, or has to explain why a process cannot see
one. Names and locations only. A value never goes in this file.

Listed by name on 2026-09-02 from Railway, GitHub Actions and the code.

## The roster

Railway staging keeps variables in two tiers, and the column below names both. The
environment holds a shared set of 21 names. `api-service` reads 16 of them by reference
and holds 5 as its own literals. `worker-runtime` and `beat` reference nothing and hold 17
literals each. So a name sits in one of three shapes:

- **shared+2**: the shared value, which `api-service` reads, plus a literal each on
  `worker-runtime` and `beat`. Three copies.
- **shared**: the shared value, read by `api-service` alone. One copy.
- **3 literals**: a literal on each of the three services, plus a shared value nothing
  reads. Four copies, one dead.

| Setting | Issued by | Local | Railway `staging` | GitHub Actions |
|---|---|---|---|---|
| `NEON_API_KEY` | Neon console, Settings, API keys | `apps/api/.env` | shared+2 | `ci.yml` uses the literal `test`; `nightly.yml` reads secret `NEON_API_KEY_TEST` |
| `NEON_ENCRYPTION_KEY` | generated locally, command in `.env.example` | `apps/api/.env` | shared+2 | `ci.yml` generates a fresh key per run; `nightly.yml` reads secret `NEON_ENCRYPTION_KEY` |
| `PLATFORM_CREDENTIAL_KEY` | generated locally, a different value from the Fernet key | `apps/api/.env` | shared+2 | `tests/conftest.py` sets a test literal for every pytest job; the nightly eval job sets nothing |
| `CONTROL_DB_URL`, `CONTROL_DB_SYNC_URL` | Neon console, the staging control project's pooler DSN, shaped per `260901-control-dsn-drivers.md` | `apps/api/.env` holds the production control DB (CLAUDE.md); local work uses the local cluster | 3 literals | literals in both workflows; a Postgres container exists in the integration and nightly jobs only, so the unit job's URL points at nothing |
| `ADMIN_KEY` | generated locally | `apps/api/.env` | shared+2 | `ci.yml` literal `vrd_admin_test`; `nightly.yml` reads secret `ADMIN_KEY` |
| `JWT_SECRET` | generated locally | `apps/api/.env` | shared+2 | `tests/conftest.py` test literal; the nightly eval job sets nothing |
| `CLERK_JWKS_URL`, `CLERK_WEBHOOK_SIGNING_SECRET` | Clerk dashboard, the dev instance `bright-puma-63.clerk.accounts.dev`, the only instance that exists | `apps/api/.env` | shared+2 | `tests/conftest.py` sets the webhook secret; the nightly eval job sets nothing |
| `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | the same Clerk dashboard | `apps/admin/.env.local`, and the console runs nowhere else | `CLERK_SECRET_KEY` is shared+2, and no API-side process reads it; `Settings` has no such field | not set |
| `OPENAI_API_KEY` | platform.openai.com, API keys | root `.env` since 2026-08-27 (#41), which the API does not read while `apps/api/.env` exists | shared+2 | not set; `nightly.yml` still reads `ANTHROPIC_API_KEY`, revoked 2026-08-27 |
| `VOYAGE_API_KEY` | dashboard.voyageai.com, API keys | `apps/api/.env` | shared+2 | `tests/conftest.py` test literal; `nightly.yml` reads secret `VOYAGE_API_KEY` |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`, `S3_UPLOADS_BUCKET` | Cloudflare dashboard, R2, an API token and the bucket's S3 API URL | MinIO, `S3_ENDPOINT_URL=http://127.0.0.1:9000` | shared | not set |
| `REDIS_URL` | Upstash console, database `firm-calf-277244`, the `rediss://` URL | local `redis-server` | 3 literals | literal; every job has a Redis container |
| The Bantuson tenant's API key, `vrd_live_...` | `POST /api/v1/tenants` on staging with `X-Admin-Key`, shown once in the response; tenant `3f572bca-0c08-454d-8051-a037662ca826`, minted 2026-09-02 | `~/.claude.json`, this project's `mcpServers.wchats` Authorization header | the control DB holds its hash and an HMAC prefix | not set |
| Per-tenant Neon DSNs | `provision_neon` creates each tenant project through the Neon API | none | `agents.neon_connection_string` and `agents.neon_direct_connection_string` in the control DB, Fernet-encrypted under `NEON_ENCRYPTION_KEY` | none |

`EMBEDDING_PROVIDER=voyage`, `ENVIRONMENT=staging`, `NEON_REGION` and `PUBLIC_API_BASE` sit
beside these on every staging service. `PUBLIC_API_BASE` is `api-service`'s public domain,
`https://api-service-staging-09dc.up.railway.app`, and `/health` there is the probe.

The staging `S3_ENDPOINT_URL` is the per-bucket URL the R2 dashboard shows, with `/wchats`
as its path. boto3 keeps that path, so every object key gains a `wchats/` prefix inside
bucket `wchats`. One client does both reads and writes, so the layout stays consistent. No
upload has run on staging yet.

No service sets `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `SENTRY_DSN`, the `SMTP_*`
settings, the Twilio settings, `AT_API_KEY`, `AT_USERNAME`, `AT_SENDER_ID` or
`COHERE_API_KEY`. The owner revoked the Anthropic and DeepSeek keys on 2026-08-27; no Railway
service and no workflow carries them.

Rotate a credential at its issuer, then in every column of its row. Nothing in the repo
compares copies, and a copy left behind keeps working until the issuer revokes it. Two
settings are key material rather than credentials, and nothing re-encrypts under a new
value: `NEON_ENCRYPTION_KEY` unlocks every tenant DSN in the control DB, and
`PLATFORM_CREDENTIAL_KEY` derives per-tenant credentials. Changing either one is a
migration, not a copy.

## The local files, and which one the API reads

Three env files exist locally and git ignores all three:

- `apps/api/.env` is what the API and the workers read. `_find_env_file` walks up from
  `app/core/` and stops at the first `.env`, so this file wins whenever it exists.
- The root `.env` is read only when `apps/api/.env` is absent. A key added here reaches
  nothing until someone copies it across. On 2026-08-27 the two files differed only in
  `ADMIN_KEY` (#41), so deleting either one is a decision.
- `apps/admin/.env.local` holds the console's Clerk keys and `NEXT_PUBLIC_API_BASE`. The
  console runs nowhere but locally, so these have one copy.

The permission layer refuses to read any of them in an agent session. Names without values
come from the owner's own shell, or from Claude Code with a `!` prefix:

```
grep -o '^[A-Z0-9_]*' apps/api/.env
```

## Railway

Project `wchats`, environments `staging` (`5356320a-411a-4e44-8dc2-cb7bbd8fba1d`) and
`production` (`fd018fcf-acc2-4a77-ba8c-cfa64d10d074`). The two tiers above are how staging
came to be: the owner set the shared set through the wizard, `api-service` was pointed at it
by reference, and the two workers received literal copies when they were created on
2026-09-01. The four S3 settings exist only in the shared set, which is enough until
`worker-pipeline` exists, because the API writes uploads and only `parse` and `chunk` on the
`pipeline` queue read them back.

`worker-pipeline` does not exist. `provision_neon` is also a `pipeline` task, so
`create_agent` on staging enqueues work nobody consumes until it does. When someone creates
it, it needs the four S3 settings plus `LD_LIBRARY_PATH` and `HF_HUB_DISABLE_XET_ENDPOINT`,
and its image carries torch and docling at about 3 GB, so it lives for the hour an upload is
under test.

`production` holds no variables, shared or per service. It contains one service, `wchats`,
created 2026-08-30 with no start command. Railway redeploys it on every merge to `main` and
the deployment fails each time (#148).

The CLI reads all of this. `railway login` opens a browser and keeps the token in the CLI's
own config, outside the repo. `railway link` chooses project, environment and service. Two
listings, both piped through Python to print keys only:

```
railway variables --service NAME --environment ENV --json
echo 'query { variables(projectId: "P", environmentId: "E", serviceId: "S", unrendered: true) }' | railway api
```

The first shows the rendered names a container sees. The second shows which of them are
`${{shared.X}}` references and which are literals, and dropping `serviceId` lists the shared
set itself. A service with no instance in an environment still answers the first listing
with its `RAILWAY_*` names, so keys coming back is not evidence the service runs there.

The variable editor stages edits until someone clicks Apply, and a staged value looks
present in the UI while being absent from the container. Before trusting a variable:

```graphql
query($e: String!) { environmentStagedChanges(environmentId: $e) { status appliedAt patch } }
```

`status: STAGED` with `appliedAt: null` means nothing is live. Snapshot the live values
before committing a staged patch, because the patch carries every value the editor held
when it opened and overwrites anything changed since.

## GitHub Actions

The repository holds no secrets and no variables, and neither do its three deployment
environments. `ci.yml` needs none: it uses test literals, generates its Fernet key per run,
and `tests/conftest.py` fills the rest with test values for every pytest job.
`nightly.yml` reads seven secrets (`NEON_API_KEY_TEST`, `NEON_ENCRYPTION_KEY`, `ADMIN_KEY`,
`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `EVAL_DEMO_AGENT_ID`, `EVAL_DEMO_API_KEY`) and
receives an empty string for each. Its eval job also sets none of `PLATFORM_CREDENTIAL_KEY`,
`JWT_SECRET` and `CLERK_WEBHOOK_SIGNING_SECRET`, and it runs uvicorn outside pytest, so
nothing fills them and the API fails at import (#147).

The three deployment environments on the repo (`wchats / staging`, `wchats / production`,
`acceptable-spirit / production`) are records Railway's GitHub app writes when it deploys.
No workflow declares an `environment:`, so nothing stored in them reaches a job.

`gh` holds the GitHub token in its own config under the user profile, and `gh auth status`
names the account.
