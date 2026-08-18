"""
capture_responses.py — Populate responses/ directory for the eval harness.

Iterates all 20 scenarios, calls the live agent per scenario's turns via
POST /widget/{agent_id}/chat + SSE drain, and writes:
  apps/api/tests/evals/responses/{scenario_id}.json

Format written:
  {
    "scenario_id": "S-001",
    "response_text": "...",
    "tool_calls_log": [
      {"tool_name": "retrieve", "input": {...}, "result": {...}},
      ...
    ]
  }

Required env vars:
  AGENT_BASE_URL  — e.g. http://localhost:8000  (default)
  AGENT_ID        — UUID of a provisioned, ingested agent
  API_KEY         — plaintext X-API-Key for the agent's tenant

Optional:
  CAPTURE_TIMEOUT — seconds to wait for agent.response SSE event (default 30)

Usage:
  AGENT_ID=<uuid> API_KEY=<key> python apps/api/tests/evals/capture_responses.py

Security (T-04-07-01): Requires AGENT_E2E_ENABLED=1 — same guard as E2E tests.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tests.evals import validate_corpus  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCENARIOS_DIR = pathlib.Path(__file__).parent / "scenarios"
RESPONSES_DIR = pathlib.Path(__file__).parent / "responses"

AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8000")
AGENT_ID = os.getenv("AGENT_ID", "")
API_KEY = os.getenv("API_KEY", "")
# 300, not 30. The 2026-08-17 run measured a 101s turn on S-012 (the prompt
# extraction attack) and had to be re-captured once the window was raised, and
# the whole run is live agent turns against a cold tenant. A default that is
# known to be under the observed worst case turns every slow adversarial turn
# into a re-run, and a re-run of this script costs real money.
CAPTURE_TIMEOUT = int(os.getenv("CAPTURE_TIMEOUT", "300"))


# ---------------------------------------------------------------------------
# SSE parser (stdlib only — no httpx dependency for capture script)
# ---------------------------------------------------------------------------

def _parse_sse_line(line: str) -> tuple[str | None, str | None]:
    """Parse a single SSE line into (field, value)."""
    if ":" not in line:
        return None, None
    field, _, value = line.partition(":")
    return field.strip(), value.strip()


def _call_chat_and_drain_sse(
    agent_id: str,
    api_key: str,
    message: str,
    conversation_id: str | None,
    base_url: str,
    timeout: int,
) -> dict:
    """POST to /widget/{agent_id}/chat and drain SSE until agent.response event.

    Returns dict with keys:
      response_text: str
      tool_calls_log: list[dict]
      conversation_id: str | None
      job_id: str | None
    """
    # Step 1: POST /widget/{agent_id}/chat to get job_id
    chat_url = f"{base_url}/widget/{agent_id}/chat"
    body = json.dumps({
        "message": message,
        "conversation_id": conversation_id,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(chat_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chat_resp = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"POST /widget/{agent_id}/chat failed: {e.code} {e.reason}") from e

    job_id = chat_resp.get("job_id")
    returned_conv_id = chat_resp.get("conversation_id")

    if not job_id:
        raise RuntimeError(f"No job_id in chat response: {chat_resp}")

    # Step 2: Stream SSE from /widget/jobs/{job_id}/events
    sse_url = f"{base_url}/widget/jobs/{job_id}/events"
    sse_req = urllib.request.Request(sse_url, headers={"Accept": "text/event-stream"})

    response_text = ""
    tool_calls_log: list[dict] = []
    deadline = time.monotonic() + timeout

    try:
        with urllib.request.urlopen(sse_req, timeout=timeout) as sse_resp:
            current_event: str | None = None
            data_parts: list[str] = []

            for raw_line in sse_resp:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"SSE drain timed out after {timeout}s for job {job_id}")

                line = raw_line.decode("utf-8").rstrip("\r\n")

                if not line:
                    # Empty line = dispatch event
                    if current_event and data_parts:
                        data_str = "\n".join(data_parts)
                        try:
                            payload = json.loads(data_str)
                        except json.JSONDecodeError:
                            payload = {"raw": data_str}

                        if current_event == "agent.response":
                            response_text = payload.get("text", "")
                            returned_conv_id = payload.get("conversation_id", returned_conv_id)
                            break  # got what we need

                        elif current_event == "agent.tool_call":
                            tool_calls_log.append({
                                # BACKLOG 7.29, second finding: this read
                                # `payload.get("tool", "")` and the emitter sends
                                # `tool_name` (agent.py:1290), so every captured
                                # row carried `tool_name: ""`. Not BACKLOG 5.9
                                # returning — that defect was on the emitting
                                # side and stays fixed. A grounding judge joins
                                # on this name, so an empty one joins to nothing.
                                "tool_name": payload.get("tool_name", ""),
                                "input": payload.get("input", {}),
                                "result": {},  # result not in SSE; filled from tool log if available
                            })

                    current_event = None
                    data_parts = []
                else:
                    field, value = _parse_sse_line(line)
                    if field == "event":
                        current_event = value
                    elif field == "data" and value is not None:
                        data_parts.append(value)

    except TimeoutError:
        raise

    return {
        "response_text": response_text,
        "tool_calls_log": tool_calls_log,
        "conversation_id": returned_conv_id,
        "job_id": job_id,
    }


# ---------------------------------------------------------------------------
# Retrieved chunks (BACKLOG 7.34)
#
# The SSE stream cannot carry these. `agent.tool_result` emits `summary[:200]`,
# a repr, and that stream is the CUSTOMER's, so widening it would ship corpus
# content to a browser. The chunks are written to `tool_calls.retrieved_chunks`
# by the worker instead, and read back here.
#
# Without them `grounding_fidelity` cannot return PASS: its rubric asks whether
# a claim is traceable to a chunk "provided in the tool_calls log", so an absent
# chunk makes FAIL the only reachable verdict, whatever the answer said.
# ---------------------------------------------------------------------------

#: Read from the control DB, decrypted per agent. Unset means the merge is
#: skipped and the corpus is captured BLIND, which validate_corpus.py then says.
CONTROL_DB_SYNC_URL = os.getenv("CONTROL_DB_SYNC_URL", "")

_LAST_TURN_TOOL_CALLS = """
    WITH last_turn AS (
        SELECT id FROM messages
        WHERE conversation_id = %s AND role = 'assistant'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    )
    SELECT tc.tool_name, tc.arguments, tc.retrieved_chunks
    FROM tool_calls tc
    JOIN last_turn lt ON tc.message_id = lt.id
    ORDER BY tc.created_at, tc.id
"""


def shape_tool_call(tool_name, arguments, retrieved_chunks) -> dict:
    """One tool_calls row as the corpus records it.

    Separate from the query so the NULL-versus-empty rule is testable on a
    machine with no PostgreSQL, which is every machine this project runs on.

    NULL becomes an ABSENT result, which validate_corpus.py reports BLIND: the
    call retrieves nothing, or its capture could not be decoded, and neither is
    evidence. `[]` becomes `{"chunks": []}`, which is present and empty: a
    retrieve ran and the corpus matched nothing. A judge shown the second knows
    the corpus was searched; a judge shown the first knows nothing at all, and
    BACKLOG 5.16 is the cost of letting it mistake one for the other.
    """
    call = {"tool_name": tool_name or "", "input": arguments or {}, "result": {}}
    if retrieved_chunks is not None:
        call["result"] = {"chunks": retrieved_chunks}
    return call


def _tenant_conn_str(agent_id: str) -> str | None:
    """Decrypt this agent's tenant connection string from the control DB."""
    import psycopg2

    from app.core.security import fernet_decrypt

    with psycopg2.connect(CONTROL_DB_SYNC_URL, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT neon_connection_string FROM agents WHERE id = %s AND deleted_at IS NULL",
                (agent_id,),
            )
            row = cur.fetchone()
    if not row or not row[0]:
        return None
    return fernet_decrypt(bytes(row[0]))


def fetch_tool_calls(agent_id: str, conversation_id: str) -> list[dict] | None:
    """The last assistant turn's tool calls, from the tenant DB. None if unavailable.

    Rebuilt from the DB rather than merged onto the SSE-derived log, because the
    DB row is what the worker actually recorded: the tool NAME comes from the
    same write, so a capture-side naming defect cannot survive here the way
    `payload.get("tool", "")` did.

    `retrieved_chunks` IS NULL and `= []` mean different things and stay
    different: NULL becomes an absent `result`, which validate_corpus.py reports
    BLIND, and `[]` becomes `{"chunks": []}`, a retrieve that ran and matched
    nothing.
    """
    import psycopg2

    conn_str = _tenant_conn_str(agent_id)
    if not conn_str:
        return None
    with psycopg2.connect(conn_str, connect_timeout=60) as conn:
        with conn.cursor() as cur:
            cur.execute(_LAST_TURN_TOOL_CALLS, (conversation_id,))
            rows = cur.fetchall()

    return [shape_tool_call(*row) for row in rows]


# ---------------------------------------------------------------------------
# Widget JWT fetch (widget path requires Bearer token)
# ---------------------------------------------------------------------------

def _get_widget_jwt(agent_id: str, api_key: str, base_url: str) -> str:
    """GET /widget/{agent_id}/config to obtain a widget JWT for Bearer auth."""
    config_url = f"{base_url}/widget/{agent_id}/config"
    req = urllib.request.Request(
        config_url,
        headers={"X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            config = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GET /widget/{agent_id}/config failed: {e.code} {e.reason}") from e

    jwt = config.get("jwt") or config.get("token") or config.get("access_token")
    if not jwt:
        raise RuntimeError(f"No JWT in widget config response: {config}")
    return str(jwt)


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------

def capture_all(
    agent_id: str,
    api_key: str,
    base_url: str = AGENT_BASE_URL,
    timeout: int = CAPTURE_TIMEOUT,
    overwrite: bool = False,
) -> dict[str, str]:
    """Capture responses for all 20 scenarios.

    Returns dict mapping scenario_id → outcome ("written" | "skipped" | "error: ...").
    """
    if not agent_id:
        raise ValueError("AGENT_ID env var is required")
    if not api_key:
        raise ValueError("API_KEY env var is required")

    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    scenario_files = sorted(SCENARIOS_DIR.glob("S-*.json"))
    if len(scenario_files) != 20:
        raise ValueError(f"Expected 20 scenarios, found {len(scenario_files)} in {SCENARIOS_DIR}")

    # A widget JWT expires 900s after minting (widget.py:178). A 20-scenario
    # capture is live agent turns and runs well past that, so a single shared
    # token 401s partway through: observed 2026-08-17, 11 written then 9
    # consecutive "401 Unauthorized" from S-012 on. Mint per scenario instead;
    # the cost is one extra config call each, and the config route is the
    # cheapest endpoint in the API.
    print(f"Minting widget JWTs per scenario for agent {agent_id}...")
    if not CONTROL_DB_SYNC_URL:
        print(
            "  WARNING: CONTROL_DB_SYNC_URL is unset, so no retrieved chunks will be "
            "recorded and grounding_fidelity cannot pass on this corpus (BACKLOG 7.34)."
        )

    outcomes: dict[str, str] = {}

    for scenario_file in scenario_files:
        scenario = json.loads(scenario_file.read_text(encoding="utf-8"))
        sid = scenario["id"]
        out_path = RESPONSES_DIR / f"{sid}.json"

        if out_path.exists() and not overwrite:
            print(f"  SKIP {sid}: response file already exists (use --overwrite to replace)")
            outcomes[sid] = "skipped"
            continue

        turns = scenario.get("turns", [])
        if not turns:
            print(f"  SKIP {sid}: no turns defined")
            outcomes[sid] = "skipped"
            continue

        print(f"  Capturing {sid}: {scenario.get('description', '')[:60]}...")

        try:
            jwt = _get_widget_jwt(agent_id, api_key, base_url)
            conversation_id: str | None = None
            final_response: dict = {}

            # Multi-turn: call sequentially; last turn's response is captured
            for i, turn in enumerate(turns):
                if turn.get("role") != "user":
                    continue
                message = turn["message"]
                result = _call_chat_and_drain_sse(
                    agent_id=agent_id,
                    api_key=jwt,
                    message=message,
                    conversation_id=conversation_id,
                    base_url=base_url,
                    timeout=timeout,
                )
                conversation_id = result.get("conversation_id")
                final_response = result

            # BACKLOG 7.34: the DB copy wins when it is available, because it
            # is what the worker recorded and it is the only place the retrieved
            # chunks exist. The SSE-derived log is the fallback, and it carries
            # no chunks, so a run without a reachable DB is captured BLIND and
            # validate_corpus.py below says so rather than letting it pass.
            tool_calls_log = final_response.get("tool_calls_log", [])
            conv_id = final_response.get("conversation_id")
            if CONTROL_DB_SYNC_URL and conv_id:
                try:
                    from_db = fetch_tool_calls(agent_id, str(conv_id))
                    if from_db is not None:
                        tool_calls_log = from_db
                except Exception as exc:
                    print(f"    WARN {sid}: retrieved chunks unavailable ({exc})")

            record = {
                "scenario_id": sid,
                "response_text": final_response.get("response_text", ""),
                "tool_calls_log": tool_calls_log,
            }
            out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"    -> written to {out_path.name}")
            outcomes[sid] = "written"

        except Exception as exc:
            print(f"  ERROR {sid}: {exc}")
            outcomes[sid] = f"error: {exc}"

    return outcomes


def main() -> None:
    """CLI entry point."""
    if not os.getenv("AGENT_E2E_ENABLED"):
        print("ERROR: Set AGENT_E2E_ENABLED=1 to run response capture (requires live services + ANTHROPIC_API_KEY).")
        sys.exit(1)

    overwrite = "--overwrite" in sys.argv

    print("W Chats M4 — Eval Response Capture")
    print(f"Agent: {AGENT_ID or '(unset)'}")
    print(f"Base URL: {AGENT_BASE_URL}")
    print(f"Responses dir: {RESPONSES_DIR}")
    print(f"Overwrite: {overwrite}")
    print()

    try:
        outcomes = capture_all(
            agent_id=AGENT_ID,
            api_key=API_KEY,
            overwrite=overwrite,
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    written = sum(1 for v in outcomes.values() if v == "written")
    skipped = sum(1 for v in outcomes.values() if v == "skipped")
    errors = sum(1 for v in outcomes.values() if v.startswith("error"))

    print()
    print(f"Done: {written} written, {skipped} skipped, {errors} errors")

    if errors:
        sys.exit(1)

    # The capture VALIDATES ITSELF, because the alternative has been tried. The
    # E2E-6 set was declared clean by this script, sat for a day, and was found
    # to carry four PII deflections and twenty unnamed tool calls only when
    # someone opened the files to score them. A capture is live agent turns
    # against a live tenant, so the moment to learn it is contaminated is now,
    # while the services are still up, not at scoring time.
    print()
    exit_code = validate_corpus.report(validate_corpus.validate(RESPONSES_DIR))
    if exit_code != validate_corpus.EXIT_CLEAN:
        print()
        print(
            "The corpus above is NOT ready to score. Fix what is named, then re-run "
            "the affected scenarios by deleting their response files and running this "
            "script again WITHOUT --overwrite."
        )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
