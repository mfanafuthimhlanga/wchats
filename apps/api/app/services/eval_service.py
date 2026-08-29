"""Ragas 0.4.x eval harness for W Chats M6.

Measures Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall per scenario.

Where each write lands (the persistence split — measurement-layer audit D2)
---------------------------------------------------------------------------
D-10 says an eval must never mutate tenant data. An earlier execution read that
as "everything the eval touches goes somewhere other than production", so
`eval_results`, the terminal `eval_runs` status and the verified_qa promotion
were all written to a copy that the task then discarded. Production therefore
never learned that a run finished: every successful run left its `eval_runs` row
at status='running' forever, and `eval_results` never existed on production at
all, which is why evals.py's LEFT JOIN always yielded NULL metrics.

Results are OBSERVATIONS ABOUT a run, not tenant data. The split is now:

    eval_runs insert / status / eval_results -> PRODUCTION (`conn_str`)
    scoring (run_ragas_eval)                 -> no database at all

The second line is a correction to an earlier version of this docstring, which
claimed scoring ran against a copy. It never did: run_ragas_eval builds an
EvaluationDataset out of rows that are already in memory and calls the judge
API, and the connection string it used to accept was never referenced anywhere
in its body.

A run must end in a terminal state on production or it never happened.

Trust tiers and verified_qa promotion (D5 / the label hierarchy)
---------------------------------------------------------------
`verified_qa` rows are served to real customers by retrieval_service's
verified_qa_lookup BEFORE hybrid search, at 0.93 cosine similarity. An earlier
build promoted any scenario clearing 0.90/0.90 into that table. That admitted a
`source='generated'` row whose "reference answer" was written by Haiku, and a
`source='production'` row whose answer is the one a human FLAGGED AS FAILING.
Those rows reached no customer only because the promotion write landed on a
throwaway Neon branch the task then deleted.

ADR 0003 deleted that writer. Nothing in this build promotes anything into
`verified_qa`, so the table keeps its reader (retrieval_service's
verified_qa_lookup) and gains no new rows.

WHAT HOLDS PROMOTION SHUT IS TWO THINGS, named strongest first above
LABEL_TRUST_TIERS. No promotion writer exists, and
VERIFIED_QA_PROMOTION_DECISION reads false and is recorded on every run, which
makes the disablement a statement in the run record rather than an absence a
later reader has to infer.

Design notes:
- All Ragas imports use the 0.4.x path (ragas.metrics.collections) — CLAUDE.md constraint.
- Dataset field is 'reference' (renamed from the old 0.3.x name in 0.4.x).
- LLM wrapper uses InstructorLLM over the factory's async instructor client (0.4.x collections requirement).
- All DB writes use psycopg2 try/finally/close pattern matching retrieval_service.py.
- No verified_qa promotion writer exists in this build. D-22's
  source='sandbox_test' / promoted_by='system' and D-23's question_vector say
  what a future writer would owe, not what any code here does.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import uuid
from collections.abc import Mapping
from types import MappingProxyType

import pandas as pd
import psycopg2
import structlog
from ragas import EvaluationDataset
from ragas.embeddings.base import BaseRagasEmbedding
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
from app.core.model_client import OPENAI_PROVIDER, LedgerContext, route_for
from app.domain.judge_identity import JUDGE_PROMPT_VERSION, JudgeIdentity
from app.services.embedding_service import EMBEDDING_MODEL, _get_vo

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The purpose each of the four metrics bills its calls under. One purpose per
#: metric, so a rollup says which dimension spent the money and a calibration
#: figure names the Judge it measured (`PURPOSE_ROUTES`, decision #34).
JUDGE_PURPOSES = (
    "judge_faithfulness",
    "judge_answer_relevancy",
    "judge_context_precision",
    "judge_context_recall",
)

#: `JUDGE_PROMPT_VERSION` is imported from `app.domain.judge_identity`, beside the
#: type whose third field it fills. The live-traffic Judge in
#: `retrieval_eval` reads the same constant, because ragas authors that prompt
#: too and two copies would let one dimension's key drift from the other's.

# Note: Ragas 0.4.x collections metrics require an InstructorLLM at construction time.
# Metrics are therefore instantiated inside run_ragas_eval(), not at module level.
# The four metrics used are: Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall (D-04)

# The kwargs each collections metric's ascore() takes. They differ per metric —
# AnswerRelevancy sees no contexts at all, ContextPrecision/ContextRecall score
# against `reference` rather than `response` — and passing the wrong set raises
# TypeError, so the mapping is data rather than four hand-written call sites.
_METRIC_ASCORE_ARGS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "faithfulness": ("user_input", "response", "retrieved_contexts"),
    "answer_relevancy": ("user_input", "response"),
    "context_precision": ("user_input", "reference", "retrieved_contexts"),
    "context_recall": ("user_input", "retrieved_contexts", "reference"),
})


# ---------------------------------------------------------------------------
# What this harness measures — and what it does not (audit D1)
# ---------------------------------------------------------------------------
# Two properties of the scoring half that every consumer of a score has to know.
# They are constants rather than prose so they can be stamped on the run record
# and pinned by a test, instead of being rediscovered by reading three files.
#
# 1. THE AGENT IS NOW INVOKED — and whether it was is an OBSERVATION, never an
#    assumption. Until D1/P2 (.dev/plans/260807-d1-agent-invocation.md) eval.py
#    built every sample with agent_response = reference_answer, so Ragas scored
#    the reference answer against the contexts that answer was written from:
#    Faithfulness and AnswerRelevancy approached 1.0 by construction and the
#    score was INVARIANT to the agent's model, prompt, retrieval configuration,
#    capability envelope and corpus. Recording those dimensions on a run without
#    recording that made an uncomparable measurement look comparable — two runs
#    differing only on config.model_id carried statistically identical scores and
#    read as "the model swap was quality-neutral".
#
#    EVAL_INVOKES_AGENT below is a claim about the CODE: this harness drives the
#    customer agent per scenario, through agent_loop.build_agent_turn. It is NOT
#    the same claim as `config["agent_invoked"]`, which is a claim about ONE RUN
#    and is written from what that run observed. The distinction is the whole
#    lesson of D1: a constant that says the agent was invoked, stamped on a run
#    that invoked nothing, is the tautology with a newer comment.
#
# 2. SCORING TOUCHES NO DATABASE. run_ragas_eval executes no statement against
#    anything. It takes rows already in memory and calls the judge API.
#
#    P2 does not change this. The agent turns happen in eval.py BEFORE scoring
#    and they read the tenant's PRODUCTION connection string, because retrieval
#    has to see the corpus the customer is served; what stops them writing is
#    recorded mode (BACKLOG 2.5), not a copy of the database.

EVAL_INVOKES_AGENT = True

# Where the text scored as the "response" comes from now that D1 is closed. The
# per-run key `config["scored_response_source"]` is derived from the run's own
# observation, not from this constant — see invocation_provenance.
EVAL_SCORED_RESPONSE_SOURCE = "agent_response"

# What the same key says on a run whose eval_runs row exists but whose
# invocation phase has not reported yet. It is the value every run carries at
# INSERT time, and it is what a run that died mid-invocation keeps.
EVAL_RESPONSE_SOURCE_PENDING = "pending_invocation"

# And what it says when the invocation phase DID report and nothing reached the
# scorer — every turn raised, or every response came back with no usable
# retrieved context. Distinct from 'pending_invocation' (the phase never ran) and
# from 'agent_response' (a set of scored rows exists and came from the agent),
# because a claim about an empty set is neither of those.
EVAL_RESPONSE_SOURCE_NONE_SCORED = "no_response_scored"

# Dimensions of the run record — config keys plus the prompt_version_id column —
# that cannot influence a score when the run did not measure an invoked agent.
# judge_model_id is deliberately NOT here: the judge does run, so a judge change
# does move the numbers whatever the agent did.
AGENT_DEPENDENT_DIMENSIONS: list[str] = [
    "prompt_version_id",
    "model_id",
    "retrieval_config_hash",
    "envelope_hash",
    "corpus_chunk_count",
    "embedding_provider",
    "embedding_model_id",
]

# libpq connect_timeout, in seconds, for every psycopg2 connection this module
# opens. It is not decoration: a Neon endpoint mid-suspend, or a black-holing
# network path, accepts the TCP connection and never completes the startup
# handshake, and an unbounded connect() then blocks forever. Nothing else in
# this system would interrupt it — celery_app.py sets neither task_time_limit
# nor soft_time_limit. The write that matters most is the one on the FAILURE
# path: run_eval_suite calls update_eval_run_status from inside its `except`,
# so a blocked connect there holds a runtime worker slot indefinitely. Same
# value as the task's own reads.
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
# THIS PARAGRAPH USED TO READ "nothing in the shipped system produces tier >=
# human_verified yet: there is no correction UI", AND D6 MADE IT FALSE.
# `label_service.record_human_label` stamps `human_authored`, rank 3, which
# CLEARS VERIFIED_QA_MIN_TRUST_TIER (rank 2) outright. ADR 0003 then deleted the
# Clerk-authenticated route that drove it, so the writer ships with no caller,
# and the tier it stamps still clears the minimum the day a console feature
# calls it.
#
# The old argument was that promotion is "unreachable BY CONSTRUCTION rather than
# by an `if False`, and becomes reachable the moment a genuinely human-verified
# source exists, without anyone having to remember to remove a flag". That
# property has inverted from a feature into a hazard: the owner settled on
# 2026-08-08 that the labelling loop is EVAL-ONLY, so "it turns itself on the
# moment a human tier exists" is precisely what must not happen. The flag that
# paragraph was proud of not having is VERIFIED_QA_PROMOTION_DECISION["enabled"]
# below.
#
# TWO LOCKS, strongest first.
#   LOCK ZERO, NO CODE. No promotion writer exists in this tree at all
#       (ADR 0003). `run_eval_suite` returns a hardcoded `promoted: 0`. This is
#       the load-bearing lock, and the decision below is defence in depth behind
#       it.
#   the DECISION. `enabled: False`, recorded with its reason on every run, so
#       the disablement is a statement in the record rather than an absence a
#       later reader has to infer.
#
# Both locks are process-local. Neither is recorded in any database, and a
# second process running a different build carries its own copy.
# VERIFIED_QA_PROMOTION_DECISION below is a `MappingProxyType` so that
# `X["enabled"] = True` raises rather than lifting the lock for the life of a
# process. Rebinding the module attribute still works, and is pinned absent by
# test_label_downstream.py's mutation scan.
LABEL_TRUST_TIERS: dict[str, int] = {
    "unknown": -1,
    "model_generated": 0,
    "customer_negative": 1,
    "human_verified": 2,
    "human_authored": 3,
}

# The minimum tier a scenario must carry before its answer may be written into
# verified_qa, which retrieval_service serves to customers ahead of retrieval.
VERIFIED_QA_MIN_TRUST_TIER = "human_verified"

# The refusal string a promotion path would count a row under when the DECISION,
# rather than any property of the row, is what held it back. Named separately
# from the reason prose because it is the key a reader greps for. It is carried
# on every run inside VERIFIED_QA_PROMOTION_DECISION below; nothing in this
# build computes a refusals dict.
PROMOTION_DISABLED_REFUSAL = "promotion_disabled:eval_only"

# Recorded verbatim on every run in eval_runs.config so the disablement is a
# statement in the run record with a reason attached, rather than an absence a
# future reader has to infer. Copied (never handed out by reference) at every
# use site so a caller mutating the returned dict cannot poison the constant.
#
# KEPT FLAT ON PURPOSE. `build_eval_run_config` copies it with `dict(...)`, which
# is shallow, so a nested dict or list here would be handed out by reference and
# a caller mutating it would poison the constant for the process —
# test_promotion_decision_is_copied_not_shared only observes the top level.
#
# THE REASON HAS TO MOVE WHEN THE CODE MOVES. A run stamping a stale reason tells
# a later reader that the door is held by something that is no longer there. An
# absence a reader has to infer is bad; a stale statement a reader will believe
# is worse.
#
# READ-ONLY AT RUNTIME (D6 P3 review, finding 4). This was a plain dict, so
# `VERIFIED_QA_PROMOTION_DECISION["enabled"] = True` from any module in the
# process opened the customer-facing write for the life of that process, with no
# pin anywhere watching for it. `dict(...)` on a
# MappingProxyType still yields a fresh plain dict, so build_eval_run_config's
# copy semantics are unchanged.
VERIFIED_QA_PROMOTION_DECISION: Mapping[str, object] = MappingProxyType({
    "enabled": False,
    "min_trust_tier": VERIFIED_QA_MIN_TRUST_TIER,
    # Eval-only: a label improves what the eval can measure and reaches no
    # customer. Owner, 2026-08-08 (.dev/plans/260808-d6-labelling-loop.md).
    "scope": "eval_only",
    "decided_on": "2026-08-08",
    # The tier the shipped human-label writer stamps. Spelled as a literal here
    # because that writer imports THIS module, so the dependency cannot run the
    # other way; pinned equal across the boundary by a test rather than by hope.
    #
    # AND THE PROSE BELOW MAY NOT NAME THAT MODULE EITHER. R2's import-boundary
    # scan in test_label_provenance.py reads every non-docstring string constant
    # in this tree and refuses any that MENTIONS the writer's module or function
    # name — because `import_module("app.services." + "...")` is how the
    # full-dotted-path version of that scan was evaded. A prose mention would be
    # indistinguishable from that, so the reason describes the writer instead of
    # naming it. The scan fired on the first draft of this constant, which is
    # the only evidence worth having that it is still doing its job.
    "producible_label_tier": "human_authored",
    "refusal_reason": PROMOTION_DISABLED_REFUSAL,
    "reason": (
        "verified_qa is served to customers by retrieval_service."
        "verified_qa_lookup AHEAD of retrieval, so one mistyped label would be "
        "answered to a real customer with no eval between the typo and them. "
        "The owner's settled decision of 2026-08-08 is that the labelling "
        "loop is eval-only. TWO things hold it shut, strongest first: no "
        "promotion writer exists in this build at all, so this flag is "
        "defence in depth rather than the thing doing the work; and it reads "
        "false. Turning promotion on is a decision plus a code change, and "
        "never a migration."
    ),
})


# ---------------------------------------------------------------------------
# The tier a LABEL carries — which is not the tier its QUESTION's origin earns
# ---------------------------------------------------------------------------
# `eval_scenarios.source` answers "where did this QUESTION come from?". Until
# migration 0016 a tier could be resolved from nothing else, which is why
# LABEL_TRUST_TIERS declared human_verified and human_authored that nothing could
# produce. No source value let a human occupy a tier without also claiming to be
# the question's origin.
#
# A mined production failure whose answer the owner then writes by hand is
# `customer_negative` in ORIGIN and `human_authored` in LABEL, at the same time,
# and both statements are true. Collapsing them into one column is how a
# model_generated string ends up admitted on a human tier, which is the failure
# the label hierarchy exists to prevent. So the label carries its own tier, in
# alembic_tenant 0016's `label_trust_tier` column, and the row's `source` keeps
# meaning exactly what it meant before.
#
# THE TWO TIERS THAT ASSERT A HUMAN. Kept in step with 0016's CHECK constraint
# by test_the_human_tiers_match_the_migrations_check_constraint, which parses the
# migration rather than restating it.
HUMAN_LABEL_TIERS: tuple[str, ...] = ("human_verified", "human_authored")

# The eval_scenarios column 0016 adds. Named here so the read path and the write
# path (label_service) agree on one spelling.
LABEL_TIER_COLUMN = "label_trust_tier"

# The nightly selector's ONLY label predicate, spelled once for the whole system.
# `run_eval_suite` filters on exactly this text in all three of its scenario
# queries, and `label_service`'s UPDATE is scoped by the same predicate's
# negation, so a label write cannot reach a row the selector already counts as
# answered.
#
# ADR 0003 deleted the third consumer. `evals.py` carried a labelling queue built
# on that same negation, and the predicate lives HERE rather than in any one
# consumer because the consumers sit on opposite sides of an import wall.
# `label_service` may not import `app.api` (R2), and a service may not be a
# dependency of `app/api/v1/evals.py`. This module is the one every consumer
# already imports, which is still why one spelling serves them all.
#
# Kept honest across the module boundary by
# test_the_selector_is_the_only_thing_standing_between_the_two_states in
# tests/unit/test_label_downstream.py, which reads this constant back out of
# `inspect.getsource(run_eval_suite)`. If the task ever stops filtering on it,
# "unlabelled" and "will never be scored" have come apart, and that test is what
# makes it audible.
SELECTOR_ELIGIBILITY_PREDICATE = "reference_answer != ''"


def is_human_label_tier(tier: str | None) -> bool:
    """True iff *tier* is one of the two tiers that assert a human wrote it."""
    return tier in HUMAN_LABEL_TIERS


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

#: Which routing-table purpose produced each metric's score.
#:
#: Built by pairing the two tuples rather than by spelling `judge_` onto a metric
#: name. `PURPOSE_ROUTES` also routes `judge_retrieval_faithfulness`, which is a
#: different Judge in a different task, and a prefix rule would hand its route to
#: this one. `strict=True` holds the two tuples the same length, so a fifth
#: metric added without a purpose raises here rather than scoring under a Judge
#: nobody named.
JUDGE_PURPOSE_BY_METRIC: Mapping[str, str] = MappingProxyType(
    dict(zip(METRIC_KEYS, JUDGE_PURPOSES, strict=True))
)


def judge_identity_for(metric: str) -> JudgeIdentity | None:
    """Which Judge produced this dimension's score, at calibration's grain.

    The model and the effort come off `PURPOSE_ROUTES`, the same table the
    request was built from, so the record cannot name a Judge the run did not
    use. Reading them anywhere else would be a second copy free to drift from
    the first.

    Returns None when the route names no reasoning effort. Decision #34 priced
    the Judge floor at effort `none` and every judge route carries it today; a
    route that dropped it would leave the identity a field short, and a key with
    a hole in it groups two different Judges together. An absent identity says
    the Judge is unknown, which is what it would be.

    Args:
        metric: one of METRIC_KEYS, the dimension the score is about.
    """
    route = route_for(JUDGE_PURPOSE_BY_METRIC[metric])
    if route.reasoning_effort is None:
        log.error(
            "judge_identity.no_reasoning_effort",
            metric=metric,
            model=route.model,
            detail=(
                "the route names no effort, so the Judge cannot be identified "
                "and its verdicts cannot be calibrated against"
            ),
        )
        return None
    return JudgeIdentity(
        model=route.model,
        reasoning_effort=route.reasoning_effort,
        prompt_version=JUDGE_PROMPT_VERSION,
    )


def result_detail(score: Mapping[str, object], metric: str) -> dict:
    """The `eval_results.detail` payload for one (scenario, metric) row.

    The whole score row unchanged, plus the identity of the Judge that produced
    THIS metric. `detail` is where the identity lands because the row is
    already keyed by run and by dimension, which is the grain #53's
    CalibrationStatus compares on, and `detail` is the one column on that row
    that holds three fields at once. So a calibration figure reads one place per
    verdict and joins nothing to learn what ran. No column and no table is added
    for it.

    `judge_identity` is null when `judge_identity_for` could not name a complete
    Judge. A verdict whose Judge is unknown is unknown, never filed under the
    Judge that happened to run last.
    """
    identity = judge_identity_for(metric)
    return {
        **score,
        "judge_identity": dataclasses.asdict(identity) if identity else None,
    }


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
# Invoking the agent: the bounds, the floor, and the observation (D1/P2)
# ---------------------------------------------------------------------------
# THE COST SHAPE CHANGED, AND SILENTLY IF NOBODY BOUNDS IT. Before P2 a nightly
# eval was seconds of arithmetic plus judge calls. Now every selected row costs
# one live agent turn - the whole golden set unsampled, plus EXPLORATORY_SAMPLE_SIZE
# rotating rows — at a 90s per-turn ceiling, per agent, every night, billed. Two
# bounds, and both are stamped on the run so a reader can tell a cheap run from a
# truncated one instead of inferring it from a bill.

#: How many agent turns run at once. ONE, and the implementation asserts it
#: rather than trusting it: this box has 4 GB of RAM (CLAUDE.md environment
#: constraints). Until #49 each turn was also an Agent SDK subprocess, which was
#: the larger half of the reason; what remains is the RAM. Raising the number
#: without changing the loop would make the provenance say something the run did
#: not do, which is the defect class this whole phase exists to remove — so
#: eval.py raises on any other value instead of quietly running sequentially.
AGENT_INVOCATION_CONCURRENCY = 1

#: The per-run ceiling on live agent turns. The binding cost control: worst-case
#: wall clock for a run is this times the per-turn timeout.
#:
#: It sits BELOW GOLDEN_SET_SOFT_CEILING (200) on purpose, and the two disagree
#: on purpose. The golden set is unsampled because a paired per-item delta is the
#: only regression signal available at n=30; a tenant who designates more golden
#: rows than this gets the first AGENT_INVOCATION_MAX_CALLS_PER_RUN of them
#: invoked and the remainder reported as `ceiling_skipped`, golden-first, never
#: silently. Truncating the golden set breaks the pairing, so the breakage is
#: made loud (a warning and a counter) rather than resolved by guessing which of
#: the two ceilings the owner meant.
AGENT_INVOCATION_MAX_CALLS_PER_RUN = 60

#: The floor under a response rate, same shape and same value as
#: tests/evals/calibration/compute_correlation.py's MIN_PAIR_RATE (0.8) — and
#: the same argument: a metric computed over the rows that happened to succeed
#: is not a measurement of the set that was scored. Below it the run reports
#: 'unknown'. Not zero, not a low score: the absence of one.
MIN_RESPONSE_RATE = 0.8

#: The ABSOLUTE floor, and compute_correlation.py's MIN_PAIRS (3) is both the
#: shape and the value. A rate alone cannot refuse a one-observation run: a
#: tenant with a single labelled scenario that answers gives response_rate 1.0
#: and would certify itself as measured off one turn.
#:
#: An earlier comment here argued the opposite — "the denominator travels, so a
#: consumer can apply its own absolute floor". No consumer does, and the one
#: that would (the deploy gate) reads `agent_invoked`, which is computed HERE.
#: A floor that every consumer must remember to reapply is a floor nobody has.
#:
#: It is applied to the rows that reached the SCORER, not to the rows that
#: answered: those are different numbers once a responded-but-never-retrieved
#: row is excluded from context scoring, and the smaller of the two is the one
#: the metrics were actually computed over.
MIN_SCORED_OBSERVATIONS = 3

#: Slack added to a run's worst-case wall clock when deriving the idempotency
#: window in eval.py. The window has to COVER a run that consumes its whole
#: ceiling, or a redelivered message starts a second concurrent invocation of the
#: same agent; it was a flat 10 minutes against a 90-minute worst case.
EVAL_RUN_IDEMPOTENCY_SLACK_S = 600

#: Statuses for the invocation phase of a run.
AGENT_INVOCATION_NOT_STARTED = "not_started"   # the row exists; no turn ran yet
AGENT_INVOCATION_MEASURED = "measured"         # enough rows answered to measure
AGENT_INVOCATION_UNKNOWN = "unknown"           # too few did — never 'pass'

#: Recorded side-effect kinds that are TELEMETRY about a turn rather than a
#: decision the agent made. Counted on the run, never carried in full: one
#: retrieval_metrics row per retrieve call, times sixty scenarios, is tens of
#: kilobytes of float in a jsonb column nobody queries for it.
#:
#: Everything NOT named here is carried in full, which is the fail-open
#: direction that matters: a new `kind` someone adds to the tool layer arrives
#: as eval signal by default instead of vanishing into a counter.
SIDE_EFFECT_KINDS_TELEMETRY: tuple[str, ...] = ("retrieval_metrics.write",)

#: Cap on how many capability attempts are carried verbatim on one run. A run
#: that exceeds it says so (`capability_attempts_truncated`) rather than
#: silently reporting the first hundred as if they were all of them.
MAX_CAPABILITY_ATTEMPTS_RECORDED = 100


def _side_effect_attempts(records: list[dict]) -> dict:
    """The recorded-mode attempts one run collected, counted and carried. Pure.

    Everything NOT named in SIDE_EFFECT_KINDS_TELEMETRY is carried in full, which
    is the fail-open direction that matters: a new `kind` someone adds to the
    tool layer arrives as eval signal by default instead of vanishing into a
    counter. A run that exceeds MAX_CAPABILITY_ATTEMPTS_RECORDED says so through
    `capability_attempts_truncated` rather than silently reporting the first
    hundred as if they were all of them.
    """
    counts: dict[str, int] = {}
    capability_attempts: list[dict] = []
    truncated = False
    for record in records:
        for entry in record.get("side_effects") or []:
            kind = str(entry.get("kind", "unknown"))
            counts[kind] = counts.get(kind, 0) + 1
            if kind in SIDE_EFFECT_KINDS_TELEMETRY:
                continue
            if len(capability_attempts) >= MAX_CAPABILITY_ATTEMPTS_RECORDED:
                truncated = True
                continue
            capability_attempts.append(
                {
                    "scenario_id": record.get("scenario_id"),
                    "kind": kind,
                    "detail": entry.get("detail"),
                }
            )
    return {
        "counts": counts,
        "capability_attempts": capability_attempts,
        "capability_attempts_truncated": truncated,
    }


def summarise_agent_invocation(
    records: list[dict],
    *,
    valid: int,
    ceiling_skipped: int,
    ceiling_skipped_golden: int,
    per_turn_timeout_s: int,
    audit_capture_char_cap: int,
    retrieved_context_chunk_char_cap: int,
) -> dict:
    """Turn per-scenario invocation records into the run's observation. Pure.

    A SCENARIO WHOSE AGENT CALL FAILED IS EXCLUDED AND COUNTED, NEVER SCORED 0.
    Zero is not a low score, it is the absence of one — the lesson
    tests/evals/calibration/compute_correlation.py:485 already learned about a
    judge that errors. The same rule applies one layer earlier here: a turn that
    timed out, raised, or came back with no text produced no observation about
    answer quality, and averaging a zero in would move every metric with the
    failure rate of the transport rather than with the agent's behaviour.

    Args:
        records: one dict per scenario an agent turn was ATTEMPTED for, each
            carrying `responded` (bool), `scorable` (bool — reached the scorer),
            `error` (str|None), `retrieve_calls` (int), `retrieve_at_cap` (bool),
            `retrieve_unparsed` (int), `retrieved_chunks` (int),
            `pii_detector` (str|None — what the output firewall found in this
            turn's answer, so the run can say how many of its scored answers were
            the deflection rather than the agent's words) and `side_effects`
            (list of the entries recorded mode collected during that turn).
        valid: rows in the run that carry a label, i.e. that could have been
            invoked. `valid == len(records) + ceiling_skipped` always.
        ceiling_skipped: valid rows the per-run ceiling did not invoke.
        ceiling_skipped_golden: how many of those were golden rows — reported
            separately because skipping a golden row breaks the paired per-item
            delta the golden set exists for.
        per_turn_timeout_s: the wall-clock bound each turn ran under, carried so
            a reader never has to look it up in a different module's source at a
            different commit.
        audit_capture_char_cap: the cap on `tool_calls_log[*]["result"]`. It
            bounds the AUDIT copy of a retrieve result and NOT the contexts that
            were scored — recorded under a name that says so, because while it
            was called `retrieved_context_char_cap` it read as the bound on the
            evidence the judge saw, and the derived `retrieved_context_at_cap`
            was consequently true on essentially every retrieving turn.
        retrieved_context_chunk_char_cap: the cap that DOES bound the scored
            evidence — agent_tools.CHUNK_CONTENT_CHAR_LIMIT, applied per chunk.

    Returns:
        The `agent_invocation` provenance object. `status` is
        AGENT_INVOCATION_MEASURED only when all three hold: at least one turn was
        attempted, the response rate cleared MIN_RESPONSE_RATE, and at least
        MIN_SCORED_OBSERVATIONS rows reached the scorer. Otherwise
        AGENT_INVOCATION_UNKNOWN, which includes the zero-attempt case — a rate
        over an empty denominator is unknown, never a pass.
    """
    attempted = len(records)
    responded = sum(1 for r in records if r.get("responded"))
    scorable = sum(1 for r in records if r.get("scorable"))
    failed = sum(1 for r in records if r.get("error"))
    # Neither responded nor errored: the turn returned, with no text. That is the
    # max_turns / max_budget signature (agent.py's D-10 notes), and it is a
    # different failure from an exception, so it is counted apart from one.
    empty = attempted - responded - failed

    errors: dict[str, int] = {}
    for record in records:
        error = record.get("error")
        if error:
            errors[str(error)] = errors.get(str(error), 0) + 1

    # WHAT THE FIREWALL SUBSTITUTED, EACH BESIDE THE DENOMINATOR IT IS OUT OF.
    # A bare count repeats the missing-denominator error one layer down: three
    # deflected answers out of four scored and three out of sixty are different
    # runs, and the count alone cannot tell them apart. Both denominators are
    # already on this object — `responded` for every substituted answer,
    # `scorable` for the subset the metrics were computed over, which is the one
    # that explains a Faithfulness that fell. This replaces #50's deleted
    # `pii_firewall_applied=False`, which was a constant rather than a reading.
    deflected = [r for r in records if r.get("pii_detector")]
    detectors: dict[str, int] = {}
    for record in deflected:
        name = str(record["pii_detector"])
        detectors[name] = detectors.get(name, 0) + 1

    response_rate = (responded / attempted) if attempted else None
    # A SECOND, EXPLICIT DENOMINATOR. `response_rate` divides by what the ceiling
    # allowed; this divides by what the tenant designated, so a run that put 60
    # of 200 labelled rows to the agent cannot report full coverage. It is the
    # shape compute_correlation.py:498 uses (`pairs / parsed["valid"]`) and this
    # function's rate silently diverged from it.
    coverage_rate = (responded / valid) if valid else None
    status = (
        AGENT_INVOCATION_MEASURED
        if (
            attempted
            and response_rate is not None
            and response_rate >= MIN_RESPONSE_RATE
            # THE ABSOLUTE FLOOR, applied to the rows that reached the SCORER.
            # Without it a one-scenario run answers once, reports 1.0, and
            # certifies a deploy off a single observation; and a run where 38 of
            # 40 responses never retrieved would report 'measured' over the two
            # rows that did.
            and scorable >= MIN_SCORED_OBSERVATIONS
        )
        else AGENT_INVOCATION_UNKNOWN
    )

    return {
        "status": status,
        # (valid, attempted, responded) are three different claims, exactly like
        # (attempted, valid, scored) one layer up. `valid` is what could have
        # been invoked, `attempted` is what the ceiling allowed, `responded` is
        # what produced text. A rate built from any two of them without the
        # third understates or overstates.
        "valid": valid,
        "attempted": attempted,
        "responded": responded,
        # Rows that reached run_ragas_eval. Smaller than `responded` by exactly
        # the rows excluded for having no retrieved context — see `no_retrieval`
        # below — and it, not `responded`, is the denominator the metrics were
        # computed over.
        "scorable": scorable,
        "failed": failed,
        "empty": empty,
        "errors": errors,
        "ceiling_skipped": ceiling_skipped,
        "ceiling_skipped_golden": ceiling_skipped_golden,
        "response_rate": response_rate,
        "min_response_rate": MIN_RESPONSE_RATE,
        "coverage_rate": coverage_rate,
        "min_scored_observations": MIN_SCORED_OBSERVATIONS,
        "concurrency": AGENT_INVOCATION_CONCURRENCY,
        "max_calls_per_run": AGENT_INVOCATION_MAX_CALLS_PER_RUN,
        "per_turn_timeout_s": per_turn_timeout_s,
        # The worst case this run could have cost in wall clock, derived from the
        # two bounds rather than asserted beside them.
        "max_wall_clock_s": AGENT_INVOCATION_MAX_CALLS_PER_RUN * per_turn_timeout_s,
        # THE TRUNCATION, MADE EXPLICIT (the plan's P2, third bullet) — and
        # pointed at the cap that actually bounds the evidence. Faithfulness over
        # a context that was CUT marks a claim unsupported when the support was
        # merely beyond the cap, so `retrieved_context_at_cap` counts the turns
        # where at least one SCORED CHUNK came back exactly at the per-chunk
        # boundary. It used to count turns where the 1800-char AUDIT capture was
        # at ITS boundary, which five 2000-char chunks exceed by construction:
        # the figure was ~100% on every retrieving turn and read as signal.
        "audit_capture_char_cap": audit_capture_char_cap,
        "retrieved_context_source": "agent_retrieve_chunks",
        "retrieved_context_chunk_char_cap": retrieved_context_chunk_char_cap,
        "retrieved_context_at_cap": sum(
            1 for r in records if r.get("retrieve_at_cap")
        ),
        "retrieved_context_chunks": sum(
            int(r.get("retrieved_chunks") or 0) for r in records
        ),
        # Retrieve results whose framed payload could not be split back into
        # chunks. Counted apart from "retrieved nothing": a turn whose evidence
        # this build could not read did not retrieve nothing, and reporting it as
        # such would be the missing-data-as-passing-data error inverted.
        "retrieved_context_unparsed": sum(
            int(r.get("retrieve_unparsed") or 0) for r in records
        ),
        # Responded, called retrieve zero times. EXCLUDED FROM SCORING and
        # counted here: Faithfulness / ContextPrecision / ContextRecall over an
        # empty context list are structurally 0 or NaN, and a 0 for an answer the
        # agent gave correctly from its system prompt is the "zero is not a low
        # score" error one metric over. It is a bucket, not a failure — an agent
        # answering "what are your opening hours?" without retrieving is behaving
        # correctly, so these rows do not depress `response_rate`.
        "no_retrieval": sum(
            1 for r in records if r.get("responded") and not r.get("retrieve_calls")
        ),
        # Responded, retrieved, and still reached the scorer with nothing: every
        # retrieve result was unparsed or empty. Also excluded, also counted, and
        # kept apart from `no_retrieval` because the remedy is different.
        "retrieved_nothing_scorable": sum(
            1
            for r in records
            if r.get("responded")
            and r.get("retrieve_calls")
            and not r.get("scorable")
        ),
        "responses_deflected": len(deflected),
        "scored_responses_deflected": sum(1 for r in deflected if r.get("scorable")),
        "deflection_detectors": detectors,
        "side_effect_attempts": _side_effect_attempts(records),
    }


def invocation_provenance(agent_invocation: dict | None) -> dict:
    """The four D1 keys of `eval_runs.config`, derived from ONE observation.

    Called twice per run and that is the point: once by build_eval_run_config
    at INSERT, with None, and once by the task after the invocation phase, with
    the summary. Two derivations of "was the agent invoked" would be two chances
    to disagree, and the one that disagreed would be the one the deploy gate
    reads.

    `agent_invoked` IS THE GATE-FACING CLAIM AND IT IS A CONJUNCTION: the scored
    responses came from real agent turns AND enough rows answered to constitute a
    measurement. A run where six of sixty scenarios answered did invoke the agent
    and measured nothing, and a gate reading a bare "we called it" would ship on
    it — missing data treated as passing data, which is the rule this repo wrote
    down after the last time. A reader who wants the raw fact reads
    `agent_invocation["attempted"]`; the two claims stay separable, they just
    stay separate.

    None (the INSERT case) yields agent_invoked False, so a run that dies between
    its eval_runs row and its first turn fails closed at the gate rather than
    inheriting a hopeful default.

    `scored_response_source` is derived from what was SCORED, not from what was
    attempted. Deriving it from `attempted` meant a run that attempted sixty
    turns and got zero responses still claimed its scored responses came from
    the agent — a claim about a set that does not exist, and one a future
    consumer could read as evidence of an agent-sourced measurement.
    """
    invoked = bool(
        agent_invocation
        and agent_invocation.get("status") == AGENT_INVOCATION_MEASURED
    )
    observation = agent_invocation or {}
    attempted = int(observation.get("attempted") or 0)
    scorable = int(observation.get("scorable") or 0)
    if scorable:
        scored_response_source = EVAL_SCORED_RESPONSE_SOURCE
    elif attempted:
        scored_response_source = EVAL_RESPONSE_SOURCE_NONE_SCORED
    else:
        scored_response_source = EVAL_RESPONSE_SOURCE_PENDING
    return {
        "agent_invoked": invoked,
        "scored_response_source": scored_response_source,
        "dimensions_not_exercised": (
            [] if invoked else list(AGENT_DEPENDENT_DIMENSIONS)
        ),
        "agent_invocation": (
            dict(agent_invocation)
            if agent_invocation is not None
            else {"status": AGENT_INVOCATION_NOT_STARTED}
        ),
    }


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
#
# Why this harness calls metric.ascore() and never ragas.evaluate() (7.18):
#   The four collections metrics descend from SimpleBaseMetric, NOT from
#   `ragas.metrics.base.Metric`. `evaluate()` rejects anything failing
#   `isinstance(m, Metric)` at ragas/evaluation.py:133 with "All metrics must
#   be initialised metric objects" — a message about the class hierarchy that
#   reads like a message about instantiation. Every metric here was already an
#   instance; the call itself was the wrong door. `ascore() -> MetricResult` is
#   the door CLAUDE.md rule 4 names.


class _VoyageRagasEmbedding(BaseRagasEmbedding):
    """Ragas 0.4.x modern embedding over the same Voyage client the corpus uses.

    AnswerRelevancy is the only one of the four metrics that needs embeddings,
    and it takes them as a REQUIRED positional argument — `AnswerRelevancy(llm=llm)`
    raises TypeError before any scoring happens. Ragas ships openai / google /
    huggingface / litellm providers and no Voyage one, so this adapter exists to
    keep relevancy scored in the same vector space the corpus was embedded in
    (voyage-3, pinned by embedding_service). Scoring it through a different
    embedding model would measure the model swap, not the answer.
    """

    def embed_text(self, text: str, **kwargs) -> list[float]:  # noqa: ARG002
        result = _get_vo().embed([text], model=EMBEDDING_MODEL, input_type="query")
        return result.embeddings[0]

    async def aembed_text(self, text: str, **kwargs) -> list[float]:  # noqa: ARG002
        # The Voyage SDK client is sync; a thread keeps it off the event loop
        # that Ragas' async metric pipeline is running on.
        return await asyncio.to_thread(self.embed_text, text)


def _build_instructor_llm(purpose: str, ledger: LedgerContext) -> InstructorLLM:
    """The InstructorLLM one metric scores through, billed under its own purpose.

    The client is async. Collections metrics await `llm.agenerate(...)`
    exclusively, and `InstructorLLM.agenerate` raises
    TypeError("Cannot use agenerate() with a synchronous client") for any client
    whose `chat.completions.create` is not a coroutine function
    (`ragas/llms/base.py`, `_check_client_async`). `make_async_client` is the
    factory's answer to that, and it carries the same ledger hook as every other
    client here, so a Ragas run lands rows without Ragas knowing this exists.

    `thinking={"type": "disabled"}` is gone with the provider that needed it.
    It cleared a DeepSeek 400 on a forced tool_choice; OpenAI has no such
    parameter, and ragas splats every extra kwarg straight into
    `client.chat.completions.create()` (`ragas/llms/base.py:1109`), so leaving it
    in would put an unknown field on the wire.

    `provider="openai"` reaches `_map_openai_params`, which forces
    `temperature=1.0` and drops `top_p` for a model it reads as a reasoning
    model. Observed 2026-08-25 against ragas 0.4.3: `gpt-5.6-luna` is NOT one.
    `is_reasoning_model` parses the version out of `gpt-<version>-...` with
    `int()`, and `int("5.6")` raises, so the branch is never taken and
    `temperature=0` reaches the wire as written. The reasoning effort the Judge
    floor was priced at rides the instructor client's defaults instead, which is
    where `make_instructor_client` puts it.

    Args:
        purpose: the routing-table key this metric's calls bill under.
        ledger:  the ids each row carries and where it is written.
    """
    return InstructorLLM(
        client=ledger.instructor_client(purpose, is_async=True),
        model=route_for(purpose).model,
        provider=OPENAI_PROVIDER,
        # BACKLOG 8.2a. Same **kwargs seam: merged into `model_args`
        # (ragas/llms/base.py:772) and splatted into the client call by agenerate
        # (:1109). Ragas metrics ARE judges, so they get the same temperature as
        # every other verdict in the system.
        #
        # CORRECTED 2026-08-18 by adversarial review: this site was NOT sampling
        # at the provider default before 8.2a. ragas 0.4.3's InstructorModelArgs
        # defaults to `temperature=0.01, top_p=0.1` whenever `model_args is None`,
        # which is how this is constructed, and 0.01 was measured on the wire. So
        # the change here is 0.01 -> 0, not "unset -> 0", and the 8.2a commit's
        # "every LLM call sampled at whatever the provider defaults to" was false
        # for 2 of the 9 sites.
        #
        # STILL OPEN and deliberately not changed here: ragas also sends
        # `top_p: 0.1` alongside, and setting temperature and top_p together is
        # against both providers' guidance. Changing it changes eval behaviour
        # and wants its own measurement. BACKLOG 8.10.
        temperature=0,
    )


def _build_ragas_metrics(ledger: LedgerContext, embeddings) -> list:
    """The four M6 metrics (D-04 LOCKED) as constructed INSTANCES.

    Order matches METRIC_KEYS. Each metric's `.name` is the column it writes,
    which is what binds this list to _METRIC_ASCORE_ARGS and to METRIC_KEYS.

    One client per metric, not one shared between four. They are four separate
    spends and JUDGE_PURPOSES is what keeps them separable in the ledger.
    """
    faithfulness, relevancy, precision, recall = (
        _build_instructor_llm(purpose, ledger) for purpose in JUDGE_PURPOSES
    )
    return [
        Faithfulness(llm=faithfulness),
        AnswerRelevancy(llm=relevancy, embeddings=embeddings),
        ContextPrecision(llm=precision),
        ContextRecall(llm=recall),
    ]


async def _score_samples(metrics: list, samples: list) -> list[dict]:
    """Score every validated sample against every metric, one row per sample.

    A metric that raises for one sample yields None for that cell and nothing
    else: a failed measurement is `unknown`, never a zero, and never a reason to
    lose the three metrics that did return. The row carries user_input and
    reference because attribute_returned_rows matches on that pair.
    """
    rows: list[dict] = []
    for sample in samples:
        row: dict = {
            "user_input": sample.user_input,
            "reference": sample.reference,
        }
        for metric in metrics:
            kwargs = {
                name: getattr(sample, name)
                for name in _METRIC_ASCORE_ARGS[metric.name]
            }
            try:
                value = (await metric.ascore(**kwargs)).value
            except Exception as exc:  # noqa: BLE001 — one metric, one sample
                log.warning(
                    "run_ragas_eval.metric_failed",
                    metric=metric.name,
                    error=str(exc),
                )
                value = None
            row[metric.name] = (
                float(value) if value is not None and value == value else None  # NaN check
            )
        rows.append(row)
    return rows


def run_ragas_eval(scenarios: list[dict], ledger: LedgerContext) -> dict:
    """Run Ragas 0.4.x evaluation over a list of eval scenarios.

    Builds an EvaluationDataset from the scenarios, calls evaluate() with
    the four M6 metrics, and returns per-scenario scores plus per-metric means.

    D-02 LOCKED: Dataset field name is 'reference' (field was renamed in Ragas 0.4.x).

    THIS FUNCTION STILL TAKES NO CONNECTION STRING AND READS NO DATABASE. Every
    field the dataset needs is already in the scenario dicts. It used to accept a
    `branch_conn_str` it never referenced (`# noqa: ARG001`), which is how the
    Neon branch came to look load-bearing to every reader of the call site,
    including a caller that abandoned an entire run when the branch could not be
    created. An unused connection string invites a false isolation claim, and its
    ABSENCE is what test_scoring_takes_no_connection_string_because_it_opens_none
    pins. Ticket #47 added the one write that IS scoring's own. Every judge call
    leaves a `model_calls` row, and the dsn for it lives in the recorder inside
    `ledger`, closed over where the caller bound it.

    Args:
        scenarios: List of scenario dicts. Each must contain:
            - question (str): The user question.
            - reference_answer (str): Ground-truth answer (D-02).
            - agent_response (str): The agent's own response text, produced by
              the turn eval.py drove through agent_loop.build_agent_turn. It was
              the reference answer itself until D1/P2 — the metrics were then
              self-scoring and approached 1.0 by construction. A caller handing
              this the reference answer again reinstates the tautology, which is
              why the pin lives in the task's tests rather than here.
            - retrieved_contexts (list[str], optional): the contexts the AGENT
              retrieved during that turn, never the scenario's stored
              `retrieved_contexts` column. Scoring faithfulness against contexts
              the agent never saw is D1 in a different costume.
        ledger: who every judge call is billed to, and where its row goes.

    Returns:
        Dict with five keys:
            "scores": list[dict] — one dict per ATTRIBUTED returned row (not per
                input row: the judge may return fewer, and a row that cannot be
                matched to the scenario it scored is dropped, never assigned by
                position; see attribute_returned_rows).
                Each dict: {scenario_id, faithfulness, answer_relevancy,
                            context_precision, context_recall}
            "means": dict — per-metric mean over the attributed rows.
            "sent" / "returned" / "unattributed": the judge's own denominators.
                `returned < sent` is a partial outage; `unattributed > 0` means rows came back that cannot be placed.
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

    # EvaluationDataset is the schema check, and it is load-bearing: the loop
    # below reads the SAMPLES IT VALIDATED, not the dicts handed to it, so a
    # sample Ragas would reject never reaches a metric.
    dataset = EvaluationDataset.from_list(samples)

    metrics = _build_ragas_metrics(ledger, _VoyageRagasEmbedding())

    df = pd.DataFrame(asyncio.run(_score_samples(metrics, list(dataset.samples))))

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
        score_row: dict[str, object] = {"scenario_id": scenario_id}
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
            v for v in (score.get(col) for score in score_rows)
            if isinstance(v, (int, float))
        ]
        means[col] = (sum(values) / len(values)) if values else None  # type: ignore[assignment]

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
    `detail` carries the score row and the Judge that produced it (result_detail).

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
                        "detail": json.dumps(result_detail(score, metric)),
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
    agent_invocation: dict | None = None,
) -> dict:
    """Collect the configuration tuple an eval run is an assertion about.

    Without this, two runs that differ on exactly one dimension cannot be
    compared and "what changed?" is unanswerable — which is the whole mechanism
    of continuous improvement. Every dimension below was already captured
    somewhere in the system; none of them was ever stamped on the run.

    WHAT THE TUPLE CERTIFIES IS NOT AUTOMATICALLY WHAT THE RUN EXERCISED. The
    dimensions describe the configuration the agent is deployed with; whether the
    run's scores are a function of any of them depends on whether the agent was
    actually invoked and enough of it answered. `config["agent_invoked"]` /
    `config["scored_response_source"]` / `config["dimensions_not_exercised"]` /
    `config["agent_invocation"]` carry that, and they come from
    invocation_provenance so there is one derivation. The pairing is
    load-bearing: a tuple that records a dimension the measurement cannot see
    turns "two runs, one difference, identical scores" into a false finding of
    quality-neutrality — which is exactly what every pre-P2 run said.

    AT INSERT TIME THE HONEST ANSWER IS ALWAYS "NOT YET". This function runs
    before the first agent turn, because the eval_runs row is also the per-agent
    idempotency key and inserting it after sixty model calls would let a concurrent
    dispatch double-invoke. So `agent_invocation` is None here on the live path
    and the run is stamped agent_invoked=False; run_eval_suite patches the
    observed value in afterwards with update_eval_run_config. A run that dies in
    between keeps the False and fails closed at the deploy gate, which is the
    direction that costs a blocked deploy rather than a shipped tautology.

    Read from the same sources the deploy gate already reads, so a checklist and
    an eval run can never disagree about what the live configuration was:
    `deployment_service._compute_envelope_hash_sync` for the envelope hash (NOT
    a second projection of capability_envelopes — a divergent projection would
    silently produce a different hash for identical configuration) and
    `_fetch_corpus_stats_sync` for the corpus figure. Both are imported inside
    the function. deployment_service pulled in the Claude Agent SDK at module
    scope until #49, and eval_service sits on the FastAPI route import chain via
    api/v1/evals.py, so the deferral now buys deployment_service's own load alone.

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
        conn_str: PRODUCTION tenant connection string (the corpus figure describes the live corpus, not the branch's copy).
        dataset: dataset_composition() for the rows this run is about to score,
            or None when the caller has none to give. It lands verbatim on the
            run as `config["dataset"]`, which is what makes a golden-set score
            comparable to the next one: a score that moved and a MEMBERSHIP that
            moved are indistinguishable after the fact unless the run recorded
            which rows it covered. None is stored as null rather than as an
            empty composition — "this run did not record its dataset" is not
            "this run scored no rows".
        agent_invocation: summarise_agent_invocation()'s observation, or None when
            the invocation phase has not reported. None is the live path's only
            value; see the paragraph above.

    Returns:
        {"prompt_version_id": str | None, "config": dict}, for insert_eval_run().
    """
    unavailable: list[str] = []

    # --- prompt_version_id (control DB) --------------------------------
    # The PRODUCTION label specifically, never resolve_prompt_version's weighted
    # canary pick: an eval must be reproducible, and a run whose attribution was
    # decided by random.random() cannot be compared to the next one. None here
    # means "no production prompt version exists", a real state in which the
    # agent still runs off its live soul_* columns.
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
        # run_agent_turn uses. It describes the DEPLOYED configuration; whether
        # these scores are a function of it is a separate claim, carried by
        # agent_invoked / dimensions_not_exercised below.
        "model_id": AGENT_TURN_MODEL,
        # A judge change moves every score without the agent changing at all, so
        # both halves of its identity come off the routing table its clients are
        # built from and the record cannot name a Judge the run did not use.
        "judge_model_id": route_for(JUDGE_PURPOSES[0]).model,
        "judge_reasoning_effort": route_for(JUDGE_PURPOSES[0]).reasoning_effort,
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
        # These four say which of them the score is a function of, and they are
        # derived from the run's OWN observation rather than from a module
        # constant. Without them the tuple actively misleads: two nightly runs
        # differing on exactly one recorded dimension (config.model_id, say)
        # carrying statistically identical scores read as "the model swap is
        # quality-neutral", which is what a tautology looks like from the
        # outside. A configuration tuple that makes an uncomparable measurement
        # look comparable is worse than no tuple at all, so the exclusion
        # travels with every run rather than living in an audit nobody queries.
        **invocation_provenance(agent_invocation),
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


_UPDATE_EVAL_RUN_CONFIG_SQL = """
    UPDATE eval_runs
    SET config = COALESCE(config, '{}'::jsonb) || %(patch)s::jsonb
    WHERE id = %(id)s::uuid
"""


def update_eval_run_config(run_id: str, patch: dict, conn_str: str) -> bool:
    """Merge observed provenance into an existing eval_runs.config. PRODUCTION.

    The one write that turns `agent_invoked` from a hope into an observation.
    The row has to exist before the first agent turn — it is the per-agent
    idempotency key, and inserting it after sixty model calls would let a
    concurrent dispatch double-invoke — so the run is stamped agent_invoked=False
    at INSERT and corrected here once the invocation phase has reported.

    `||` is a SHALLOW jsonb merge, which is the semantics wanted: the whole
    `agent_invocation` object is replaced by the observed one rather than
    half-merged with the `{"status": "not_started"}` placeholder, and no key the
    caller did not name is disturbed.

    FAILURE LEAVES THE RUN CLAIMING LESS THAN IT DID, NEVER MORE. If this write
    does not land, the run keeps agent_invoked=False and the deploy gate refuses
    it. That is a blocked deploy on a run that was fine — annoying, and the right
    direction, because the other direction ships on a run whose measurement
    nobody can vouch for. So the exception is caught, logged at error level, and
    reported as False rather than failing a run that has already been scored.

    Tolerates a tenant DB that predates migration 0013 exactly as insert_eval_run
    does, and by the same narrow `UndefinedColumn` catch: a broad `except` here
    would swallow a genuine write failure and report the patch as applied.

    Args:
        run_id: UUID string of the eval_runs row.
        patch: the config keys to merge. Serialised as jsonb by this function.
        conn_str: PRODUCTION tenant connection string — never the eval branch.

    Returns:
        True when the patch landed; False when the column is absent or the write
        failed. Never raises.
    """
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
        try:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        _UPDATE_EVAL_RUN_CONFIG_SQL,
                        {"patch": json.dumps(patch), "id": run_id},
                    )
                conn.commit()
                return True
            except psycopg2.errors.UndefinedColumn:
                conn.rollback()
                log.warning(
                    "update_eval_run_config.config_column_absent",
                    run_id=run_id,
                    detail=(
                        "tenant DB predates alembic_tenant 0013 — the run cannot "
                        "record that the agent was invoked, so the deploy gate "
                        "will refuse it"
                    ),
                )
                return False
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "update_eval_run_config.failed",
            run_id=run_id,
            error=str(exc),
            detail=(
                "the run keeps agent_invoked=false and will be refused by the "
                "deploy gate — fail-closed, but the measurement is lost"
            ),
        )
        return False
