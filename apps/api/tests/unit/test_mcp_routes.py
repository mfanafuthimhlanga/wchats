"""
Unit tests for the MCP endpoint (app/api/mcp.py, #56).

Protocol surface:
    1. tools/list returns the fifteen tools in deterministic order with cache hints
    2. GET and DELETE /mcp return 405 with Allow: POST
    3. No credential returns 401
    4. Unknown method returns HTTP 404 with JSON-RPC -32601
    5. A notification (no id) returns 202 with no body
    6. A non-JSON body returns 400 with -32700
    7. A foreign Origin header returns 403
    8. The modern header contract: MCP-Protocol-Version without Mcp-Method is
       400 with -32020; a fully-declared modern request succeeds

Tool wrapping (the route IS the behaviour — nothing here re-tests route logic):
    9.  tools/call replays the real route: a route 404 arrives as an isError
        tool result carrying status 404
    10. a successful tools/call carries the route's JSON as structuredContent
    11. the API key crosses to the route: the inner route authenticates the
        same key through the real argon2 path (no inner override), so dropping
        the forwarded header turns this red
    12. table invariants: legal unique names, decision #10's fourteen tools all
        present plus register_golden_scenarios, trigger tools name the poll
        loop, body-model fields are spread flat into the input schema
"""

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_credential_kind, get_current_tenant
from app.api.mcp import _POLL, TOOLS, get_mcp_tenant
from app.core.security import hash_api_key, hmac_key_prefix
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant

EXPECTED_TOOL_NAMES = [
    "create_agent",
    "get_agent",
    "update_soul",
    "upload_documents",
    "register_golden_scenarios",
    "get_job",
    "trigger_eval",
    "list_eval_runs",
    "get_eval_results",
    "trigger_red_team",
    "get_red_team_run",
    "run_checklist",
    "acknowledge_warning",
    "approve_deployment",
    "get_embed_snippet",
]

# Decision #10's fourteen, verbatim from the resolution comment.
DECISION_10_TOOLS = [
    "create_agent",
    "get_agent",
    "update_soul",
    "upload_documents",
    "get_job",
    "trigger_eval",
    "list_eval_runs",
    "get_eval_results",
    "trigger_red_team",
    "get_red_team_run",
    "run_checklist",
    "acknowledge_warning",
    "approve_deployment",
    "get_embed_snippet",
]


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _rpc(method: str, params: dict | None = None, rpc_id: int | None = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "method": method}
    if rpc_id is not None:
        body["id"] = rpc_id
    if params is not None:
        body["params"] = params
    return body


async def _post(payload, headers: dict | None = None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        if isinstance(payload, (bytes, str)):
            return await client.post(
                "/mcp",
                content=payload,
                headers={"content-type": "application/json", **(headers or {})},
            )
        return await client.post("/mcp", json=payload, headers=headers or {})


def _override_outer_auth(tenant: Tenant) -> None:
    app.dependency_overrides[get_mcp_tenant] = lambda: tenant


# ---------------------------------------------------------------------------
# 1. tools/list
# ---------------------------------------------------------------------------


class TestToolsList:
    async def test_lists_the_fifteen_tools_in_deterministic_order(self):
        tenant = _make_fake_tenant()
        _override_outer_auth(tenant)
        try:
            response = await _post(_rpc("tools/list"))
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        result = response.json()["result"]
        names = [t["name"] for t in result["tools"]]
        assert names == EXPECTED_TOOL_NAMES
        assert result["cacheScope"] == "private"
        assert result["ttlMs"] > 0
        assert result["resultType"] == "complete"


# ---------------------------------------------------------------------------
# 2-8. Protocol surface
# ---------------------------------------------------------------------------


class TestProtocolSurface:
    async def test_get_and_delete_are_405(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            get_response = await client.get("/mcp")
            delete_response = await client.delete("/mcp")
        assert get_response.status_code == 405
        assert get_response.headers["allow"] == "POST"
        assert delete_response.status_code == 405

    async def test_no_credential_is_401(self):
        app.dependency_overrides[get_async_db] = lambda: AsyncMock()
        try:
            response = await _post(_rpc("tools/list"))
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 401

    async def test_unknown_method_is_404_with_32601(self):
        _override_outer_auth(_make_fake_tenant())
        try:
            response = await _post(_rpc("bogus/method"))
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 404
        assert response.json()["error"]["code"] == -32601

    async def test_notification_is_202_with_no_body(self):
        _override_outer_auth(_make_fake_tenant())
        try:
            response = await _post(_rpc("notifications/initialized", rpc_id=None))
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 202
        assert response.content == b""

    async def test_non_json_body_is_400_with_parse_error(self):
        _override_outer_auth(_make_fake_tenant())
        try:
            response = await _post(b"{this is not json")
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32700

    async def test_foreign_origin_is_403(self):
        _override_outer_auth(_make_fake_tenant())
        try:
            response = await _post(
                _rpc("tools/list"), headers={"Origin": "https://evil.example"}
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 403

    async def test_modern_header_without_mcp_method_is_400_with_32020(self):
        _override_outer_auth(_make_fake_tenant())
        try:
            response = await _post(
                _rpc("tools/list"), headers={"MCP-Protocol-Version": "2026-07-28"}
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020

    async def test_fully_declared_modern_request_succeeds(self):
        _override_outer_auth(_make_fake_tenant())
        params = {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        }
        try:
            response = await _post(
                _rpc("tools/list", params=params),
                headers={
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/list",
                },
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 200
        assert len(response.json()["result"]["tools"]) == len(EXPECTED_TOOL_NAMES)


# ---------------------------------------------------------------------------
# 9-11. Tool wrapping
# ---------------------------------------------------------------------------


class TestToolWrapping:
    async def test_route_404_arrives_as_is_error_result(self):
        """trigger_eval on an unknown agent: the route's 404 becomes isError."""
        tenant = _make_fake_tenant()
        _override_outer_auth(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            response = await _post(
                _rpc(
                    "tools/call",
                    params={
                        "name": "trigger_eval",
                        "arguments": {"agent_id": str(uuid4())},
                    },
                )
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["isError"] is True
        payload = json.loads(result["content"][0]["text"])
        assert payload["status"] == 404

    async def test_successful_call_carries_route_json_as_structured_content(self):
        """register_golden_scenarios: the route's 201 JSON is the tool result."""
        tenant = _make_fake_tenant()
        agent = MagicMock(spec=Agent)
        agent.id = uuid4()
        agent.tenant_id = tenant.id
        agent.deleted_at = None
        agent.neon_connection_string = b"encrypted"

        _override_outer_auth(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_credential_kind] = lambda: "api_key"
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://x"),
                patch(
                    "app.api.v1.evals._register_golden_sync",
                    return_value=(2, [], 2),
                ),
            ):
                response = await _post(
                    _rpc(
                        "tools/call",
                        params={
                            "name": "register_golden_scenarios",
                            "arguments": {
                                "agent_id": str(agent.id),
                                "pairs": [
                                    {"question": "q1", "reference_answer": "a1"},
                                    {"question": "q2", "reference_answer": "a2"},
                                ],
                            },
                        },
                    )
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        result = response.json()["result"]
        assert result.get("isError") is not True
        assert result["structuredContent"] == {
            "registered": 2,
            "skipped_duplicates": [],
            "golden_total": 2,
        }

    async def test_the_key_is_forwarded_and_verified_by_the_route(self):
        """No inner auth override: the forwarded key must clear real argon2.

        The outer layer and the inner route both resolve the same mocked DB
        row, so the only way this reaches the route's 404 is the forwarded
        X-API-Key surviving the in-process hop and verifying. Dropping the
        header turns this into a 401 payload and the test red.
        """
        raw_key = "vrd_live_forward_proof"
        tenant = _make_fake_tenant()
        tenant.api_key_hash = hash_api_key(raw_key)
        tenant.api_key_prefix = hmac_key_prefix(raw_key)

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = tenant
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.get = AsyncMock(return_value=None)  # the route's agent lookup
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            response = await _post(
                _rpc(
                    "tools/call",
                    params={
                        "name": "trigger_eval",
                        "arguments": {"agent_id": str(uuid4())},
                    },
                ),
                headers={"Authorization": f"Bearer {raw_key}"},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["isError"] is True
        payload = json.loads(result["content"][0]["text"])
        assert payload["status"] == 404  # 401 here means the key never crossed


# ---------------------------------------------------------------------------
# 12. Table invariants
# ---------------------------------------------------------------------------


class TestToolTable:
    def test_fifteen_tools_with_legal_unique_names(self):
        names = [t.name for t in TOOLS]
        assert len(names) == 15
        assert len(set(names)) == 15
        for name in names:
            assert re.fullmatch(r"[A-Za-z0-9_.\-]{1,128}", name)

    def test_decision_10_tools_all_present_plus_golden_registration(self):
        names = set(t.name for t in TOOLS)
        for required in DECISION_10_TOOLS:
            assert required in names
        assert "register_golden_scenarios" in names

    def test_every_path_param_is_satisfiable(self):
        for tool in TOOLS:
            # raises KeyError if the template names a param not in path_params
            tool.path.format(**{p: "x" for p in tool.path_params})

    def test_trigger_tools_name_the_poll_loop(self):
        for name in ("create_agent", "upload_documents", "trigger_eval", "trigger_red_team", "run_checklist"):
            tool = next(t for t in TOOLS if t.name == name)
            assert _POLL in tool.description

    def test_body_model_fields_are_spread_flat(self):
        create_agent = next(t for t in TOOLS if t.name == "create_agent")
        properties = create_agent.input_schema["properties"]
        assert {"name", "soul", "role"} <= set(properties)

        golden = next(t for t in TOOLS if t.name == "register_golden_scenarios")
        assert {"agent_id", "pairs"} <= set(golden.input_schema["properties"])
        assert "agent_id" in golden.input_schema["required"]
