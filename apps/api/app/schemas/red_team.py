"""Pydantic schemas for M7 red team API routes — request/response models for red_team_runs endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RedTeamRunResponse(BaseModel):
    id: str
    kind: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    findings: list[dict[str, Any]]  # raw JSONB list from tenant DB, each dict is a finding object
    max_severity: str | None
    deployment_blocked: bool
    # The run's own (vectors_attempted, vectors_valid, invalid_vectors,
    # complete), stored on the row at completion by migration 0015. None means
    # the run did not record it — every run written before 0015 — and
    # coverage_recorded says which of the two it is, because an empty findings
    # list without a denominator cannot distinguish "nothing succeeded" from
    # "nothing could try". Defaulted so a construction predating the column
    # still validates.
    coverage: dict[str, Any] | None = None
    coverage_recorded: bool = False


class RedTeamRunListResponse(BaseModel):
    runs: list[RedTeamRunResponse]


class RedTeamTriggerRequest(BaseModel):
    """Empty request body — triggers a manual red team run for the specified agent."""


class RedTeamTriggerResponse(BaseModel):
    job_id: str    # the Celery task ID
    run_id: str    # placeholder (actual run_id assigned inside the task; return the task_id here as a correlator)
    message: str   # human-readable status message, e.g., "Red team run queued"
