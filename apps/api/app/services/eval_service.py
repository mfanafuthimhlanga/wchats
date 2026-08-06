"""Ragas 0.4.x eval harness for W Chats M6.

Measures Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall per scenario.

Where each write lands (the persistence split — measurement-layer audit D2)
---------------------------------------------------------------------------
D-10 says an eval must never mutate tenant data, and that is why a Neon branch
is created per run. The original execution read that as "everything the eval
touches goes to the branch", so `eval_results`, the terminal `eval_runs` status
and the verified_qa promotion were all written to a branch that the task then
deletes in `finally`. Production therefore never learned that a run finished:
every successful run left its `eval_runs` row at status='running' forever, and
`eval_results` never existed on production at all, which is why evals.py's LEFT
JOIN always yielded NULL metrics.

Results are OBSERVATIONS ABOUT a run, not tenant data. The split is now:

    eval_runs insert / status / eval_results -> PRODUCTION (`conn_str`)
    scoring (run_ragas_eval)                 -> no database at all

The second line is a correction to an earlier version of this docstring, which
claimed scoring ran "against the branch". It never did: run_ragas_eval builds
an EvaluationDataset out of rows that are already in memory and calls the judge
API, and the connection string it used to accept was never referenced anywhere
in its body. Saying "scoring runs against the branch" made a resource nothing
reads look load-bearing, which is how a Neon outage came to be able to abandon
an eval that needs no Neon branch. See EVAL_SCORING_REQUIRES_BRANCH below.

A run must end in a terminal state on production or it never happened.

Trust tiers and verified_qa promotion (D5 / the label hierarchy)
---------------------------------------------------------------
`verified_qa` rows are served to real customers by retrieval_service's
verified_qa_lookup BEFORE hybrid search, at 0.93 cosine similarity. Promotion
used to write any scenario clearing 0.90/0.90 — including a `source='generated'`
row whose "reference answer" was written by Haiku, and a `source='production'`
row whose answer is the one a human FLAGGED AS FAILING. Only the branch write
(above) stopped those rows reaching customers, by accident.

Promotion is therefore gated on the label trust hierarchy below and is
UNREACHABLE for every scenario source the shipped schema allows. That is a
deliberate disablement recorded on each run in `eval_runs.config`, not an
oversight: re-enabling it is a decision that needs human-verified labels behind
it, not a side effect of repairing persistence.

Design notes:
- All Ragas imports use the 0.4.x path (ragas.metrics.collections) — CLAUDE.md constraint.
- Dataset field is 'reference' (renamed from the old 0.3.x name in 0.4.x).
- LLM wrapper uses InstructorLLM(instructor.from_anthropic(Anthropic())) — 0.4.x collections requirement.
- All DB writes use psycopg2 try/finally/close pattern matching retrieval_service.py.
- verified_qa promotion writes source='sandbox_test', promoted_by='system' (D-22) —
  retained for the day a promotable trust tier exists; unreachable today.
- question_vector populated via Voyage embed at promotion time (D-23).
"""

from __future__ import annotations

import hashlib
import json
import uuid

import anthropic
import instructor
import psycopg2
import structlog
from ragas import EvaluationDataset, evaluate
from ragas.llms import InstructorLLM

# ---------------------------------------------------------------------------
# Ragas 0.4.x imports — D-01 LOCKED: exact import path
# Do NOT use the 0.3.x ragas.metrics path — it has been removed.
# ---------------------------------------------------------------------------
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)
from sqlalchemy import text as sa_text

from app.core.config import AGENT_TURN_MODEL, settings
from app.core.database import get_sync_db
from app.services.embedding_service import _get_vo

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HAIKU_MODEL = "claude-haiku-4-5"

# Note: Ragas 0.4.x collections metrics require an InstructorLLM at construction time.
# Metrics are therefore instantiated inside run_ragas_eval(), not at module level.
# The four metrics used are: Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall (D-04)


# ---------------------------------------------------------------------------
# What this harness measures — and what it does not (audit D1)
# ---------------------------------------------------------------------------
# Two properties of the shipped scoring half that every consumer of a score has
# to know. They are constants rather than prose so they can be stamped on the
# run record and pinned by a test, instead of being rediscovered by reading
# three files.
#
# 1. THE AGENT IS NEVER INVOKED. eval.py builds every sample with
#    agent_response = reference_answer, so Ragas scores the reference answer
#    against the contexts that reference answer was written from. Faithfulness
#    and AnswerRelevancy approach 1.0 by construction, and — the part that
#    matters for the configuration tuple — the score is INVARIANT to the
#    agent's model, prompt, retrieval configuration, capability envelope and
#    corpus. Recording those dimensions on a run without recording this makes
#    an uncomparable measurement look comparable: two runs differing only on
#    config.model_id would carry statistically identical scores and read as
#    "the model swap was quality-neutral". Fixing D1 is not this phase's scope;
#    hiding it would be worse than leaving it.
#
# 2. SCORING TOUCHES NO DATABASE. run_ragas_eval executes no statement against
#    anything — it takes rows already in memory and calls the judge API. The
#    Neon branch the caller creates per run (D-10) is therefore isolation held
#    IN RESERVE for the day this harness starts invoking retrieval or the agent
#    against tenant data, not isolation in use. The caller reads
#    EVAL_SCORING_REQUIRES_BRANCH to decide whether a branch it cannot create
#    is fatal; while it is False, abandoning a run over that branch would throw
#    away a night's measurement for a resource nothing reads.

EVAL_INVOKES_AGENT = False

# Where the text scored as the "response" comes from while D1 stands.
EVAL_SCORED_RESPONSE_SOURCE = "reference_answer"

# Dimensions of the run record — config keys plus the prompt_version_id column —
# that cannot influence a score while EVAL_INVOKES_AGENT is False. judge_model_id
# is deliberately NOT here: the judge does run, so a judge change does move the
# numbers.
AGENT_DEPENDENT_DIMENSIONS: list[str] = [
    "prompt_version_id",
    "model_id",
    "retrieval_config_hash",
    "envelope_hash",
    "corpus_chunk_count",
    "embedding_provider",
    "embedding_model_id",
]

# Does the scoring half execute any statement against a database? Flip this the
# day it does, and the caller's branch handling becomes strict again in the same
# edit — the branch is what stands between an agent-invoking eval and production
# tenant data.
EVAL_SCORING_REQUIRES_BRANCH = False


# libpq connect_timeout, in seconds, for every psycopg2 connection this module
# opens. It is not decoration: a Neon endpoint mid-suspend, or a black-holing
# network path, accepts the TCP connection and never completes the startup
# handshake, and an unbounded connect() then blocks forever. Nothing else in
# this system would interrupt it — celery_app.py sets neither task_time_limit
# nor soft_time_limit. The write that matters most is the one on the FAILURE
# path: run_eval_suite calls update_eval_run_status from inside its `except`,
# which runs BEFORE the `finally` that deletes the Neon eval branch, so a
# blocked connect there leaks a live copy of tenant data indefinitely and holds
# a runtime worker slot with it. Same value as the task's own reads.
CONNECT_TIMEOUT_S = 5


# ---------------------------------------------------------------------------
# Label trust hierarchy
# ---------------------------------------------------------------------------
# A label's authority is a property of WHO WROTE IT, not of how well it scores.
# Ranked, lowest authority first:
#
#   unknown          (-1) — an unrecognised provenance. Ranked BELOW model
#                           output on purpose: an unmapped source is a source
#                           nobody has reasoned about, so it fails closed.
#   model_generated   (0) — a model wrote the answer. Exploratory metrics only.
#                           Never gates a deploy, never served to a customer.
#   customer_negative (1) — a customer thumbs-down, a mined production failure,
#                           an owner-filed failing trace, a red-team finding.
#                           These label a NEGATIVE. They identify a question the
#                           agent got wrong; they never assert what the right
#                           answer is, so they can never become a served answer.
#   human_verified    (2) — a human read a candidate answer and confirmed it.
#   human_authored    (3) — a human wrote the answer.
#
# Nothing in the shipped system produces tier >= human_verified yet: there is no
# correction UI. That is precisely why VERIFIED_QA_MIN_TRUST_TIER is set there —
# promotion is unreachable BY CONSTRUCTION rather than by an `if False`, and it
# becomes reachable the moment a genuinely human-verified source exists, without
# anyone having to remember to remove a flag.
LABEL_TRUST_TIERS: dict[str, int] = {
    "unknown": -1,
    "model_generated": 0,
    "customer_negative": 1,
    "human_verified": 2,
    "human_authored": 3,
}

# eval_scenarios.source -> trust tier. The key set must stay in step with the
# widened CHECK constraint in alembic_tenant 0011
# (source IN ('generated', 'mined', 'production', 'red_team')); a new source
# value that lands without a tier here resolves to 'unknown' and is refused.
SCENARIO_SOURCE_TRUST_TIER: dict[str, str] = {
    # scenario_service.generate_eval_suite_for_agent — Haiku wrote the answer.
    "generated": "model_generated",
    # scenario_service.mine_production_scenarios — a production failure, stored
    # with reference_answer='' because no ground truth exists for it.
    "mined": "customer_negative",
    # bench.promote_trace_to_scenario — an owner FILED this trace as failing.
    # The agent turn attached to it is a known-BAD answer, not a label.
    "production": "customer_negative",
    # red_team.py finding containment — an attack that succeeded.
    "red_team": "customer_negative",
}

# The minimum tier a scenario must carry before its answer may be written into
# verified_qa, which retrieval_service serves to customers ahead of retrieval.
VERIFIED_QA_MIN_TRUST_TIER = "human_verified"

# Recorded verbatim on every run in eval_runs.config so the disablement is a
# statement in the run record with a reason attached, rather than an absence a
# future reader has to infer. Copied (never handed out by reference) at every
# use site so a caller mutating the returned dict cannot poison the constant.
VERIFIED_QA_PROMOTION_DECISION: dict = {
    "enabled": False,
    "min_trust_tier": VERIFIED_QA_MIN_TRUST_TIER,
    "reason": (
        "verified_qa is served to customers ahead of retrieval, so only a "
        "human-verified or human-authored answer may enter it. Every scenario "
        "source the schema currently allows is model-generated or labels a "
        "negative, so no row is promotable until a correction UI produces "
        "human-verified answers."
    ),
}


def scenario_trust_tier(source: str | None) -> str:
    """Return the label trust tier for an eval_scenarios.source value.

    An unrecognised (or missing) source resolves to 'unknown', which ranks
    BELOW 'model_generated' — a provenance nobody has classified is treated as
    less trustworthy than one that has been classified as untrustworthy.
    """
    return SCENARIO_SOURCE_TRUST_TIER.get(source or "", "unknown")


def trust_tier_rank(tier: str) -> int:
    """Return the numeric rank of a trust tier; an unknown name ranks lowest."""
    return LABEL_TRUST_TIERS.get(tier, LABEL_TRUST_TIERS["unknown"])


def is_promotable_to_verified_qa(source: str | None) -> bool:
    """True iff a scenario from *source* may have its answer served to customers.

    Returns False for every source value the shipped schema allows. This is the
    single gate; callers must not reimplement the comparison.
    """
    return trust_tier_rank(scenario_trust_tier(source)) >= trust_tier_rank(
        VERIFIED_QA_MIN_TRUST_TIER
    )


# ---------------------------------------------------------------------------
# The golden set, and the denominators every measurement travels with
# ---------------------------------------------------------------------------
# TWO DATASETS, NEVER ONE NUMBER. A golden-set score and an exploratory score
# are different measurements. The golden set is FIXED and run in full on every
# eval, so the same items are scored twice and a regression shows up as a
# paired per-item delta — detectable at n=30 where an unpaired mean comparison
# would bury it in sampling noise. The exploratory sample rotates, which is what
# stops the golden set from being quietly overfit, and its mean moves whenever
# the draw moves. Averaging the two destroys the only property that makes the
# golden set worth having, so summarise_run_validity() reports metrics PER
# DATASET and the run-level entry carries denominators only. That is a
# structural refusal, not a convention: there is no run-level mean to misread.
#
# MISSING DATA IS NEVER PASSING DATA. Every figure below travels with the count
# of observations it was computed from. A metric over zero valid observations is
# `{"value": None, "measured": False, "observations": 0}` — unknown, never a
# pass — and a rate cannot be constructed from this shape without its
# denominator being in the reader's hand at the same moment.

DATASET_GOLDEN = "golden"
DATASET_EXPLORATORY = "exploratory"

# The order every report iterates in, so two runs' payloads compare key-for-key.
EVAL_DATASETS: tuple[str, ...] = (DATASET_GOLDEN, DATASET_EXPLORATORY)

# How many exploratory rows a run draws. The golden rows are NOT sampled — all
# of them run, every time, which is the whole point of designating them.
EXPLORATORY_SAMPLE_SIZE = 30

# Not a cap — a tripwire. The golden set is deliberately unsampled, so its size
# is the run's cost, and a tenant that designates a thousand rows would quietly
# multiply the nightly judge bill by thirty. Exceeding this is reported on the
# run (`golden_over_soft_ceiling`) rather than silently truncated: truncating
# would break the paired comparison the golden set exists for, and doing it
# silently would hide the breakage.
GOLDEN_SET_SOFT_CEILING = 200

# The four metrics a run reports, in the order the console reads them (D-04).
METRIC_KEYS: tuple[str, ...] = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


def dataset_of(value: str | None) -> str:
    """Resolve an eval_scenarios.dataset value to one of EVAL_DATASETS.

    Anything that is not exactly DATASET_GOLDEN — NULL (every row that predates
    migration 0014), an empty string, an unrecognised value — resolves to
    'exploratory'. Membership of the golden set is an assertion somebody has to
    make; it is never inherited by default, because a golden set that fills
    itself is just the old random sample wearing a stable name.
    """
    return DATASET_GOLDEN if value == DATASET_GOLDEN else DATASET_EXPLORATORY


# ---------------------------------------------------------------------------
# Attributing a returned score to the scenario it is about
# ---------------------------------------------------------------------------
# A SCORE THAT CANNOT BE ATTRIBUTED IS NOT AN OBSERVATION. run_ragas_eval used
# to walk the returned dataframe with `enumerate` and hand row i to
# valid_scenarios[i], which is correct only when the judge returns exactly as
# many rows as it was given, in order. Ragas can return fewer (a judge outage, a
# parse failure), and P2 made that failure load-bearing by ordering the golden
# rows first: five surviving scores from scenarios at positions 2, 7, 11, 19 and
# 26 were assigned to positions 0-4, i.e. to the golden row and the first four
# exploratory rows. The golden set's paired per-item delta — the entire reason
# for the split and for migration 0014 — was then computed against a number
# belonging to a different scenario, and it looked exactly like a real
# measurement.
#
# So attribution is by IDENTITY whenever the count does not prove order:
#   * len(returned) == len(sent) — one row per sample, in order. This is the
#     condition under which positional attribution is sound, and it is checked
#     rather than assumed.
#   * otherwise — recover each returned row's scenario from the sample fields
#     the judge echoes back (user_input / reference). A row whose key matches no
#     sent scenario, or matches more than one, is UNATTRIBUTED: it is counted
#     and dropped, never assigned to a neighbour and never given a synthetic id.
#
# Dropping is not the same as hiding. The count travels out on the run
# (`unattributed`), because a run that scored five rows and could place none of
# them measured nothing, and that has to be visible.

# The sample columns EvaluationDataset was built from. to_pandas() carries them
# back beside the metric columns, and they are the only thing in a returned row
# that says which sample it is.
SAMPLE_KEY_COLUMNS: tuple[str, ...] = ("user_input", "reference")


def scenario_identity_key(scenario: dict) -> tuple[str, str]:
    """The (question, reference_answer) pair a returned judge row echoes back.

    Built from the same two fields run_ragas_eval puts into `user_input` and
    `reference`, so a returned row and the scenario it came from produce the
    same key by construction.
    """
    return (
        str(scenario.get("question", "")),
        str(scenario.get("reference_answer", "")),
    )


def attribute_returned_rows(
    returned_keys: list[tuple[str, str] | None],
    valid_scenarios: list[dict],
) -> list[int | None]:
    """Map each returned judge row to the index of the scenario it scored.

    Pure — no I/O, no pandas. Returns one entry per returned row: the index into
    `valid_scenarios`, or None when the row cannot be attributed.

    Args:
        returned_keys: one scenario_identity_key per returned row, in the order
            the judge returned them. None for a row whose sample columns are
            absent, which is itself unattributable.
        valid_scenarios: the scenarios that were sent, in the order they were
            sent.

    Positional attribution is used ONLY when the lengths match, which is the
    exact condition under which the judge returned one row per sample in order.
    An ambiguous key (two sent scenarios sharing the same question AND the same
    reference answer) resolves to None rather than to the first match: the two
    are interchangeable as inputs but not as identities, and writing the wrong
    scenario_id into eval_results is what makes a paired comparison lie.
    """
    if len(returned_keys) == len(valid_scenarios):
        return list(range(len(valid_scenarios)))

    keys = [scenario_identity_key(s) for s in valid_scenarios]
    seen: dict[tuple[str, str], int] = {}
    ambiguous: set[tuple[str, str]] = set()
    for index, key in enumerate(keys):
        if key in seen:
            ambiguous.add(key)
        else:
            seen[key] = index

    return [
        None if key is None or key in ambiguous else seen.get(key)
        for key in returned_keys
    ]


def _is_valid_scenario(scenario: dict) -> bool:
    """True iff this row can be scored at all — i.e. it carries a label.

    The same condition the selector's `reference_answer != ''` expresses in SQL.
    It is the VALID denominator: rows that were fetched but cannot be scored are
    attempted, not valid, and dividing by the attempted count would understate
    every rate.
    """
    return bool(scenario.get("reference_answer"))


def dataset_composition(
    scenarios: list[dict],
    *,
    dataset_column_available: bool,
) -> dict:
    """Describe WHICH rows a run is about to score, before it scores them.

    Stamped on the run in `eval_runs.config["dataset"]` so that two runs can be
    compared over the same items. Without it, a golden-set score that moved is
    indistinguishable from a golden set whose membership changed, and the paired
    comparison the golden set exists for cannot be made after the fact.

    Args:
        scenarios: the rows fetched for this run, each carrying `dataset`.
        dataset_column_available: False when the tenant DB predates migration
            0014 and the selector fell back to the pre-0014 single query. Then
            every row is exploratory because the column that could say otherwise
            does not exist — which is a different claim from "this tenant has no
            golden rows", and the flag is what keeps them apart.

    Returns:
        {"dataset_column_available", "attempted", "valid", "golden": {...},
         "exploratory": {...}, "golden_set_present", "golden_over_soft_ceiling",
         "exploratory_sample_size"}
    """
    per_dataset: dict[str, dict] = {
        name: {"attempted": 0, "valid": 0} for name in EVAL_DATASETS
    }
    for scenario in scenarios:
        bucket = per_dataset[dataset_of(scenario.get("dataset"))]
        bucket["attempted"] += 1
        if _is_valid_scenario(scenario):
            bucket["valid"] += 1

    golden_attempted = per_dataset[DATASET_GOLDEN]["attempted"]
    return {
        "dataset_column_available": dataset_column_available,
        "attempted": sum(b["attempted"] for b in per_dataset.values()),
        "valid": sum(b["valid"] for b in per_dataset.values()),
        DATASET_GOLDEN: per_dataset[DATASET_GOLDEN],
        DATASET_EXPLORATORY: per_dataset[DATASET_EXPLORATORY],
        # False is a real and expected state — no tenant has designated a golden
        # row until someone does — and it says the run made no comparable
        # measurement at all, only a rotating exploratory one.
        "golden_set_present": golden_attempted > 0,
        "golden_over_soft_ceiling": golden_attempted > GOLDEN_SET_SOFT_CEILING,
        "exploratory_sample_size": EXPLORATORY_SAMPLE_SIZE,
    }


def summarise_run_validity(
    scenarios: list[dict],
    scenario_scores: list[dict],
) -> dict:
    """Report (attempted, valid, scored) for a run and per dataset. Pure — no I/O.

    The three counts are different claims and collapsing any two of them is how
    a run comes to report a rate it never measured:

        attempted — rows the selector returned.
        valid     — rows carrying a label, i.e. the rows that could be scored.
                    THIS IS THE DENOMINATOR.
        scored    — rows for which at least one metric came back as a real
                    number. Ragas can return fewer rows than it was given (a
                    judge outage, a parse failure), and every one of those is a
                    row that was valid and produced nothing.

    `scored < valid` is the signal that a run measured less than it attempted,
    and it is unreadable unless all three travel together.

    Metrics are reported per dataset only. There is deliberately no run-level
    mean: a golden mean and an exploratory mean answer different questions, and
    a single number over both would move whenever the exploratory draw moved
    while looking like a quality change. See the section comment above.

    Args:
        scenarios: every row fetched for the run, each carrying `dataset`,
            `reference_answer` and `id`.
        scenario_scores: run_ragas_eval()'s "scores" list. May be empty — a run
            that scored nothing reports scored=0 and every metric unmeasured,
            which is 'unknown', not zero and not a pass.

    Returns:
        {"attempted", "valid", "scored", "unattributed",
         "datasets": {"golden": {attempted, valid, scored, metrics},
                      "exploratory": {...}}}
        where each metric is {"value": float|None, "measured": bool,
        "observations": int} and value is None exactly when measured is False.

    ONE RULE FOR AN UNATTRIBUTABLE SCORE, STATED IN BOTH READERS. A score whose
    scenario is not in the fetched set joins NEITHER dataset — it cannot, and
    inventing a bucket for it puts an unplaceable observation inside a
    comparable measurement. It is counted in `unattributed` so nothing vanishes
    silently. api/v1/evals.py's _LIST_EVAL_RUN_DATASETS_SQL applies the same
    rule to the same rows (its own third bucket, also counted and never
    averaged); the two used to disagree, each calling itself the honest one,
    which meant one run had two denominators differing by exactly these rows.
    run_ragas_eval no longer produces such a row at all — this stays as the
    fail-closed floor under rows written by older builds.
    """
    dataset_by_scenario_id = {
        str(s.get("id", "")): dataset_of(s.get("dataset")) for s in scenarios
    }

    buckets: dict[str, dict] = {
        name: {
            "attempted": 0,
            "valid": 0,
            "scored": 0,
            # metric -> list of real observations, means computed at the end.
            "_observations": {metric: [] for metric in METRIC_KEYS},
        }
        for name in EVAL_DATASETS
    }

    for scenario in scenarios:
        bucket = buckets[dataset_of(scenario.get("dataset"))]
        bucket["attempted"] += 1
        if _is_valid_scenario(scenario):
            bucket["valid"] += 1

    unattributed = 0
    for score in scenario_scores:
        # A score whose scenario is not in the fetched set is attributed to
        # neither dataset: it cannot be, and inventing a bucket for it would put
        # an unattributable observation into a comparable measurement. It is
        # COUNTED, though — see the docstring: a dropped observation nobody
        # reports is how the two readers of these rows came to disagree.
        name = dataset_by_scenario_id.get(str(score.get("scenario_id")))
        if name is None:
            unattributed += 1
            continue
        bucket = buckets[name]
        observed_any = False
        for metric in METRIC_KEYS:
            value = score.get(metric)
            if value is None:
                continue
            bucket["_observations"][metric].append(float(value))
            observed_any = True
        if observed_any:
            bucket["scored"] += 1

    datasets: dict[str, dict] = {}
    for name in EVAL_DATASETS:
        bucket = buckets[name]
        observations = bucket.pop("_observations")
        metrics = {}
        for metric in METRIC_KEYS:
            values = observations[metric]
            metrics[metric] = {
                "value": (sum(values) / len(values)) if values else None,
                "measured": bool(values),
                "observations": len(values),
            }
        datasets[name] = {**bucket, "metrics": metrics}

    return {
        "attempted": sum(d["attempted"] for d in datasets.values()),
        "valid": sum(d["valid"] for d in datasets.values()),
        "scored": sum(d["scored"] for d in datasets.values()),
        # Scored rows that belong to no fetched scenario. Not part of `scored`:
        # they were not scored FOR THIS RUN's datasets, and adding them to a
        # dataset would be the invention this function refuses to make.
        "unattributed": unattributed,
        "datasets": datasets,
    }


# ---------------------------------------------------------------------------
# Task 1: Ragas evaluation harness
# ---------------------------------------------------------------------------

def run_ragas_eval(scenarios: list[dict]) -> dict:
    """Run Ragas 0.4.x evaluation over a list of eval scenarios.

    Builds an EvaluationDataset from the scenarios, calls evaluate() with
    the four M6 metrics, and returns per-scenario scores plus per-metric means.

    D-02 LOCKED: Dataset field name is 'reference' (field was renamed in Ragas 0.4.x).

    NO DATABASE IS TOUCHED HERE. Every field the dataset needs is already in the
    scenario dicts, and the only remote call is to the judge API. This function
    used to accept a `branch_conn_str` it never referenced (`# noqa: ARG001`),
    which is how the Neon branch came to look load-bearing to every reader of
    the call site — including a caller that abandoned an entire run when the
    branch could not be created. The parameter is gone rather than renamed: an
    unused connection string is exactly the thing that invites a false isolation
    claim, and its ABSENCE is what
    test_scoring_takes_no_connection_string_because_it_opens_none pins.

    Args:
        scenarios: List of scenario dicts. Each must contain:
            - question (str): The user question.
            - reference_answer (str): Ground-truth answer (D-02).
            - agent_response (str, optional): The agent's generated answer.
              While EVAL_INVOKES_AGENT is False this is the reference answer
              itself (audit D1), so the metrics below are self-scoring.
            - retrieved_contexts (list[str], optional): Retrieved chunk contents.

    Returns:
        Dict with five keys:
            "scores": list[dict] — one dict per ATTRIBUTED returned row (not per
                input row: the judge may return fewer, and a row that cannot be
                matched to the scenario it scored is dropped rather than
                assigned by position — see attribute_returned_rows).
                Each dict: {scenario_id, faithfulness, answer_relevancy,
                            context_precision, context_recall}
            "means": dict — per-metric mean over the attributed rows.
            "sent" / "returned" / "unattributed": the judge's own denominators.
                `returned < sent` is a partial judge outage; `unattributed > 0`
                means rows came back that cannot be placed at all.
    """
    # Filter to only scenarios that have a reference_answer (required by Ragas)
    # D-02 LOCKED: field name is 'reference' (renamed in Ragas 0.4.x)
    samples = [
        {
            "user_input": s["question"],
            "response": s.get("agent_response", ""),
            "retrieved_contexts": s.get("retrieved_contexts", []),
            "reference": s["reference_answer"],   # D-02 LOCKED
        }
        for s in scenarios
        if s.get("reference_answer")
    ]

    # Keep only the scenarios that produced samples (same order)
    valid_scenarios = [s for s in scenarios if s.get("reference_answer")]

    if not samples:
        log.warning("run_ragas_eval.no_valid_scenarios")
        return {
            "scores": [],
            "means": {metric: None for metric in METRIC_KEYS},
            "sent": 0,
            "returned": 0,
            "unattributed": 0,
        }

    log.info("run_ragas_eval.start", scenario_count=len(samples))

    dataset = EvaluationDataset.from_list(samples)

    # Ragas 0.4.x requires InstructorLLM (InstructorBaseRagasLLM) for collections metrics.
    # Build the LLM wrapper at call time (not module level) — metrics are instantiated here.
    _anthropic_client = instructor.from_anthropic(anthropic.Anthropic())
    llm = InstructorLLM(client=_anthropic_client, model=HAIKU_MODEL, provider="anthropic")
    metrics = [
        Faithfulness(llm=llm),
        AnswerRelevancy(llm=llm),
        ContextPrecision(llm=llm),
        ContextRecall(llm=llm),
    ]

    results = evaluate(dataset=dataset, metrics=metrics, llm=llm)

    df = results.to_pandas()

    # Build per-scenario score dicts. The metric names come from the one
    # METRIC_KEYS tuple rather than a local literal list: audit D3 is a second
    # copy of a column name drifting from the first, and this module writes
    # these same four names into eval_results, reports them per dataset and
    # hands them to the console.
    metric_columns = list(METRIC_KEYS)

    # Which scenario each returned row is about. See attribute_returned_rows:
    # positional attribution holds only when the judge returned one row per
    # sample, and this is where that used to be assumed.
    returned_rows = [row for _, row in df.iterrows()]
    have_key_columns = all(col in df.columns for col in SAMPLE_KEY_COLUMNS)
    returned_keys: list[tuple[str, str] | None] = [
        (str(row.get("user_input", "")), str(row.get("reference", "")))
        if have_key_columns
        else None
        for row in returned_rows
    ]
    attribution = attribute_returned_rows(returned_keys, valid_scenarios)

    score_rows = []
    unattributed = 0
    for row, scenario_index in zip(returned_rows, attribution):
        scenario = (
            valid_scenarios[scenario_index] if scenario_index is not None else None
        )
        scenario_id = str(scenario.get("id", "")) if scenario is not None else ""
        if not scenario_id:
            # No synthetic uuid4 here any more. A fabricated scenario_id
            # produced an eval_results row that joins no eval_scenarios row,
            # which summarise_run_validity drops from both datasets and the
            # eval-runs route counted as exploratory — two denominators for the
            # same run, differing by exactly this row. A score nobody can place
            # is reported as unplaced and written nowhere.
            unattributed += 1
            continue
        score_row = {"scenario_id": scenario_id}
        for col in metric_columns:
            raw = row.get(col)
            score_row[col] = float(raw) if raw is not None and raw == raw else None  # NaN check
        score_rows.append(score_row)

    # Per-metric means over the ATTRIBUTED rows only, for the same reason: a
    # mean that includes an observation the run cannot place is a mean over a
    # denominator the run does not have.
    means = {}
    for col in metric_columns:
        values = [
            score[col] for score in score_rows if score.get(col) is not None
        ]
        means[col] = (sum(values) / len(values)) if values else None

    if unattributed:
        log.warning(
            "run_ragas_eval.unattributed_rows",
            sent=len(samples),
            returned=len(returned_rows),
            unattributed=unattributed,
            have_key_columns=have_key_columns,
            detail=(
                "the judge returned rows that cannot be matched to a scenario; "
                "they are counted and dropped rather than assigned by position"
            ),
        )

    log.info(
        "run_ragas_eval.complete",
        scenario_count=len(samples),
        returned=len(returned_rows),
        attributed=len(score_rows),
        faithfulness_mean=means.get("faithfulness"),
        answer_relevancy_mean=means.get("answer_relevancy"),
    )

    return {
        "scores": score_rows,
        "means": means,
        # (sent, returned, unattributed) — the judge's own denominators. A run
        # that sent forty and got five back has measured five, and nothing
        # downstream can see that from `scores` alone.
        "sent": len(samples),
        "returned": len(returned_rows),
        "unattributed": unattributed,
    }


# ---------------------------------------------------------------------------
# Task 1 continued: write eval results to tenant DB
# ---------------------------------------------------------------------------

def write_eval_results(
    eval_run_id: str,
    scenario_scores: list[dict],
    conn_str: str,
) -> None:
    """Insert per-scenario, per-metric rows into eval_results on PRODUCTION.

    The eval_results table exists from migration 0001:
      id UUID, eval_run_id UUID, scenario_id TEXT, metric TEXT, score NUMERIC, detail JSONB

    One row is inserted per (scenario, metric) pair — four rows per scenario for
    Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall.

    The third argument used to be named `branch_conn_str` and was given the Neon
    branch, which the caller then deleted — so these rows never survived the run
    that produced them (audit D2). It is named `conn_str` now because the name
    is load-bearing: results are observations about a run, they belong on
    production, and a parameter named after the branch invites the old bug back.

    Uses psycopg2 try/finally/close pattern matching retrieval_service.py (D-11).

    Args:
        eval_run_id: UUID string of the eval_runs row.
        scenario_scores: List of per-scenario score dicts from run_ragas_eval().
        conn_str: PRODUCTION tenant connection string — never the eval branch.
    """
    if not scenario_scores:
        log.info("write_eval_results.no_scores")
        return

    sql = """
        INSERT INTO eval_results (id, eval_run_id, scenario_id, metric, score, detail)
        VALUES (%(id)s::uuid, %(eval_run_id)s::uuid, %(scenario_id)s, %(metric)s, %(score)s, %(detail)s::jsonb)
    """

    metric_columns = list(METRIC_KEYS)

    conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
    try:
        with conn.cursor() as cur:
            for score in scenario_scores:
                for metric in metric_columns:
                    cur.execute(sql, {
                        "id": str(uuid.uuid4()),
                        "eval_run_id": eval_run_id,
                        "scenario_id": str(score["scenario_id"]),
                        "metric": metric,
                        "score": score.get(metric),
                        "detail": json.dumps(score),
                    })
        conn.commit()
    finally:
        conn.close()

    log.info(
        "write_eval_results.complete",
        eval_run_id=eval_run_id,
        rows_written=len(scenario_scores) * len(metric_columns),
    )


def update_eval_run_status(
    eval_run_id: str,
    status: str,
    finished_at: bool,
    conn_str: str,
) -> None:
    """Update the status (and optionally finished_at) on an eval_runs row on PRODUCTION.

    The eval_runs table exists from migration 0001 (+ 0013's two nullable
    attribution columns, which this function does not touch):
      id UUID, kind TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, status TEXT

    A run must reach a terminal state on production or it never happened. The
    previous execution sent every terminal status to the branch, so a production
    row could only ever say 'running' (successful run) or 'failed' (and only for
    the one failure mode that fires before the branch exists) — a successful run
    was indistinguishable from a hung one. Hence `conn_str`, not
    `branch_conn_str`.

    Args:
        eval_run_id: UUID string of the eval_runs row.
        status: New status value (e.g. 'running', 'complete', 'failed').
        finished_at: When True, sets finished_at = NOW().
        conn_str: PRODUCTION tenant connection string — never the eval branch.
    """
    if finished_at:
        sql = """
            UPDATE eval_runs
            SET status = %(status)s, finished_at = NOW()
            WHERE id = %(id)s::uuid
        """
    else:
        sql = """
            UPDATE eval_runs
            SET status = %(status)s
            WHERE id = %(id)s::uuid
        """

    conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {"status": status, "id": eval_run_id})
        conn.commit()
    finally:
        conn.close()

    log.info("update_eval_run_status.complete", eval_run_id=eval_run_id, status=status)


# ---------------------------------------------------------------------------
# The configuration tuple (migration 0013)
# ---------------------------------------------------------------------------

_INSERT_EVAL_RUN_WITH_CONFIG_SQL = """
    INSERT INTO eval_runs (id, kind, started_at, status, prompt_version_id, config)
    VALUES (%(id)s::uuid, %(kind)s, NOW(), 'running',
            %(prompt_version_id)s::uuid, %(config)s::jsonb)
"""

# The pre-0013 shape. Used only when the wide INSERT raises UndefinedColumn.
_INSERT_EVAL_RUN_BASE_SQL = """
    INSERT INTO eval_runs (id, kind, started_at, status)
    VALUES (%(id)s::uuid, %(kind)s, NOW(), 'running')
"""


def _canonical_hash(payload: dict) -> str:
    """sha256 of a dict serialised with sorted keys and no insignificant space.

    Same canonicalisation discipline as capability_service.canonical_envelope_hash:
    key order and whitespace must never vary the digest, or two identical
    configurations would compare as different and every run would look like a
    change.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_eval_run_config(
    agent_id: str,
    conn_str: str,
    dataset: dict | None = None,
) -> dict:
    """Collect the configuration tuple an eval run is an assertion about.

    Without this, two runs that differ on exactly one dimension cannot be
    compared and "what changed?" is unanswerable — which is the whole mechanism
    of continuous improvement. Every dimension below was already captured
    somewhere in the system; none of them was ever stamped on the run.

    WHAT THE TUPLE CERTIFIES IS NOT WHAT THE RUN EXERCISED. The dimensions
    describe the configuration the agent is deployed with; while
    EVAL_INVOKES_AGENT is False the scores are invariant to all of them
    (audit D1), and `config["agent_invoked"]` / `config["scored_response_source"]`
    / `config["dimensions_not_exercised"]` say so on every run. That pairing is
    load-bearing: a tuple that records a dimension the measurement cannot see
    turns "two runs, one difference, identical scores" into a false finding of
    quality-neutrality.

    Read from the same sources the deploy gate already reads, so a checklist and
    an eval run can never disagree about what the live configuration was:
    `deployment_service._compute_envelope_hash_sync` for the envelope hash (NOT
    a second projection of capability_envelopes — a divergent projection would
    silently produce a different hash for identical configuration) and
    `_fetch_corpus_stats_sync` for the corpus figure. Both are imported inside
    the function: deployment_service pulls in the Claude Agent SDK at module
    scope, and eval_service sits on the FastAPI route import chain via
    api/v1/evals.py, so the cost is paid only by the Celery task that needs it.

    MISSING DATA IS NEVER PASSING DATA. Each dimension is collected
    independently and a dimension that could not be READ is recorded as None
    AND named in `config["unavailable"]`. That is deliberately distinguishable
    from a dimension that was read and is genuinely absent — an agent with no
    production prompt version has prompt_version_id None with nothing in
    `unavailable`, and those are different claims. Nothing here raises: an
    unattributable run is worth less than an attributed one but far more than
    no run at all, so a collector failure degrades attribution, never the run.

    Args:
        agent_id: UUID string of the agent being evaluated.
        conn_str: PRODUCTION tenant connection string (the corpus figure
            describes the live corpus, not the branch's copy of it).
        dataset: dataset_composition() for the rows this run is about to score,
            or None when the caller has none to give. It lands verbatim on the
            run as `config["dataset"]`, which is what makes a golden-set score
            comparable to the next one: a score that moved and a MEMBERSHIP that
            moved are indistinguishable after the fact unless the run recorded
            which rows it covered. None is stored as null rather than as an
            empty composition — "this run did not record its dataset" is not
            "this run scored no rows".

    Returns:
        {"prompt_version_id": str | None, "config": dict} — ready to hand
        straight to insert_eval_run().
    """
    unavailable: list[str] = []

    # --- prompt_version_id (control DB) --------------------------------
    # The PRODUCTION label specifically, never resolve_prompt_version's
    # weighted canary pick: an eval must be reproducible, and a run whose
    # attribution was decided by random.random() cannot be compared to the
    # next one. None here means "no production prompt version exists" — a
    # real state (the agent still runs off its live soul_* columns).
    prompt_version_id: str | None = None
    try:
        with get_sync_db() as db:
            row = db.execute(
                sa_text(
                    "SELECT id FROM prompt_versions "
                    "WHERE agent_id = :agent_id AND label = 'production' "
                    "ORDER BY version_number DESC LIMIT 1"
                ),
                {"agent_id": agent_id},
            ).first()
        prompt_version_id = str(row[0]) if row else None
    except Exception as exc:
        unavailable.append("prompt_version_id")
        log.warning(
            "build_eval_run_config.prompt_version_unavailable",
            agent_id=agent_id,
            error=str(exc),
        )

    # --- retrieval_config_hash (control DB: agents.retrieval_strategy) --
    # Hashed through RetrievalStrategy.model_validate so that an absent key
    # and an explicitly-default key produce the SAME hash — otherwise two
    # identically-behaving agents would appear to differ.
    retrieval_config_hash: str | None = None
    try:
        from app.services.retrieval_service import RetrievalStrategy  # noqa: PLC0415

        with get_sync_db() as db:
            row = db.execute(
                sa_text("SELECT retrieval_strategy FROM agents WHERE id = :agent_id"),
                {"agent_id": agent_id},
            ).first()
        strategy = RetrievalStrategy.model_validate((row[0] if row else None) or {})
        retrieval_config_hash = _canonical_hash(strategy.model_dump(mode="json"))
    except Exception as exc:
        unavailable.append("retrieval_config_hash")
        log.warning(
            "build_eval_run_config.retrieval_config_unavailable",
            agent_id=agent_id,
            error=str(exc),
        )

    # --- envelope_hash (control DB: capability_envelopes) ---------------
    envelope_hash: str | None = None
    try:
        from app.services.deployment_service import (  # noqa: PLC0415
            _compute_envelope_hash_sync,
        )

        envelope_hash = _compute_envelope_hash_sync(agent_id)
    except Exception as exc:
        unavailable.append("envelope_hash")
        log.warning(
            "build_eval_run_config.envelope_hash_unavailable",
            agent_id=agent_id,
            error=str(exc),
        )

    # --- corpus_chunk_count (tenant DB, production) ---------------------
    corpus_chunk_count: int | None = None
    try:
        from app.services.deployment_service import (  # noqa: PLC0415
            _fetch_corpus_stats_sync,
        )

        corpus_chunk_count = _fetch_corpus_stats_sync(agent_id, conn_str)["chunk_count"]
    except Exception as exc:
        unavailable.append("corpus_chunk_count")
        log.warning(
            "build_eval_run_config.corpus_stats_unavailable",
            agent_id=agent_id,
            error=str(exc),
        )

    config = {
        # The model that serves a customer turn, read from the one constant
        # run_agent_turn uses. It describes the DEPLOYED configuration; while
        # agent_invoked below is False it is not a dimension these scores
        # measure — see dimensions_not_exercised.
        "model_id": AGENT_TURN_MODEL,
        # The model grading the run. A different dimension entirely: a judge
        # change moves every score without the agent changing at all.
        "judge_model_id": HAIKU_MODEL,
        "retrieval_config_hash": retrieval_config_hash,
        "envelope_hash": envelope_hash,
        "corpus_chunk_count": corpus_chunk_count,
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_model_id": (
            settings.BEDROCK_EMBED_MODEL_ID
            if settings.EMBEDDING_PROVIDER == "bedrock"
            else "voyage-3"
        ),
        # The promotion policy in force for this run, stated rather than implied.
        "verified_qa_promotion": dict(VERIFIED_QA_PROMOTION_DECISION),
        # WHICH ROWS this run covered — golden (fixed, unsampled) versus
        # exploratory (rotating), with the attempted and valid counts for each.
        # None when the caller recorded no composition.
        "dataset": dict(dataset) if dataset is not None else None,
        # --- What the run actually exercised (audit D1) -----------------
        # The keys above certify the configuration the agent is DEPLOYED with.
        # These three say which of them the score is a function of, and the
        # honest answer today is: none. eval.py scores each scenario's
        # reference answer against the contexts it was written from, so no
        # agent turn, no retrieval and no capability envelope participates.
        # Without this, the tuple actively misleads: two nightly runs
        # differing on exactly one recorded dimension (config.model_id, say)
        # would carry statistically identical scores, and the reader the tuple
        # was built for would conclude the model swap is quality-neutral and
        # ship it. A configuration tuple that makes an uncomparable
        # measurement look comparable is worse than no tuple at all, so the
        # exclusion travels with every run rather than living in an audit
        # nobody queries.
        "agent_invoked": EVAL_INVOKES_AGENT,
        "scored_response_source": EVAL_SCORED_RESPONSE_SOURCE,
        "dimensions_not_exercised": (
            [] if EVAL_INVOKES_AGENT else list(AGENT_DEPENDENT_DIMENSIONS)
        ),
        # Names the dimensions that could not be READ. Empty list = every
        # dimension was collected; a None value with nothing here means the
        # dimension was read and is genuinely absent.
        "unavailable": unavailable,
    }

    log.info(
        "build_eval_run_config.complete",
        agent_id=agent_id,
        prompt_version_id=prompt_version_id,
        unavailable=unavailable,
        golden_set_present=(
            dataset.get("golden_set_present") if dataset is not None else None
        ),
    )
    return {"prompt_version_id": prompt_version_id, "config": config}


def insert_eval_run(
    run_id: str,
    kind: str,
    prompt_version_id: str | None,
    config: dict | None,
    conn_str: str,
) -> bool:
    """Insert the eval_runs row on PRODUCTION with its configuration tuple.

    Migration 0013 added `prompt_version_id` and `config` as nullable columns.
    Tenant DBs are migrated with `alembic upgrade head` at PROVISION time only,
    so a tenant provisioned before 0013 does not have them until it is
    re-migrated — and a downgrade removes them again. Writing the wide INSERT
    unconditionally would turn "this tenant is a migration behind" into "no eval
    can start at all", which is a far worse failure than losing attribution.

    So: attempt the wide INSERT, and on psycopg2.errors.UndefinedColumn ONLY,
    fall back to the pre-0013 shape and report it. The narrow except matters —
    catching Exception here would swallow a genuine write failure and report the
    run as started when nothing was inserted.

    Args:
        run_id: UUID string for the new row.
        kind: The eval_runs.kind value (also the per-agent idempotency key).
        prompt_version_id: UUID string or None.
        config: The configuration-tuple dict, or None.
        conn_str: PRODUCTION tenant connection string.

    Returns:
        True when the configuration tuple was recorded; False when the columns
        were absent and the run was inserted without attribution.
    """
    params = {
        "id": run_id,
        "kind": kind,
        "prompt_version_id": prompt_version_id,
        "config": json.dumps(config) if config is not None else None,
    }

    conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
    try:
        try:
            with conn.cursor() as cur:
                cur.execute(_INSERT_EVAL_RUN_WITH_CONFIG_SQL, params)
            conn.commit()
            return True
        except psycopg2.errors.UndefinedColumn:
            # The aborted transaction must be rolled back before the connection
            # will accept another statement.
            conn.rollback()
            log.warning(
                "insert_eval_run.config_columns_absent",
                run_id=run_id,
                kind=kind,
                detail="tenant DB predates alembic_tenant 0013 — run recorded without attribution",
            )
            with conn.cursor() as cur:
                cur.execute(_INSERT_EVAL_RUN_BASE_SQL, {"id": run_id, "kind": kind})
            conn.commit()
            return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Task 2: verified_qa promotion helper
# ---------------------------------------------------------------------------

def _meets_score_thresholds(score: dict) -> bool:
    """True iff a score row clears both promotion thresholds (D-21 LOCKED).

    A missing metric is never a pass — a None faithfulness means the metric
    produced no valid observation for that scenario, which is 'unknown', not
    'good enough'.
    """
    faithfulness = score.get("faithfulness")
    answer_relevancy = score.get("answer_relevancy")
    return (
        faithfulness is not None
        and answer_relevancy is not None
        and faithfulness >= settings.EVAL_FAITHFULNESS_THRESHOLD
        and answer_relevancy >= settings.EVAL_RELEVANCY_THRESHOLD
    )


def select_promotion_candidates(
    scenarios: list[dict],
    scenario_scores: list[dict],
) -> tuple[list[tuple[dict, dict]], dict[str, int]]:
    """Decide which scored scenarios may enter verified_qa. Pure — no I/O.

    Two independent gates, applied in this order:

    1. TRUST TIER — is this scenario's answer allowed to be served to a
       customer at all? Checked FIRST and it is not a tiebreak: a high score on
       a model-written answer is evidence about the model's self-consistency,
       not about the answer's truth, so no score may buy a source out of its
       tier. Checking it first also means an unpromotable row never reaches the
       embedding call below.
    2. SCORE THRESHOLD — D-21's 0.90/0.90 quality bar, applied only to answers
       that cleared the tier gate.

    A score whose scenario cannot be found is refused ('scenario_not_found'),
    not skipped silently: promoting an answer we cannot attribute to a question
    is exactly the failure this gate exists to prevent.

    Returns:
        (candidates, refusals) where candidates is a list of (scenario, score)
        pairs cleared for promotion, and refusals maps a reason string to the
        number of scored rows it refused. `sum(refusals.values()) +
        len(candidates) == len(scenario_scores)` always — every scored row is
        accounted for exactly once, so a promotion rate can never be computed
        without its denominator.
    """
    scenario_by_id = {str(s.get("id", "")): s for s in scenarios}

    candidates: list[tuple[dict, dict]] = []
    refusals: dict[str, int] = {}

    def _refuse(reason: str) -> None:
        refusals[reason] = refusals.get(reason, 0) + 1

    for score in scenario_scores:
        scenario = scenario_by_id.get(str(score.get("scenario_id")))
        if scenario is None:
            _refuse("scenario_not_found")
            continue

        source = scenario.get("source")
        if not is_promotable_to_verified_qa(source):
            _refuse(f"trust_tier:{scenario_trust_tier(source)}")
            continue

        if not _meets_score_thresholds(score):
            _refuse("below_score_threshold")
            continue

        candidates.append((scenario, score))

    return candidates, refusals


def promote_to_verified_qa(
    scenarios: list[dict],
    scenario_scores: list[dict],
    conn_str: str,
) -> dict:
    """Promote eligible scenarios into verified_qa. Unreachable in this build.

    verified_qa rows are served to real customers by
    retrieval_service.verified_qa_lookup BEFORE hybrid search, at 0.93 cosine
    similarity — so this function's output goes straight to end users. Its gate
    is therefore the label trust hierarchy first (select_promotion_candidates),
    the D-21 score thresholds second. No scenario source the shipped schema
    allows clears the trust gate today, so this function performs zero writes
    and does not open a connection at all.

    It is retained rather than deleted for two reasons: the promotion machinery
    (D-22 provenance, D-23 question_vector, the SELECT-first idempotency check)
    is correct and will be needed once human-verified labels exist, and a
    surviving second lock on the door means a future caller that reintroduces
    the call still cannot serve a model-written answer to a customer.

    Promoted rows are written with source='sandbox_test', promoted_by='system'
    (D-22 LOCKED) and a Voyage question_vector (D-23 LOCKED). Idempotency on
    Celery retry (acks_late rule) is ensured by a SELECT-first existence check
    on question before INSERT — gen_random_uuid() PK means ON CONFLICT cannot
    fire.

    Args:
        scenarios: Original scenario dicts (same list passed to run_ragas_eval).
            Each must carry `source` — a scenario with no source is refused.
        scenario_scores: Per-scenario score dicts from run_ragas_eval() "scores" list.
        conn_str: Tenant connection string (never stored — D-18). Only used if a
            candidate exists, which cannot happen in this build.

    Returns:
        {"scored": int, "promoted": int, "refused": int, "refusals": dict} —
        `scored` is the denominator; promoted + refused == scored always.
    """
    candidates, refusals = select_promotion_candidates(scenarios, scenario_scores)
    refused = sum(refusals.values())

    if not candidates:
        # No connection is opened. With promotion disabled by trust tier this
        # is the only path, and it makes "did an eval run write to verified_qa?"
        # answerable by observing that it never even connected.
        log.info(
            "promote_to_verified_qa.no_candidates",
            scored=len(scenario_scores),
            refused=refused,
            refusals=refusals,
            min_trust_tier=VERIFIED_QA_MIN_TRUST_TIER,
        )
        return {
            "scored": len(scenario_scores),
            "promoted": 0,
            "refused": refused,
            "refusals": refusals,
        }

    insert_sql = """
        INSERT INTO verified_qa (
            id, question, question_vector, answer, citations,
            source, faithfulness, relevance, promoted_at, promoted_by, use_count
        )
        VALUES (
            gen_random_uuid(),
            %(question)s,
            %(question_vector)s::vector,
            %(answer)s,
            %(citations)s::jsonb,
            'sandbox_test',
            %(faithfulness)s,
            %(relevance)s,
            NOW(),
            'system',
            0
        )
    """

    exists_sql = "SELECT id FROM verified_qa WHERE question = %(question)s LIMIT 1"

    promoted_count = 0
    conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
    try:
        with conn.cursor() as cur:
            for scenario, score in candidates:
                question = scenario["question"]

                # Idempotency: skip if a verified_qa row with this question already exists
                # (ON CONFLICT DO NOTHING cannot fire because id is gen_random_uuid())
                cur.execute(exists_sql, {"question": question})
                if cur.fetchone() is not None:
                    log.info(
                        "verified_qa.already_exists",
                        scenario_id=score["scenario_id"],
                    )
                    refusals["already_promoted"] = refusals.get("already_promoted", 0) + 1
                    refused += 1
                    continue

                # D-23 LOCKED: Voyage embedding for question_vector
                question_vector = _get_vo().embed(
                    [question], model="voyage-3", input_type="query"
                ).embeddings[0]

                answer = scenario.get("agent_response", "")
                citations = scenario.get("citations", [])

                cur.execute(insert_sql, {
                    "question": question,
                    # str(vector) then cast with ::vector — matching retrieval_service.py pattern
                    "question_vector": str(question_vector),
                    "answer": answer,
                    "citations": json.dumps(citations),
                    "faithfulness": score.get("faithfulness"),
                    "relevance": score.get("answer_relevancy"),
                })

                promoted_count += 1
                log.info(
                    "verified_qa.promoted",
                    scenario_id=score["scenario_id"],
                    source=scenario.get("source"),
                    trust_tier=scenario_trust_tier(scenario.get("source")),
                    faithfulness=score.get("faithfulness"),
                    relevance=score.get("answer_relevancy"),
                )

        conn.commit()
    finally:
        conn.close()

    log.info(
        "promote_to_verified_qa.complete",
        promoted=promoted_count,
        refused=refused,
        scored=len(scenario_scores),
    )
    return {
        "scored": len(scenario_scores),
        "promoted": promoted_count,
        "refused": refused,
        "refusals": refusals,
    }


# ---------------------------------------------------------------------------
# Top-level orchestrator (used by 06-05 Celery task)
# ---------------------------------------------------------------------------

def run_eval_for_agent(
    eval_run_id: str,
    scenarios: list[dict],
    conn_str: str,
) -> dict:
    """Run a full eval cycle for one agent: score in memory, record on production.

    Takes ONE connection string, and it is production's. The signature used to
    take a second, `branch_conn_str`, handed straight to run_ragas_eval, which
    never referenced it — so the parameter documented an isolation boundary that
    did not exist. A parameter no statement is ever issued against is not a
    safety property, it is a claim, and this one was false.

    Sequence:
        1. update_eval_run_status → 'running'   (production)
        2. run_ragas_eval — four metrics         (no database; judge API only)
        3. write_eval_results                    (production)
        4. update_eval_run_status → 'complete'   (production)

    verified_qa promotion is deliberately NOT part of this sequence — see
    VERIFIED_QA_PROMOTION_DECISION. Restoring it means clearing the trust gate
    in promote_to_verified_qa, not re-adding the call here.

    On exception: update_eval_run_status → 'failed' on production, then re-raise.

    Args:
        eval_run_id: UUID string — the eval_runs row already created by caller.
        scenarios: List of scenario dicts from the eval_scenarios table.
        conn_str: PRODUCTION tenant connection string — status + results land here.

    Returns:
        Dict: {
            "eval_run_id": str,
            "scenario_count": int,
            "means": dict,
            "promoted_count": int,   # always 0 while promotion is disabled
        }
    """
    log.info("run_eval_for_agent.start", eval_run_id=eval_run_id)
    update_eval_run_status(eval_run_id, "running", finished_at=False, conn_str=conn_str)

    try:
        result = run_ragas_eval(scenarios)
        scenario_scores = result["scores"]
        means = result["means"]

        write_eval_results(eval_run_id, scenario_scores, conn_str)

        update_eval_run_status(eval_run_id, "complete", finished_at=True, conn_str=conn_str)

        log.info(
            "run_eval_for_agent.complete",
            eval_run_id=eval_run_id,
            scenario_count=len(scenarios),
        )

        return {
            "eval_run_id": eval_run_id,
            "scenario_count": len(scenarios),
            "means": means,
            "promoted_count": 0,
        }

    except Exception as exc:
        log.error(
            "run_eval_for_agent.failed",
            eval_run_id=eval_run_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        update_eval_run_status(eval_run_id, "failed", finished_at=True, conn_str=conn_str)
        raise
