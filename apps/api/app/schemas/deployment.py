"""Pydantic schemas for M8 deployment API routes — request/response models for checklist-runs endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class ChecklistRunResponse(BaseModel):
    id: str
    agent_id: str
    status: str                         # "running" | "complete" | "failed"
    recommendation: str | None          # "ship" | "ship_with_warnings" | "block" | None
    report: dict[str, Any] | None       # full DeploymentReport JSONB
    warnings: list[dict[str, Any]]      # list of DeploymentWarning dicts
    warning_acknowledgments: dict[str, Any]
    all_warnings_acknowledged: bool
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime


class ChecklistRunListResponse(BaseModel):
    runs: list[ChecklistRunResponse]


class ChecklistRunTriggerResponse(BaseModel):
    checklist_run_id: str   # Celery task ID (correlator for polling)
    status: str             # "queued"


class AcknowledgeRequest(BaseModel):
    warning_ids: list[str]  # list of warning_id slugs to acknowledge


class AcknowledgeResponse(BaseModel):
    all_warnings_acknowledged: bool


class ApproveDeploymentRequest(BaseModel):
    checklist_run_id: UUID  # UUID of the checklist_run to approve — typed UUID so db.get(ChecklistRun, ...) matches UUID PK


class ApproveDeploymentResponse(BaseModel):
    deployed: bool
    agent_id: str
    iframe_snippet: str
