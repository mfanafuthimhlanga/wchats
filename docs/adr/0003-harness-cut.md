# 0003: The Harness cut

Status: accepted. Decided with the owner 2026-08-23 on issue #6, from the call-graph
inventory of the four Harness files: 7,193 lines, of which the one observed run
(`.dev/traces/260822-harness-first-run.md`) executed 2,917.

The Harness keeps only the path that ran: generate a suite, invoke the Agent, score with
Ragas, write the run record, read it back. Three subsystems built on purpose are deleted
with their tests rather than finished:

- `decision_eval_service.py`, 1,897 lines. No route, task or Orchestrator import reaches
  it; only its own tests call it.
- The label queue, trust-tier resolvers and promotion to verified QA, 1,269 lines across
  `app/api/v1/evals.py` and `app/services/eval_service.py`. No admin page hits them.
  Calibration labels live in the CSVs the calibration harness reads.
- Neon branch isolation in `run_eval_suite`. The branch was provisioned and deleted
  without ever being scored against; an eval turn only reads the tenant database.

The alternative was to wire them up, since each is a real feature of a mature eval
platform. They lose because no Agent has ever been live: every line spent finishing an
unreachable subsystem is a line not spent on the first deployed Agent, and each deleted
subsystem has a named way back that starts from a need rather than from stranded code.
A database label queue returns as a spec'd console feature when a Tenant needs one.
Branch isolation returns as a decision under the Mellow Transactional Agent's `ship`,
when an eval turn can write.

Consequences a reader will meet: `decision_eval` migrations remain in the alembic trees
(applied history is never deleted) with no service behind them, and the gate baselines
in `scripts/gates.py` shrink by the deleted functions and test files in the same change.
