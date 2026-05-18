# Deferred Items — Phase 4.2

## Pre-existing Test Failures (Out of Scope)

### test_patch_agent_strips_empty_list_items
- **File:** apps/api/tests/unit/test_agents_patch.py
- **Status:** FAILING before plan 04.2-02 (confirmed by git stash + rerun)
- **Error:** 422 returned instead of 200 — `AgentSoulUpdate` field validator rejects `["valid item", "", "  ", "another item"]` because `soul_do_list` items have `min_length=1` constraint from `Annotated[str, Field(min_length=1, ...)]`
- **Root cause:** The empty string `""` violates `min_length=1`. The route strips empty strings, but Pydantic validation runs before the route body — so 422 is returned before the route can strip them. This is a design conflict in the existing schema.
- **When introduced:** Before Phase 4.2 (pre-existing)
- **Action:** Do not fix here — out of scope. Recommend fixing in Phase 4.1 retrospective or M5 planning.
