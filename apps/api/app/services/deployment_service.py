"""
M8 Deployment service: pre-deployment readiness orchestrator.

Architecture notes:
- Signals are collected synchronously (psycopg2) BEFORE the model is called.
- The orchestrator's prose turn runs on `app.services.tool_loop.run_tool_loop`
  (ticket #49, ADR 0008). Nothing here runs on the Agent SDK.
- The agent calls submit_report as a side-effect tool. The handler writes the
  report into result_container and `stop_after` ends the loop on that call, so
  the ack it returns is never sent to a second model call.
- asyncio.run(asyncio.wait_for(..., ORCHESTRATOR_TIMEOUT_S)) bridge in the Celery task.
- DEP-01 latency/cost signals deferred to M10 (OPS-04). M8 reads only eval, red team,
  verified QA, and corpus stats from the DB.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

import psycopg2
import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.model_client import LedgerContext, make_async_client, route_for
from app.domain.eval_result import (
    EVAL_DATASETS,
    EvalResult,
    InvalidEvalResult,
    metrics_of,
    run_level_metrics,
    unmeasured_metrics,
)
from app.domain.tool_def import ToolDefinition, tool
from app.domain.verdict import Outcome, Verdict
from app.services.calibration_service import load_calibration_status, summary_of
from app.services.capability_service import canonical_envelope_hash
from app.services.eval_service import EVAL_RUN_STATUS_COMPLETE, GATED_METRIC_KEYS
from app.services.tool_loop import run_tool_loop
from app.services.transactional.enforcement import _parse_rate_limit

#: The routing-table key every model call this orchestrator makes bills under,
#: and the row a rollup groups its spend by. The model comes from that row rather
#: than from a literal here, so the wire and a spend report cannot name two
#: different models. ADR 0008 routes this purpose to OpenAI.
ORCHESTRATOR_PURPOSE = "deployment_orchestrator"

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Signal states — the difference between "we measured this" and "we did not"
# ---------------------------------------------------------------------------
# Audit D3: the eval query raised UndefinedColumn on every run, the Celery task
# swallowed it and substituted `{"pass_rates": {}, ...}`, and the orchestrator's
# blocking condition "any eval metric pass_rate < 0.70" then evaluated against
# an empty dict — which cannot fire. The eval half of the deploy gate failed
# OPEN, silently, for the whole of its life.
#
# The column names are repaired below, but repairing them is the smaller half.
# An absent signal has to be REPRESENTABLE before it can be refused, so every
# signal collector reports which of these it is producing, and
# apply_signal_evidence_gate() turns anything other than MEASURED into a refusal
# to ship. A signal that cannot say it is absent will always be read as clean.

EVAL_SIGNAL_MEASURED = "measured"
EVAL_SIGNAL_NO_RUNS = "no_runs"
EVAL_SIGNAL_NO_VALID_SCORES = "no_valid_scores"
EVAL_SIGNAL_UNAVAILABLE = "unavailable"
# Audit D1 (P3). The fifth state, and the only one whose scores EXIST and are
# still not evidence. eval.py:374-375 read
#     "agent_response": row[3],   # row[3] IS reference_answer
# so Ragas scored the reference answer against the contexts that reference
# answer was written from. Faithfulness and AnswerRelevancy approach 1.0 BY
# CONSTRUCTION, the agent is never invoked, and the resulting run arrives at
# this collector as a full set of high pass_rates over thirty scenarios. Every
# other absent-signal state is absent by having nothing; this one is absent by
# having a number that is about the label rather than about the agent.
#
# So the score is suppressed, exactly as it is in the other three states.
# Letting a tautology's 0.99 travel while the recommendation blocks would
# reproduce BACKLOG 5.4 one layer down: the orchestrator narrates the number it
# was given, the owner reads "excellent answer quality" above a refusal, and
# the prose is the part they believe.
EVAL_SIGNAL_AGENT_NOT_INVOKED = "agent_not_invoked"

# The sixth state (P3 review). A run whose own terminal status is 'failed' is
# not a completed measurement, however many scores survived it — and after P2
# "failed WITH a full set of scores and agent_invoked=true" is an ORDINARY
# outcome rather than an exotic one. `run_eval_suite` patches the invocation
# claim in BEFORE scoring (eval.py:1082-1083, deliberately: the invocation is
# the expensive, unrepeatable half) and marks the run 'complete' at
# eval.py:1146 — but `summarise_run_validity` runs AFTER that write, at :1155,
# and anything raising from there to the end of the body drops into the except
# at :1222, whose `_mark_failed_on_production` writes status='failed' over a
# row that already carries True and a full set of eval_results.
#
# The collector read that as EVAL_SIGNAL_MEASURED and the gate shipped on it:
# `last_run_status` has travelled on the payload since P1 and nothing anywhere
# gated on it. Same family as every other state here — a run that did not
# reach the end of its own body has no admissible account of what it covered,
# so its numbers are withheld like the rest.
EVAL_SIGNAL_RUN_FAILED = "run_failed"

# The seventh state (#51 slice 4). A run that completed, invoked the agent, and
# wrote no `eval_runs.result` record. Until this slice the collector rebuilt the
# run's numbers with its own `AVG`/`COUNT` over `eval_results`, a second
# arithmetic over one run, free to disagree with the console's, and the one that
# disagreed was whichever the deploy gate happened to read. The record is now the
# only derivation, so a run without one has no numbers at all and says so.
#
# Three things reach it and they read the same way here: a tenant DB that
# predates alembic_tenant 0022, a run that died before `write_eval_result`, and a
# stored payload that breaks a construction rule on the way out. The log says
# which. It is NOT reported as 'no_valid_scores', which is the claim that the
# judge produced nothing, which is a different failure with a different remedy.
EVAL_SIGNAL_NO_RECORD = "no_record"

# The eighth state (#54 review). The checklist STARTED this run and the run had
# not reached a terminal status when the checklist's ceiling expired. It is not
# `no_runs` — a run exists — and it is emphatically not the previous night's
# run, which is what the collector returns when asked: `_LATEST_RUN_SQL` filters
# `status <> 'running'`, so the in-flight run this checklist caused is invisible
# to it and the newest PRIOR terminal run answers in its place. The task
# therefore never calls the collector for a half the wait did not see finish,
# and substitutes this instead. Without it the report's eval numbers describe a
# run the checklist did not sequence, which is the exact staleness #54 exists to
# remove.
EVAL_SIGNAL_DID_NOT_FINISH = "did_not_finish"

RED_TEAM_SIGNAL_MEASURED = "measured"
# An agent that has never been security-tested. This state is the whole reason
# the signal field exists on the security half, and the P2 review found it
# missing: the collector logged `red_team_summary.no_runs`, then returned
# 'measured' anyway, so a brand-new agent with zero open findings — because zero
# attacks had ever been run against it — carried a signal asserting the security
# surface HAD been measured. Day 1 is exactly when that lie is told, and exactly
# when it matters. Zero findings from zero runs is not a clean result; it is the
# absence of a result, and 'measured' must never describe it.
RED_TEAM_SIGNAL_NO_RUNS = "no_runs"
RED_TEAM_SIGNAL_UNAVAILABLE = "unavailable"

# The security half's answer to EVAL_SIGNAL_RUN_FAILED (#54 review). The eval
# half has refused a run whose own terminal status is not 'complete' since the
# P3 review; the security half had no such state, so `run_red_team` writing
# status='failed' at its own except handler still produced a 'measured' signal
# with whatever open findings the table happened to hold. A run that fell out of
# its body part-way has no admissible account of what it attacked.
RED_TEAM_SIGNAL_RUN_FAILED = "run_failed"

# And the security half's answer to EVAL_SIGNAL_DID_NOT_FINISH, with a sharper
# edge on it. `_fetch_red_team_summary_sync` read `red_team_runs ORDER BY
# started_at DESC LIMIT 1` with NO status filter, so the 'running' row
# `run_red_team` inserts before it attacks anything satisfied "a run exists" and
# the collector returned MEASURED with zero open findings and the current
# build's coverage. An agent that had never been attacked read as clean. The
# status filter below closes the collector's half of that; the task substituting
# this signal, rather than calling the collector at all for a half the wait did
# not see finish, closes the other half.
RED_TEAM_SIGNAL_DID_NOT_FINISH = "did_not_finish"

#: Whether the verified-QA figures were read at all (#121, #131). The two
#: knowledge collectors carry the same signal vocabulary the two gated ones
#: already carry, for the same reason: a collector that never executed must
#: not answer in the vocabulary of one that did.
VERIFIED_QA_SIGNAL_MEASURED = "measured"
VERIFIED_QA_SIGNAL_UNAVAILABLE = "unavailable"

#: The same pair for the corpus counts.
CORPUS_SIGNAL_MEASURED = "measured"
CORPUS_SIGNAL_UNAVAILABLE = "unavailable"

# The only state either signal may be in for `ship` to survive the gate.
SHIPPABLE_SIGNAL = "measured"

# The one `eval_runs.status` that means "this run reached the end of its own
# body", imported from the module that WRITES it. `latest_run_record` asks the
# same question of the same column one module down, and two copies of the
# allow-list is how one of them comes to admit a status the other refuses.


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DeploymentWarning(BaseModel):
    """A single warning produced by the deployment readiness orchestrator."""

    warning_id: str          # unique slug, e.g. "verified_qa_low_count"
    category: str            # "eval_quality" | "security" | "knowledge_depth" | "corpus_coverage"
    message: str             # plain-language text for the non-technical owner
    severity_level: str      # "info" | "warning"


class DeploymentReport(BaseModel):
    """Full deployment readiness report returned by the orchestrator."""

    recommendation: Literal["ship", "ship_with_warnings", "block"]
    summary: str             # 2-3 plain-language sentences for the owner
    warnings: list[DeploymentWarning]
    eval_summary: dict       # raw signal from _fetch_eval_summary_sync
    red_team_summary: dict   # raw signal from _fetch_red_team_summary_sync
    verified_qa_stats: dict  # raw signal from _fetch_verified_qa_stats_sync
    corpus_stats: dict       # raw signal from _fetch_corpus_stats_sync
    # Phase 18 BLR-01: raw signal from _fetch_blast_radius_sync. Defaults to {}
    # so any existing construction site that predates this field still validates.
    blast_radius: dict = {}


# ---------------------------------------------------------------------------
# Orchestrator system prompt
# ---------------------------------------------------------------------------

_DEPLOYMENT_SYSTEM_PROMPT = """\
You are the pre-deployment readiness writer for a customer-service AI agent.

THE DECISION HAS ALREADY BEEN MADE. The platform computed it from this agent's
own evaluation and security records before this turn, and it is in the `verdict`
block of the signals below, together with every reason it turned on. You do not
decide, you do not weigh anything up, and there is no bar for you to apply. Your
job is to say in plain language what was decided and why.

Write for a non-technical business owner who is about to launch this agent.

THE SUMMARY. Two or three sentences, no jargon. Say what was decided and name
the reasons in the owner's own terms. `verdict.outcome` is one of 'ship',
'ship_with_warnings' and 'block'. Each reason carries what was looked at
(`signal`), what came back (`observed`) and what would have been needed
(`threshold`), and a reason marked `provisional` is one whose bar is still being
re-derived, which is worth saying if you mention it. Never contradict
`verdict.outcome` and never restate it as a different one.

THE WARNINGS. One per concern worth reading, each with a unique warning_id slug,
written as narrative rather than as a rule. The platform derives its own warning
for every verdict reason, for financial blast radius and for its evidence
checks, and merges them with yours by warning_id, so a concern you leave out is
not lost.

WHAT THE OTHER BLOCKS ARE.

eval_summary is the quality run. `eval_signal` says whether it measured
anything, and every number on it is null in each state but 'measured'. Not zero,
and not an empty object. The other states are 'no_runs' (never evaluated),
'no_record' (a run that finished and wrote down nothing about what it measured),
'no_valid_scores' (a run whose record reports no metric on any dataset),
'agent_not_invoked' (a run that scored something OTHER than this agent's own
answers), 'run_failed' (a run whose own terminal status is not 'complete'),
'did_not_finish' (a run that was still going when the platform stopped waiting)
and 'unavailable' (the signal could not be read). `agent_invoked` is the same
question asked of the run's own record: until recently the eval scored each
scenario's reference answer instead of asking the agent, so a run that does not
record having invoked the agent says nothing about it. Do not describe such a
run's quality at all, and do not read its absence as a no.

Two datasets, 'golden' and 'exploratory', are carried separately under `datasets`
and are never averaged: the golden set is fixed and runs in full, while the
exploratory sample rotates, so one number over both would move with the draw
while looking like a quality change. `pass_rates` carries a run-level number only
when exactly one dataset scored anything, and `pass_rates_dataset` names which
one, so quote that name whenever you quote those numbers. A metric whose
`measured` is false was not scored at all: it is unknown, never a zero and never
a pass. `scenario_count`, `valid_scenario_count` and `scored_scenario_count` are
three different denominators and collapsing them misreports the run, and
`denominator_source` says where all three came from. `failing_scenarios` is how
many the judge decided against and `unmeasured_scenarios` is how many it did not
decide at all.

eval_summary.calibration says whether the judge behind those scores has itself
been measured against human labels. Its status is one of 'calibrated',
'not_calibrated', 'not_calibrated_yet' and 'setup_error', and `reason` names
which absence it is when there is one: 'no_artifact' (nobody has written a
calibration figure yet), 'no_single_judge_identity' (this run's metrics came
from more than one judge), 'artifact_names_no_judge' (a figure exists and names
no judge), 'identity_mismatch' (a figure exists and measures a different judge),
'unreadable' and 'invalid'. `kappa`, `matthews` and the intervals are figures
about that judge's agreement with people, never a quality score for the agent,
so never quote them as one.

red_team_summary is the security run. `signal` says whether it measured
anything, and the counts are null when it did not: zero open findings from zero
runs is the absence of a result, never a clean one. `coverage_source` says
whether the coverage figures came from the run itself ('run') or describe what
today's code can test ('current_build'), which may not be what that run tested.

verified_qa_stats and corpus_stats are how much verified knowledge the agent has
to answer from. Each carries a `signal`, and every count and average beside it
is null when that signal is not 'measured'. The platform could not reach the
tenant's own database, so say that rather than describing an empty knowledge
base.
`row_count` is a count and is real at nought, but `avg_faithfulness` and
`avg_relevance` are null whenever there were no pairs to average, and a null
average is unknown rather than a low score.

blast_radius is what the agent is authorized to spend and what it has actually
spent. Keep the configured ceiling and the observed maximum as two separate
claims: a ceiling is what the owner authorized and a maximum is what has
happened. Never emit a warning for it.

Call submit_report exactly once.
"""
# THE PROMPT NAMES NO THRESHOLD, AND THAT IS CRITERION 2 OF TICKET 17 (#54).
# Until this release it listed every blocking, warning and ship condition with
# its number, and asked the model for a `recommendation` alongside the prose. So
# the deploy decision was a model-generated label reached off prose that quoted
# its own thresholds, which is issue #36. A model can restate a decision and it
# can restate it wrongly, and nothing downstream could tell the two apart
# because the label and the prose came out of one completion.
#
# `app.domain.verdict.decide()` is the whole decision now. Every number lives on
# a constant in that module and nowhere else, a Verdict refuses to hold an
# outcome that is not the fold of its own reasons, and `submit_report` has no
# `recommendation` field for the model to fill. The turn above can restate the
# decision and cannot reach it.
#
# AND NOTHING HERE OBSERVES THE MODEL OBEYING ANY OF IT (P3 review). The prompt
# tests are drift protection over a string, never evidence that the narration is
# constrained. What actually prevents the summary from praising a tautology's
# 0.99 is that _eval_summary does not put pass_rates on the payload at all
# outside EVAL_SIGNAL_MEASURED: the model cannot narrate a number it was not
# given. What prevents it from shipping a blocked agent is that the outcome the
# task persists never passes through this turn. Read every "the prompt says X"
# claim in this module as consistency, not as a control.
#
# apply_signal_evidence_gate stays under all of it as the one-way floor, and
# derive_blast_radius_warnings and derive_quality_warnings stay as the
# deterministic warnings: a financial or coverage gate must not depend on an LLM
# performing an arithmetic comparison (CLAUDE.md: programmatic core, agentic
# edges).


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

# BACKLOG 1.30 / 1.33 — the orchestrator's wall-clock ceiling.
#
# Defined HERE, in the service, because the Celery task imports this module and
# the reverse would be a cycle. Both entry points (`run_orchestrator` below and
# `worker/tasks/runtime/deployment.py`) read this one name, so the ceiling
# cannot drift between them — it already had, at 120.0 in one and 300.0 in the
# other, which is what an adversarial review caught.
#
# 120.0 was never measured against a real orchestrator turn because none had
# ever run (`3.10`). The first that did took 127s and was killed by it.
ORCHESTRATOR_TIMEOUT_S = 300.0


# BACKLOG 1.32. Spelled once, because three places must agree on it: the tool
# the model is GIVEN, the `stop_after` set that ends the loop, and the prompt's
# own prose telling the model to call it. 1.32 is what a disagreement costs. A
# tool described in the prompt and registered nowhere failed every checklist
# ever run with "Orchestrator did not produce a report".
SUBMIT_REPORT_TOOL_NAME = "submit_report"

# NO `recommendation` FIELD, and its absence is the enforcement (#54, #36). A
# field the model can fill is a field the model can fill wrongly, and the
# checklist would then have to decide which of two answers to persist. The
# outcome comes from `decide()` and reaches this turn already computed, so the
# only thing the tool accepts is prose.
_TOOL_SUBMIT_REPORT = {
    "name": SUBMIT_REPORT_TOOL_NAME,
    "description": "Submit the owner-facing write-up of the readiness decision the platform computed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "warning_id": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "eval_quality",
                                "security",
                                "knowledge_depth",
                                "corpus_coverage",
                            ],
                        },
                        "message": {"type": "string"},
                        "severity_level": {
                            "type": "string",
                            "enum": ["info", "warning"],
                        },
                    },
                    "required": ["warning_id", "category", "message", "severity_level"],
                },
            },
        },
        "required": ["summary", "warnings"],
    },
}


def build_report_tools(result_container: dict) -> list[ToolDefinition]:
    """Make `_TOOL_SUBMIT_REPORT` a tool the orchestrator can actually call.

    BACKLOG `1.32`. Until 2026-08-13 `_TOOL_SUBMIT_REPORT` was referenced
    **exactly once in the repository, its own definition.** It was passed to
    nothing. The orchestrator was instructed to "call submit_report" while
    holding no such tool, so it could never call it, the loop always fell
    through, and **every deployment checklist ever run failed with
    "Orchestrator did not produce a report".**

    This is audit defect **D4 exactly** — the one already found and fixed in
    `red_team_service` ("5 of 7 attackers were never given their tools, so they
    reported clean"). The same shape survived here because, per `3.10`,
    `run_orchestrator` had never been executed by anything.

    Since #49 the list this returns is the turn's whole tool set AND its whole
    allowlist. `tool_loop.dispatch` refuses any name that is not in it, so
    registering the tool and authorising it are one act, and the two cannot
    drift the way `3.7` records the SDK's three names drifting.

    The handler is a pure side-effect recorder, mirroring `report_finding`: it
    writes the report into `result_container` and returns a minimal ack. It
    never raises, and that is what keeps the loop's `stop_after` honest. A
    handler that raised would leave the container empty, so `dispatch_outcome`
    reports it as not run and the loop carries on to its turn ceiling rather
    than stopping on a report nobody filed.
    """

    @tool(
        _TOOL_SUBMIT_REPORT["name"],
        _TOOL_SUBMIT_REPORT["description"],
        _TOOL_SUBMIT_REPORT["input_schema"],
    )
    async def _submit_report(args: dict) -> dict:
        # First call wins, matching the loop's documented "capture the first
        # submit_report and stop" contract.
        result_container.setdefault("report", args)
        return {"content": [{"type": "text", "text": "report recorded"}]}

    return [_submit_report]


# ---------------------------------------------------------------------------
# iframe snippet helper
# ---------------------------------------------------------------------------

# Hosts that resolve to the machine running the browser, not to this API. A
# snippet carrying one of these is dead on every visitor's device.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


class SnippetNotConfigured(RuntimeError):
    """PUBLIC_API_BASE is unusable, so no embed tag can be issued.

    Mirrors storage_service.StorageNotConfigured: a missing or local-only
    configuration is an unavailable service, and callers translate this to 503
    rather than letting a dead snippet reach a customer's clipboard.
    """


def _refuse_unservable_base(name: str, base: str) -> None:
    """Refuse a base a visitor's browser cannot reach (production only).

    Empty and loopback are dead on every visitor's device; a non-https base
    is silently mixed-content-blocked on every https customer page, which is
    worse than dead because nothing logs it. #135 review, findings 8 and 9.
    """
    split = urlsplit(base)
    if not base or (split.hostname or "") in _LOOPBACK_HOSTS or split.scheme != "https":
        raise SnippetNotConfigured(
            f"{name} is {base!r} while ENVIRONMENT=production. A visitor's "
            "browser cannot reach it (empty, loopback, or not https), and "
            f"the loader would not warn. Set {name} to a public https origin."
        )


def _make_iframe_snippet(agent_id: str) -> str:
    """Return the embeddable widget script tag for the given agent.

    BACKLOG 7.1. This is the ONLY generator of that tag. It used to hardcode
    `https://widget.wchats.app` and emit no `data-api` at all, while the console
    computed a second, different snippet of its own and rendered that instead.
    The loader treats a missing API base as warn-and-continue
    (`apps/widget/embed/widget.js`), so the tag this function returned produced
    a widget that painted on the customer's site and could not reach anything.

    Both hosts now come from `Settings` (`WIDGET_CDN_BASE` and
    `PUBLIC_API_BASE`, documented in both `.env.example` files). An empty
    WIDGET_CDN_BASE, the default since #135, derives the bundle host as
    PUBLIC_API_BASE + "/wchats", where this API serves the synced bundle
    itself (main.py mounts apps/api/static/wchats there), so the production
    refusal below guards the derived host too. Trailing slashes are stripped
    so a base configured as `https://api.example.com/` cannot yield
    `https://api.example.com//`.

    PUBLIC_API_BASE defaults to `http://localhost:8000`, and the loader only
    warns when the API base is EMPTY — so the default is silent in a way the
    old empty-string default was not: no console signal, and no mixed-content
    block either, because http://localhost is potentially-trustworthy. It is
    therefore REFUSED outright when ENVIRONMENT == "production", the same way
    storage_service._get_s3() refuses S3_ENDPOINT_URL there. Refusing to issue
    a snippet is recoverable; a customer pasting one that calls their own
    visitors' machines is not.

    Outside production nothing is refused, and an empty PUBLIC_API_BASE
    derives a page-relative src that resolves against the customer's own
    site; the localhost default cannot hit that, and production refuses it.

    Raises:
        SnippetNotConfigured: ENVIRONMENT is production and either base is
            empty, loopback, or not https (a visitor's https page silently
            mixed-content-blocks an http script src, and the loader cannot
            warn about a script that never ran).
    """
    api_base = settings.PUBLIC_API_BASE.strip().rstrip("/")
    # #135: empty means "this API serves the bundle at /wchats" (main.py mount).
    configured_cdn = settings.WIDGET_CDN_BASE.strip().rstrip("/")
    cdn_base = configured_cdn or f"{api_base}/wchats"

    if settings.ENVIRONMENT == "production":
        _refuse_unservable_base("PUBLIC_API_BASE", api_base)
        if configured_cdn:
            _refuse_unservable_base("WIDGET_CDN_BASE", configured_cdn)

    return (
        f'<script src="{cdn_base}/widget.js" '
        f'data-agent="{agent_id}" '
        f'data-api="{api_base}" async></script>'
    )


# ---------------------------------------------------------------------------
# Signal collection functions (sync psycopg2 — safe in Celery tasks)
# CTL-08: conn_str is NEVER passed to log statements — only agent_id.
# ---------------------------------------------------------------------------


# Where the run's three counts came from. Since #51 slice 4 there is one answer
# and the constant exists to say so on the payload rather than to choose between
# sources. `eval_runs.result` is the record `run_eval_suite` writes once, at the
# end, out of the summaries it already holds. The two figures this collector used
# to pick between are both gone: the `COUNT(DISTINCT scenario_id)` over
# `eval_results` counted the scenarios the judge came BACK about, so it was
# bounded below by `scored` and could never expose the thirty-five rows a partial
# judge outage dropped, and `config["dataset"]` was the run's own account read
# through a second parser. A rate over a denominator that is not the run's is not
# the run's rate, and two parsers for one denominator is how they came to differ.
DENOMINATOR_SOURCE_EVAL_RECORD = "eval_result"

#: What the run reports when it has a record, and when it has none. Same two
#: words the console route reports, so an owner reading the deploy report and an
#: operator reading the eval list see one vocabulary.
RESULT_PRESENT = "present"
RESULT_ABSENT = "absent"

#: A metric with no reading, as JSON. `observations` is 0 rather than absent
#: because the shape has to match `Measurement.payload` key for key.
UNMEASURED_READING = {"value": None, "measured": False, "observations": 0}

#: A dataset the record does not report. Counts are null, not zero: "this run
#: covered no golden rows" and "this payload cannot say" are different claims and
#: a zero asserts the first about a question nobody asked.
_UNREPORTED_DATASET = {
    "scenario_count": None,
    "valid_scenario_count": None,
    "scored_scenario_count": None,
}


def _readings(metrics: dict, *, measured: bool) -> dict:
    """Four Measurements as JSON, suppressed whole when the signal is not evidence.

    `measured` is the SIGNAL's state, not the metric's. Every absent-signal state
    withholds its numbers: a tautological run's 0.99 and a failed run's full set
    of scores are both about something other than this agent's answer quality,
    and an orchestrator handed them narrates them above a refusal, which is the
    part a non-technical owner believes. Withheld reads as unmeasured, which is
    what those numbers actually are.
    """
    return {
        metric: (reading.payload if measured else dict(UNMEASURED_READING))
        for metric, reading in metrics.items()
    }


def _dataset_block(record: EvalResult | None, *, measured: bool) -> dict:
    """Per dataset: the three counts always, the four metrics only as evidence.

    The counts travel on every state, refusals included. A run that attempted
    forty and scored none is a different event from a run that attempted none,
    and the orchestrator is told to weigh a rate against its denominator.

    THE TWO HALVES ARE NEVER ADDED TOGETHER. The golden set is fixed and paired
    across runs; the exploratory sample rotates. One mean over both moves
    whenever the draw moves while looking like a quality change.
    """
    outcomes = record.datasets if record is not None else {}
    absent = unmeasured_metrics()
    return {
        "available": record is not None,
        **{
            name: (
                {
                    "scenario_count": outcomes[name].attempted,
                    "valid_scenario_count": outcomes[name].valid,
                    "scored_scenario_count": outcomes[name].scored,
                    "metrics": _readings(
                        metrics_of(outcomes[name]), measured=measured
                    ),
                }
                if name in outcomes
                else {
                    **_UNREPORTED_DATASET,
                    "metrics": _readings(absent, measured=False),
                }
            )
            for name in EVAL_DATASETS
        },
    }


def _pass_rates(metrics: dict, *, measured: bool) -> dict | None:
    """The run-level metrics as the bare {metric: value} the prompt has always read.

    None whenever there is no reading, NEVER an empty dict. Audit D3's fail-open
    was `{}` reaching "any eval metric pass_rate < 0.70", which cannot fire over
    nothing, and "all eval metrics >= 0.85", which is vacuously true over
    nothing. A null cannot be iterated into a clean bill of health by accident.

    An unmeasured metric is absent from the dict rather than present as a zero,
    and the whole dict is None when none of the four was measured. That is the
    ordinary state of a run whose two datasets both scored: there is no
    run-level number to pool them into, `datasets` carries the two halves, and
    apply_signal_evidence_gate reads its evidence there.
    """
    if not measured:
        return None
    rates = {
        metric: reading["value"]
        for metric, reading in metrics.items()
        if reading["measured"]
    }
    return rates or None


def _record_counts(record: EvalResult | None, *, measured: bool) -> dict:
    """The run's counts, its verdicts, where they came from, what it cost, its proxy.

    Every value is null without a record and none of them is zero. "This run
    covered nothing" is a measurement and a payload with no record did not make
    it. `invocation` is here too: it is the record's own counter block, which is
    absent for the same reason and under the summariser's own names.

    `failing_scenarios` and `unmeasured_scenarios` are the run's own per-scenario
    counts, summed over its datasets. Summing counts is not pooling means: the
    two halves may never be averaged into one rate, and "how many scenarios
    failed" is the same number whichever half they came from.

    THE TWO OF THEM FOLLOW `measured`, WHICH THE THREE DENOMINATORS DO NOT. The
    denominators describe the run's size and travel on a refusal so the owner can
    see what was blocked. `failing_scenarios: 0` is the nearest thing this
    payload has to a quality claim, and beside a refusal the orchestrator would
    narrate it as one, which is why `pass_rates` is suppressed there too.
    """
    if record is None:
        return {
            "scenario_count": None,
            "valid_scenario_count": None,
            "scored_scenario_count": None,
            "denominator_source": None,
            "result": RESULT_ABSENT,
            "invocation": None,
            "cost": None,
            "context_proxy_version": None,
            "failing_scenarios": None,
            "unmeasured_scenarios": None,
        }
    return {
        # attempted, the VALID denominator, and what actually scored.
        "scenario_count": record.attempted,
        "valid_scenario_count": record.valid,
        "scored_scenario_count": record.scored,
        "denominator_source": DENOMINATOR_SOURCE_EVAL_RECORD,
        "result": RESULT_PRESENT,
        "invocation": record.invocation.payload,
        "cost": record.cost.payload,
        "context_proxy_version": record.context_proxy_version,
        "failing_scenarios": record.scenarios_failed if measured else None,
        "unmeasured_scenarios": record.scenarios_unmeasured if measured else None,
    }


def _calibration_block(record: EvalResult | None) -> dict:
    """What the calibration artifact says about the Judge THIS run scored with.

    THE IDENTITY COMES OFF THE RUN'S OWN RECORD. `EvalResult.judge_identity` is
    run-level and is already None when the four metric routes disagree, and a
    payload with no record has no identity to ask about at all. Both reach the
    loader as None and come back as `no_single_judge_identity`, which is the
    honest reading. There is no one Judge here for an artifact to be about.

    NOTHING GATES ON THIS YET (#54). apply_signal_evidence_gate does not read the
    key and no warning is derived from it. It travels so that ticket 17 has a
    status to read and the orchestrator can name it in prose.

    THE EXPECTED VALUE TODAY IS `not_calibrated_yet` WITH REASON `no_artifact`.
    No calibration run against the platform's own Judge exists yet. The harness
    scores the five AI-SPEC 5.2 rubric dimensions rather than the four Ragas
    metrics `judge_identity_for` maps, and the judge it calls names no identity
    at all, so nothing it writes can be about the Judge an eval run stamps. The
    Slice 2 section of `.dev/traces/260830-calibration-status.md` traces that,
    and the owner's comment of 2026-08-30 on #58 makes scoring with the
    platform's Judge that ticket's prerequisite work.
    """
    identity = record.judge_identity if record is not None else None
    return summary_of(
        load_calibration_status(settings.CALIBRATION_ARTIFACT_PATH, identity)
    )


def _eval_summary(
    signal: str,
    *,
    last_run_at: str | None = None,
    last_run_status: str | None = None,
    record: EvalResult | None = None,
    agent_invoked: bool | None = None,
    detail: str | None = None,
) -> dict:
    """Build an eval signal payload in which absence is always distinguishable.

    EVERY NUMBER IS LIFTED OFF `record` (#51 criterion 1). This function computes
    no mean, no rate, no denominator and no count. `run_level_metrics` decides
    which dataset a run-level reading may come from and refuses to pool the two,
    and the per-scenario verdict counts are the ones the run reached at scoring
    time, off the JudgeRecords it built, and stored per dataset.

    THREE COUNTS, THREE CLAIMS. `scenario_count` is what the run attempted,
    `valid_scenario_count` how many of those could be scored at all, and
    `scored_scenario_count` how many produced a score. All three are null without
    a record, because a payload with no record knows none of them and a zero
    would assert that the run covered nothing.

    `failing_scenarios` and `unmeasured_scenarios` are two counts, not one. A
    scenario fails when a gated verdict went against it and is unmeasured when
    one of them was never reached. Both are null without a record, and the pair
    is why a judge outage is visible: nought failing out of forty undecided
    scenarios is not nought failing. The collector counted them itself, over
    `eval_results`, until the review pass; deleting those rows then read as a
    run in which nothing failed.

    `agent_invoked` DEFAULTS TO None, NOT False (audit D1, P3). False is the claim
    "this run looked and the agent was not invoked". None is "no run said either
    way", which is what a state with no run at all (no_runs, unavailable) has. Both
    are refused by apply_signal_evidence_gate, which tests `is not True`.
    """
    measured = signal == EVAL_SIGNAL_MEASURED
    lifted, lifted_from = run_level_metrics(record)
    metrics = _readings(lifted, measured=measured)
    return {
        "eval_signal": signal,
        "signal_detail": detail,
        # The D1 provenance claim, read out of eval_runs.config where
        # eval_service.invocation_provenance() writes it. True only when the
        # run both invoked the agent and got enough answers back to constitute
        # a measurement — it is a conjunction on the writing side, and this
        # side must not try to reconstitute either half.
        "agent_invoked": agent_invoked,
        "last_run_at": last_run_at,
        # A run that FAILED still has a started_at and now, since the P1
        # persistence split, still lands a terminal status on production. Its
        # timestamp must not be read as "an eval finished at T".
        "last_run_status": last_run_status,
        **_record_counts(record, measured=measured),
        "pass_rates": _pass_rates(metrics, measured=measured),
        # Which dataset the run-level reading was lifted from, null when no
        # single dataset produced one. A reader finding numbers here and no name
        # would be reading numbers nobody attributed.
        "pass_rates_dataset": lifted_from if measured else None,
        "metrics": metrics,
        "datasets": _dataset_block(record, measured=measured),
        "calibration": _calibration_block(record),
    }


def _agent_invoked_from_run_config(config: object) -> bool | None:
    """Read the D1 provenance claim out of an eval_runs.config JSONB payload.

    Returns True / False when the run recorded one, and None when it recorded
    nothing readable. THE THREE ARE KEPT APART AND ONLY ONE OF THEM SHIPS:

      True   — eval_service.invocation_provenance() observed that the agent was
               invoked AND that enough scenarios answered to be a measurement.
      False  — the same function looked and said no. A run below
               MIN_RESPONSE_RATE, or one that died before its first turn.
      None   — no claim exists. A run from before D1 (the whole of history), a
               run on a tenant DB provisioned before alembic_tenant 0013 and so
               having no `config` column at all, or a config whose value is not
               a bool.

    None IS NOT A MILDER FAILURE THAN False, and the caller must not treat it
    as one. Every eval run persisted before this branch was produced by the
    tautology at eval.py:374-375 and carries no such key, so a gate that
    refused only False would keep shipping on all of it — the exact shape of
    BACKLOG 3.1, where pre-P4 red-team runs still read signal='measured' with
    clean findings because absence was read as assent. The accepted consequence
    is that every pre-D1 run, and every pre-0013 tenant, fails closed until a
    fresh eval runs on the current build.

    A non-bool value is None rather than passed through. `bool("false")` is
    True, and a string is the shape a hand-written or externally-patched config
    would most plausibly arrive in; coercing it would turn the string "false"
    into a shipping signal.
    """
    if not isinstance(config, dict):
        return None
    invoked = config.get("agent_invoked")
    # `is True` / `is False` rather than isinstance: numpy bools and 0/1 ints
    # are not this claim either, and the gate's fail-closed direction means
    # anything unrecognised costs a blocked deploy rather than a shipped one.
    if invoked is True:
        return True
    if invoked is False:
        return False
    return None


#: The run's own columns, its config and its record, newest terminal run first.
#: `status <> 'running'` rather than an IN-list of terminal names: a status this
#: query has not heard of is still terminal, and excluding it would let an
#: in-flight run shadow the last finished one for the whole of a nightly eval.
#: kind is 'm6:{agent_id}', so a second agent sharing a tenant DB cannot have its
#: run read as this agent's.
_LATEST_RUN_SQL = (
    "SELECT id, finished_at, status, config, result FROM eval_runs "
    "WHERE kind = %s AND status <> 'running' "
    "ORDER BY started_at DESC LIMIT 1"
)

#: The same run on a tenant DB provisioned before alembic_tenant 0022 gave
#: `eval_runs` its `result` column. No run on such a tenant recorded what it
#: measured, which is what the collector reports.
_LATEST_RUN_PRE_0022_SQL = (
    "SELECT id, finished_at, status, config FROM eval_runs "
    "WHERE kind = %s AND status <> 'running' "
    "ORDER BY started_at DESC LIMIT 1"
)

#: And again before 0013 gave it `config`. Such a run records no invocation
#: claim either, so it fails closed at the gate.
_LATEST_RUN_PRE_0013_SQL = (
    "SELECT id, finished_at, status FROM eval_runs "
    "WHERE kind = %s AND status <> 'running' "
    "ORDER BY started_at DESC LIMIT 1"
)

def _latest_run(cur, conn, agent_id: str) -> tuple[tuple | None, object, object]:
    """The newest terminal run for this agent, with its config and its record.

    Two degradations, each narrow. `UndefinedColumn` on the wide SELECT means the
    tenant DB predates a migration, which is a degradation and not an outage: the
    narrower query still answers the question the gate is asking, minus a column
    the tenant cannot have. A broad except would hide a real read failure behind
    a payload that looks like a successful degraded read. The aborted transaction
    is rolled back before the connection will accept another statement.

    Returns:
        (row3, config, result), where row3 is (id, finished_at, status) or None and
        config and result are None on the tenants that cannot carry them.
    """
    for sql, width in (
        (_LATEST_RUN_SQL, 5),
        (_LATEST_RUN_PRE_0022_SQL, 4),
        (_LATEST_RUN_PRE_0013_SQL, 3),
    ):
        try:
            cur.execute(sql, (f"m6:{agent_id}",))
            row = cur.fetchone()
        except psycopg2.errors.UndefinedColumn:
            conn.rollback()
            log.warning(
                "deployment_service.eval_summary.column_absent",
                agent_id=agent_id,
                columns=width,
                detail="the tenant DB predates the migration that added it",
            )
            continue
        if row is None:
            return (None, None, None)
        return (tuple(row[:3]), row[3] if width > 3 else None, row[4] if width > 4 else None)
    return (None, None, None)


def _record_of(run_id: str, payload: object) -> EvalResult | None:
    """One run's stored record, or None when it has none that can be read.

    `eval_runs.result` is jsonb, so the column can hold a list or a bare string
    as readily as the object the writer put there. `EvalResult.from_payload`
    needs a mapping, and the check below refuses anything else, naming what the
    row holds. None reaches the collector as EVAL_SIGNAL_NO_RECORD,
    which blocks the deploy, so a record this function cannot read costs a
    refusal and never a shipped run.
    """
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        log.error(
            "deployment_service.eval_summary.record_unreadable",
            run_id=run_id,
            error=f"the stored record is a {type(payload).__name__}, not a mapping",
            detail="the stored record breaks a rule; the run reads as unmeasured",
        )
        return None
    try:
        return EvalResult.from_payload(payload)
    except InvalidEvalResult as exc:
        log_failure(
            log, "deployment_service.eval_summary.record_unreadable", exc, level="error",
            run_id=run_id,
            detail="the stored record breaks a rule; the run reads as unmeasured",
        )
        return None


# ---------------------------------------------------------------------------
# The wait the checklist does BEFORE it reads anything (#54, decision 19 rule 5)
# ---------------------------------------------------------------------------
# THE CHECKLIST USED TO GRADE WHATEVER IT HAPPENED TO FIND. It dispatched an eval
# on two of the seven eval signal states, dispatched no red team at all, and then
# ran the collectors straight away, so the first checklist ever executed reported
# eval_signal=no_runs about the eval it had started seconds earlier. Every number
# in that report described the state BEFORE the checklist acted.
#
# The three pieces below remove the race by construction rather than by widening
# a timeout: the task dispatches both jobs, waits here until each has a terminal
# row of its OWN, and only then collects. A run that does not finish inside the
# ceiling reads as an ABSENT measurement, never as the pre-dispatch summary, so a
# slow red team costs a blocked deploy rather than a stale one.
#
#   _dispatch_moment          the tenant DB's clock, so the boundary carries no skew
#   _latest_run_since         "has the run this checklist started finished yet,"
#                             "and which row was it?"
#   poll_terminal_statuses    one look at both runs; the broker holds the sleep between looks

#: The two statuses that END a run. `run_eval_suite` and `run_red_team` both write
#: one of them at the end of their bodies. Anything else, including a status this
#: build has never heard of, keeps the wait waiting: a name we cannot interpret is
#: not evidence that a run finished.
TERMINAL_RUN_STATUSES = frozenset({"complete", "failed"})

#: Both runs key on `kind` rather than an agent_id column, which neither table
#: has: 'm6:{agent_id}' for evals (matching _LATEST_RUN_SQL above) and
#: 'm7:{agent_id}' for red team (matching run_red_team's own INSERT and guard).
#: `started_at >= %s` is the whole point of the query. Without it the wait would
#: be satisfied by last night's terminal run and the checklist would go straight
#: back to grading a row it did not cause.
#:
#: `status <> 'running'` IS THE OTHER HALF OF THAT BOUNDARY (#128). The newest
#: row since the mark is not the question; the question is whether the run this
#: checklist started has finished. Without the predicate the checklist's own run
#: completes, the nightly beat starts a second one before the next poll, and
#: every poll from then on reads the newer 'running' row: the wait burns the
#: whole ceiling for a run that finished in minutes, and the id
#: `latest_eval_run_id_since` re-queries at collect time names a run still going,
#: so `read_eval_result` returns None and the report blocks with nothing in the
#: log naming why. Same predicate and same reasoning as `_LATEST_RUN_SQL` above:
#: a status this query has not heard of is still finished, so the one
#: non-terminal name is excluded rather than the terminal ones listed.
#:
#: `id::text` because the id is a uuid column and every consumer of it is a
#: string: `read_eval_result` and `read_red_team_result` both cast it back with
#: `%(id)s::uuid`, and a UUID object would have to be stringified somewhere in
#: between anyway.
_EVAL_RUN_SINCE_SQL = (
    "SELECT id::text, status FROM eval_runs "
    "WHERE kind = %s AND started_at >= %s AND status <> 'running' "
    "ORDER BY started_at DESC LIMIT 1"
)

_RED_TEAM_RUN_SINCE_SQL = (
    "SELECT id::text, status FROM red_team_runs "
    "WHERE kind = %s AND started_at >= %s AND status <> 'running' "
    "ORDER BY started_at DESC LIMIT 1"
)


def _dispatch_moment(conn_str: str) -> datetime:
    """The tenant DB's own clock, read at the moment the checklist dispatches.

    The boundary comes from the DB and not from the worker because both jobs
    INSERT their row with the DB's NOW(), and the wait compares `started_at >=
    since`. A worker clock a few seconds ahead would place the boundary after the
    row the checklist just caused, and the wait would then expire on a run that
    had actually finished. The worker clock is the fallback and it carries that
    risk, which is why the fallback is logged.
    """
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT now()")
                return cur.fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:
        log_failure(
            log, "deployment_service.dispatch_moment.tenant_clock_unread", exc,
            detail="falling back to the worker clock; skew can outlast the wait",
        )
        return datetime.now(timezone.utc)


def _latest_run_since(
    sql: str, kind: str, conn_str: str, since: datetime
) -> tuple[str, str] | None:
    """(id, status) for the newest FINISHED run of this kind started since `since`.

    None means no such row yet, or the read failed. Both keep the wait waiting
    and both end at the ceiling as an absent measurement, which is the
    fail-closed direction: a tenant DB we cannot reach must not resolve into a
    finished run.

    A run that is merely in flight is not a row this returns (#128), so a second
    run started after the awaited one cannot answer for it.
    """
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (kind, since))
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        log_failure(log, "deployment_service.run_status.unread", exc, kind=kind)
        return None
    return None if row is None else (row[0], row[1])


def latest_eval_run_status_since(
    agent_id: str, conn_str: str, since: datetime
) -> str | None:
    """The newest finished eval run this checklist could have started, by status."""
    row = _latest_run_since(_EVAL_RUN_SINCE_SQL, f"m6:{agent_id}", conn_str, since)
    return None if row is None else row[1]


def latest_red_team_run_status_since(
    agent_id: str, conn_str: str, since: datetime
) -> str | None:
    """The newest finished red-team run this checklist could have started."""
    row = _latest_run_since(_RED_TEAM_RUN_SINCE_SQL, f"m7:{agent_id}", conn_str, since)
    return None if row is None else row[1]


def latest_eval_run_id_since(
    agent_id: str, conn_str: str, since: datetime
) -> str | None:
    """The id of that same run, which is what its stored record is read by.

    ASKED SEPARATELY FROM THE STATUS, and once, after the wait has settled. The
    poll answers "has it finished yet" and is taken many times; this answers
    "which row was it" and is taken once. Folding them together would have
    `poll_terminal_statuses` carrying ids it has no use for through every
    continuation.
    """
    row = _latest_run_since(_EVAL_RUN_SINCE_SQL, f"m6:{agent_id}", conn_str, since)
    return None if row is None else row[0]


def latest_red_team_run_id_since(
    agent_id: str, conn_str: str, since: datetime
) -> str | None:
    """The id of the newest finished red-team run started at or after `since`."""
    row = _latest_run_since(_RED_TEAM_RUN_SINCE_SQL, f"m7:{agent_id}", conn_str, since)
    return None if row is None else row[0]


#: The same two tables and the same boundary as the since-readers above, asking
#: the one thing they exclude: whether a run is still going. The checklist's
#: idempotency guard reads this before it reaps a chain that has gone quiet,
#: because a clock alone cannot tell a dead chain from a queued one.
#:
#: `ORDER BY started_at ASC` rather than DESC, which is the other readers' order.
#: They want the newest FINISHED run because that is the one this checklist
#: caused; this wants the OLDEST still going, because that is the run a fresh
#: dispatch is refused in favour of and therefore the one a replacement checklist
#: has to wait on.
_EVAL_RUN_RUNNING_SINCE_SQL = (
    "SELECT id::text, started_at FROM eval_runs "
    "WHERE kind = %s AND started_at >= %s AND status = 'running' "
    "ORDER BY started_at ASC LIMIT 1"
)

_RED_TEAM_RUN_RUNNING_SINCE_SQL = (
    "SELECT id::text, started_at FROM red_team_runs "
    "WHERE kind = %s AND started_at >= %s AND status = 'running' "
    "ORDER BY started_at ASC LIMIT 1"
)


def running_runs_since(
    agent_id: str, conn_str: str, since: datetime
) -> dict[str, tuple[str, datetime]] | None:
    """This agent's jobs still running, of those started at or after `since`.

    Keyed "eval" and "red_team", each carrying (run id, started_at). A kind with
    nothing in flight is absent from the dict, so `{}` says the tenant DB was
    read and holds nothing of this agent's still going.

    NONE IS A DIFFERENT ANSWER FROM `{}`, AND A CALLER THAT MERGES THEM BREAKS
    THE POINT. None means the tenant DB could not be read. An outage is exactly
    what makes a chain go quiet, so reading an unread database as "nothing is
    running" would supply the evidence for reaping a live chain out of the same
    failure that silenced it.

    Both statements go over ONE connection. Two would double the connect cost of
    a read the guard takes on every first pass, and a tenant DB reachable for the
    first query but not the second is a distinction with no consequence here:
    either way the answer is None.
    """
    found: dict[str, tuple[str, datetime]] = {}
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                for name, sql, kind in (
                    ("eval", _EVAL_RUN_RUNNING_SINCE_SQL, f"m6:{agent_id}"),
                    ("red_team", _RED_TEAM_RUN_RUNNING_SINCE_SQL, f"m7:{agent_id}"),
                ):
                    cur.execute(sql, (kind, since))
                    row = cur.fetchone()
                    if row is not None:
                        found[name] = (row[0], row[1])
        finally:
            conn.close()
    except Exception as exc:
        log_failure(log, "deployment_service.running_runs.unread", exc, agent_id=agent_id)
        return None
    return found


def poll_terminal_statuses(
    known: Mapping[str, str | None],
    fetchers: Mapping[str, Callable[[], str | None]],
) -> dict[str, str | None]:
    """One look at every named run that has not already reported terminal.

    Pure apart from the fetchers the caller injects, and it takes ONE look
    rather than owning a loop. That is the whole shape of the #54 review's third
    finding: the loop it replaces slept inside the Celery task, on the same
    `runtime` queue as the two jobs it was waiting for, so on the documented
    local topology (one solo worker, `-Q pipeline,runtime`) the checklist held
    the only execution slot for the whole ceiling and neither job it had just
    dispatched could start. The wait could never be satisfied. The caller now
    polls once, re-queues itself with a countdown and returns, so the slot is
    free between looks.

    `known` carries the statuses already observed across earlier polls. A run
    that reported terminal once is never re-read: its status is the answer, and
    a later poll could only find a NEWER run started by something else.

    Returns:
        {name: terminal status, or None while the run has not finished}. The
        None is the point of the return type. It says this run did not finish,
        so the caller reports an absent measurement rather than reaching for
        whatever row the table happens to hold.
    """
    statuses: dict[str, str | None] = {name: known.get(name) for name in fetchers}
    for name, fetch in fetchers.items():
        if statuses[name] is not None:
            continue
        status = fetch()
        if status in TERMINAL_RUN_STATUSES:
            statuses[name] = status
    return statuses


def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch the most recent eval run's own record from the tenant DB.

    THE RUN OWNS ITS NUMBERS AND THIS FUNCTION READS THEM (#51 criterion 1).
    Until slice 4 this ran `SELECT metric, AVG(score), COUNT(score) ... GROUP BY
    metric` and a second `COUNT(DISTINCT scenario_id)` pair, so the deploy gate
    derived the run's quality figures a second time, in different SQL, from rows
    the console was aggregating a third way. Three arithmetics over one run, free
    to disagree, and the one that disagreed was whichever the gate happened to
    read. `run_eval_suite` now writes one `EvalResult` at the end of its body and
    every reader lifts from it.

    Audit D3 lived in the query this replaces. It selected `metric_name` and
    `run_id` against a table whose columns are `metric` and `eval_run_id`
    (alembic_tenant/0001:165-174), raised UndefinedColumn on every invocation,
    and the Celery task caught it and substituted an empty `pass_rates` dict, so
    the eval half of the gate failed OPEN, because "any eval metric pass_rate
    < 0.70" over `{}` cannot fire and "all eval metrics >= 0.85" over `{}` is
    vacuously true. The column names are gone with the query; the discipline they
    forced is not. Every absent state below returns a DISTINGUISHABLE value and
    apply_signal_evidence_gate refuses to ship on it.

    The seven states this can report, all different claims:
        measured:          a run exists, it completed, it invoked the agent, and
                           it wrote a record.
        no_runs:           no FINISHED eval run exists for this agent: either
                           nothing has ever been measured, or the only run is
                           still in flight. Both mean "there is no result to
                           read", and both are remedied by waiting for or
                           starting a run. The gate's day-1 path dispatches one
                           and run_eval_suite's own idempotency guard refuses the
                           duplicate.
        run_failed:        the run's own terminal status is not 'complete'. It
                           may carry a full set of scores and an invocation
                           claim; it did not reach the end of its own body, so
                           its account of what it covered is unreliable and its
                           numbers are withheld like every other absent state's.
        agent_not_invoked: a run exists and may well carry excellent scores, but
                           it does not record having asked the agent anything
                           (audit D1). The scores are about the dataset's own
                           reference answers.
        no_record:         the run completed and invoked the agent and wrote no
                           `eval_runs.result`. A tenant DB predating migration
                           0022, a run that died before `write_eval_result`, or a
                           stored payload that breaks a construction rule. The
                           run measured nothing that can be read.
        no_valid_scores:   the record exists and reports no metric on any
                           dataset. The judge produced no valid observation.
        unavailable:       the query could not be executed. We did not look.

    Returns dict with keys: eval_signal, signal_detail, agent_invoked,
    last_run_at, last_run_status, scenario_count, valid_scenario_count,
    scored_scenario_count, denominator_source, result, pass_rates,
    pass_rates_dataset, metrics, datasets, invocation, cost,
    context_proxy_version, failing_scenarios, unmeasured_scenarios, calibration.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            try:
                run_row, run_config, stored = _latest_run(cur, conn, agent_id)
                if run_row is None:
                    log.info(
                        "deployment_service.eval_summary.no_runs", agent_id=agent_id
                    )
                    return _eval_summary(
                        EVAL_SIGNAL_NO_RUNS,
                        detail="no eval run has ever been recorded for this agent",
                    )

                run_id = str(run_row[0])
                last_run_at = run_row[1].isoformat() if run_row[1] else None
                last_run_status = run_row[2]
                agent_invoked = _agent_invoked_from_run_config(run_config)
                record = _record_of(run_id, stored)

                # A RUN THAT DID NOT COMPLETE IS NOT A COMPLETED MEASUREMENT
                # (P3 review), asked first because it is the coarsest
                # admissibility question there is: a run that fell out of its own
                # body part-way has no reliable account of what it covered, so
                # neither its record nor its config claims are worth
                # interpreting. The reachable shape is not exotic. The
                # invocation claim is patched in BEFORE scoring, so a run that
                # scored everything, wrote its eval_results, marked itself
                # 'complete' and then raised one line later ends as
                # status='failed' carrying agent_invoked=true and a full record.
                if last_run_status != EVAL_RUN_STATUS_COMPLETE:
                    log.warning(
                        "deployment_service.eval_summary.run_did_not_complete",
                        agent_id=agent_id,
                        run_status=last_run_status,
                        recorded_claim=agent_invoked,
                    )
                    return _eval_summary(
                        EVAL_SIGNAL_RUN_FAILED,
                        last_run_at=last_run_at,
                        last_run_status=last_run_status,
                        record=record,
                        agent_invoked=agent_invoked,
                        detail=(
                            "the most recent eval run did not complete "
                            f"(status {last_run_status!r})"
                        ),
                    )

                # THE ROOT CAUSE IS REPORTED BEFORE THE SYMPTOM (audit D1, P3).
                # This is asked ahead of the record because a run can be in both
                # states at once and only one of them names what is wrong. A
                # pre-D1 run has scores AND no invocation claim; a below-floor
                # run writes no eval_results AND records agent_invoked=false.
                # Reporting the second as a missing record would send the owner
                # after the writer when the judge was never the problem.
                if agent_invoked is not True:
                    log.warning(
                        "deployment_service.eval_summary.agent_not_invoked",
                        agent_id=agent_id,
                        run_status=last_run_status,
                        recorded_claim=agent_invoked,
                    )
                    return _eval_summary(
                        EVAL_SIGNAL_AGENT_NOT_INVOKED,
                        last_run_at=last_run_at,
                        last_run_status=last_run_status,
                        record=record,
                        agent_invoked=agent_invoked,
                        detail=(
                            "the most recent eval run recorded that the agent "
                            "was not invoked"
                            if agent_invoked is False
                            else "the most recent eval run does not record "
                            "whether the agent was invoked at all"
                        ),
                    )

                if record is None:
                    # The run has no numbers, so this payload has none either.
                    # Every count on it is null rather than zero: a zero asserts
                    # that the run covered nothing, which is a measurement
                    # nobody made.
                    log.warning(
                        "deployment_service.eval_summary.no_record",
                        agent_id=agent_id,
                        run_id=run_id,
                        run_status=last_run_status,
                    )
                    return _eval_summary(
                        EVAL_SIGNAL_NO_RECORD,
                        last_run_at=last_run_at,
                        last_run_status=last_run_status,
                        agent_invoked=agent_invoked,
                        detail=(
                            "the most recent eval run recorded no result, so "
                            "what it measured cannot be read"
                        ),
                    )

                if not any(
                    reading.measured
                    for outcome in record.datasets.values()
                    for reading in outcome.metrics.values()
                ):
                    # The record exists and reports no metric on any dataset.
                    # Unknown, not clean. The counts still travel: a run that
                    # attempted forty and scored none is a different event from
                    # a run that attempted none.
                    log.warning(
                        "deployment_service.eval_summary.no_valid_scores",
                        agent_id=agent_id,
                        run_status=last_run_status,
                        attempted=record.attempted,
                    )
                    return _eval_summary(
                        EVAL_SIGNAL_NO_VALID_SCORES,
                        last_run_at=last_run_at,
                        last_run_status=last_run_status,
                        record=record,
                        agent_invoked=agent_invoked,
                        detail=(
                            "the most recent eval run produced no valid score "
                            "for any metric"
                        ),
                    )

                return _eval_summary(
                    EVAL_SIGNAL_MEASURED,
                    last_run_at=last_run_at,
                    last_run_status=last_run_status,
                    record=record,
                    agent_invoked=agent_invoked,
                )
            except Exception as exc:
                # An UNKNOWN rather than the zeros _fetch_verified_qa_stats_sync
                # returns. Zeros are a measurement; this is the absence of one.
                log_failure(log, "deployment_service.eval_summary.query_failed", exc, agent_id=agent_id)
                return _eval_summary(
                    EVAL_SIGNAL_UNAVAILABLE,
                    detail=f"eval signal could not be read: {type(exc).__name__}",
                )
    finally:
        conn.close()


# Where the coverage figures beside the red-team counts came from (P2 review).
# 'run' is the coverage the run that produced these counts recorded for ITSELF;
# 'current_build' is what today's code can test. They are different claims, and
# the second one silently rewrites history: the day P4 wires the four SDK
# attackers and flips SDK_ATTACKERS_CAN_PROBE, every stored run — including the
# three-of-seven runs from before the fix — would be described to the gate as
# seven-of-seven, because red_team_coverage() only ever describes the code that
# is running now.
COVERAGE_SOURCE_RUN = "run"
COVERAGE_SOURCE_CURRENT_BUILD = "current_build"

#: The one `red_team_runs.status` that means the run reached the end of its own
#: body. `run_red_team` writes it in the three `_store_completion` UPDATEs and
#: writes 'failed' from its own except handler, both in raw SQL — that module
#: holds no constant to import, so the name lives here beside the reader that
#: needs it.
RED_TEAM_RUN_STATUS_COMPLETE = "complete"

#: The newest run of THIS AGENT'S that finished, and its status. Two predicates,
#: each closing a way the security half used to answer about a run it was not
#: asked about.
#:
#: `status <> 'running'` is the #54 review's correction: without it the 'running'
#: row `run_red_team` INSERTs before it attacks anything satisfied "a run
#: exists", and the collector answered MEASURED with zero open findings for an
#: agent nothing had ever probed. A status this query has not heard of is still
#: terminal, so the one non-terminal name is excluded rather than the terminal
#: ones listed.
#:
#: `kind = %s` is #127's, and it is the eval half's filter arriving on the
#: security half. `red_team_runs` has no agent_id column, so 'm7:{agent_id}' is
#: the scope, matching what `run_red_team` INSERTs and what its own idempotency
#: guard reads. Without it, two agents on one tenant DB share this read: A's own
#: wait sees its run fail, because the since-readers ARE kind-scoped, and then
#: this query hands back B's completed run so A's report carries B's coverage as
#: measured. decide() reads the right record by id, so the verdict was safe; the
#: gate's coverage checks and the owner-facing summary were not.
_RED_TEAM_LATEST_SQL = (
    "SELECT started_at, status, coverage FROM red_team_runs "
    "WHERE kind = %s AND status <> 'running' "
    "ORDER BY started_at DESC LIMIT 1"
)

#: The same row on a tenant DB provisioned before alembic_tenant 0015 gave
#: `red_team_runs` its `coverage` column. Kind-scoped too: a degraded read must
#: lose the run's own coverage and nothing else. `kind` needs no rung of its own
#: at either width, because `red_team_runs` has carried it NOT NULL since
#: alembic_tenant 0001 created the table.
_RED_TEAM_LATEST_PRE_0015_SQL = (
    "SELECT started_at, status FROM red_team_runs "
    "WHERE kind = %s AND status <> 'running' "
    "ORDER BY started_at DESC LIMIT 1"
)


def _red_team_summary(
    signal: str,
    *,
    last_run_at: str | None = None,
    counts: dict[str, int] | None = None,
    coverage: dict | None = None,
    coverage_source: str | None = None,
    detail: str | None = None,
) -> dict:
    """Build a red-team signal payload in which absence is distinguishable.

    The security half of the deploy gate had two states where the eval half had
    four, and the missing one was the one that matters on day 1: an agent that
    has never been attacked has zero open findings, and zero open findings read
    as a clean bill of health. Every count is therefore None outside
    RED_TEAM_SIGNAL_MEASURED, for the same reason `pass_rates` is None outside
    EVAL_SIGNAL_MEASURED — a zero that nobody measured is not a zero.

    `deployment_blocked` stays False in the non-measured states and does NOT
    carry the refusal: it means "no open critical finding is known", which is
    true and useless when nothing was ever asked. apply_signal_evidence_gate
    refuses on the SIGNAL, never on this flag.
    """
    measured = signal == RED_TEAM_SIGNAL_MEASURED
    counts = counts if measured else None
    coverage = coverage if measured else None
    return {
        "signal": signal,
        "signal_detail": detail,
        "last_run_at": last_run_at,
        "deployment_blocked": bool(counts["critical"] > 0) if counts else False,
        "critical_count": counts["critical"] if counts else None,
        "high_count": counts["high"] if counts else None,
        "medium_count": counts["medium"] if counts else None,
        "low_count": counts["low"] if counts else None,
        "vectors_attempted": coverage["vectors_attempted"] if coverage else None,
        "vectors_valid": coverage["vectors_valid"] if coverage else None,
        "invalid_vectors": coverage["invalid_vectors"] if coverage else None,
        "coverage_complete": coverage["complete"] if coverage else None,
        # Which of the two claims the coverage figures are making. None when
        # there are no coverage figures at all.
        "coverage_source": coverage_source if coverage else None,
    }


def _coverage_from_run(stored: object) -> dict | None:
    """Read a red_team_runs.coverage JSONB payload, or None for any other shape.

    red_team.py stamps the run's OWN coverage onto the row at completion, so a
    run carries the coverage IT had rather than the coverage the reader's build
    has. A pre-0015 tenant (no column), a run that predates the write, or a
    payload missing any of the four keys all return None and the caller falls
    back to the current build with COVERAGE_SOURCE_CURRENT_BUILD attached —
    labelled, never silently substituted.

    Four keys, and a payload carrying more is read down to them. Ticket 15 added
    `k` and per-vector attempt counts beside them and folded the k requirement
    into `complete`, so `complete` is now False for a run whose vectors all
    reported but did not all finish their attempts — a state vectors_valid alone
    cannot express.
    """
    if not isinstance(stored, dict):
        return None
    required = ("vectors_attempted", "vectors_valid", "invalid_vectors", "complete")
    if any(key not in stored for key in required):
        return None
    return {
        "vectors_attempted": int(stored["vectors_attempted"]),
        "vectors_valid": int(stored["vectors_valid"]),
        "invalid_vectors": list(stored["invalid_vectors"] or []),
        "complete": bool(stored["complete"]),
    }


def _latest_red_team_run(cur, conn, agent_id: str) -> tuple[tuple | None, object]:
    """This agent's newest FINISHED red-team run, and whatever coverage it recorded.

    Two rungs, and each says which tenant DBs it serves. The wide query serves a
    tenant migrated to alembic_tenant 0015 or later, where `red_team_runs` has
    its `coverage` column. The narrow one serves a tenant provisioned before it,
    since tenant DBs are migrated at PROVISION time only. Same narrow-except
    degradation as the eval collector's pre-0022 and pre-0013 fallbacks:
    UndefinedColumn costs the run's own coverage and nothing else, and a broad
    except would hide a real read failure behind a payload that looks like a
    successful degraded read.

    BOTH RUNGS ARE KIND-SCOPED (#127). A degradation that drops a column must not
    also drop the agent, or a pre-0015 tenant answers with the run belonging to
    whichever agent finished last.

    Returns (row, stored_coverage) where row is (started_at, status) or
    (started_at, status, coverage). A None row means no run of this agent's has
    finished.
    """
    kind = (f"m7:{agent_id}",)
    try:
        cur.execute(_RED_TEAM_LATEST_SQL, kind)
        run_row = cur.fetchone()
        return run_row, (run_row[2] if run_row is not None else None)
    except psycopg2.errors.UndefinedColumn:
        # The aborted transaction must be rolled back before the connection will
        # accept another statement.
        conn.rollback()
        log.warning(
            "deployment_service.red_team_summary.coverage_column_absent",
            agent_id=agent_id,
            detail=(
                "tenant DB predates alembic_tenant 0015, so the run's own "
                "coverage cannot be read"
            ),
        )
        cur.execute(_RED_TEAM_LATEST_PRE_0015_SQL, kind)
        return cur.fetchone(), None


def _fetch_red_team_summary_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch the live open-finding severity summary from the tenant DB.

    OPS-15 (21-08): reads the first-class red_team_findings table — WHERE
    status='open' GROUP BY severity — instead of parsing the red_team_runs
    findings JSONB blob. deployment_blocked is True iff there is at least
    one open critical finding, so a live critical finding always drives the
    deploy gate to recommendation='block' regardless of which run produced
    it (a finding stays "live" across runs until contained/closed via
    POST /red-team/findings/{id}/contain).

    last_run_at still comes from the most recent red_team_runs row —
    red_team_findings has no run timestamp of its own.

    THREE WAYS A RUN IS NOT A MEASUREMENT, and all three used to read 'measured'.
    An EMPTY table gave a brand-new agent zero open findings, which is also what
    a surviving agent has (P2 review). An IN-FLIGHT row satisfied "a run exists",
    because the query had no status filter, so a never-probed agent read as clean
    whenever its own run outlasted the checklist's ceiling. A FAILED run reported
    findings belonging to whatever ran before it. `_RED_TEAM_LATEST_SQL` excludes
    'running' and a newest run that is not 'complete' reports
    RED_TEAM_SIGNAL_RUN_FAILED (#54 review).

    Coverage (P2) travels with the counts. Zero open findings means one of two
    very different things — "seven attack vectors ran and none succeeded" or
    "three ran and four could not probe at all" (audit D4) — and a gate reading
    only the counts cannot tell them apart. The figures come from the RUN when
    it recorded them (migration 0015's red_team_runs.coverage), and only from
    red_team_service.red_team_coverage() when it did not, with `coverage_source`
    saying which. Deriving them from the current build unconditionally would
    re-describe every historical run with today's numbers the moment P4 flips
    SDK_ATTACKERS_CAN_PROBE. red_team_service is imported inside the function: it
    pulled the Agent SDK there until #49, so the deferral buys its own load now.

    Returns dict with keys: signal, signal_detail, last_run_at,
    deployment_blocked, critical_count, high_count, medium_count, low_count,
    vectors_attempted, vectors_valid, invalid_vectors, coverage_complete,
    coverage_source. Every count is None outside the 'measured' state.
    """
    from app.services.red_team_service import red_team_coverage  # noqa: PLC0415

    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            run_row, stored_coverage = _latest_red_team_run(cur, conn, agent_id)
            last_run_at = run_row[0].isoformat() if run_row and run_row[0] else None
            last_run_status = run_row[1] if run_row else None

            if run_row is None:
                # Never security-tested. The findings query below would return
                # zeros, and those zeros describe an empty table rather than a
                # surviving agent.
                log.info(
                    "deployment_service.red_team_summary.no_runs", agent_id=agent_id
                )
                return _red_team_summary(
                    RED_TEAM_SIGNAL_NO_RUNS,
                    detail=(
                        "no red-team run has ever been recorded for this agent, "
                        "so its security surface is unmeasured"
                    ),
                )

            # A RUN THAT DID NOT COMPLETE IS NOT A COMPLETED MEASUREMENT, the
            # same question the eval collector asks first and the security half
            # never asked (#54 review). `run_red_team` writes status='failed'
            # from its own except handler, and the open-finding query below
            # counts findings across ALL runs, so a run that died on its second
            # attack vector produced a signal saying the surface had been probed
            # over counts belonging to whatever ran before it.
            if last_run_status != RED_TEAM_RUN_STATUS_COMPLETE:
                log.warning(
                    "deployment_service.red_team_summary.run_did_not_complete",
                    agent_id=agent_id,
                    run_status=last_run_status,
                )
                return _red_team_summary(
                    RED_TEAM_SIGNAL_RUN_FAILED,
                    last_run_at=last_run_at,
                    detail=(
                        "the most recent red-team run did not complete "
                        f"(status {last_run_status!r})"
                    ),
                )

            cur.execute(
                "SELECT severity, COUNT(*) FROM red_team_findings "
                "WHERE status = 'open' GROUP BY severity"
            )
            counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            for sev, cnt in cur.fetchall():
                if sev in counts:
                    counts[sev] = int(cnt)

            run_coverage = _coverage_from_run(stored_coverage)
            coverage_source = (
                COVERAGE_SOURCE_RUN
                if run_coverage is not None
                else COVERAGE_SOURCE_CURRENT_BUILD
            )
            coverage = run_coverage if run_coverage is not None else red_team_coverage()

            # The counts below were read. A collector failure substitutes
            # RED_TEAM_SIGNAL_UNAVAILABLE and the gate refuses to ship on it,
            # because zeros we could not read are not zeros.
            return _red_team_summary(
                RED_TEAM_SIGNAL_MEASURED,
                last_run_at=last_run_at,
                counts=counts,
                coverage=coverage,
                coverage_source=coverage_source,
            )
    finally:
        conn.close()


def _verified_qa_stats(
    signal: str,
    *,
    row_count: int | None = None,
    avg_faithfulness: float | None = None,
    avg_relevance: float | None = None,
    detail: str | None = None,
) -> dict:
    """Build the verified-QA payload, in which a count and an average differ.

    A COUNT over an empty table is a reading. Nought verified pairs is a fact
    about the tenant, and derive_quality_warnings is right to warn on it. An
    AVERAGE over those nought rows is not a reading at all, and the shipped
    `COALESCE(AVG(faithfulness), 0.0)` published it as a score of nought (#121),
    which is the one thing a faithfulness of 0.0 can never mean.

    So the count travels whenever the query executed, and the averages travel
    only when there were rows to average. Outside VERIFIED_QA_SIGNAL_MEASURED
    nothing travels: the read did not happen, and a metric over zero valid
    observations is unknown rather than a number.
    """
    measured = signal == VERIFIED_QA_SIGNAL_MEASURED
    averaged = measured and bool(row_count)
    return {
        "signal": signal,
        "signal_detail": detail,
        "row_count": row_count if measured else None,
        "avg_faithfulness": avg_faithfulness if averaged else None,
        "avg_relevance": avg_relevance if averaged else None,
    }


def _corpus_stats(
    signal: str,
    *,
    document_count: int | None = None,
    chunk_count: int | None = None,
    last_ingested_at: str | None = None,
    detail: str | None = None,
) -> dict:
    """Build the corpus payload. Every figure is a count, so measured decides.

    A count is a reading at nought, which is why this collector's own body has
    no honest-absence branch. The dishonest state was the Celery task's outage
    fallback (#131): it claimed nought documents about a tenant DB nobody
    reached, and the deploy page rendered that as an empty knowledge base.
    """
    measured = signal == CORPUS_SIGNAL_MEASURED
    return {
        "signal": signal,
        "signal_detail": detail,
        "document_count": document_count if measured else None,
        "chunk_count": chunk_count if measured else None,
        "last_ingested_at": last_ingested_at if measured else None,
    }


def _fetch_verified_qa_stats_sync(agent_id: str, conn_str: str) -> dict:
    """Row count and average scores from the tenant DB verified_qa table.

    Columns are 'faithfulness' and 'relevance' (see alembic_tenant migration
    0005). AVG is read raw: a NULL, whether from an empty table or from rows
    nobody scored, arrives as None and stays None all the way to the report.

    A read that never executed answers VERIFIED_QA_SIGNAL_UNAVAILABLE and counts
    nothing. It used to answer with the same zeros an empty table produced, so a
    missing verified_qa table and a tenant who has written no pairs were one
    value to every reader downstream.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT COUNT(*), AVG(faithfulness), AVG(relevance) "
                    "FROM verified_qa"
                )
                row = cur.fetchone()
                if row is None:
                    return _verified_qa_stats(
                        VERIFIED_QA_SIGNAL_UNAVAILABLE,
                        detail="the verified_qa aggregate returned no row",
                    )
                return _verified_qa_stats(
                    VERIFIED_QA_SIGNAL_MEASURED,
                    row_count=int(row[0]),
                    avg_faithfulness=None if row[1] is None else float(row[1]),
                    avg_relevance=None if row[2] is None else float(row[2]),
                )
            except Exception as exc:
                log_failure(log, "deployment_service.verified_qa_stats.query_failed", exc, agent_id=agent_id)
                return _verified_qa_stats(
                    VERIFIED_QA_SIGNAL_UNAVAILABLE,
                    detail=f"the verified_qa read failed: {type(exc).__name__}",
                )
    finally:
        conn.close()


def _fetch_corpus_stats_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch document and chunk counts from the tenant DB.

    Three counts, all of them readings at nought, so this body has one outcome.
    The signal is on the payload for the Celery task's outage fallback to answer
    with, and for a reader that must tell nought documents from no read at all.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM documents")
            doc_row = cur.fetchone()
            document_count = int(doc_row[0]) if doc_row else 0

            cur.execute("SELECT COUNT(*) FROM chunks")
            chunk_row = cur.fetchone()
            chunk_count = int(chunk_row[0]) if chunk_row else 0

            cur.execute("SELECT MAX(created_at) FROM documents")
            ts_row = cur.fetchone()
            last_ingested_at = (
                ts_row[0].isoformat() if ts_row and ts_row[0] else None
            )

            return _corpus_stats(
                CORPUS_SIGNAL_MEASURED,
                document_count=document_count,
                chunk_count=chunk_count,
                last_ingested_at=last_ingested_at,
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Blast-radius collector (BLR-01) — the one collector that reads the
# CONTROL DB via get_sync_db() rather than the tenant DB via psycopg2.
# capability_envelopes, tool_calls_audit and tenants are all control-DB
# tables, so this collector needs no tenant connection string at all —
# _fetch_blast_radius_sync takes only agent_id (CLAUDE.md rule 4 satisfied
# by construction, not by omission).
# ---------------------------------------------------------------------------

# Safe-default fallback the Celery task substitutes when
# _fetch_blast_radius_sync raises. Every figure is None, not 0 — a fallback
# of 0 would assert "this agent's largest authorized action is nothing",
# which is a false measurement rather than an honestly missing one (OD-1).
BLAST_RADIUS_DEFAULT_SIGNAL: dict = {
    "configured_max_single_action_cents": None,
    "configured_max_hourly_aggregate_cents": None,
    "observed_max_single_action_cents": None,
    "observed_max_hourly_aggregate_cents": None,
    "observed_window_days": settings.BLAST_RADIUS_OBSERVED_WINDOW_DAYS,
    "warn_threshold_single_cents": settings.BLAST_RADIUS_WARN_SINGLE_CENTS,
    "warn_threshold_hourly_cents": settings.BLAST_RADIUS_WARN_HOURLY_CENTS,
    "enabled_skill_count": 0,
}


def _resolve_blast_radius_thresholds(agent_id: str) -> tuple[int, int]:
    """Resolve the per-tenant blast-radius warning thresholds (OD-1b).

    Joins agents to tenants on agents.tenant_id and reads the two nullable
    tenants.blast_radius_warn_*_cents columns. A NULL column (or no row at
    all — an agent with no owning tenant should never happen, but the
    fallback is honest either way) falls back to the platform default in
    settings.BLAST_RADIUS_WARN_SINGLE_CENTS / _HOURLY_CENTS. COALESCE
    semantics are applied in Python, not in SQL, so the fallback value is
    always the live settings value rather than a value baked into the
    query at authoring time.

    Returns:
        (warn_threshold_single_cents, warn_threshold_hourly_cents)
    """
    with get_sync_db() as db:
        row = db.execute(
            text(
                "SELECT t.blast_radius_warn_single_cents, "
                "t.blast_radius_warn_hourly_cents "
                "FROM agents a JOIN tenants t ON a.tenant_id = t.id "
                "WHERE a.id = :agent_id"
            ),
            {"agent_id": agent_id},
        ).first()

    if row is None:
        return (
            settings.BLAST_RADIUS_WARN_SINGLE_CENTS,
            settings.BLAST_RADIUS_WARN_HOURLY_CENTS,
        )

    single_cents = row[0] if row[0] is not None else settings.BLAST_RADIUS_WARN_SINGLE_CENTS
    hourly_cents = row[1] if row[1] is not None else settings.BLAST_RADIUS_WARN_HOURLY_CENTS
    return (single_cents, hourly_cents)


def _fetch_blast_radius_sync(agent_id: str) -> dict:
    """Fetch the financial blast-radius signal from the CONTROL DB (BLR-01).

    This is the one signal collector that reads the control DB via
    get_sync_db() rather than the tenant DB via psycopg2.connect(conn_str,
    ...), because capability_envelopes, tool_calls_audit and tenants are
    all control-DB tables — it therefore needs no tenant connection string
    at all. Signature takes only agent_id.

    Reports two independent claims as four separately-named figures, never
    merged and never coerced from None to 0 (OD-1):

    - configured_max_single_action_cents / configured_max_hourly_aggregate_cents:
      what the owner has AUTHORIZED, derived from enabled capability_envelopes
      rows. None means "no ceiling configured" — either because at least one
      enabled skill carries no bound (a partially-bounded configuration is
      honestly reported as unbounded, never as the max of only the bounded
      rows) or because a rate_limit string failed to parse.
    - observed_max_single_action_cents / observed_max_hourly_aggregate_cents:
      what has actually HAPPENED, derived from successful tool_calls_audit
      rows over the trailing observed_window_days. None means "never
      observed" — a query that returned no qualifying rows.

    A configured None means "no ceiling configured"; an observed None means
    "never observed". These are different claims and callers must not
    collapse them (UI-SPEC D3, D4).

    Returns dict with keys: configured_max_single_action_cents,
    configured_max_hourly_aggregate_cents, observed_max_single_action_cents,
    observed_max_hourly_aggregate_cents, observed_window_days,
    warn_threshold_single_cents, warn_threshold_hourly_cents,
    enabled_skill_count.
    """
    observed_window_days = settings.BLAST_RADIUS_OBSERVED_WINDOW_DAYS

    with get_sync_db() as db:
        # --- configured single-action ceiling -------------------------------
        configured_max_row = db.execute(
            text(
                "SELECT MAX((constraints->>'max_amount_cents')::int) "
                "FROM capability_envelopes "
                "WHERE agent_id = :agent_id AND enabled = true"
            ),
            {"agent_id": agent_id},
        ).scalar()
        unbounded_single_count = db.execute(
            text(
                "SELECT COUNT(*) FROM capability_envelopes "
                "WHERE agent_id = :agent_id AND enabled = true "
                "AND constraints->>'max_amount_cents' IS NULL"
            ),
            {"agent_id": agent_id},
        ).scalar()
        # A partially-bounded configuration (some enabled skills capped, one
        # not) is honestly unbounded — the ceiling is only real if EVERY
        # enabled skill carries one (UI-SPEC D4.2's "No ceiling" verdict).
        configured_max_single_action_cents = (
            None
            if (unbounded_single_count or 0) > 0 or configured_max_row is None
            else int(configured_max_row)
        )

        # --- configured hourly aggregate ceiling (per-skill ceiling x rate) --
        enabled_rows = db.execute(
            text(
                "SELECT rate_limit, constraints->>'max_amount_cents' AS max_amount_cents "
                "FROM capability_envelopes "
                "WHERE agent_id = :agent_id AND enabled = true"
            ),
            {"agent_id": agent_id},
        ).fetchall()
        enabled_skill_count = len(enabled_rows)

        hourly_total = 0.0
        hourly_unbounded = False
        for rate_limit, max_amount_cents in enabled_rows:
            if rate_limit is None or max_amount_cents is None:
                hourly_unbounded = True
                break
            parsed = _parse_rate_limit(rate_limit)
            if parsed is None:
                hourly_unbounded = True
                break
            max_calls, window_secs = parsed
            calls_per_hour = (max_calls * 3600) / window_secs
            hourly_total += int(max_amount_cents) * calls_per_hour
        # Round up so the figure never understates authorized exposure.
        configured_max_hourly_aggregate_cents = (
            None if hourly_unbounded else math.ceil(hourly_total)
        )

        # --- observed single-action maximum (history, trailing window) ------
        observed_single_row = db.execute(
            text(
                "SELECT MAX(COALESCE((arguments->>'amount_cents')::int, "
                "(arguments->>'refund_amount_cents')::int)) "
                "FROM tool_calls_audit "
                "WHERE agent_id = :agent_id AND error IS NULL "
                # CAST(:p AS text), never :p::text — SQLAlchemy's bindparam regex
                # is (?<![:\w\x5c]):(\w+)(?!:), whose trailing (?!:) exists to avoid
                # matching PostgreSQL's :: cast. Against `:window_days::text` it
                # backtracks one character and silently binds `window_day`, so the
                # value this call site passes matches nothing, the literal `:`
                # reaches Postgres, and the statement raises. It did, on every
                # checklist run, since Phase 18. See tests/unit/
                # test_sql_paramstyle_collisions.py, which gates the whole class.
                "AND created_at > now() - (CAST(:window_days AS text) || ' days')::interval"
            ),
            {"agent_id": agent_id, "window_days": observed_window_days},
        ).scalar()
        observed_max_single_action_cents = (
            int(observed_single_row) if observed_single_row is not None else None
        )

        # --- observed hourly aggregate maximum (max over hour buckets) ------
        observed_hourly_row = db.execute(
            text(
                "SELECT MAX(hourly_total) FROM ("
                "  SELECT SUM(COALESCE((arguments->>'amount_cents')::int, "
                "  (arguments->>'refund_amount_cents')::int, 0)) AS hourly_total "
                "  FROM tool_calls_audit "
                "  WHERE agent_id = :agent_id AND error IS NULL "
                # CAST(:p AS text), never :p::text — see the note on the query above.
                "  AND created_at > now() - (CAST(:window_days AS text) || ' days')::interval "
                "  GROUP BY date_trunc('hour', created_at)"
                ") hourly_buckets"
            ),
            {"agent_id": agent_id, "window_days": observed_window_days},
        ).scalar()
        observed_max_hourly_aggregate_cents = (
            int(observed_hourly_row) if observed_hourly_row is not None else None
        )

    warn_threshold_single_cents, warn_threshold_hourly_cents = (
        _resolve_blast_radius_thresholds(agent_id)
    )

    return {
        "configured_max_single_action_cents": configured_max_single_action_cents,
        "configured_max_hourly_aggregate_cents": configured_max_hourly_aggregate_cents,
        "observed_max_single_action_cents": observed_max_single_action_cents,
        "observed_max_hourly_aggregate_cents": observed_max_hourly_aggregate_cents,
        "observed_window_days": observed_window_days,
        "warn_threshold_single_cents": warn_threshold_single_cents,
        "warn_threshold_hourly_cents": warn_threshold_hourly_cents,
        "enabled_skill_count": enabled_skill_count,
    }


# ---------------------------------------------------------------------------
# Envelope-hash reader (BLR-02) — the sync twin of api/v1/deployment.py's
# async reader. Both project exactly capability_service.HASHED_ENVELOPE_FIELDS
# at the query layer and delegate hashing to the one shared canonicaliser, so
# the checklist task and the approve route can never disagree on what "the
# current envelope" hashes to.
# ---------------------------------------------------------------------------


def _fetch_envelope_rows_sync(agent_id: str) -> list[dict]:
    """Read this agent's capability_envelopes rows for the BLR-02 envelope hash.

    Projects exactly the seven capability_service.HASHED_ENVELOPE_FIELDS
    columns (skill, enabled, rate_limit, constraints, requires_confirmation,
    requires_identity_verification, actor_mode) at the query layer — id,
    agent_id and updated_at are never in the SELECT list, which makes it
    structurally impossible for a non-semantic, DB-managed column to reach
    the hash. That is a stronger guarantee than relying on the canonicaliser
    to drop them after the fact.

    ORDER BY skill is defensive, not load-bearing — the canonicaliser itself
    sorts the projected rows, so input row order never varies the hash.

    Returns a list of plain dicts, one per envelope row, ready to hand
    directly to capability_service.canonical_envelope_hash.
    """
    with get_sync_db() as db:
        rows = (
            db.execute(
                text(
                    "SELECT skill, enabled, rate_limit, constraints, "
                    "requires_confirmation, requires_identity_verification, "
                    "actor_mode FROM capability_envelopes "
                    "WHERE agent_id = :agent_id ORDER BY skill"
                ),
                {"agent_id": agent_id},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _compute_envelope_hash_sync(agent_id: str) -> str:
    """Sync twin of api/v1/deployment.py's async envelope-hash reader (BLR-02).

    Both this function (called from run_deployment_checklist at checklist-run
    time) and the approve route's reader delegate hashing to the same
    capability_service.canonical_envelope_hash — a divergence between the two
    readers' field projections would fire the BLR-02 drift gate on every
    single deploy, since the two sides would then be hashing different data
    for what is supposed to be the identical live configuration.
    """
    rows = _fetch_envelope_rows_sync(agent_id)
    return canonical_envelope_hash(rows)


# ---------------------------------------------------------------------------
# The evidence gate (P2) — deterministic, in Python, never the orchestrator's
# arithmetic. Same division of labour as derive_blast_radius_warnings above:
# the LLM narrates, the platform decides.
# ---------------------------------------------------------------------------

# Substituted by the Celery task when a collector raises. Each key is present
# with an honestly absent value rather than a plausible zero — the old eval
# substitution was `{"pass_rates": {}, "failing_scenarios": 0}`, which asserts
# "no metric is failing" about a query that never executed.
# Built through the same constructor the collector uses, so the substitute and a
# real absent signal cannot drift apart key-for-key. It was a hand-written dict
# until #51 slice 4, and the two zeros it carried (`scenario_count`,
# `scored_scenario_count`) were the last place in this module where a collector
# that never executed still asserted a count. `agent_invoked` is None and not
# False for the same reason: the collector raised, so no run was asked.
EVAL_SUMMARY_UNAVAILABLE_SIGNAL: dict = _eval_summary(
    EVAL_SIGNAL_UNAVAILABLE,
    detail="the eval signal collector raised",
)

RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL: dict = _red_team_summary(
    RED_TEAM_SIGNAL_UNAVAILABLE,
    detail="the red-team signal collector raised",
)

# The last two plausible zeros in the substitution set (#131). These two gate
# nothing, which is why they outlived the gated pair, but they are read by the
# owner's report and by derive_quality_warnings, and
# `{"row_count": 0, "avg_faithfulness": 0.0, "avg_relevance": 0.0}` told both
# that a tenant DB nobody reached holds no reviewed answers scoring nought.
VERIFIED_QA_STATS_UNAVAILABLE_SIGNAL: dict = _verified_qa_stats(
    VERIFIED_QA_SIGNAL_UNAVAILABLE,
    detail="the verified-QA collector raised",
)

CORPUS_STATS_UNAVAILABLE_SIGNAL: dict = _corpus_stats(
    CORPUS_SIGNAL_UNAVAILABLE,
    detail="the corpus collector raised",
)


# ---------------------------------------------------------------------------
# The two payloads the checklist substitutes for a job that never finished
# ---------------------------------------------------------------------------
# NEITHER COLLECTOR IS CALLED FOR A HALF THE WAIT DID NOT SEE REACH TERMINAL,
# and this is the #54 review's first two findings in one decision. Both
# collectors answer a question the checklist is not asking. `_latest_run`
# filters `status <> 'running'`, so asking it about a still-running eval returns
# LAST NIGHT'S run, graded as `measured`, in a report that claims every number
# describes the run the checklist sequenced. `_fetch_red_team_summary_sync` had
# no filter at all, so asking it about a still-running red team returned the
# in-flight row as `measured` with zero open findings, and a never-probed agent
# read as clean. One reads a stale run, the other reads a phantom one, and both
# are the same mistake: a collector reports on the table, and only the wait
# knows whether the run in the table is the one this checklist started.
#
# The observed wait travels in the detail because it is what separates the two
# incidents a reader has to tell apart: a red team that needs forty minutes and
# a tenant DB the poller could not reach both end here with None.


def eval_summary_did_not_finish(waited_s: float, *, dispatched: bool = True) -> dict:
    """The eval half of a wait that ended with nothing to read. Every number null.

    TWO WAYS IN, AND THEY ARE DIFFERENT FACTS (#130). The run was started and had
    not finished when the ceiling expired, or the broker refused the dispatch and
    no run of this check's ever existed. Both are absent measurements and both
    block, so only the detail separates them, and it has to: since the wait stops
    on the first poll for a refusal, "had not finished after 0s" would read as a
    platform that gave up instantly rather than one that never reached a queue.
    """
    if not dispatched:
        return _eval_summary(
            EVAL_SIGNAL_DID_NOT_FINISH,
            detail=(
                "the eval run for this readiness check could not be started, so "
                "there is nothing it measured to read"
            ),
        )
    return _eval_summary(
        EVAL_SIGNAL_DID_NOT_FINISH,
        detail=(
            "the eval run this readiness check started had not finished after "
            f"{round(waited_s)}s, so there is nothing it measured to read"
        ),
    )


def red_team_summary_did_not_finish(
    waited_s: float, *, dispatched: bool = True
) -> dict:
    """The security half of the same two ways in."""
    if not dispatched:
        return _red_team_summary(
            RED_TEAM_SIGNAL_DID_NOT_FINISH,
            detail=(
                "the red-team run for this readiness check could not be started, "
                "so its security surface is unmeasured"
            ),
        )
    return _red_team_summary(
        RED_TEAM_SIGNAL_DID_NOT_FINISH,
        detail=(
            "the red-team run this readiness check started had not finished "
            f"after {round(waited_s)}s, so its security surface is unmeasured"
        ),
    )


def _agent_not_invoked_warning(eval_summary: dict) -> DeploymentWarning:
    """The owner-facing half of the D1 refusal (P3).

    ONE warning_id for both routes into it — the collector's
    EVAL_SIGNAL_AGENT_NOT_INVOKED and the gate's own `agent_invoked is not
    True` — because the precedent this module sets is that a distinct
    warning_id marks a distinct REMEDY, not a distinct cause (see the
    eval_never_run / eval_signal_unavailable pair, split precisely because
    "wait for the run we started" and "try again in a few minutes" are
    different instructions). Here the remedy is identical in every case: a
    fresh run on the current build.

    THE MESSAGE STILL HAS TO BRANCH, AND IT DID NOT (P3 review). One warning_id
    is not one sentence. The shipped text told every owner that the check
    "scored a set of pre-written model answers", which is true of the ABSENT
    case (all of history, produced by the tautology at eval.py:374-375) and
    false in every particular of the FALSE case: a below-floor P2 run invoked
    the agent, scored nothing at all, wrote no eval_results, and involved no
    pre-written answers anywhere. It also promised the numbers would come out
    "lower than the old ones", when a below-floor run has no old ones to be
    lower than. Narrating a cause we did not observe is the exact defect class
    this phase exists to remove, and the console renders nothing else — a grep
    of apps/admin for `agent_invoked` or `eval_signal` returns nothing, so this
    sentence IS the owner-visible account.

    Even the absent branch does not assert the tautology as fact: absence also
    arrives from a pre-0013 tenant DB with no `config` column, and from a P2 run
    whose config patch failed. It names the historical cause as a conditional
    and lets the drop be explained if it applies.

    The message does not use the word "eval": a non-technical owner reading
    "the evaluation did not invoke the agent" learns nothing actionable. It
    says what was measured instead.

    `eval_summary` IS READ, both for `agent_invoked` and for `eval_dispatched`
    — the same wait-vs-go-find-a-page split the eval_never_run warning makes,
    for the same reason. It used to be an unread parameter, which made
    test_the_collector_state_and_the_gate_arm_reach_the_same_warning true by
    construction: two call sites of a constant-returning function cannot
    produce different payloads.
    """
    invoked = eval_summary.get("agent_invoked")
    started = bool(eval_summary.get("eval_dispatched"))
    if invoked is False:
        # The run looked and said no: below MIN_RESPONSE_RATE, below
        # MIN_SCORED_OBSERVATIONS, or dead before its first turn (the value
        # every eval_runs row is INSERTed with). No scores exist for it —
        # run_eval_suite skips the scorer entirely below the floor — so there
        # is nothing here to describe as pre-written, and nothing to fall from.
        cause = (
            "This agent's last quality check could not get enough of the "
            "agent's own replies back to judge, so it measured nothing and "
            "cannot be used to approve a launch."
        )
    else:
        cause = (
            "This agent's last quality check does not record whether it ever "
            "put a question to this agent, so it cannot be used to approve a "
            "launch. Checks from before this release scored pre-written model "
            "answers rather than the agent's own replies, which is why their "
            "scores were near-perfect. If this was one of those, the fresh "
            "numbers will look lower, and that drop is the measurement "
            "starting to work rather than the agent getting worse."
        )
    remedy = (
        " We have started a fresh check and it takes a few minutes. Run this "
        "readiness check again once it finishes."
        if started
        else " Run a fresh check from the Evaluation page and try again."
    )
    return DeploymentWarning(
        warning_id="eval_agent_not_invoked",
        category="eval_quality",
        message=cause + remedy,
        severity_level="warning",
    )


#: The warning a 'measured' signal carrying no quality evidence produces. One id
#: for both causes, because the remedy is one thing: run a fresh eval.
EVAL_QUALITY_UNMEASURED_WARNING_ID = "eval_quality_unmeasured"


def _unmeasured_gated_metrics(eval_summary: dict) -> list[str]:
    """Which gated metrics no reported dataset measured. Fail-closed reading.

    Read off `datasets` rather than off the run-level `metrics`, and the
    difference is the whole point. A run whose golden and exploratory halves both
    scored has NO run-level reading at all, because there is no pooled mean and
    this codebase refuses to invent one. A gate reading `metrics` alone would
    refuse every tenant with a designated golden set while its numbers sat one
    key over. A gate reading `pass_rates` alone would do worse: null there is
    both "we measured nothing" and "we measured both halves", and shipping over
    the second because it looks like the first is the fail-open this function
    exists to close.

    `is True` rather than truthiness: a missing key, a missing dataset block and
    a hand-built summary that dropped the field all read as unmeasured, which is
    the same discipline the `agent_invoked` arm applies one field over.
    """
    datasets = eval_summary.get("datasets") or {}
    missing = []
    for metric in GATED_METRIC_KEYS:
        measured = any(
            ((datasets.get(name) or {}).get("metrics") or {})
            .get(metric, {})
            .get("measured")
            is True
            for name in EVAL_DATASETS
        )
        if not measured:
            missing.append(metric)
    return missing


def _quality_evidence_warning(eval_summary: dict) -> DeploymentWarning | None:
    """Refuse a 'measured' signal that carries no quality evidence, or None.

    TWO CAUSES, ONE REMEDY. A gated metric no dataset measured, and a run whose
    per-scenario verdicts could not be read at all. Both are missing evidence
    and neither is a low score, so both refuse rather than being narrated as
    quality.

    `failing_scenarios is None` is the second. The count is read off the stored
    `binary_verdict` column, so None means the column could not be read and NOT
    that no scenario failed. Reading it as zero failures is exactly the
    substitution audit D3 made with `pass_rates: {}`, an absence iterated into a
    clean bill of health.

    An `unmeasured_scenarios` above zero does NOT refuse on its own. It is a
    partial judge outage, both counts travel to the orchestrator beside each
    other, and the numbers that did survive are real.
    """
    missing = _unmeasured_gated_metrics(eval_summary)
    if missing:
        cause = (
            "no answers were scored for "
            + " or ".join(metric.replace("_", " ") for metric in missing)
        )
    elif eval_summary.get("failing_scenarios") is None:
        cause = "its per-question results could not be read"
    else:
        return None
    return DeploymentWarning(
        warning_id=EVAL_QUALITY_UNMEASURED_WARNING_ID,
        category="eval_quality",
        message=(
            "This agent's last quality check reports that it ran, but "
            f"{cause}, so there is nothing to approve a launch on. Run a fresh "
            "check from the Evaluation page and try again."
        ),
        severity_level="warning",
    )


def _never_evaluated_warning(eval_summary: dict) -> DeploymentWarning:
    """The day-1 state, and the one the owner can actually act on.

    The checklist task starts the first eval itself when it finds this
    (run_deployment_checklist step 4b) and records the dispatch ON the signal, so
    the message says "wait" rather than sending a non-technical owner to a page
    the onboarding flow never routes to.
    """
    started = bool(eval_summary.get("eval_dispatched"))
    return DeploymentWarning(
        warning_id="eval_never_run",
        category="eval_quality",
        message=(
            "This agent's answer quality has never been measured, so it cannot "
            "be approved for launch yet. "
            + (
                "We have started its first evaluation, which takes a few "
                "minutes. "
                "Run this readiness check again once it finishes."
                if started
                else "Run an eval from the Evaluation page and try again."
            )
        ),
        severity_level="warning",
    )


def _eval_did_not_finish_warning(eval_summary: dict) -> DeploymentWarning:
    """The eval half of a wait that ended with nothing to read.

    Distinct from `eval_signal_unavailable` because the remedy is distinct.
    "Your quality results could not be read" sends the owner looking for a
    broken thing; the run is running and the answer is to come back. Distinct
    from `eval_never_run` too: that one says the platform has just started the
    first measurement, and this one says a measurement it started is still
    going.

    AND THE SIGNAL HAS TWO WAYS IN (#130). `eval_summary_did_not_finish` already
    told the two apart in `signal_detail`, and this message did not: a broker
    that refused the dispatch was described to the owner as a check that "was
    still running", so waiting a little while would never produce one. The state
    field the collector rides carries which one happened.
    """
    return DeploymentWarning(
        warning_id="eval_did_not_finish",
        category="eval_quality",
        message=(
            "The quality check for this agent was still running when this "
            "readiness check gave up waiting, so there are no results to "
            "approve on yet. The check keeps running on its own; run the "
            "readiness check again in a little while."
            if eval_summary.get("eval_dispatched") is not False
            else "The quality check for this agent was never started, so there "
            "are no results to approve on yet. Run the readiness check again to "
            "start one."
        ),
        severity_level="warning",
    )


def _signal_unavailable_warning(detail: str) -> DeploymentWarning:
    """Every remaining absent state, with the collector's own reason quoted."""
    return DeploymentWarning(
        warning_id="eval_signal_unavailable",
        category="eval_quality",
        message=(
            "This agent's answer quality has not been measured, so it cannot be "
            f"approved for launch yet ({detail}). Run an eval from the "
            "Evaluation page and try again."
        ),
        severity_level="warning",
    )


def _eval_evidence_warnings(eval_summary: dict) -> list[DeploymentWarning]:
    """Every reason this eval signal is not evidence, as owner-facing warnings.

    An empty list means the signal is admissible. Any entry blocks: the caller
    does not weigh them, because each one is an absence of measurement rather
    than a degree of it.

    The arms are ordered by which one names the cause. A payload can be in
    several of these states at once and the owner needs the one they can act on.
    """
    warnings: list[DeploymentWarning] = []
    eval_signal = eval_summary.get("eval_signal")
    if eval_signal != SHIPPABLE_SIGNAL:
        detail = eval_summary.get("signal_detail") or "no eval signal was produced"
        if eval_signal == EVAL_SIGNAL_AGENT_NOT_INVOKED:
            warnings.append(_agent_not_invoked_warning(eval_summary))
        elif eval_signal == EVAL_SIGNAL_NO_RUNS:
            warnings.append(_never_evaluated_warning(eval_summary))
        elif eval_signal == EVAL_SIGNAL_DID_NOT_FINISH:
            warnings.append(_eval_did_not_finish_warning(eval_summary))
        else:
            warnings.append(_signal_unavailable_warning(detail))
    elif eval_summary.get("agent_invoked") is not True:
        # A payload that claims 'measured' and does not claim to have invoked
        # the agent. In production _fetch_eval_summary_sync has already turned
        # this into EVAL_SIGNAL_AGENT_NOT_INVOKED above, so reaching here means
        # the payload came from somewhere else, and somewhere else is exactly
        # where the next fail-open comes from. Unreachable today; see
        # apply_signal_evidence_gate's THE COLLECTOR IS THE ENFORCEMENT
        # paragraph, which says so rather than claiming a second live layer.
        warnings.append(_agent_not_invoked_warning(eval_summary))
    else:
        # A signal that says 'measured' is a claim that the run wrote a record,
        # not a claim that the record holds a number.
        quality = _quality_evidence_warning(eval_summary)
        if quality is not None:
            warnings.append(quality)
    return warnings


#: The security half's absent states whose remedy the signal alone decides.
#:
#: FIVE ABSENCES WITH FIVE REMEDIES, and telling them apart is the whole point.
#: "Never security-tested" sends the owner to start a run; "stopped before it
#: finished" says run it again; "could not be read" describes an outage; and the
#: did_not_finish signal splits in two on whether the run was ever dispatched,
#: which is why `_red_team_did_not_finish_warning` composes that pair instead of
#: this table. Every one of them was reported as a clean measurement at some
#: point in this module's history, and every one of them blocks.
_RED_TEAM_ABSENCE_WARNINGS: dict[str, tuple[str, str]] = {
    RED_TEAM_SIGNAL_NO_RUNS: (
        "red_team_never_run",
        "This agent has never been security-tested, so it cannot be approved "
        "for launch yet. Run a red-team check from the Security page and try "
        "again.",
    ),
    RED_TEAM_SIGNAL_RUN_FAILED: (
        "red_team_run_failed",
        "The last security check stopped before it finished, so what it managed "
        "to attack cannot be trusted and this agent cannot be approved for "
        "launch. Run the check again from the Security page.",
    ),
}

#: What a signal this build cannot name reads as. A MISSING key lands here too,
#: which is the fail-closed direction: a caller that hand-builds a summary and
#: forgets the state field is refused rather than believed.
_RED_TEAM_UNREADABLE_WARNING: tuple[str, str] = (
    "red_team_signal_unavailable",
    "The security-test results for this agent could not be read, so its safety "
    "cannot be confirmed. Try the readiness check again in a few minutes.",
)


def _red_team_did_not_finish_warning(red_team_summary: dict) -> tuple[str, str]:
    """The security half of a wait that ended with nothing to read, both ways in.

    The k=3 red-team bound is roughly forty-five minutes, so a run this checklist
    started and did not see finish is the ordinary case rather than an exotic
    one, and "come back in a little while" is the right instruction for it. A
    dispatch the broker refused is the other way into the same signal (#130), and
    it never became a run at all, so telling that owner to wait describes a job
    nothing is working on.
    """
    if red_team_summary.get("red_team_dispatched") is False:
        return (
            "red_team_did_not_finish",
            "The security check for this agent was never started, so its safety "
            "cannot be confirmed yet. Run the readiness check again to start "
            "one.",
        )
    return (
        "red_team_did_not_finish",
        "The security check for this agent was still running when this "
        "readiness check gave up waiting, so its safety cannot be confirmed "
        "yet. The check keeps running on its own; run the readiness check "
        "again in a little while.",
    )


def _red_team_evidence_warnings(red_team_summary: dict) -> list[DeploymentWarning]:
    """The security half's absent states, one warning each. Empty means measured."""
    red_team_signal = red_team_summary.get("signal")
    if red_team_signal == SHIPPABLE_SIGNAL:
        return []
    if red_team_signal == RED_TEAM_SIGNAL_DID_NOT_FINISH:
        warning_id, message = _red_team_did_not_finish_warning(red_team_summary)
    elif isinstance(red_team_signal, str):
        warning_id, message = _RED_TEAM_ABSENCE_WARNINGS.get(
            red_team_signal, _RED_TEAM_UNREADABLE_WARNING
        )
    else:
        # An absent or non-string signal is the unreadable case, which is where
        # a table miss already landed it, so the narrowing decides nothing new.
        # It replaces a `type: ignore[call-overload]` that stopped covering its
        # own error the moment this function started reading the signal off the
        # summary dict: `.get("signal")` is `Any | None` where the parameter had
        # been bare `Any`, so mypy resolved an overload and reported `arg-type`
        # instead. A narrower type is the honest fix; a wider ignore is not.
        warning_id, message = _RED_TEAM_UNREADABLE_WARNING
    return [
        DeploymentWarning(
            warning_id=warning_id,
            category="security",
            message=message,
            severity_level="warning",
        )
    ]


def _red_team_finding_warnings(
    red_team_summary: dict,
) -> tuple[bool, list[DeploymentWarning]]:
    """Open critical and high findings. Returns (blocked, warnings).

    ENFORCED HERE RATHER THAN ONLY IN THE PROMPT (P4 review).
    `deployment_blocked == True` and `DEP_BLOCK_ON_HIGH_RED_TEAM is True and
    high_count > 0` were stated in _DEPLOYMENT_SYSTEM_PROMPT and enforced
    nowhere: DEP_BLOCK_ON_HIGH_RED_TEAM appeared in config.py and in that prompt
    string and in no other Python. Executed against the shipped code, the gate
    returned 'ship' over one open CRITICAL finding and over four open high ones.
    A gate that depends on an LLM correctly reading a state field fails open the
    first time the model is confident and wrong.
    """
    warnings: list[DeploymentWarning] = []
    blocked = False
    critical_count = red_team_summary.get("critical_count") or 0
    high_count = red_team_summary.get("high_count") or 0

    if red_team_summary.get("deployment_blocked") or critical_count > 0:
        blocked = True
        warnings.append(
            DeploymentWarning(
                warning_id="red_team_critical_finding",
                category="security",
                message=(
                    "The security check found a critical problem that is still "
                    "open, so this agent cannot be approved for launch. Fix it, "
                    "then mark it contained on the Security page and run this "
                    "check again."
                ),
                severity_level="warning",
            )
        )

    if settings.DEP_BLOCK_ON_HIGH_RED_TEAM and high_count > 0:
        blocked = True
        warnings.append(
            DeploymentWarning(
                warning_id="red_team_high_finding",
                category="security",
                message=(
                    f"The security check left {high_count} serious finding(s) "
                    "open, so this agent cannot be approved for launch. Review "
                    "each one on the Security page — a finding that says the "
                    "check itself could not observe the agent means the test "
                    "did not run, not that the agent is safe."
                ),
                severity_level="warning",
            )
        )
    return blocked, warnings


def _red_team_coverage_warnings(
    red_team_summary: dict,
) -> tuple[bool, list[DeploymentWarning]]:
    """How much of the attack surface was tested. Returns (blocked, warnings).

    A run that RECORDED incomplete coverage refuses to ship; a run that recorded
    none warns. The distinction is the remedy, not the severity.
    `coverage_source == 'run'` means this run measured its own coverage and said
    part of the surface went untested, and the owner can re-run the check on a
    worker that can reach the attack tooling, so refusing is actionable and "a
    clean result over 3 of 7 vectors" is not a clean result. `current_build`
    means no run-level figure exists at all (a tenant DB provisioned before
    migration 0015, or a run written before the task stored it); nothing the
    owner can do produces one, so blocking there would be a permanent, unfixable
    refusal. It is still not evidence, and it says so.

    `is not True` rather than `is False`: None must fail the same way.
    """
    coverage_complete = red_team_summary.get("coverage_complete")
    coverage_source = red_team_summary.get("coverage_source")
    attempted = red_team_summary.get("vectors_attempted")
    valid = red_team_summary.get("vectors_valid")

    if coverage_source == COVERAGE_SOURCE_RUN and coverage_complete is not True:
        return True, [
            DeploymentWarning(
                warning_id="red_team_coverage_incomplete",
                category="security",
                message=(
                    f"The last security check did not finish: {valid} of "
                    f"{attempted} attack types reported a result, and each must "
                    "also complete every independent attempt it owes. Run the "
                    "check again and approve once it says complete."
                ),
                severity_level="warning",
            )
        ]
    if coverage_source != COVERAGE_SOURCE_RUN:
        return False, [
            DeploymentWarning(
                warning_id="red_team_coverage_unrecorded",
                category="security",
                message=(
                    "The last security check did not record how many attack "
                    "types it managed to test, so we cannot confirm it covered "
                    "all of them. Run it again for a result that says so."
                ),
                severity_level="warning",
            )
        ]
    return False, []


def apply_signal_evidence_gate(
    recommendation: str,
    eval_summary: dict,
    red_team_summary: dict,
) -> tuple[str, list[DeploymentWarning]]:
    """Refuse to ship over an absent quality signal. Pure — no DB, no LLM.

    THE DIRECTION IS ONE-WAY. This function can only make a recommendation more
    conservative: `ship` becomes `block` when a signal is missing, and a
    `block` the orchestrator already reached is never softened. An evidence gate
    that could upgrade a recommendation would be a second, weaker opinion about
    the signals rather than a floor under them.

    Why block rather than ship_with_warnings: `ship_with_warnings` is a
    SHIPPABLE state — api/v1/deployment.py's approve route lets it through once
    the owner acknowledges the warnings — so routing an unmeasured agent there
    would still permit the deploy, only with a note attached. The forbidden
    outcome is shipping over an absent eval signal, and the two states that are
    not "absent signal" (measured-and-good, measured-and-bad) both remain
    entirely the orchestrator's call.

    `no_runs` blocks as firmly as `unavailable`, and deliberately: an agent that
    has never been evaluated has no evidence of quality, and the previous
    behaviour — vacuous satisfaction of "all eval metrics >= 0.85" over an empty
    dict — is exactly the fail-open this branch exists to close. The remedy is
    one eval run, which POST /agents/{id}/eval-runs/trigger performs on demand
    and which run_deployment_checklist now starts by itself on the day-1 path.

    BOTH HALVES HAVE A no_runs STATE, and they carry different warning_ids from
    'unavailable' on purpose. "Your security results could not be read" and
    "this agent has never been security-tested" have different remedies, and the
    second was being reported as 'measured' until the P2 review — an agent with
    zero red-team runs has zero open findings, which is what a clean run also
    has.

    AND BOTH HALVES NOW HAVE A did_not_finish STATE, which is the one a slow run
    lands in (#54 review). The red-team k=3 bound is around forty-five minutes,
    so a checklist that dispatches its own red team and then stops waiting is an
    ordinary event, not an exotic one. Its warning says "come back once the run
    finishes" rather than "you have never been tested" or "we could not read
    it", because those are three different remedies. All three block.

    A MISSING key is treated as an absent signal, not as a measured one. A
    caller constructing a summary dict by hand and forgetting the state field
    fails closed.

    FOUR REFUSALS, NOT TWO (P4 review). Beside the two signal-state conditions
    this function shipped with, it now enforces the two severity conditions the
    orchestrator prompt has always claimed — an open critical finding, and open
    high findings while DEP_BLOCK_ON_HIGH_RED_TEAM is set — plus a run whose own
    recorded coverage says part of the attack surface went untested. All three
    were prose in a system prompt and nothing else; run against the shipped
    code, this function returned 'ship' for all three.

    SIX NOW. `_quality_evidence_warning` refuses a 'measured' signal whose gated
    metrics were measured on no dataset, and one whose per-scenario verdicts
    could not be read. Both became reachable when the collector stopped
    computing its own averages over `eval_results` and started lifting the run's
    own record (#51 slice 4): 'measured' is now a claim that the run wrote a
    record, and a record can be present and hold no gated number. It reads the
    per-DATASET measurements rather than the run-level ones, because a run whose
    two halves both scored has no run-level reading at all and refusing it would
    block every tenant with a designated golden set over numbers it does hold.

    FIVE: `agent_invoked is not True` (audit D1, P3). A signal that says
    'measured' is a claim that a run produced scores, not a claim that the
    scores are about this agent — and until this release they were not. The
    eval set `agent_response` to the scenario's own `reference_answer`
    (eval.py:374-375), so every stored run reports near-perfect faithfulness
    over answers the agent never wrote, and this gate shipped on all of them.

    THE COLLECTOR IS THE ENFORCEMENT; THE `elif` BELOW IS THE INVARIANT (P3
    review corrects the original claim here, which said both were load-bearing
    today). _fetch_eval_summary_sync already downgrades such a run to
    EVAL_SIGNAL_AGENT_NOT_INVOKED, and it is the only producer of a 'measured'
    payload in the tree — neuter the `elif` alone and every collector test stays
    green, because the production path never reaches it. The other payload that
    exists, EVAL_SUMMARY_UNAVAILABLE_SIGNAL, carries eval_signal='unavailable'
    and cannot reach it either. So the arm guards a payload shape that does not
    exist yet: a hand-built summary, a second collector added later, a caller
    that copies the dict and drops a key. That is a real defence and it is
    defence against a FUTURE caller — the same shape as the "A MISSING key"
    paragraph above, and worth keeping for the same reason, but do not read it
    as a second live layer under today's code.

    ABSENT IS REFUSED EXACTLY AS FALSE IS, and this is the whole decision.
    `is not True`, never `is False`: None must fail the same way, because None
    is what the entire history of stored runs carries. A gate refusing only
    False would have been satisfied by every tautological run ever written —
    the same failure as BACKLOG 3.1, where pre-P4 red-team runs still read
    'measured' with clean findings because nobody had recorded the absence.
    The accepted consequence, settled by the owner 2026-08-07, is that every
    pre-D1 run and every tenant DB older than alembic_tenant 0013 fails closed
    until a fresh eval runs. That costs blocked deploys; the alternative costs
    shipped agents nobody measured.

    Args:
        recommendation: the orchestrator's own recommendation.
        eval_summary: _fetch_eval_summary_sync's payload, or the unavailable
            substitute.
        red_team_summary: _fetch_red_team_summary_sync's payload, or the
            unavailable substitute.

    Returns:
        (recommendation, warnings) — warnings carry a stated reason for each
        refusal and are merged into the persisted warning list by warning_id.
    """
    warnings: list[DeploymentWarning] = []
    blocked = False

    eval_warnings = _eval_evidence_warnings(eval_summary)
    if eval_warnings:
        blocked = True
        warnings.extend(eval_warnings)

    red_team_signal = red_team_summary.get("signal")
    red_team_warnings = _red_team_evidence_warnings(red_team_summary)
    if red_team_warnings:
        blocked = True
        warnings.extend(red_team_warnings)

    # The severity conditions only ask their question of a MEASURED result. An
    # absent signal has already blocked above, and counting findings from a run
    # nobody read would be the fail-open this module exists to close.
    if red_team_signal == SHIPPABLE_SIGNAL:
        for find in (_red_team_finding_warnings, _red_team_coverage_warnings):
            refused, found = find(red_team_summary)
            blocked = blocked or refused
            warnings.extend(found)

    if blocked:
        return "block", warnings
    return recommendation, warnings


# The message the approve route answers 422 with when the stored run's own
# evidence does not claim the agent was ever asked anything. Module-level so the
# route and its tests cannot drift on the wording.
STORED_RUN_NOT_INVOKED_DETAIL = (
    "This readiness check was decided on a quality result that does not record "
    "having put a question to the agent. Run a fresh check from the Evaluation "
    "page, then run the readiness check again."
)


def stored_run_records_agent_invocation(report: object) -> bool:
    """Does a PERSISTED checklist run's own report claim the agent was invoked?

    THE GATE DOES NOT REACH A RUN THAT IS ALREADY FINISHED (P3 review), and this
    is the hole the rest of the phase left open. apply_signal_evidence_gate has
    exactly one caller — run_deployment_checklist, at checklist time — and
    `agent.is_deployed` has exactly one writer: POST /approve-deployment, which
    validates against `checklist_runs.recommendation`, a value FROZEN by whatever
    gate was running the day the row was written. So every readiness check
    completed before this release carries a 'ship' computed by the pre-P3 gate
    over a tautological eval, and stays approvable indefinitely: status is
    'complete', recommendation is not 'block', warnings do not apply, and the
    envelope hash has not moved. `{"deployed": true}`, and the agent this phase
    exists to refuse is live.

    That is BACKLOG 3.1's shape — pre-P4 red-team runs still reading
    'measured' with clean findings — which is the very argument P3's commit
    message used to justify refusing an ABSENT claim, applied one layer up to
    the artifact the approve route actually reads. Nothing on checklist_runs
    expires (no TTL, no gate-version column, app/models/checklist_run.py), so
    the run has to be re-read rather than aged out.

    `is True`, matching the gate arm exactly, so absence and falsehood and the
    string "true" all fail the same way. Every non-dict shape on the path
    (report NULL on a run that never reached step 6, an eval_summary key the
    orchestrator never wrote, a JSONB payload of some other shape) returns
    False: this is a gate, and a gate that cannot read its evidence has not been
    satisfied.

    Args:
        report: `ChecklistRun.report` as stored — a JSONB dict carrying the five
            signal payloads, or None.

    Returns:
        True only when report["eval_summary"]["agent_invoked"] is exactly True.
    """
    if not isinstance(report, dict):
        return False
    eval_summary = report.get("eval_summary")
    if not isinstance(eval_summary, dict):
        return False
    return eval_summary.get("agent_invoked") is True


#: What kind of concern each rule slug is, so a console can group the warnings a
#: Verdict produces beside the ones the evidence gate and the blast-radius
#: deriver produce. The slugs come from `app.domain.verdict._RULES` and this
#: table names every one of them at DECISION_RULE_VERSION 2.
_VERDICT_WARNING_CATEGORIES: dict[str, str] = {
    "absent_eval_measurement": "eval_quality",
    "golden_failure": "eval_quality",
    "golden_unconfirmed": "eval_quality",
    "golden_set_below_floor": "eval_quality",
    "exploratory_ci_blocks": "eval_quality",
    "exploratory_ci_inconclusive": "eval_quality",
    "eval_coverage_below_floor": "eval_quality",
    "judge_not_calibrated": "eval_quality",
    "absent_red_team_measurement": "security",
    "critical_breach": "security",
    "high_breach": "security",
    "red_team_coverage_incomplete": "security",
}

#: Where a rule this build does not know about lands. Deliberately NOT one of
#: the real categories: a new rule slug grouped under 'eval_quality' would read
#: as a quality finding whatever it was actually about, and a security rule
#: filed as a quality one is worse than one filed as unknown. This value is
#: visibly not a category, so the gap shows.
VERDICT_WARNING_CATEGORY_UNMAPPED = "unmapped_rule"

#: The summary a report carries when the narration turn produced nothing. The
#: decision still stands: it was computed before the turn ran and does not
#: depend on it (#54 criterion 5). What is missing is only the prose.
NARRATION_UNAVAILABLE_SUMMARY = (
    "This readiness decision was computed from the agent's own evaluation and "
    "security results. The plain-language write-up could not be produced this "
    "time, so read the listed warnings for the reasons behind it."
)


class Narration(NamedTuple):
    """What the narration turn actually contributed, once it was read.

    `dropped_warnings` counts the warning payloads this build refused. A
    `warnings` value that is not a list at all counts as one refusal, because the
    whole value was refused. `summary_replaced` is True when the fallback stands
    in for prose the turn did not supply in readable form.
    """

    summary: str
    warnings: list[DeploymentWarning]
    dropped_warnings: int
    summary_replaced: bool


def parse_narration(report_data: object) -> Narration:
    """Read the model's report, and keep only the parts that are its declared shape.

    THE TOOL LOOP VALIDATES NOTHING, and that is why this exists (#54 review).
    `build_report_tools` stores submit_report's arguments verbatim and
    `dispatch_outcome` awaits the handler on raw args, so whatever the model
    emitted reaches `DeploymentReport` exactly as emitted. A warnings item
    missing `category`, a summary that came back None, a `warnings` value that is
    not a list: each of those raised a pydantic ValidationError inside the
    persist block, which marked the run 'failed' and threw away a verdict the
    platform had already computed. That is the outcome criterion 5 exists to
    prevent, reached through the one door it left open.

    So a malformed narration costs what an absent one costs: the prose, and
    nothing else. Warnings are validated ONE AT A TIME rather than as a list, so
    a single unreadable item does not discard the readable ones beside it.

    Pure, and it never raises. The caller owns the log line, because the ids that
    make it findable (agent, run) live on the task rather than here.
    """
    if not isinstance(report_data, Mapping):
        return Narration(NARRATION_UNAVAILABLE_SUMMARY, [], 0, True)

    raw_summary = report_data.get("summary")
    summary = raw_summary.strip() if isinstance(raw_summary, str) else ""

    raw_warnings = report_data.get("warnings")
    if isinstance(raw_warnings, list):
        items, dropped = raw_warnings, 0
    elif raw_warnings is None:
        # The turn submitted no warnings at all, which is a whole report about a
        # clean run rather than a defect. Nothing was refused.
        items, dropped = [], 0
    else:
        items, dropped = [], 1

    warnings: list[DeploymentWarning] = []
    for item in items:
        try:
            warnings.append(DeploymentWarning.model_validate(item))
        except ValidationError:
            dropped += 1

    return Narration(
        summary or NARRATION_UNAVAILABLE_SUMMARY,
        warnings,
        dropped,
        not summary,
    )


def render_verdict(verdict: Verdict) -> dict:
    """The decision as the narration turn reads it. Prose fields, no slugs.

    THE TURN IS HANDED THE ANSWER (#54 criterion 2). It gets the outcome and the
    reason sentences, which already carry each threshold in words, so nothing in
    the prompt has to quote a number and nothing the model writes can change what
    is persisted. `rule` is left off: it is the key a console groups on, and a
    slug in a model's context is one more thing for it to quote at an owner.
    """
    return {
        "outcome": Outcome(verdict.outcome).value,
        "rule_version": verdict.rule_version,
        "reasons": [
            {
                "signal": reason.signal,
                "observed": reason.observed,
                "threshold": reason.threshold,
                "outcome": Outcome(reason.outcome).value,
                "provisional": reason.provisional,
            }
            for reason in verdict.reasons
        ],
    }


def verdict_warnings(verdict: Verdict) -> list[DeploymentWarning]:
    """One owner-facing warning per reason, so 'block' never arrives unexplained.

    Criterion 4 of the ticket, carried from the domain record to the column the
    console reads. The `warning_id` IS the rule slug, so acknowledging a warning
    and grouping on the rule that produced it are the same key, and the message
    is the reason's own three sentences joined: what was looked at, what came
    back, and what would have been needed.

    Every reason becomes a warning, not only the blocking ones. A
    `ship_with_warnings` whose reasons were dropped on the floor is a launch
    approved over concerns nobody was shown.

    severity_level is 'warning' throughout. DeploymentWarning's other level is
    'info', and a rule that fired is never merely informational.
    """
    return [
        DeploymentWarning(
            warning_id=reason.rule,
            category=_VERDICT_WARNING_CATEGORIES.get(
                reason.rule, VERDICT_WARNING_CATEGORY_UNMAPPED
            ),
            message=(
                f"This check read {reason.signal} and found that "
                f"{reason.observed}. To approve a launch, {reason.threshold}."
            ),
            severity_level="warning",
        )
        for reason in verdict.reasons
    ]


#: How many verified question-and-answer pairs an agent should have before it is
#: answering mostly from its own reviewed knowledge rather than from scratch.
#: Warns and never blocks, so it is a floor on the owner's attention, not on the
#: deploy. It moved here from a sentence in _DEPLOYMENT_SYSTEM_PROMPT (#54): the
#: prompt used to state it as `verified_qa_stats.row_count < 50` and the model
#: was trusted to do the comparison.
VERIFIED_QA_DEPTH_FLOOR = 50

#: How many open medium-severity red-team findings are worth reading before a
#: launch. Same provenance and same status: it warns, it never blocks.
RED_TEAM_MEDIUM_WARN_ABOVE = 2


def _verified_qa_unavailable_warning() -> DeploymentWarning:
    """The knowledge collector could not read the tenant DB (#131).

    A DISTINCT warning_id because it is a distinct remedy, which is the split
    this module already makes between eval_never_run and eval_signal_unavailable:
    "write more verified pairs" and "try again in a few minutes" are different
    instructions, and the outage used to arrive under the first one.
    """
    return DeploymentWarning(
        warning_id="verified_qa_unavailable",
        category="knowledge_depth",
        message=(
            "We could not read how many reviewed question-and-answer pairs "
            "this agent has to draw on, so this launch is going ahead without "
            "knowing that. Nothing here says the number is low, only that "
            "nobody could count it."
        ),
        severity_level="warning",
    )


def _verified_qa_low_count_warning(row_count: int) -> DeploymentWarning:
    """A corpus that WAS counted and came in under the floor."""
    return DeploymentWarning(
        warning_id="verified_qa_low_count",
        category="knowledge_depth",
        message=(
            f"This agent has {row_count} reviewed question-and-answer "
            f"pair(s) to draw on, fewer than the "
            f"{VERIFIED_QA_DEPTH_FLOOR} we look for, so it will be "
            "working things out from your documents more often than "
            "repeating an answer you have already approved."
        ),
        severity_level="warning",
    )


def _red_team_medium_warning(medium_count: int) -> DeploymentWarning:
    """Open moderate findings from a run that measured something."""
    return DeploymentWarning(
        warning_id="red_team_medium_findings",
        category="security",
        message=(
            f"The security check left {medium_count} moderate "
            "finding(s) open. None of them stops the launch, and "
            "each is worth reading on the Security page before you "
            "approve it."
        ),
        severity_level="warning",
    )


def _knowledge_depth_warnings(verified_qa_stats: dict) -> list[DeploymentWarning]:
    """At most one warning about the verified corpus, because at most one holds.

    An unread signal ENDS the question, which is why this returns rather than
    falling through. A payload the collector built carries None in `row_count`
    beside an unread signal, so the depth check below is unreachable there, but
    a payload a caller builds by hand can carry a zero, and a zero IS an int.
    The owner then read "nobody could count your pairs" and "you have too few
    pairs" one under the other: two remedies, and only one of them applies to a
    tenant database nobody reached.

    A payload with no `signal` at all is read as a measurement. That is every
    report stored before #131, and the one this function is handed by any caller
    that predates the two knowledge signals.
    """
    signal = verified_qa_stats.get("signal")
    if signal is not None and signal != VERIFIED_QA_SIGNAL_MEASURED:
        return [_verified_qa_unavailable_warning()]

    row_count = verified_qa_stats.get("row_count")
    if isinstance(row_count, int) and row_count < VERIFIED_QA_DEPTH_FLOOR:
        return [_verified_qa_low_count_warning(row_count)]

    return []


def derive_quality_warnings(
    verified_qa_stats: dict, red_team_summary: dict
) -> list[DeploymentWarning]:
    """The prompt-era warn conditions `decide()` cannot see. Never blocking.

    Pure. None of these is about the run's own result, so none belongs in the
    rule table: `decide()` reads an EvalResult, a RedTeamResult and a
    calibration status, and knowledge depth is a property of the tenant's corpus
    while an open medium finding is a property of the findings table rather than
    of one run. They warn, they are merged by warning_id like every other
    derived warning, and they change no outcome.

    Measurement honesty governs both conditions. A medium count is only read
    when the security signal says 'measured', because the counts are null in
    every other state and `None > 2` is not a comparison anyone meant to make.
    The two knowledge-depth warnings (unread, then thin) live in
    `_knowledge_depth_warnings`, which reads the row count only when the signal
    beside it says somebody counted one.
    """
    warnings: list[DeploymentWarning] = _knowledge_depth_warnings(verified_qa_stats)

    if red_team_summary.get("signal") == SHIPPABLE_SIGNAL:
        medium_count = red_team_summary.get("medium_count") or 0
        if medium_count > RED_TEAM_MEDIUM_WARN_ABOVE:
            warnings.append(_red_team_medium_warning(medium_count))

    return warnings


def derive_blast_radius_warnings(blast_radius: dict) -> list[DeploymentWarning]:
    """Derive blast-radius warnings deterministically in Python (OD-1b).

    Pure function — no DB, no LLM. Warnings are derived from CONFIGURED
    values only. A figure describing history (what has actually happened)
    is not the same claim as current authorization, and warning on it would
    ask the owner to acknowledge a risk they may already have tightened
    away — so this function reads only the two "configured_max_*" keys and
    never touches the historical-maximum keys blast_radius also carries.

    Args:
        blast_radius: the dict returned by _fetch_blast_radius_sync (or the
            BLAST_RADIUS_DEFAULT_SIGNAL fallback).

    Returns:
        A list of DeploymentWarning, empty when enabled_skill_count == 0 —
        an agent with no enabled transactional skill has no blast radius to
        warn about.
    """
    if blast_radius.get("enabled_skill_count", 0) == 0:
        return []

    warnings: list[DeploymentWarning] = []

    configured_single = blast_radius.get("configured_max_single_action_cents")
    configured_hourly = blast_radius.get("configured_max_hourly_aggregate_cents")
    warn_single = blast_radius.get("warn_threshold_single_cents")
    warn_hourly = blast_radius.get("warn_threshold_hourly_cents")

    if configured_single is None:
        warnings.append(
            DeploymentWarning(
                warning_id="blast_radius_no_ceiling_configured",
                category="financial_exposure",
                message=(
                    "This agent can move money but has no configured ceiling on "
                    "a single action - set a maximum amount for every enabled "
                    "transactional skill."
                ),
                severity_level="warning",
            )
        )
    elif warn_single is not None and configured_single > warn_single:
        warnings.append(
            DeploymentWarning(
                warning_id="blast_radius_single_action_above_threshold",
                category="financial_exposure",
                message=(
                    f"The configured ceiling for a single action is "
                    f"R{configured_single / 100:.2f}, above the "
                    f"R{warn_single / 100:.2f} warning threshold - review "
                    f"whether this limit is intentional."
                ),
                severity_level="warning",
            )
        )

    if (
        configured_hourly is not None
        and warn_hourly is not None
        and configured_hourly > warn_hourly
    ):
        warnings.append(
            DeploymentWarning(
                warning_id="blast_radius_hourly_aggregate_above_threshold",
                category="financial_exposure",
                message=(
                    f"The configured hourly exposure ceiling is "
                    f"R{configured_hourly / 100:.2f}, above the "
                    f"R{warn_hourly / 100:.2f} warning threshold - review "
                    f"whether this limit is intentional."
                ),
                severity_level="warning",
            )
        )

    return warnings


# ---------------------------------------------------------------------------
# Orchestrator loop
# ---------------------------------------------------------------------------

# WHAT FOUR SDK OPTIONS BECAME ONE ARGUMENT. `run_tool_loop`'s `tools` list is
# the turn's whole tool set AND its whole authorisation. The SDK needed four
# options to reach the same place, because its default was to hand the agent the
# CLI's own toolset: `tools=[]` kept Bash/Read/Edit off the worker's filesystem,
# `strict_mcp_config=True` stopped a project `.mcp.json` server being merged in,
# and `allowed_tools` plus `permission_mode="dontAsk"` named the approved subset
# and denied the rest without blocking on a prompt no worker would answer.
# `run_tool_loop` has no built-ins, no config file and no permission model, and
# `tool_loop.dispatch` refuses any name the `tools` list does not carry. Deleting
# those four options removed no control.

#: How many times the model may be asked in one orchestrator turn. Carried over
#: from the SDK options at the #49 cutover and never re-measured. One call reads
#: the signals and calls submit_report; the other four are headroom for a model
#: that sends an argument string which does not parse and has to correct itself.
ORCHESTRATOR_MAX_TURNS = 5


async def _close_client(client) -> None:
    """Close the client the loop was handed. `run_tool_loop` closes none itself.

    Every exception is swallowed. This runs from a `finally`, and on the timeout
    path (`ORCHESTRATOR_TIMEOUT_S`, BACKLOG 1.30) it runs while a cancellation is
    already on its way out. A raise here would replace the reason the
    orchestrator failed with a footnote about a socket. `asyncio.CancelledError`
    is a BaseException and passes straight through, so the timeout still fires.
    """
    try:
        await client.close()
    except Exception as exc:
        log_failure(log, "deployment_orchestrator.client_not_closed", exc)


async def _run_orchestrator_loop(
    signals_json: str,
    result_container: dict,
    *,
    ledger: LedgerContext,
) -> None:
    """Ask the model for a readiness report, on the owned tool loop (#49).

    submit_report is a side-effect tool. `build_report_tools`' handler writes the
    report into `result_container`, and `stop_after` ends the loop on that call,
    AFTER the handler has run. The ack the handler returns is appended to a
    message list the loop never sends again, so a report costs one model call.

    Args:
        signals_json:     the quality signals the model reasons over.
        result_container: where the handler leaves the report. Untouched when
                          the model never calls the tool, and the caller reads
                          that absence as a failed run.
        ledger:           the three ids each `model_calls` row carries and the
                          tenant database it is written to. Mandatory, with no
                          default: a client that records nothing spends a
                          tenant's money leaving no row to show for it.
    """
    route = route_for(ORCHESTRATOR_PURPOSE)
    client = make_async_client(
        ORCHESTRATOR_PURPOSE,
        tenant_id=ledger.tenant_id,
        recorder=ledger.recorder,
        agent_id=ledger.agent_id,
        job_id=ledger.job_id,
    )
    try:
        result = await run_tool_loop(
            f"Here are the agent's quality signals:\n\n{signals_json}\n\n"
            "Assess deployment readiness and call submit_report.",
            client=client,
            model=route.model,
            system_prompt=_DEPLOYMENT_SYSTEM_PROMPT,
            # BACKLOG 1.32: the tool must be REGISTERED, not merely described
            # in the prompt. It is the whole allowlist too; see the note above.
            tools=build_report_tools(result_container),
            max_turns=ORCHESTRATOR_MAX_TURNS,
            stop_after=frozenset({SUBMIT_REPORT_TOOL_NAME}),
            reasoning_effort=route.reasoning_effort,
        )
    finally:
        await _close_client(client)
    if "report" not in result_container:
        # BACKLOG 1.30's family. The caller logs `no_report`, which names no
        # cause; these three fields are the cause. The model stopped talking, or
        # it hit ORCHESTRATOR_MAX_TURNS, or it called something else.
        log.warning(
            "deployment_orchestrator.no_report",
            stop_reason=result.stop_reason,
            num_turns=result.num_turns,
            tool_names=result.tool_names,
        )


def run_orchestrator(
    signals_json: str, result_container: dict, *, ledger: LedgerContext
) -> None:
    """Synchronous bridge. NOT on the live path, and kept deliberately.

    BACKLOG `1.33`. `run_deployment_checklist` does not call this: it goes
    `deployment.py -> _call_orchestrator_async -> _run_orchestrator_loop`
    directly, to avoid a nested `asyncio.run`. An adversarial review found this
    copy still carrying **both** defects `1.30` exists to remove — an inline
    `120.0` ceiling declared insufficient by E2E-4, and `error=str(exc)` with no
    `error_type`, which is the blank-diagnosis line itself.

    It is fixed rather than deleted because `tests/unit/test_deployment_service.py`
    drives it, and a second entry point that behaves differently from the live
    one is worse than either deleting it or aligning it. It now shares the live
    path's constant, so the two cannot drift.
    """
    try:
        asyncio.run(
            asyncio.wait_for(
                _run_orchestrator_loop(signals_json, result_container, ledger=ledger),
                timeout=ORCHESTRATOR_TIMEOUT_S,
            )
        )
    except Exception as exc:
        # error_type, not str(exc): str(asyncio.TimeoutError()) is "" (1.30).
        log_failure(log, "deployment_orchestrator.failed", exc, timeout_s=ORCHESTRATOR_TIMEOUT_S)
