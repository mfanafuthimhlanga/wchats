# Connecting Claude Code to W Chats over MCP

For anyone provisioning an Agent from Claude Code instead of the admin console. The server
exposes the whole lifecycle: create, soul, ingest, golden set, eval, red team, checklist,
approve, embed.

## Connect

```
claude mcp add --transport http wchats https://<api-host>/mcp --header "Authorization: Bearer <tenant-api-key>"
```

The tenant API key (`vrd_live_...`) is the only credential (ADR 0004). `X-API-Key` works as
the header name too. A successful connection lists fifteen tools.

## The tools

| Tool | Wraps |
|---|---|
| `create_agent` | `POST /api/v1/agents` |
| `get_agent` | `GET /api/v1/agents/{agent_id}` |
| `update_soul` | `PATCH /api/v1/agents/{agent_id}` |
| `upload_documents` | `POST /api/v1/agents/{agent_id}/documents` |
| `register_golden_scenarios` | `POST /api/v1/agents/{agent_id}/golden-scenarios` |
| `get_job` | `GET /api/v1/jobs/{job_id}` |
| `trigger_eval` | `POST /api/v1/agents/{agent_id}/eval-runs/trigger` |
| `list_eval_runs` | `GET /api/v1/agents/{agent_id}/eval-runs` |
| `get_eval_results` | `GET /api/v1/agents/{agent_id}/eval-runs/{run_id}/results` |
| `trigger_red_team` | `POST /api/v1/agents/{agent_id}/red-team-runs` |
| `get_red_team_run` | `GET /api/v1/agents/{agent_id}/red-team-runs/{run_id}` |
| `run_checklist` | `POST /api/v1/agents/{agent_id}/checklist-runs` |
| `acknowledge_warning` | `POST .../checklist-runs/{run_id}/acknowledge` |
| `approve_deployment` | `POST /api/v1/agents/{agent_id}/approve-deployment` |
| `get_embed_snippet` | `GET /api/v1/agents/{agent_id}/embed-snippet` |

Each tool is its route: same request schema, same validation, same status codes. An error
from the route arrives as an `isError` tool result carrying the HTTP status and detail.

## Long operations poll

`create_agent`, `upload_documents`, `trigger_eval`, `trigger_red_team` and `run_checklist`
return immediately with a job or run id. Poll `get_job` until `status` is terminal; eval
and red-team runs are also readable through their own list/get tools.

## The golden set

Every Agent needs at least ten owner-authored golden pairs before the checklist can reach
`ship`. Write them in a file, then register them:

```
register_golden_scenarios(
  agent_id: "...",
  pairs: [{question: "...", reference_answer: "..."}, ...],
  source_file: "golden.md"
)
```

Re-registering a question already in the golden set is skipped, so re-running the same
file is safe. The rows record `source='authored'` with provenance derived from the
credential; the server never accepts a claim about which human wrote a pair.

## The lifecycle, end to end

```
create_agent → poll get_job
upload_documents → poll get_job
register_golden_scenarios
run_checklist → poll get_job          (sequences eval + red team, computes the Verdict)
get_eval_results / get_red_team_run   (read what the Verdict saw)
acknowledge_warning                    (if the Verdict shipped with warnings)
approve_deployment → get_embed_snippet
```
