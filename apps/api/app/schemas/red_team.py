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


class RedTeamRunListResponse(BaseModel):
    runs: list[RedTeamRunResponse]


class RedTeamTriggerRequest(BaseModel):
    """Empty request body — triggers a manual red team run for the specified agent."""


class RedTeamTriggerResponse(BaseModel):
    job_id: str    # the Celery task ID
    run_id: str    # placeholder (actual run_id assigned inside the task; return the task_id here as a correlator)
    message: str   # human-readable status message, e.g., "Red team run queued"
