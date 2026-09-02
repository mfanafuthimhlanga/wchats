"""
capture_responses.py — Populate responses/ directory for the eval harness.

Iterates all 20 scenarios, calls the live agent per scenario's turns via
POST /widget/{agent_id}/chat + SSE drain, and writes:
  apps/api/tests/evals/responses/{scenario_id}.json

Format written (BACKLOG 8.1 - k runs per scenario, position is the run index):
  {
    "scenario_id": "S-001",
    "runs": [
      {
        "response_text": "...",
        "tool_calls_log": [
          {"tool_name": "retrieve", "input": {...}, "result": {...}},
          ...
        ]
      },
      ...
    ]
  }

Run 0 is the run the human scores and the judge is calibrated against; the
calibrated judge then scores the rest. See tests/evals/corpus.py.

Required env vars:
  AGENT_BASE_URL  — e.g. http://localhost:8000  (default)
  AGENT_ID        — UUID of a provisioned, ingested agent
  API_KEY         — plaintext X-API-Key for the agent's tenant

Optional:
  CAPTURE_TIMEOUT — seconds to wait for agent.response SSE event (default 300)

Flags:
  --runs K     capture K runs per scenario (default 1). A scenario already
               holding K or more is skipped; one holding fewer is TOPPED UP, so
               a corpus never ends up at 5 for some scenarios and 1 for others.
  --overwrite  discard every recorded run and capture K fresh ones.

Usage:
  AGENT_ID=<uuid> API_KEY=<key> python apps/api/tests/evals/capture_responses.py --runs 5

COST. Every run is a live agent turn against a live tenant, so --runs 5 over
twenty scenarios is 100 turns. k=1 is the default for that reason, and it is
also the k at which pass@k and reliable@k are the same number and neither is
evidence of anything.

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

from tests.evals import corpus, validate_corpus  # noqa: E402

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

def capture_one_run(
    scenario: dict,
    agent_id: str,
    api_key: str,
    base_url: str,
    timeout: int,
) -> dict:
    """One independent run of a scenario: a fresh conversation, start to finish.

    BACKLOG 8.1. Each run starts with `conversation_id = None` and mints its own
    widget JWT. A run that continued the previous run's conversation would be
    turn k+1 of one long session rather than an independent attempt, and
    reliable@k over those measures the session rather than the agent. The JWT
    expires 900s after minting (widget.py:178), and k runs cross that window k
    times sooner than one did.
    """
    jwt = _get_widget_jwt(agent_id, api_key, base_url)
    conversation_id: str | None = None
    final_response: dict = {}

    for turn in scenario.get("turns", []):
        if turn.get("role") != "user":
            continue
        final_response = _call_chat_and_drain_sse(
            agent_id=agent_id,
            api_key=jwt,
            message=turn["message"],
            conversation_id=conversation_id,
            base_url=base_url,
            timeout=timeout,
        )
        conversation_id = final_response.get("conversation_id")

    # BACKLOG 7.34: the DB copy wins when it is available, because it is what the
    # worker recorded and it is the only place the retrieved chunks exist. The
    # SSE-derived log is the fallback, and it carries no chunks, so a run without
    # a reachable DB is captured BLIND and validate_corpus.py says so rather than
    # letting it pass.
    tool_calls_log = final_response.get("tool_calls_log", [])
    conv_id = final_response.get("conversation_id")
    if CONTROL_DB_SYNC_URL and conv_id:
        try:
            from_db = fetch_tool_calls(agent_id, str(conv_id))
            if from_db is not None:
                tool_calls_log = from_db
        except Exception as exc:
            print(f"    WARN {scenario['id']}: retrieved chunks unavailable ({exc})")

    return {
        "response_text": final_response.get("response_text", ""),
        "tool_calls_log": tool_calls_log,
    }


def capture_all(
    agent_id: str,
    api_key: str,
    base_url: str = AGENT_BASE_URL,
    timeout: int = CAPTURE_TIMEOUT,
    overwrite: bool = False,
    runs: int = 1,
) -> dict[str, str]:
    """Capture `runs` independent runs of all 20 scenarios.

    A scenario already holding `runs` or more is skipped; one holding fewer is
    TOPPED UP to `runs` rather than skipped on its file existing. The shipped
    behaviour skipped on existence, which under k > 1 means "captured at some k,
    possibly 1", so a --runs 5 pass over a k=1 tree left a corpus that was 5 for
    some scenarios and 1 for others, and a rate pooled over that is decided by
    the previous capture rather than by the agent.

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
    # consecutive "401 Unauthorized" from S-012 on. Mint per RUN instead (BACKLOG
    # 8.1 moved it down from per scenario, because k runs cross the window k
    # times sooner); the cost is one extra config call each, and the config route
    # is the cheapest endpoint in the API.
    print(f"Minting a widget JWT per run for agent {agent_id}, target k={runs}...")
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

        if not scenario.get("turns"):
            print(f"  SKIP {sid}: no turns defined")
            outcomes[sid] = "skipped"
            continue

        recorded: list[dict] = []
        if out_path.exists() and not overwrite:
            try:
                recorded = corpus.load_runs(out_path)
            except (json.JSONDecodeError, corpus.CorpusShapeError) as exc:
                print(f"  ERROR {sid}: recorded file unreadable ({exc}). Delete it, or --overwrite.")
                outcomes[sid] = f"error: unreadable record: {exc}"
                continue

        needed = corpus.runs_to_capture(len(recorded), runs, overwrite)
        if needed == 0:
            print(f"  SKIP {sid}: {len(recorded)} run(s) recorded, target is {runs}")
            outcomes[sid] = "skipped"
            continue

        print(
            f"  Capturing {sid}: {len(recorded)} of {runs} run(s) recorded, {needed} to go"
            f" - {scenario.get('description', '')[:50]}"
        )

        captured = [] if overwrite else list(recorded)
        failure: str | None = None
        for attempt in range(needed):
            try:
                captured.append(capture_one_run(scenario, agent_id, api_key, base_url, timeout))
                print(f"    run {len(captured) - 1} captured")
            except Exception as exc:
                # Partial results are WRITTEN, never discarded. Every run already
                # captured is a live agent turn that has been paid for, and the
                # next invocation tops the scenario up from where this one
                # stopped instead of re-running the turns that succeeded.
                print(f"  ERROR {sid} on run {len(captured)}: {exc}")
                failure = str(exc)
                break

        if captured:
            record = corpus.build_record(sid, captured)
            out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"    -> {len(captured)} run(s) in {out_path.name}")

        outcomes[sid] = f"error: {failure}" if failure else "written"

    return outcomes


def parse_runs(argv: list[str]) -> int:
    """Read `--runs K` off the command line. Defaults to 1.

    Refuses a missing, non-integer or non-positive K rather than falling back to
    1. A silent fallback would spend a full capture and produce a k=1 corpus
    while its operator believed they had asked for five, and the difference is
    invisible in the output until someone reads a rate off it.
    """
    if "--runs" not in argv:
        return 1
    index = argv.index("--runs")
    if index + 1 >= len(argv):
        raise ValueError("--runs needs a number, e.g. --runs 5")
    raw = argv[index + 1]
    try:
        runs = int(raw)
    except ValueError:
        raise ValueError(f"--runs takes an integer, got {raw!r}") from None
    if runs < 1:
        raise ValueError(f"--runs must be at least 1, got {runs}")
    return runs


def main() -> None:
    """CLI entry point."""
    if not os.getenv("AGENT_E2E_ENABLED"):
        print("ERROR: Set AGENT_E2E_ENABLED=1 to run response capture (requires live services + OPENAI_API_KEY).")
        sys.exit(1)

    overwrite = "--overwrite" in sys.argv
    try:
        runs = parse_runs(sys.argv)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print("W Chats M4 — Eval Response Capture")
    print(f"Agent: {AGENT_ID or '(unset)'}")
    print(f"Base URL: {AGENT_BASE_URL}")
    print(f"Responses dir: {RESPONSES_DIR}")
    print(f"Runs per scenario (k): {runs}")
    print(f"Overwrite: {overwrite}")
    if runs == 1:
        print(
            "  NOTE: at k=1 pass@k and reliable@k are the same number, so this corpus "
            "cannot separate a capability failure from a variance one (BACKLOG 8.1)."
        )
    print()

    try:
        outcomes = capture_all(
            agent_id=AGENT_ID,
            api_key=API_KEY,
            overwrite=overwrite,
            runs=runs,
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
