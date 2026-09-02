# Where every credential lives

Every credential W Chats needs, the dashboard that issues it, and the place each copy is
set, for whoever has to rotate one, add one, or work out why a process cannot see one.
Names and locations only. No value appears here, and none should be added.

Observed 2026-09-02 by listing variable names on Railway and GitHub, never values.

## The roster

| Setting | Issued by | Local | Railway `staging` | GitHub Actions |
|---|---|---|---|---|
| `NEON_API_KEY` | Neon console, Settings, API keys | `apps/api/.env` | all three services | `ci.yml` uses the literal `test`; `nightly.yml` reads secret `NEON_API_KEY_TEST` |
| `NEON_ENCRYPTION_KEY` | generated locally, command in `.env.example` | `apps/api/.env` | all three | `ci.yml` generates a fresh key per run; `nightly.yml` reads secret `NEON_ENCRYPTION_KEY` |
| `PLATFORM_CREDENTIAL_KEY` | generated locally, a different value from the Fernet key | `apps/api/.env` | all three | not set |
| `CONTROL_DB_URL`, `CONTROL_DB_SYNC_URL` | Neon console, the staging control project's pooler DSN, shaped per `260901-control-dsn-drivers.md` | `apps/api/.env` holds the production control DB (CLAUDE.md); local work uses the local cluster | all three | the job's own Postgres container |
| `ADMIN_KEY` | generated locally | `apps/api/.env` | all three | `ci.yml` literal `vrd_admin_test`; `nightly.yml` reads secret `ADMIN_KEY` |
| `JWT_SECRET` | generated locally | `apps/api/.env` | all three | not set |
| `CLERK_SECRET_KEY`, `CLERK_WEBHOOK_SIGNING_SECRET`, `CLERK_JWKS_URL` | Clerk dashboard, the dev instance `bright-puma-63.clerk.accounts.dev` | secret and publishable key in `apps/admin/.env.local`; webhook secret in `apps/api/.env` | all three | not set |
| `OPENAI_API_KEY` | platform.openai.com, API keys | root `.env` since 2026-08-27, which the API does not read while `apps/api/.env` exists (#41) | all three | not set; `nightly.yml` still reads `ANTHROPIC_API_KEY`, revoked 2026-08-27 |
| `VOYAGE_API_KEY` | dashboard.voyageai.com, API keys | `apps/api/.env` | all three | `nightly.yml` reads secret `VOYAGE_API_KEY` |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`, `S3_UPLOADS_BUCKET` | Cloudflare dashboard, R2, an API token scoped to bucket `wchats`; the endpoint is `https://<account-id>.r2.cloudflarestorage.com` | MinIO, `S3_ENDPOINT_URL=http://127.0.0.1:9000` | `api-service` only | not set |
| `REDIS_URL` | Upstash console, database `firm-calf-277244`, the `rediss://` URL | local `redis-server` | all three | the job's own Redis container |

`EMBEDDING_PROVIDER=voyage` and `ENVIRONMENT=staging` sit beside these on every staging
service. `PUBLIC_API_BASE` is the Railway public domain of `api-service`,
`https://api-service-staging-09dc.up.railway.app`, and `/health` there is the probe.

Set on no service today: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `SENTRY_DSN`, the
`SMTP_*` and Twilio settings, `COHERE_API_KEY`. The Anthropic and DeepSeek keys were
revoked on 2026-08-27 and exist nowhere.

To rotate a credential, change it at the issuer, then in every column of its row. A copy
left behind keeps working until the issuer revokes it, and nothing in the repo compares
copies.

## The local files, and which one the API reads

Three env files exist locally and none is committed:

- `apps/api/.env` is what the API and the workers read. `_find_env_file` walks up from
  `app/core/` and stops at the first `.env`, so this file wins whenever it exists.
- The root `.env` is read only when `apps/api/.env` is absent. A key added here reaches
  nothing until it is copied across. `ADMIN_KEY` differs between the two files (#41), so
  deleting either one is a decision, not cleanup.
- `apps/admin/.env.local` holds the console's Clerk keys and `NEXT_PUBLIC_API_BASE`.

The permission layer blocks reading any of them from an agent session. To check a name is
present, ask the owner to run the listing with a `!` prefix.

## Railway

Project `wchats`, environments `staging` (`5356320a-411a-4e44-8dc2-cb7bbd8fba1d`) and
`production` (`fd018fcf-acc2-4a77-ba8c-cfa64d10d074`). Variables are set per service,
not shared at project level, so a new service starts empty. `staging` carries the roster
above on `api-service`, `worker-runtime` and `beat`; the four S3 settings are on
`api-service` alone, which is enough until `worker-pipeline` exists, because the API
writes uploads and only `parse` and `chunk` on the `pipeline` queue read them back.

`production` holds no variables on any service. It contains one service, `wchats`,
created 2026-08-30 with no start command and no variables. Railway redeploys it on every
merge to `main` and the deployment fails each time.

The CLI is the interface. `railway login` opens a browser and keeps the token in the
CLI's own config, outside the repo. `railway link` chooses project, environment and
service. `railway variables --service NAME --environment ENV --json` lists a service's
variables; pipe it through Python and print only the keys.

The variable editor stages edits until Apply is clicked, and a staged value looks
present in the UI while being absent from the container. Before trusting a variable:

```graphql
query($e: String!) { environmentStagedChanges(environmentId: $e) { status appliedAt patch } }
```

`status: STAGED` with `appliedAt: null` means nothing is live. Snapshot the live values
before committing a staged patch, because the patch carries every value the editor held
when it opened and overwrites anything changed since.

## GitHub Actions

The repository holds no secrets and no variables. `ci.yml` needs none; it uses test
literals and generates its Fernet key per run. `nightly.yml` reads seven secrets
(`NEON_API_KEY_TEST`, `NEON_ENCRYPTION_KEY`, `ADMIN_KEY`, `ANTHROPIC_API_KEY`,
`VOYAGE_API_KEY`, `EVAL_DEMO_AGENT_ID`, `EVAL_DEMO_API_KEY`) and receives an empty string
for each.

The three deployment environments on the repo (`wchats / staging`, `wchats / production`,
`acceptable-spirit / production`) are records Railway's GitHub app writes when it deploys.
No workflow declares an `environment:`, so nothing stored in them reaches a job.

`gh auth login` holds the GitHub token, in the CLI's own config.

## Not yet anywhere

- A production Clerk instance. The console and the API share the dev instance by owner
  decision until something runs in production.
- Any variable in Railway `production`.
- A `worker-pipeline` service; its image carries torch and docling at about 3 GB, so it
  is created for the hour an upload is being tested and removed after.
