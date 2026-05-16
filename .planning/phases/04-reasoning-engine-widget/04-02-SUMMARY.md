---
phase: 04-reasoning-engine-widget
plan: "02"
subsystem: agent-services
tags: [agent-prompt, agent-tools, mcp, tdd, retrieval, allowlist, security]
dependency_graph:
  requires: [04-01]
  provides: [agent_prompt.build_system_prompt, agent_tools.build_tool_server]
  affects: [04-03-agent-task, 04-04-chat-api]
tech_stack:
  added: [claude-agent-sdk==0.1.81]
  patterns: [module-globals-for-solo-worker, tool-decorator, mcp-server-factory, allowlist-hard-block]
key_files:
  created:
    - apps/api/app/services/agent_prompt.py
    - apps/api/app/services/agent_tools.py
    - apps/api/tests/unit/test_agent_prompt.py
    - apps/api/tests/unit/test_agent_tools.py
  modified: []
decisions:
  - "FEW_SHOT_SUFFIX is a module-level constant in agent_prompt.py — not dynamic retrieval (deferred post-M6)"
  - "Module-level globals for tool state injection — safe for worker_pool=solo; ContextVar upgrade deferred"
  - "claude_agent_sdk monkeypatched in tests via sys.modules — SDK binary not required at test time"
  - "Content truncated at 2000 chars (MAX_CHUNK_TOKENS * 4) before returning to agent"
  - "_mark_conversation_escalated uses psycopg2 direct connect — matches project connection pattern"
metrics:
  duration: "~18 minutes"
  completed_date: "2026-05-16T16:14:00Z"
  tasks_completed: 2
  files_created: 4
---

# Phase 04 Plan 02: Agent Prompt + Tools Summary

## One-liner

System prompt assembler from structured soul fields plus four MCP tools (retrieve, lookup_structured, escalate_to_human, clarify) with G-04 allowlist hard block and build_tool_server factory.

## What Was Built

### Task 1: `build_system_prompt` (TDD)

**File:** `apps/api/app/services/agent_prompt.py`

**Signature:**
```python
def build_system_prompt(agent: Agent) -> str
```

**Default values when fields are None/empty:**
- `soul_role` → `"customer service representative"`
- `soul_voice` → `"helpful, professional, and concise"`
- `soul_do_list` empty → `"Answer questions accurately based on retrieved content"`
- `soul_donot_list` empty → `"Make up information not present in retrieved content"`

**Mandatory output guarantees:**
- `CITATIONS:` literal block with `Document:` and `Section:` format
- AI-disclosure sentence containing `"AI assistant"` (California SB-1001)
- `FEW_SHOT_SUFFIX` containing "Example of a correct response with citation" and "Example of correct escalation"
- Literal tokens `soul_role` and `soul_voice` do NOT appear in output

**Unit tests: 4 — all pass**
| Test | Description |
|------|-------------|
| `test_build_system_prompt_empty_soul` | Defaults applied, CITATIONS present, AI-disclosure present |
| `test_build_system_prompt_populated_soul` | Role/voice/do/donot values appear verbatim |
| `test_build_system_prompt_includes_few_shot` | FEW_SHOT_SUFFIX substrings present |
| `test_build_system_prompt_does_not_leak_field_names` | `soul_role` and `soul_voice` absent |

---

### Task 2: `agent_tools` four MCP tools + factory (TDD)

**File:** `apps/api/app/services/agent_tools.py`

**Module-level constants:**
```python
ALLOWED_LOOKUP_TABLES: frozenset[str] = frozenset({"chunks", "documents", "chunk_metadata"})
MAX_CHUNKS: int = 5
MAX_CHUNK_TOKENS: int = 500
```

**Module-level globals (worker_pool=solo injection):**
```python
_conn_str: str = ""
_agent_id: str = ""
_agent_name: str = ""
_strategy: RetrievalStrategy | None = None
_conversation_id: str = ""
_notify_fn = None
```

**Four `@tool`-decorated async functions:**

| Tool | Input Schema | Behavior |
|------|-------------|----------|
| `retrieve_tool` | `{query: str, filters: list}` | embed_query → rrf_fuse → rerank → top `MAX_CHUNKS` chunks, each content ≤ 2000 chars; `_citations` field per chunk |
| `lookup_structured_tool` | `{table: str, filters: dict}` | G-04 check → if not in allowlist: `is_error=True`, NO SQL; else psycopg2.connect + parameterised SELECT |
| `escalate_to_human_tool` | `{reason: str, context: str}` | `_mark_conversation_escalated` (jsonb_set UPDATE) + `_notify_fn(reason, context)` |
| `clarify_tool` | `{question: str}` | Returns `{"content": [{"type":"text","text": args["question"]}]}` |

**Factory:**
```python
def build_tool_server(
    conn_str: str,
    agent_id: str,
    agent_name: str,
    strategy: RetrievalStrategy,
    conversation_id: str,
    notify_fn,
) -> object:
```
Uses `global` to assign all six module variables, then returns:
```python
create_sdk_mcp_server(name="customer-tools", version="1.0.0", tools=[...four tools...])
```

**Unit tests: 7 — all pass**
| Test | Description |
|------|-------------|
| `test_lookup_structured_rejects_non_allowlist_table` | table="users" → is_error=True, psycopg2.connect NOT called |
| `test_lookup_structured_accepts_allowlist_table` | table="chunks" → psycopg2.connect called |
| `test_retrieve_truncates_to_max_chunks` | 20 input chunks → ≤5 returned, content ≤ 2000 chars |
| `test_escalate_calls_notify_fn` | notify_fn called once with reason/context |
| `test_clarify_returns_question_text` | question="Which size?" appears in response |
| `test_build_tool_server_sets_globals` | All 6 sentinels propagated to module globals |
| `test_allowed_lookup_tables_is_frozenset` | Exact set `{"chunks","documents","chunk_metadata"}` |

## Commits

| Hash | Message |
|------|---------|
| `fc9f454` | `feat(04-02): add agent_prompt.build_system_prompt with few-shot and citation footer` |
| `5b1aabd` | `feat(04-02): add agent_tools with four MCP tools, allowlist, and build_tool_server factory` |

## Deviations from Plan

None — plan executed exactly as written.

## TDD Gate Compliance

- RED commit: both test files written with import failures confirmed before implementation
- GREEN commit: implementation made all 11 tests pass
- No REFACTOR step needed — code was clean on first pass

## Known Stubs

None — all four tools are fully implemented. The `_citations` field in retrieve_tool uses `document_id` as `document_name` (since chunk dicts from retrieval_service don't carry a `document_name` field). This is a data shape limitation from M3's retrieval pipeline, not a code stub. The Wave 3 agent task will need to join document names if citation display requires them.

## Threat Surface Scan

No new network endpoints introduced. Security surface additions:
- `lookup_structured_tool` implements G-04 allowlist (ALLOWED_LOOKUP_TABLES) before any SQL — confirmed.
- `_mark_conversation_escalated` opens a direct psycopg2 connection using the tenant's decrypted conn_str — same pattern as retrieval_service.py; no new threat surface.
- Filter values in lookup_structured are passed as psycopg2 `%s` parameters, never f-string interpolated.

## Self-Check: PASSED

Files exist:
- `apps/api/app/services/agent_prompt.py` ✓
- `apps/api/app/services/agent_tools.py` ✓
- `apps/api/tests/unit/test_agent_prompt.py` ✓
- `apps/api/tests/unit/test_agent_tools.py` ✓

Commits exist:
- `fc9f454` ✓
- `5b1aabd` ✓

Test results: 11/11 passed (4 + 7)
