# Where every credential lives

This note lists every credential W Chats needs, the dashboard that issues it, and each place
a copy sits. It is for whoever rotates one, adds one, or has to explain why a process cannot
see one. Names and locations only.

Listed by name on 2026-09-02 from Railway, GitHub Actions and the code.

## The roster

Railway staging keeps variables in two tiers, and the column below names both. The
environment holds a shared set of 21 names. `api-service` reads 16 of them by reference
and holds 5 as its own literals. `worker-runtime` and `beat` reference nothing and hold 17
literals each (#55 copied them from `api-service` through `serviceInstanceUpdate` on
2026-09-01). So a name sits in one of three shapes:

- **shared+2**: the shared value, which `api-service` reads, plus a literal each on
  `worker-runtime` and `beat`. Three copies.
- **shared**: the shared value, read by `api-service` alone. One copy.
- **3 literals**: a literal on each of the three services, plus a shared value nothing
  reads. Four copies, one dead. Editing the shared one changes nothing.

The GitHub Actions column names three kinds of source. `ci.yml` runs pytest in three jobs.
The unit and integration jobs set six names in the workflow; the eval job sets nothing. `tests/conftest.py` fills every name a job leaves unset, with placeholders whose
database and Redis URLs point at nothing. `nightly.yml` reads repository secrets, and the
repository holds none.

| Setting | Issued by | Local | Railway `staging` | GitHub Actions |
|---|---|---|---|---|
| `NEON_API_KEY` | Neon console, Settings, API keys | `apps/api/.env` | shared+2 | `ci.yml` literal `test` in the unit and integration jobs; conftest in the eval job; `nightly.yml` reads secret `NEON_API_KEY_TEST` |
| `NEON_ENCRYPTION_KEY` | generated locally, command in `.env.example` | `apps/api/.env` | shared+2 | the unit and integration jobs generate a fresh key per run, conftest does the same for the eval job; `nightly.yml` reads secret `NEON_ENCRYPTION_KEY` |
| `PLATFORM_CREDENTIAL_KEY` | generated locally, a different value from the Fernet key | `apps/api/.env` | shared+2 | conftest generates a random key per process in every pytest job; the nightly eval job sets nothing |
| `CONTROL_DB_URL`, `CONTROL_DB_SYNC_URL` | Neon console, the staging control project's pooler DSN, shaped per `260901-control-dsn-drivers.md` | `apps/api/.env` holds the production control DB (CLAUDE.md); local work uses the local cluster | 3 literals | workflow literals in the unit, integration and nightly jobs, conftest in the eval job; a Postgres container exists only in the integration and nightly jobs, so the unit and eval URLs point at nothing |
| `ADMIN_KEY` | generated locally | `apps/api/.env` | shared+2 | `ci.yml` literal `vrd_admin_test` in the unit and integration jobs; conftest in the eval job; `nightly.yml` reads secret `ADMIN_KEY` |
| `JWT_SECRET` | generated locally | `apps/api/.env` | shared+2 | conftest literal in every pytest job; the nightly eval job sets nothing |
| `CLERK_JWKS_URL`, `CLERK_WEBHOOK_SIGNING_SECRET` | Clerk dashboard, the dev instance `bright-puma-63.clerk.accounts.dev`, the only instance that exists | `apps/api/.env` | shared+2 | conftest sets both in every pytest job; the nightly eval job sets nothing |
| `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | the same Clerk dashboard | `apps/admin/.env.local`; Clerk's Next.js SDK reads both from the console's environment, and no line in `apps/admin` names them | `CLERK_SECRET_KEY` is shared+2, and no API-side process reads it; `Settings` has no such field | not set |
| `OPENAI_API_KEY` | platform.openai.com, API keys | root `.env` since 2026-08-27 (#41), which the API does not read while `apps/api/.env` exists | shared+2 | conftest placeholder; no workflow sets a real one |
| `VOYAGE_API_KEY` | dashboard.voyageai.com, API keys | `apps/api/.env` | shared+2 | conftest literal in every pytest job; `nightly.yml` reads secret `VOYAGE_API_KEY` |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`, `S3_UPLOADS_BUCKET` | Cloudflare dashboard, R2, an API token and the bucket's S3 API URL | MinIO, `S3_ENDPOINT_URL=http://127.0.0.1:9000` | shared | not set |
| `REDIS_URL` | Upstash console, database `firm-calf-277244`, the `rediss://` URL | local `redis-server` | 3 literals | workflow literal in the unit, integration and nightly jobs, each with a Redis container; conftest in the eval job, pointing at nothing |
| The Bantuson tenant's API key, `vrd_live_...` | `POST /api/v1/tenants` on staging with `X-Admin-Key`, shown once in the response; tenant `3f572bca-0c08-454d-8051-a037662ca826`, minted 2026-09-02 | `~/.claude.json`, this project's `mcpServers.wchats` Authorization header | the control DB holds its hash and an HMAC prefix | not set |
| Per-tenant Neon DSNs | `provision_neon` creates each tenant project through the Neon API | none | `agents.neon_connection_string` and `agents.neon_direct_connection_string` in the control DB, Fernet-encrypted under `NEON_ENCRYPTION_KEY` | none |

Four settings that are not credentials sit beside these. `NEON_REGION` and
`EMBEDDING_PROVIDER=voyage` are shared+2. `ENVIRONMENT=staging` and `PUBLIC_API_BASE` are 3
literals, so editing either in the shared set changes nothing on `api-service`.
`PUBLIC_API_BASE` is `api-service`'s public domain,
`https://api-service-staging-09dc.up.railway.app`, and `/health` there is the probe.

The staging `S3_ENDPOINT_URL` is the per-bucket URL the R2 dashboard shows, with `/wchats`
as its path. boto3 keeps that path, so every key `upload_key` builds as
`{agent_id}/{doc_id}{ext}` lands at `wchats/{agent_id}/{doc_id}{ext}` inside bucket
`wchats`. One client does both reads and writes, so staging agrees with itself. Local
MinIO has no path, so a local key and a staging key for the same document differ by that
prefix. No upload has run on staging yet.

No service sets `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `SENTRY_DSN`, the `SMTP_*`
settings, the Twilio settings, `AT_API_KEY`, `AT_USERNAME`, `AT_SENDER_ID` or
`COHERE_API_KEY`. The owner revoked the Anthropic and DeepSeek keys on 2026-08-27, and no
Railway service carries them. `nightly.yml` still reads `ANTHROPIC_API_KEY` from a secret
that does not exist, conftest sets a placeholder for it, and `Settings` still declares the
field with an empty default.

Rotate a credential at its issuer, then in every column of its row. Nothing in the repo
compares copies, and a copy left behind keeps working until the issuer revokes it. Two
settings are key material rather than credentials, and the code has no path for changing
either. `NEON_ENCRYPTION_KEY` decrypts the two DSN columns in the control DB.
`PLATFORM_CREDENTIAL_KEY` derives, per tenant, the Fernet key that unlocks the
`integration_credentials` rows in that tenant's own database. Changing the first means
re-encrypting one control table. Changing the second means re-encrypting a table in every
tenant database.

## The local files, and which one the API reads

Three env files exist locally and git ignores all three.

- `apps/api/.env` is what the API and the workers read. `_find_env_file` walks up from
  `app/core/` and stops at the first `.env`, so this file wins whenever it exists.
- The root `.env` is read only when `apps/api/.env` is absent. A key added here reaches
  nothing until someone copies it across. On 2026-08-27 the two files differed only in
  `ADMIN_KEY` (#41), so deleting either one is a decision.
- `apps/admin/.env.local` holds the console's Clerk keys and `NEXT_PUBLIC_API_BASE`. The
  repo carries no deployment target for the console, so these have one copy.

An agent session cannot read these files. The owner lists names without values from a
shell, or from Claude Code with a `!` prefix:

```
grep -o '^[A-Z0-9_]*' apps/api/.env
```

## Railway

Project `wchats`, environments `staging` (`5356320a-411a-4e44-8dc2-cb7bbd8fba1d`) and
`production` (`fd018fcf-acc2-4a77-ba8c-cfa64d10d074`). The four S3 settings exist only in
the shared set, which is enough until `worker-pipeline` exists, because the API writes
uploads and only `parse` and `chunk` on the `pipeline` queue read them back.

`worker-pipeline` does not exist. `provision_neon` is also a `pipeline` task, so
`create_agent` on staging enqueues work nobody consumes until it does. Whoever creates it
gives it the four S3 settings plus `LD_LIBRARY_PATH` and `HF_HUB_DISABLE_XET_ENDPOINT`. Its
image carries torch and docling at about 3 GB, so it lives for the hour an upload is under
test.

`production` holds no variables, shared or per service. It contains one service, `wchats`,
created 2026-08-30 with no start command. Railway redeploys it on every merge to `main` and
the deployment fails each time (#148).

The Railway token lives in the CLI's own config after `railway login`, outside the repo.
Two listings, both piped through Python to print keys only:

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

Read the `patch`. While `appliedAt` is null, every name in `patch` is not live. An empty patch with
`status: STAGED` is nothing pending, which is what both environments show today. Snapshot
the live values before committing a staged patch, because the patch carries every value the
editor held when it opened and overwrites anything changed since.

## GitHub Actions

The repository holds no secrets and no variables, and neither do its three deployment
environments. `nightly.yml` reads seven secrets (`NEON_API_KEY_TEST`,
`NEON_ENCRYPTION_KEY`, `ADMIN_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`,
`EVAL_DEMO_AGENT_ID`, `EVAL_DEMO_API_KEY`) and receives an empty string for each. Its eval
job also sets none of `PLATFORM_CREDENTIAL_KEY`, `JWT_SECRET` and
`CLERK_WEBHOOK_SIGNING_SECRET`. That job runs uvicorn outside pytest, so conftest cannot
fill them, and `Settings` raises for the three missing fields when the API starts (#147).

The three deployment environments on the repo (`wchats / staging`, `wchats / production`,
`acceptable-spirit / production`) are records Railway's GitHub app writes when it deploys.
No workflow declares an `environment:`, so nothing stored in them reaches a job.

The GitHub token lives in `gh`'s own config after `gh auth login`, and `gh auth status`
names the account.
