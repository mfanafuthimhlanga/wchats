# TRACE: the Harness ran once against a live local Agent (#5)

**The Harness invoked the Agent.** `run_eval_suite` attempted 20 Scenarios, the Agent answered 18,
Ragas scored all 18 on four metrics, and the `eval_runs` row reads `status=complete` with
`agent_invoked: true`. The sentence in PRODUCTION-READINESS E2E-4, "the eval was never observed
invoking the agent", is false as of 2026-08-22 20:41:56 SAST. No `ship` Verdict was produced.

Two sessions ran this. The first stood up the stack, provisioned the Agent and triggered the
checklist, then died of a login expiry at about 20:14 while the eval was queued. The stack kept
running unattended and finished the eval at 20:41:59. The second session read the results, stopped
the stack and wrote this trace. Every number below comes from the worker log, the API routes, or a
direct read of the two databases.

## The stack

Env overlay sourced into each process (names only): `CONTROL_DB_URL`, `CONTROL_DB_SYNC_URL`,
`REDIS_URL` (all local), `S3_ENDPOINT_URL`, `S3_UPLOADS_BUCKET`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`,
`EMBEDDING_PROVIDER=voyage`, `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE`, and exported from `.env`:
`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` (host `api.deepseek.com`), `PLATFORM_CREDENTIAL_KEY`,
`NEON_API_KEY`, `VOYAGE_API_KEY`, `ADMIN_KEY`. The overlay aborts unless `CONTROL_DB_URL` names
`localhost:5432/wchats_control` and `REDIS_URL` names `localhost:6379`.

```
minio.exe server C:/Users/Bantu/minio/data --address 127.0.0.1:9000
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/Scripts/python.exe -m celery -A app.worker.celery_app worker -Q runtime,pipeline -P solo -l info
```

Worker banner: `transport: redis://localhost:6379/0`, queues `pipeline` and `runtime`, 28 tasks
registered. `curl 127.0.0.1:8000/health` returned `{"status":"ok","redis":"ok","db":"ok"}`.
`GET /api/v1/health` returned 404; the health route lives at `/health`.

## Step 1. Provision one Agent from the Corpus

Corpus: three markdown files for a fictional bike shop, 536 + 530 + 629 bytes (`delivery.md`,
`pricing.md` with one table, `returns-policy.md`).

```
POST /api/v1/tenants        (X-Admin-Key)   201   tenant e2e-harness-5-probe  d421a3b0
POST /api/v1/agents         (X-API-Key)     202   agent aba4f73a  job 227137a2
POST /api/v1/agents/aba4f73a/documents      202   job 73e0c65e, 3 document_ids
```

Observed in the worker log and `job_events`:

| stage | wall | observed |
|---|---|---|
| `provision_neon` | 49.1s | real Neon project `steep-haze-82236017` created; `apply_migrations` ran 0001 to 0017 in 39.3s; agent row `status=ready schema_version=0017` |
| `parse_documents` | 265.3s | `storage_service.get_bytes` at 20:05:37, `detected formats` at 20:09:20: docling's cold start cost 3m43s for three 500-byte files; each convert took under 1.5s |
| `chunk_documents` | 75.0s | 1 + 2 + 3 = 6 chunks; `Warning: You are sending unauthenticated requests to the HF Hub` |
| `generate_metadata` | 47.9s | all three batches failed, `chunks_enriched=0` for every document, task still `succeeded` (breakage 1) |
| `embed_and_migrate` | 18.8s | `total_chunks_embedded=6` |
| `synthesize_retrieval_strategy` | 7.0s | `chunk_count=6 doc_count=3 entity_count=0` |

Tenant DB read after the run: `chunks` 6 rows, `embeddings` 6 rows with 6 non-null vectors,
`documents` 3. Ingestion finished at 20:12:05, 6m48s after the upload.

## Step 2. Trigger one eval suite through the real entry point

The 08-13 E2E-4 path: `POST /api/v1/agents/aba4f73a/checklist-runs` returned 202 at 20:13:23.
`run_deployment_checklist` read `eval_signal=no_runs`, logged `eval_dispatched`, and completed in
86.9s with `recommendation=block`. Its report still reads `eval_summary.eval_signal: "no_runs"`
because the checklist finished at 20:14:50 and the eval at 20:41:56. Nobody ran the checklist a
second time.

The dispatch chained `generate_eval_suite` (35.7s, `count=20`, every Scenario `source=generated`)
into `run_eval_suite` task `4ea8218c`, which ran 1592.8s (26m33s):

```
20:15:42  neon.branch_created     br-orange-paper-auamxp8z
20:15:44  first build_tool_server.ready; bundled claude.exe spawned per Scenario
20:16:05  first retrieve_tool.start  query='repair options pricing turnaround times'
20:31:36  run_eval_suite.invocation_complete attempted=20 responded=18 failed=2 scorable=18
          coverage_rate=0.9 response_rate=0.9 status=measured
20:31:38  run_ragas_eval.start  scenario_count=18
20:41:30  run_ragas_eval.complete  faithfulness_mean=0.7288  answer_relevancy_mean=0.7688
20:41:53  write_eval_results.complete  eval_run_id=29754ceb  rows_written=72
20:41:56  run_eval_suite.complete  agent_invoked=True attempted=20 valid=20 scored=18
          branch_isolation=provisioned_unused golden_set_present=False promoted=0
20:41:58  neon.branch_deleted     br-orange-paper-auamxp8z
```

Job events: none. The eval task writes no `jobs` row and no `job_events` on purpose
(`app/worker/tasks/runtime/eval.py:225`), so there is nothing to replay over SSE for an eval run.

`eval_runs` row `29754ceb`: `kind=m6:aba4f73a`, `status=complete`, `started_at 18:15:38Z`,
`finished_at 18:41:56Z`, `prompt_version_id=NULL`. Its `config` records `model_id
claude-haiku-4-5-20251001`, `judge_model_id claude-haiku-4-5`, `embedding_model_id voyage-3`,
`corpus_chunk_count 6`, `agent_invocation {attempted 20, responded 18, failed 2, errors
{TimeoutError: 2}, concurrency 1, per_turn_timeout_s 90, max_calls_per_run 60, retrieved_context_chunks
69, side_effect_attempts {retrieval_metrics.write: 24}}`, `dataset {attempted 20, valid 20, golden 0,
exploratory 20}`, `verified_qa_promotion.enabled false`.

## Step 3. What the Harness measured

| | |
|---|---|
| Harness invoked the Agent | yes: 18 `_run_sdk_turn.result` lines, each `is_error=False stop_reason=end_turn`, `num_turns` 2 or 3 |
| Scenarios run | 20 attempted, 18 responded, 2 `TimeoutError` at the 90s per-turn bound (Scenarios `611fc713`, `bcddeebe`) |
| Retrieval | 25 `retrieve_tool.start`, every completed call `chunk_count=3`; RRF over `bm25_k=15 vector_k=15 final_k=3`; the 24 `retrieval_metrics.write` side effects were suppressed |
| Judge calls | not logged; Ragas wrote 72 `eval_results` rows (18 Scenarios x 4 metrics), zero NULL scores |
| Scores | faithfulness 0.7288 (min 0.167), answer_relevancy 0.7688 (min 0.409), context_precision 0.9319, context_recall 1.0 |
| `summarise_run_validity` | `attempted=20 valid=20 scored=18 unattributed=0`; golden `0/0/0`, every golden metric `measured: false, value: null`; exploratory `20/20/18` |
| Per-Scenario gate | `GET .../eval-runs/29754ceb/results`: 18 rows, `passed` True 2, False 16, None 0 |
| Verdict | `block`, from the checklist that ran before the eval existed |
| Wall time | provision 56s, ingest 6m48s, checklist 87s, suite generation 36s, eval 26m33s, `POST /tenants` to `eval_runs.finished_at` 38m35s |
| DeepSeek tokens | not logged anywhere. The SDK's `total_cost_usd` sums to 1.0005 USD over 18 turns, priced by the bundled CLI's Anthropic price book for the requested alias, while every request went to `api.deepseek.com`. DeepSeek balance after the run: 1.83 USD; nobody recorded the balance before, so the run's spend is unknown |
| Tenant rows the Agent wrote | `conversations` 0, `messages` 0, `tool_calls` 0, `turn_metrics` 0, `retrieval_metrics` 0 |

## Breakages, in the order met

1. `generate_metadata.batch_extraction_failed` on all three documents. Verbatim, first of three:
   `error="1 validation error for BatchResult\n  Invalid JSON: expected value at line 1 column 1
   [type=json_invalid, input_value='### Chunk 0\\n\\n**Summary...| normalized: cape town',
   input_type=str]"`. The model answered in markdown, the parser wanted JSON, `chunks_enriched=0`,
   `entity_count=0` downstream, and the Celery task reported `succeeded`.
2. `parse_documents` spent 3m43s between reading the first file from S3 and docling's `detected
   formats` line. Three 500-byte markdown files cost 265s to parse.
3. `run_eval_suite.scenario_invocation_failed ... error= error_type=TimeoutError` twice. The
   `error` field is still the empty string that `1.30` found in the deployment task; `error_type`
   carries the diagnosis here. Both Scenarios had logged `retrieve_tool.start`; one never logged
   `retrieve_tool.done`, the other logged it 26s later and then timed out.
4. `GET /api/v1/agents/{id}/eval-runs` reports `scenario_count: 18` and `scored_scenario_count:
   18` for a run whose task and `config` record `attempted=20 valid=20 scored=18`. The two timed-out
   Scenarios do not appear in the list route's denominator.
5. `eval_scenarios.dataset` is NULL on all 20 rows. Both readers bucket NULL as `exploratory`, so
   the run reports `exploratory 20/20/18` over a column nothing set.
6. `branch_isolation=provisioned_unused`: the task created Neon branch `br-orange-paper-auamxp8z`
   at 20:15:42 and deleted it at 20:41:58 without running anything on it.
7. `config.model_id` and `config.judge_model_id` name the Anthropic aliases the code requested. The
   served model is whatever DeepSeek maps `claude-haiku-4-5` to, and no row records that.
8. The run produces no token count at any layer: not the SDK turn line, not Ragas, not
   `eval_runs.config`. Spend is unmeasurable after the fact.

## Step 4. Shutdown

`taskkill /PID 13236 /T /F` (worker, child 13104), `taskkill /PID 3448 /T /F` (uvicorn, child
3640), `taskkill /PID 11212 /T /F` (MinIO). `netstat -ano | findstr LISTENING` afterwards shows only
`:6379` (pid 4696, redis) and `:5432` (pid 6272, postgres). `tasklist` shows no `minio.exe`; the two
remaining `python.exe` (10264, 8468) run `trendcast.ward.app` and belong to another project.

Left behind on purpose, delete by id only: Neon project `steep-haze-82236017`; tenant
`e2e-harness-5-probe` (`d421a3b0`) and agent `aba4f73a` in the local `wchats_control`; MinIO objects
under `C:/Users/Bantu/minio/data`.

## Claim in PRODUCTION-READINESS vs observed today

| claim | observed 2026-08-22 |
|---|---|
| E2E-4: "the eval was never observed invoking the agent" | `agent_invoked=True`, 20 attempted, 18 responded, 18 scored, `eval_runs.status=complete` |
| E2E-4: checklist completes in 79.7s with `recommendation=block` | 86.9s, `block`, both signals `no_runs`, plus `eval_dispatched=true` |
| E2E-1: `POST /tenants` then `POST /agents` provisions an Agent, 12/12 | 201 then 202, Neon project created, migrations to 0017, `status=ready` in 56s |
| E2E-2: ingestion needs S3 (`1.24`); `S3_ENDPOINT_URL` is a local seam | MinIO on `:9000` served upload, parse and chunk; `storage_service.endpoint_override_active` logged once per process |
| "No `ship` verdict has ever been produced" | still true; 2 of 18 Scenarios clear both thresholds |
| "Every LLM judge is uncalibrated" | unchanged; the four Ragas means above are uncalibrated numbers |
| `1.30`: a timeout logged `error=` and erased its diagnosis | the eval path logs `error=` empty and `error_type=TimeoutError` |
| CLAUDE.md: `ANTHROPIC_API_KEY` must be exported into `os.environ` | exported through the overlay; SDK turns, scenario generation and Ragas all reached DeepSeek |
| E2E-4: "red-team never ran 7/7 with tools" | red team did not run in this session; `red_team_summary.signal=no_runs` |
