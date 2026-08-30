"""
Scenario services for W Chats M6 eval system.

Generates eval scenarios from tenant knowledge chunks via Claude Haiku (EVL-02)
and mines production conversations with Gatekeeper/Auditor failures into new
scenarios (EVL-03). Both write to the tenant DB eval_scenarios table.
"""

import json
import uuid

import psycopg2
import structlog
from sqlalchemy import text

from app.core.model_client import LedgerContext, ledger_recorder
from app.domain.eval_result import DATASET_EXPLORATORY, EVAL_DATASETS

log = structlog.get_logger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"


# WHY ALL FOUR WRITERS BELOW WRITE `exploratory` AND NONE WRITES `golden`.
# A model chose the question on every path in this module: Haiku generates them,
# the miner lifts them off flagged production traffic, and the provenance path
# files them from a promoted trace or a contained red-team finding. The golden
# set is the owner's own assertion, which #19 sizes at ten authored pairs before
# a calibration may run, and a golden set that filled itself from generation
# would be the old random sample wearing a stable name. No writer in the tree
# designates a golden row today; the corpus ticket is where that path lands.


class InvalidScenario(ValueError):
    """A scenario row that would land without a dataset, refused before any INSERT.

    A ValueError, so a caller already catching ValueError keeps catching it, the
    same choice `InvalidEvalResult` and `InvalidModelCall` made.
    """


def _scenario_dataset(scenario: dict) -> str:
    """Which dataset this row joins, refusing a row that does not say.

    #27: every one of the twenty rows `generate_eval_suite_for_agent` wrote
    carried `dataset = NULL`, both readers folded NULL to exploratory, and the
    golden set was therefore 0 rows by construction while `summarise_run_validity`
    reported `golden 0/0/0`. Nothing failed. The column was nullable and every
    writer left it out, so the defect was invisible until a run reported a
    calibration figure over a golden set nobody had ever populated.

    The refusal is here rather than at the reader because a reader can only ever
    guess, and `dataset_of`'s guess is the one that hid this for three weeks.

    Args:
        scenario: one scenario dict on its way to `eval_scenarios`.

    Returns:
        The row's dataset, one of EVAL_DATASETS.

    Raises:
        InvalidScenario: the row names no dataset, or names one nobody reports.
    """
    value = scenario.get("dataset")
    if value not in EVAL_DATASETS:
        raise InvalidScenario(
            f"a scenario must name its dataset, one of {', '.join(EVAL_DATASETS)}, "
            f"got {value!r}. A row that does not say joins the exploratory half by "
            "default, which is how the golden set comes to be empty and measured."
        )
    return value

# ---------------------------------------------------------------------------
# SCENARIO_TOOL — forced structured output via tool_choice (D-12 / RESEARCH §5)
# ---------------------------------------------------------------------------

SCENARIO_TOOL = {
    "name": "submit_scenarios",
    "description": "Submit generated eval scenarios as structured JSON.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scenarios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "reference_answer": {"type": "string"},
                        "scenario_category": {
                            "type": "string",
                            "enum": ["factual", "edge_case", "out_of_scope", "multi_step"],
                        },
                    },
                    "required": ["question", "reference_answer", "scenario_category"],
                },
                "minItems": 3,
                "maxItems": 10,
            }
        },
        "required": ["scenarios"],
    },
}


# ---------------------------------------------------------------------------
# Task 1: Scenario generator (EVL-02) — Claude API direct, Haiku, D-12/D-13
# ---------------------------------------------------------------------------


def generate_scenarios_from_chunks(chunks: list[dict], ledger: LedgerContext, n: int = 5) -> list[dict]:
    """Generate n eval scenarios from a batch of tenant knowledge chunks using Claude Haiku.

    Uses the direct API (NOT Agent SDK, D-12 LOCKED) with forced tool_choice
    structured output matching validation_service.py. Generated scenarios are
    tagged source='generated' (D-13 LOCKED), and the client comes from
    `app.core.model_client` (#47) so each call leaves a `model_calls` row.

    Args:
        chunks: List of dicts, each with at least a 'content' key (chunk text).
        ledger: the ids this generation is billed to and where its rows go.
        n: Number of scenarios to generate (clamped to 3–10 by SCENARIO_TOOL schema).

    Returns:
        List of dicts with keys question, reference_answer, scenario_category,
        retrieved_contexts, source='generated' and dataset='exploratory'.

    Raises:
        ValueError: If no tool_use block is returned by the scenario generator.
    """
    chunk_text = "\n\n---\n\n".join(f"CHUNK {i + 1}:\n{c['content']}" for i, c in enumerate(chunks[:5]))
    response = ledger.client("scenario_generation").messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        system=(
            "You are an evaluation scenario generator. Given business knowledge base content, "
            "generate realistic customer service questions a user might ask, along with reference "
            "answers grounded in the provided content. Generate exactly the number requested. "
            "Call submit_scenarios with your output."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Generate {n} evaluation scenarios from this knowledge base content.\n\n"
                    "KNOWLEDGE BASE CONTENT:\n"
                    f"{chunk_text}\n\n"
                    "For each scenario: write a realistic user question, the correct reference "
                    "answer based ONLY on the provided content, and classify the scenario category."
                ),
            }
        ],
        tools=[SCENARIO_TOOL],  # type: ignore[call-overload] # anthropic/agent-sdk stubs are narrower than the runtime contract
        tool_choice={"type": "tool", "name": "submit_scenarios"},
        # Forced tool_choice 400s on the DeepSeek endpoint unless thinking is off.
        thinking={"type": "disabled"},
    )

    chunk_contents = [c["content"] for c in chunks[:5]]

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_scenarios":
            raw_scenarios = block.input["scenarios"]
            return [
                {
                    "question": s["question"],
                    "reference_answer": s["reference_answer"],
                    "scenario_category": s["scenario_category"],
                    "retrieved_contexts": chunk_contents,
                    "source": "generated",
                    "dataset": DATASET_EXPLORATORY,
                }
                for s in raw_scenarios
            ]

    raise ValueError("No tool_use block returned by scenario generator")


def store_scenarios(scenarios: list[dict], tenant_conn_str: str) -> int:
    """Insert eval scenarios into the tenant DB eval_scenarios table.

    Idempotent via ON CONFLICT DO NOTHING. Each scenario is assigned a new UUID
    if no id is provided. The retrieved_contexts list is stored as JSONB.

    Args:
        scenarios: List of scenario dicts (from generate_scenarios_from_chunks or
                   mine_production_scenarios). Each must have: question,
                   reference_answer, source, dataset. Optional: scenario_category,
                   retrieved_contexts, id.
        tenant_conn_str: Decrypted Neon connection string for the tenant DB.

    Returns:
        Number of rows actually inserted (ON CONFLICT rows are not counted).

    Raises:
        InvalidScenario: any row names no dataset. Every row is checked BEFORE
            the connection opens, so a batch carrying one such row writes
            nothing rather than half of itself (#27).
    """
    if not scenarios:
        return 0

    datasets = [_scenario_dataset(s) for s in scenarios]

    conn = psycopg2.connect(tenant_conn_str, connect_timeout=5)
    inserted = 0
    try:
        with conn.cursor() as cur:
            for s, dataset in zip(scenarios, datasets, strict=True):
                cur.execute(
                    """
                    INSERT INTO eval_scenarios
                      (id, source, question, reference_answer, retrieved_contexts,
                       scenario_category, dataset, created_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        str(s.get("id") or uuid.uuid4()),
                        s["source"],
                        s["question"],
                        s.get("reference_answer", ""),
                        json.dumps(s.get("retrieved_contexts", [])),
                        s.get("scenario_category"),
                        dataset,
                    ),
                )
                inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()

    return inserted


def generate_eval_suite_for_agent(
    agent_id: str, tenant_id: str, tenant_conn_str: str, num_scenarios: int = 20
) -> int:
    """Generate an eval scenario suite for an agent from its tenant knowledge chunks.

    Fetches the most recent 100 chunks from the tenant DB, batches them into groups
    of up to 5, calls generate_scenarios_from_chunks per batch, and stores the
    results via store_scenarios. They carry source='generated' (D-13 LOCKED), and
    it continues until num_scenarios accumulate or the batches run out.

    Args:
        agent_id: UUID string of the agent, for logging and on each ledger row.
        tenant_id: UUID string of the tenant billed for the generation calls.
        tenant_conn_str: Decrypted Neon connection string for the tenant DB.
        num_scenarios: Target number of scenarios to generate (default 20).

    Returns:
        Total number of scenarios inserted into eval_scenarios.
    """
    # Step 1: Fetch up to 100 recent chunks from tenant DB
    conn = psycopg2.connect(tenant_conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM chunks ORDER BY created_at DESC LIMIT 100"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    chunks = [{"content": row[0]} for row in rows]

    if not chunks:
        log.info("scenario_service.no_chunks", agent_id=agent_id)
        return 0

    # Step 2: Compute batch size
    batch_size = min(5, max(1, len(chunks) // max(1, num_scenarios // 5)))

    all_scenarios: list[dict] = []
    ledger = LedgerContext(tenant_id=tenant_id, agent_id=agent_id, recorder=ledger_recorder(tenant_conn_str))

    # Step 3: Process in batches until num_scenarios reached
    for start in range(0, len(chunks), batch_size):
        if len(all_scenarios) >= num_scenarios:
            break
        batch = chunks[start : start + batch_size]
        try:
            new_scenarios = generate_scenarios_from_chunks(batch, ledger, n=5)
            all_scenarios.extend(new_scenarios)
        except Exception as exc:
            log.warning(
                "scenario_service.batch_failed",
                agent_id=agent_id,
                batch_start=start,
                error=str(exc),
            )

    # Trim to num_scenarios
    all_scenarios = all_scenarios[:num_scenarios]

    # Step 4: Persist to tenant DB
    total = store_scenarios(all_scenarios, tenant_conn_str)

    log.info("scenario_service.suite_generated", agent_id=agent_id, count=total)
    return total


# ---------------------------------------------------------------------------
# OPS-11/OPS-14: shared provenance-scenario insert path
# ---------------------------------------------------------------------------


def insert_provenance_scenario(
    conn,
    source: str,
    question: str,
    reference_answer: str,
    retrieved_contexts: list,
    provenance: str | None,
    origin_trace_id: str | None,
) -> str:
    """Insert one eval_scenarios row carrying provenance metadata.

    Shared insertion path for both the production promote flow (OPS-11,
    app.worker.tasks.runtime.bench.promote_trace_to_scenario) and the
    red-team finding-file flow (OPS-14, 21-08) — both callers pass an
    already-open psycopg2 connection so the caller controls the transaction
    (e.g. wrapping an idempotency pre-check + this insert in one commit).

    Requires migration 0011 (widened source CHECK + provenance/origin_trace_id
    columns) to already be applied — inserting source='production' or
    source='red_team' before that migration raises psycopg2.errors.CheckViolation
    (21-RESEARCH.md Pitfall 2).

    Args:
        conn: An open psycopg2 connection. This function does NOT commit or
            close it — the caller owns the transaction lifecycle.
        source: One of 'generated' | 'mined' | 'production' | 'red_team'.
        question: The scenario's question text.
        reference_answer: The scenario's reference answer text.
        retrieved_contexts: List of context strings/dicts (stored as JSONB).
        provenance: Human-readable origin tag (e.g. the trace_id or finding_id
            that produced this scenario).
        origin_trace_id: Structured trace/job id — used by callers for the
            idempotency pre-check on repeat promotion/file attempts.

    Returns:
        The new scenario's UUID (str). The row joins the exploratory dataset.
    """
    scenario_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eval_scenarios
              (id, source, question, reference_answer, retrieved_contexts,
               provenance, origin_trace_id, dataset, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, NOW())
            """,
            (
                scenario_id,
                source,
                question,
                reference_answer,
                json.dumps(retrieved_contexts),
                provenance,
                origin_trace_id,
                DATASET_EXPLORATORY,
            ),
        )
    return scenario_id


# ---------------------------------------------------------------------------
# Task 2: Production conversation miner (EVL-03) — D-15/D-16
# ---------------------------------------------------------------------------


def _fetch_messages_for_conversation(
    tenant_conn_str: str,
    conversation_id: str,
) -> list[dict]:
    """Fetch all messages for a conversation from the tenant DB.

    Args:
        tenant_conn_str: Decrypted Neon connection string for the tenant DB.
        conversation_id: UUID string of the conversation to fetch.

    Returns:
        List of dicts with 'role' and 'content' keys, ordered by created_at ASC.
    """
    conn = psycopg2.connect(tenant_conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content FROM messages
                WHERE conversation_id = %(conv_id)s::uuid
                ORDER BY created_at ASC
                """,
                {"conv_id": conversation_id},
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [{"role": row[0], "content": row[1]} for row in rows]


def mine_production_scenarios(
    agent_id: str,
    tenant_conn_str: str,
    control_db,
    lookback_hours: int = 168,
) -> list[dict]:
    """Mine production conversations with Gatekeeper/Auditor failures into eval scenarios.

    Implements the cross-DB join strategy (RESEARCH §6):
      Step 1: Query the control DB job_events for flagged validation events
              (gatekeeper.complete / auditor.complete with fail/ungrounded/partial
              verdict) for the given agent_id within the lookback window.
              Extract distinct job_ids and the question from the payload.
      Step 2: neither conversation_id nor question reaches the job_events
              payload. validators.py takes both as Celery task args and emits
              verdict.model_dump() plus agent_id, so this step recovers the
              question through the job's conversation linkage and drops any
              flagged job whose conversation it cannot reach.

    Note: Mined scenarios have reference_answer='' because there is no ground truth
    for production failures. The run_eval_suite task filters scenarios with empty
    reference_answer before building the Ragas EvaluationDataset.

    Mined scenarios have source='mined' (D-16 LOCKED).

    Args:
        agent_id: UUID string of the agent.
        tenant_conn_str: Decrypted Neon connection string for the tenant DB.
        control_db: A sync SQLAlchemy Session on the control DB (get_sync_db()).
        lookback_hours: Hours to look back for flagged events (default 168 = 7 days).

    Returns:
        List of mined scenario dicts, source='mined' and dataset='exploratory'.
    """
    # Step 1: Query control DB for flagged validation events for this agent
    # The job_events payload (from validators.py emit calls) contains:
    #   - verdict, confidence, reason, agent_id (from verdict.model_dump() + agent_id)
    # NOTE: conversation_id and question are NOT in the emit payload — they are
    # Celery task args only. We correlate via job_id and attempt to fetch the
    # conversation from the tenant DB using the job's linked conversation.
    flagged_rows = control_db.execute(
        text("""
            SELECT DISTINCT
                je.job_id            AS job_id,
                je.payload->>'verdict' AS verdict
            FROM job_events je
            WHERE je.event_type IN ('gatekeeper.complete', 'auditor.complete')
              AND je.payload->>'agent_id' = :agent_id
              AND je.payload->>'verdict' IN ('fail', 'ungrounded', 'partial')
              AND je.created_at > NOW() - make_interval(hours => :hours)
        """),
        {"agent_id": agent_id, "hours": lookback_hours},
    ).fetchall()

    if not flagged_rows:
        log.info(
            "scenario_service.mined",
            agent_id=agent_id,
            count=0,
            reason="no_flagged_events",
        )
        return []

    # Step 2: For each flagged job, attempt to get conversation context from tenant DB
    # The jobs table in the control DB may link job_id → conversation_id
    mined: list[dict] = []
    seen_job_ids: set[str] = set()

    for row in flagged_rows:
        job_id = str(row.job_id)
        if job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)

        # Try to get conversation_id from jobs table in control DB
        job_row = control_db.execute(
            text(
                "SELECT conversation_id FROM jobs WHERE id = :job_id LIMIT 1"
            ),
            {"job_id": job_id},
        ).fetchone()

        conversation_id = None
        if job_row and getattr(job_row, "conversation_id", None):
            conversation_id = str(job_row.conversation_id)

        if conversation_id:
            # Step 2b: Fetch actual messages from tenant DB
            messages = _fetch_messages_for_conversation(tenant_conn_str, conversation_id)
            # Extract the user turn as the question
            user_messages = [m["content"] for m in messages if m["role"] == "user"]
            question = user_messages[0] if user_messages else ""
        else:
            # No conversation linkage available — use job_id as a proxy key
            # but we cannot reconstruct the question without the messages table
            question = ""

        if not question:
            # Skip scenarios where we cannot recover a meaningful question
            continue

        mined.append(
            {
                "question": question,
                "reference_answer": "",  # honest about missing ground truth (D-16)
                "retrieved_contexts": [],
                "source": "mined",
                "scenario_category": "production_failure",
                "dataset": DATASET_EXPLORATORY,
            }
        )

    log.info("scenario_service.mined", agent_id=agent_id, count=len(mined))
    return mined
