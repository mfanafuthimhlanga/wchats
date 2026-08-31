"""The MCP server: one stateless POST at /mcp (#56, ADR 0004).

Fifteen tools, each a one-to-one wrapper over an existing REST route: the
fourteen lifecycle operations decision #10 lists, plus golden registration.
The server carries no business logic. A tools/call builds the tool's REST
request and replays it against this same FastAPI app in-process, so the
route's own auth, ownership checks, validation and status codes are the
tool's behaviour, and the route tests cover it.

Protocol: revision 2026-07-28, stateless by design (no session id, no SSE,
no server-initiated requests). A legacy `initialize` is answered minimally so
an older client can still hand-shake; nothing stateful is minted for it. The
normative requirements implemented here are quoted in
`.dev/reference/260822-mcp-spec-requirements.md` on `research/mcp-spec`.

Auth (ADR 0004): the tenant API key as a static bearer header. The endpoint
accepts `Authorization: Bearer vrd_...` (what `claude mcp add --header`
sends) or `X-API-Key`, resolves the tenant through the same lookup every
route uses, and forwards the key as `X-API-Key` on the in-process call. The
key never leaves the process.
"""

import base64
import binascii
import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars as structlog_contextvars

from app.api.deps import get_current_tenant
from app.core.config import settings
from app.core.database import get_async_db
from app.models.tenant import Tenant
from app.schemas.agent import AgentCreate, AgentSoulUpdate
from app.schemas.deployment import AcknowledgeRequest, ApproveDeploymentRequest
from app.schemas.eval import GoldenScenariosRegisterRequest

router = APIRouter(tags=["mcp"])
log = structlog.get_logger(__name__)

PROTOCOL_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-06-18", "2025-11-25")
SERVER_INFO = {"name": "wchats", "version": "1.0.0"}
CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}

ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_HEADER_MISMATCH = -32020
META_PROTOCOL = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"

_POLL_JOB = " Returns a job id immediately; poll get_job until status is terminal."
_POLL_EVAL = " Returns immediately; poll list_eval_runs until the newest run is terminal."
_POLL_RED_TEAM = (
    " Returns immediately; poll list_red_team_runs until the newest run is terminal."
)
_POLL_CHECKLIST = (
    " Returns immediately; poll list_checklist_runs until the newest run is "
    "terminal. Its run id and warning ids feed acknowledge_warning and "
    "approve_deployment."
)

# ---------------------------------------------------------------------------
# The tool table. One row per tool, one existing route per row. This is the map.
# ---------------------------------------------------------------------------

_UUID_PARAM = {"type": "string", "description": "UUID"}

_UPLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent_id": _UUID_PARAM,
        "urls": {
            "type": "array",
            "items": {"type": "string"},
            "description": "URLs to fetch and ingest.",
        },
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content_base64": {"type": "string"},
                },
                "required": ["filename", "content_base64"],
                "additionalProperties": False,
            },
            "description": "Files to ingest, content base64-encoded.",
        },
    },
    "required": ["agent_id"],
    "additionalProperties": False,
}


def _merge_schema(
    path_params: tuple[str, ...], body_model: type[BaseModel] | None
) -> dict[str, Any]:
    """Path params plus the route's request schema, as one JSON Schema object.

    The body model's schema is spread at the top level so a tool call reads as
    one flat argument object; a name collision between a path param and a body
    field would silently shadow, so it fails at import instead.
    """
    properties: dict[str, Any] = {p: _UUID_PARAM for p in path_params}
    required = list(path_params)
    defs: dict[str, Any] = {}
    if body_model is not None:
        body = body_model.model_json_schema()
        overlap = set(properties) & set(body.get("properties", {}))
        if overlap:
            raise RuntimeError(f"tool schema collision on {sorted(overlap)}")
        properties.update(body.get("properties", {}))
        required.extend(body.get("required", []))
        defs = body.get("$defs", {})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    if defs:
        schema["$defs"] = defs
    return schema


class _Tool:
    """One tool row: the route it wraps and the schema callers see."""

    def __init__(
        self,
        name: str,
        description: str,
        method: str,
        path: str,
        path_params: tuple[str, ...],
        body_model: type[BaseModel] | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.method = method
        self.path = path
        self.path_params = path_params
        self.body_model = body_model
        self.input_schema = (
            input_schema
            if input_schema is not None
            else _merge_schema(path_params, body_model)
        )


TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "create_agent",
        "Create an Agent for the authenticated Tenant." + _POLL_JOB,
        "POST",
        "/api/v1/agents",
        (),
        AgentCreate,
    ),
    _Tool(
        "get_agent",
        "Read one Agent: status, soul, provisioning state.",
        "GET",
        "/api/v1/agents/{agent_id}",
        ("agent_id",),
    ),
    _Tool(
        "update_soul",
        "Update soul fields on an Agent; only the fields sent change.",
        "PATCH",
        "/api/v1/agents/{agent_id}",
        ("agent_id",),
        AgentSoulUpdate,
    ),
    _Tool(
        "upload_documents",
        "Upload documents and/or URLs into the Agent's Corpus through the "
        "real ingestion pipeline." + _POLL_JOB,
        "POST",
        "/api/v1/agents/{agent_id}/documents",
        ("agent_id",),
        input_schema=_UPLOAD_SCHEMA,
    ),
    _Tool(
        "register_golden_scenarios",
        "Register owner-authored golden pairs for an Agent. The golden set "
        "gates every deploy absolutely and needs at least ten pairs before "
        "ship is possible. Re-registering a known question is skipped, so "
        "re-running a file is safe.",
        "POST",
        "/api/v1/agents/{agent_id}/golden-scenarios",
        ("agent_id",),
        GoldenScenariosRegisterRequest,
    ),
    _Tool(
        "get_job",
        "Read one job with its recent events. Poll this until status is "
        "terminal (complete or failed).",
        "GET",
        "/api/v1/jobs/{job_id}",
        ("job_id",),
    ),
    _Tool(
        "trigger_eval",
        "Run the eval suite against an Agent." + _POLL_EVAL,
        "POST",
        "/api/v1/agents/{agent_id}/eval-runs/trigger",
        ("agent_id",),
    ),
    _Tool(
        "list_eval_runs",
        "List an Agent's eval runs with each run's stored record.",
        "GET",
        "/api/v1/agents/{agent_id}/eval-runs",
        ("agent_id",),
    ),
    _Tool(
        "get_eval_results",
        "Read per-scenario results for one eval run.",
        "GET",
        "/api/v1/agents/{agent_id}/eval-runs/{run_id}/results",
        ("agent_id", "run_id"),
    ),
    _Tool(
        "trigger_red_team",
        "Run the red-team programme against an Agent." + _POLL_RED_TEAM,
        "POST",
        "/api/v1/agents/{agent_id}/red-team-runs",
        ("agent_id",),
    ),
    _Tool(
        "list_red_team_runs",
        "List an Agent's red-team runs, newest first. The run ids here are the "
        "ones get_red_team_run reads; the id a trigger returns is its task, "
        "not the run.",
        "GET",
        "/api/v1/agents/{agent_id}/red-team-runs",
        ("agent_id",),
    ),
    _Tool(
        "get_red_team_run",
        "Read one red-team run with all findings and coverage.",
        "GET",
        "/api/v1/agents/{agent_id}/red-team-runs/{run_id}",
        ("agent_id", "run_id"),
    ),
    _Tool(
        "run_checklist",
        "Run the deployment checklist: it sequences eval and red team, then "
        "computes the Verdict." + _POLL_CHECKLIST,
        "POST",
        "/api/v1/agents/{agent_id}/checklist-runs",
        ("agent_id",),
    ),
    _Tool(
        "list_checklist_runs",
        "List an Agent's checklist runs, newest first, with each run's status, "
        "warnings and recommendation. The run ids here are the ones the "
        "approve and acknowledge steps take.",
        "GET",
        "/api/v1/agents/{agent_id}/checklist-runs",
        ("agent_id",),
    ),
    _Tool(
        "get_checklist_run",
        "Read one checklist run: status, gate results, warnings, Verdict.",
        "GET",
        "/api/v1/agents/{agent_id}/checklist-runs/{run_id}",
        ("agent_id", "run_id"),
    ),
    _Tool(
        "acknowledge_warning",
        "Acknowledge warnings on a completed checklist run. Call once per "
        "warning or with several warning_ids; each call is independent.",
        "POST",
        "/api/v1/agents/{agent_id}/checklist-runs/{run_id}/acknowledge",
        ("agent_id", "run_id"),
        AcknowledgeRequest,
    ),
    _Tool(
        "approve_deployment",
        "Approve deployment after a checklist run whose Verdict allows it. "
        "Arms the Agent's schedules and returns the embed snippet.",
        "POST",
        "/api/v1/agents/{agent_id}/approve-deployment",
        ("agent_id",),
        ApproveDeploymentRequest,
    ),
    _Tool(
        "get_embed_snippet",
        "Read the embed tag for an Agent, the same snippet approval returns.",
        "GET",
        "/api/v1/agents/{agent_id}/embed-snippet",
        ("agent_id",),
    ),
)

_TOOL_BY_NAME = {t.name: t for t in TOOLS}

_TOOLS_LISTING = [
    {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
    for t in TOOLS
]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _extract_api_key(request: Request) -> str | None:
    """The tenant key, from Authorization: Bearer or X-API-Key."""
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.headers.get("x-api-key")


async def get_mcp_tenant(
    request: Request, db: AsyncSession = Depends(get_async_db)
) -> Tenant:
    """Resolve the Tenant from the static key, via the same lookup every route uses.

    A bearer value here is always treated as a tenant API key, never as a Clerk
    session token: MCP is a machine surface (ADR 0004), and the routes it
    replays record credential_kind='api_key' accordingly.
    """
    api_key = _extract_api_key(request)
    if not api_key:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await get_current_tenant(request, None, api_key, db)


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------


class _ProtocolError(Exception):
    """A request the protocol layer refuses, with its HTTP status and RPC code."""

    def __init__(self, http_status: int, code: int, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message


def _rpc_error(rpc_id: Any, http_status: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": code, "message": message},
        },
    )


def _rpc_result(rpc_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": "2.0", "id": rpc_id, "result": result},
    )


def _check_origin(request: Request) -> None:
    """Servers MUST validate the Origin header; present and invalid is 403."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.CORS_ORIGINS:
        raise _ProtocolError(403, ERR_INVALID_REQUEST, "Origin not allowed")


def _check_modern_headers(request: Request, body: dict[str, Any]) -> None:
    """The 2026-07-28 header contract, applied when the client declares it.

    A request without MCP-Protocol-Version, or one declaring a legacy revision
    (which sends no Mcp-Method and no _meta), skips these checks; an unknown
    revision is refused rather than trusted with the modern contract.
    """
    header_version = request.headers.get("mcp-protocol-version")
    if header_version is None or header_version in LEGACY_VERSIONS:
        return
    if header_version != PROTOCOL_VERSION:
        raise _ProtocolError(
            400,
            ERR_HEADER_MISMATCH,
            f"unsupported protocol version {header_version!r}",
        )
    method = body.get("method")
    if request.headers.get("mcp-method") != method:
        raise _ProtocolError(
            400, ERR_HEADER_MISMATCH, "Mcp-Method header missing or mismatched"
        )
    meta = (body.get("params") or {}).get("_meta") or {}
    if META_PROTOCOL not in meta or META_CLIENT_CAPS not in meta:
        raise _ProtocolError(
            400,
            ERR_INVALID_PARAMS,
            f"_meta must carry {META_PROTOCOL} and {META_CLIENT_CAPS}",
        )
    if meta[META_PROTOCOL] != header_version:
        raise _ProtocolError(
            400, ERR_HEADER_MISMATCH, "MCP-Protocol-Version disagrees with _meta"
        )
    if method == "tools/call":
        name = (body.get("params") or {}).get("name")
        if request.headers.get("mcp-name") != name:
            raise _ProtocolError(
                400, ERR_HEADER_MISMATCH, "Mcp-Name header missing or mismatched"
            )


# ---------------------------------------------------------------------------
# tools/call: build the REST request, replay it in-process, map the response
# ---------------------------------------------------------------------------


def _text_result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "resultType": "complete",
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }
    if is_error:
        result["isError"] = True
    else:
        result["structuredContent"] = payload
    return result


def _build_upload(args: dict[str, Any]) -> dict[str, Any]:
    """httpx kwargs for the one multipart route. Raises ValueError on bad base64."""
    files = []
    for f in args.pop("files", []) or []:
        if not isinstance(f, dict) or "filename" not in f or "content_base64" not in f:
            raise ValueError("each files[] entry needs 'filename' and 'content_base64'")
        try:
            content = base64.b64decode(f["content_base64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                f"files[].content_base64 is not valid base64: {exc}"
            ) from exc
        files.append(("files", (f["filename"], content)))
    kwargs: dict[str, Any] = {"data": {"urls": args.pop("urls", []) or []}}
    if files:
        kwargs["files"] = files
    return kwargs


async def _call_tool(
    request: Request, tool: _Tool, arguments: dict[str, Any], api_key: str
) -> dict[str, Any]:
    """Replay one tool call against its route on this same app, in-process."""
    args = dict(arguments)
    try:
        path = tool.path.format(**{p: args.pop(p) for p in tool.path_params})
    except KeyError as exc:
        return _text_result(
            {"error": f"missing required argument {exc}"}, is_error=True
        )

    kwargs: dict[str, Any] = {}
    if tool.name == "upload_documents":
        try:
            kwargs = _build_upload(args)
            args = {}
        except ValueError as exc:
            return _text_result({"error": str(exc)}, is_error=True)
    elif tool.body_model is not None:
        kwargs = {"json": args}
        args = {}
    if args:
        return _text_result(
            {"error": f"unexpected arguments: {sorted(args)}"}, is_error=True
        )

    # raise_app_exceptions=False: a route that dies past FastAPI's handlers
    # comes back as its 500 response and maps to an isError result below,
    # instead of dropping the JSON-RPC contract on exactly the failure path.
    transport = httpx.ASGITransport(app=request.app, raise_app_exceptions=False)
    # The inner request's RequestIdMiddleware clears and rebinds the structlog
    # contextvars in this same task, so the outer request's binding is saved
    # and restored around the hop.
    saved_context = structlog_contextvars.get_contextvars()
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mcp.internal"
        ) as client:
            response = await client.request(
                tool.method, path, headers={"X-API-Key": api_key}, **kwargs
            )
    finally:
        structlog_contextvars.clear_contextvars()
        structlog_contextvars.bind_contextvars(**saved_context)

    try:
        payload: Any = response.json()
    except json.JSONDecodeError:
        payload = {"raw": response.text}
    if 200 <= response.status_code < 300:
        return _text_result(payload)
    return _text_result(
        {"status": response.status_code, "detail": payload}, is_error=True
    )


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


async def _dispatch(
    request: Request, body: dict[str, Any], rpc_id: Any, api_key: str
) -> JSONResponse:
    method = body.get("method")
    params = body.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return _rpc_result(
            rpc_id,
            {
                "protocolVersion": version,
                "capabilities": CAPABILITIES,
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _rpc_result(rpc_id, {"resultType": "complete"})
    if method == "server/discover":
        return _rpc_result(
            rpc_id,
            {
                "versions": [PROTOCOL_VERSION],
                "serverInfo": SERVER_INFO,
                "capabilities": CAPABILITIES,
                "resultType": "complete",
            },
        )
    if method == "tools/list":
        return _rpc_result(
            rpc_id,
            {
                "tools": _TOOLS_LISTING,
                "ttlMs": 3_600_000,
                "cacheScope": "private",
                "resultType": "complete",
            },
        )
    if method == "tools/call":
        name = params.get("name")
        tool = _TOOL_BY_NAME.get(name or "")
        if tool is None:
            return _rpc_error(rpc_id, 400, ERR_INVALID_PARAMS, f"unknown tool: {name!r}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(
                rpc_id, 400, ERR_INVALID_PARAMS, "arguments must be an object"
            )
        result = await _call_tool(request, tool, arguments, api_key)
        return _rpc_result(rpc_id, result)

    return _rpc_error(rpc_id, 404, ERR_METHOD_NOT_FOUND, f"unknown method: {method!r}")


@router.post("/mcp")
async def mcp_endpoint(
    request: Request, tenant: Tenant = Depends(get_mcp_tenant)
) -> Response:
    """One stateless endpoint; every MCP request is a POST here (ADR 0004)."""
    try:
        _check_origin(request)
        try:
            body = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise _ProtocolError(400, ERR_PARSE, "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise _ProtocolError(
                400, ERR_INVALID_REQUEST, "a single request object is required"
            )
    except _ProtocolError as exc:
        return _rpc_error(None, exc.http_status, exc.code, exc.message)

    rpc_id = body.get("id")
    if body.get("jsonrpc") != "2.0":
        return _rpc_error(rpc_id, 400, ERR_INVALID_REQUEST, "jsonrpc must be '2.0'")
    if "id" not in body:
        # A notification gets its 202 before the header contract is judged:
        # the spec's MUST on the 202 outranks a refusal nobody would read.
        return Response(status_code=202)

    try:
        _check_modern_headers(request, body)
    except _ProtocolError as exc:
        return _rpc_error(rpc_id, exc.http_status, exc.code, exc.message)

    api_key = _extract_api_key(request) or ""
    log.info("mcp.request", method=body.get("method"), tenant_id=str(tenant.id))
    return await _dispatch(request, body, rpc_id, api_key)


@router.get("/mcp")
@router.delete("/mcp")
async def mcp_method_not_allowed() -> Response:
    """GET and DELETE get 405: there is no stream to open and no session to end."""
    return Response(status_code=405, headers={"Allow": "POST"})
