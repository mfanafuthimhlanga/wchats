## Deferred Items

### Pre-existing Unit Test Failures (discovered during 08-06 execution)

These failures pre-existed Plan 08-06. Verified by running the affected tests
before our changes (git stash / stash pop). NOT caused by deployment router
addition to main.py. Scope boundary: out of scope for 08-06.

| Test File | Test | Notes |
|-----------|------|-------|
| test_agent_chat_routes.py | TestAgentChatPost202::test_valid_post_returns_202_with_job_id_and_events_url | Hits /agents/{id}/chat (missing /api/v1 prefix) |
| test_agents_patch.py | TestPatchAgent::test_patch_agent_soul_full_update | Route prefix mismatch |
| test_chunking_service.py | multiple | Chunking service import/assertion failures |
| test_docling_service.py | test_parse_document_from_bytes_uses_document_stream | Docling service issue |
| test_eval_routes.py | TestGetEvalRunResults::test_passed_flag_false_when_any_score_below_threshold | Eval routes issue |
| test_jobs_routes.py | multiple | Jobs routes prefix mismatch |
| test_jwt.py | test_validate_widget_jwt_tampered_token_raises_401 | JWT test issue |
| test_parse_task.py | multiple | Parse task issue |
| test_services.py | TestCreateNeonProject | Neon service tests |
| test_tenants_route.py | multiple | Tenants route prefix mismatch |

All of the above are pre-existing failures unrelated to M8 deployment work.
They should be investigated in a future `/gsd-debug` session.
