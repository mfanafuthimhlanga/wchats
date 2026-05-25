---
status: investigating
trigger: "Agent 7674f65a-fd13-4755-b9b2-572692f3f39a stuck — UI shows Provisioning indefinitely"
created: "2026-05-25"
updated: "2026-05-25"
---

## Current Focus

hypothesis: "Celery workers (PIDs 6700, 12860) are zombie processes — alive in task list
  but dead broker connections to Upstash Redis. The pipeline queue has 1 unconsumed task.
  Workers started 22+ hours ago; Upstash drops idle TCP connections silently after ~10min.
  The consumer BLPOP socket is half-open; workers cannot receive any tasks."
test: "celery inspect ping → empty reply; queue has 1 task; DB shows agent=pending (task never ran)"
expecting: "N/A — hypothesis confirmed by all evidence"
next_action: "Kill zombie workers, restart, verify task is consumed"

## Symptoms

expected: Agent advances from pending -> provisioning -> ready within 60s
actual: Agent and job remain stuck at status=pending; UI shows 'Provisioning' banner
errors: None visible in browser console or API log
reproduction: Create agent while workers are zombie (>10min after Upstash idle timeout)
started: 2026-05-25 06:56 UTC

## Evidence

- timestamp: 2026-05-25T07:00:00Z
  checked: Neon project count via API
  found: 13 projects (all legitimate — creation test succeeded, cap NOT hit)
  implication: Neon free tier cap is NOT the cause this time

- timestamp: 2026-05-25T07:01:00Z
  checked: DB agent row for 7674f65a
  found: status=pending, neon_project_id=NULL (task NEVER ran, not stuck mid-provision)
  implication: Task was dispatched but never consumed by any worker

- timestamp: 2026-05-25T07:02:00Z
  checked: Redis pipeline queue
  found: 1 task in queue (96b8f787) — provision_neon for the stuck agent
  implication: FastAPI dispatched task correctly; task is waiting

- timestamp: 2026-05-25T07:03:00Z
  checked: Celery inspect ping (broadcast)
  found: Empty reply ([]) — no workers responding within 3s timeout
  implication: Both running Celery processes are not connected to the broker

- timestamp: 2026-05-25T07:04:00Z
  checked: Windows process list
  found: PID 6700 and 12860 both running python -m celery worker, started 2026-05-24 ~16:51
  implication: Workers have been running 22+ hours without consuming any tasks

- timestamp: 2026-05-25T07:05:00Z
  checked: celery.log
  found: Last entry 2026-05-23 18:23:41 — workers logged nothing for 2+ days
  implication: The currently running workers (PIDs 6700/12860) are NOT logging to celery.log

- timestamp: 2026-05-25T07:06:00Z
  checked: _kombu.binding.celeryev in Redis
  found: 54 stale worker event bindings accumulated
  implication: Each restart leaves a dead binding; 54 = 54 zombie restarts over time

- timestamp: 2026-05-25T07:07:00Z
  checked: celery_app.conf broker_transport_options
  found: {} (empty — no socket_timeout, no socket_keepalive configured)
  implication: No keepalive on Upstash Redis connection; idle TCP drops silently

- timestamp: 2026-05-25T07:08:00Z
  checked: start_dev.ps1 stop logic (line 29)
  found: Kills processes named "celery" — but running workers use "python.exe" (python -m celery)
  implication: start_dev.ps1 -Stop does NOT kill python-invoked workers → zombies accumulate

## Eliminated

- hypothesis: Neon free tier cap hit again
  evidence: 13 projects found; test project created successfully (14th) then deleted
  timestamp: 2026-05-25T07:00:00Z

- hypothesis: Task was never dispatched from FastAPI
  evidence: Task 96b8f787 found in Redis pipeline queue with correct agent_id
  timestamp: 2026-05-25T07:02:00Z

- hypothesis: Worker crashed mid-provision (same bug as prior session)
  evidence: agent.status=pending (not provisioning); neon_project_id=NULL; no Redis task result
  timestamp: 2026-05-25T07:01:00Z

## Resolution

root_cause: |
  Two compounding issues (different from prior session):
  1. Celery workers (PIDs 6700, 12860) started ~22 hours ago via start_native.ps1.
     Upstash Redis drops idle TCP connections silently after ~10 minutes with no keepalive.
     The workers' BLPOP consumer loop socket becomes half-open — workers appear alive in
     the process list but cannot receive any broker messages. Celery's broker_heartbeat=120
     does not protect the Redis transport (heartbeat is AMQP-level, not Redis-level).
  2. start_dev.ps1 -Stop only kills processes named "celery.exe" but workers launched via
     "python -m celery" appear as "python.exe". Old workers accumulate as zombies.
  3. No broker_transport_options configured (no socket_keepalive, no socket_timeout) so
     the half-open socket is never detected or reconnected.

fix: |
  Immediate fix (unblock current agent):
    Kill zombie workers (PIDs 6700 and 12860), restart a fresh worker — it will pick up
    the task waiting in the pipeline queue.

  Code fix (prevent recurrence):
    Add socket_keepalive and socket_timeout to broker_transport_options in celery_app.py
    so Upstash Redis idle connection drops are detected and reconnected automatically.

  Script fix (prevent zombie accumulation):
    Update start_dev.ps1 -Stop to kill by python.exe process with matching commandline,
    not just by process name "celery".

files_changed: []
