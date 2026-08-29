"""RedTeamFinding, one attack that landed and what it landed with (ticket 15, issue #52).

WHY IT MOVED DOWN A RUNG
    The type shipped in `app.services.red_team_service`, which sits two rungs
    above `app.domain`, so the frozen `RedTeamResult` could not hold one. A run's
    findings then had nowhere to live except a second column, `red_team_runs.findings`,
    written from a list the task carried alongside the record. Ticket 15 says a run
    produces one frozen result, and a result a reader has to join a second column to
    understand is the state the record was built to end. So the finding comes down to
    the record's rung, and `RedTeamResult.findings` holds it.

WHY IT IS STILL PYDANTIC AND NOT A DATACLASS
    Every other frozen type on this rung is a dataclass. This one is the exception
    because `run_red_team` writes `f.model_dump()` into the findings column and the
    stored shape is that dump. A dataclass conversion would change the write, and the
    criterion here is what a finding KEEPS, so the stored shape is the one thing that
    may not move. `frozen=True` on the config is pydantic's version of the same
    guarantee the dataclasses give, and it raises ValidationError rather than
    FrozenInstanceError.

WHY severity IS FOUR STRINGS AND NOT THE Severity ENUM
    `app.domain.red_team_result.Severity` has five members. The fifth is `none`, which
    a VectorOutcome needs to say that a vector breached nothing. A finding IS a breach,
    so `none` is a grade it can never carry, and typing this field as that enum would
    make `severity="none"` constructible. The four graded strings are pinned against
    the enum's four graded members by a test, because two lists of four strings in one
    package is one list that can drift.

WHY attack_vector IS NOT CHECKED AGAINST RED_TEAM_VECTORS
    It does not name the dispatch vector. `run_identity_bypass_agent` is dispatched as
    `identity_bypass` and reports its findings as `identity_verification_bypass`, and
    the M7 conversational findings still say `prompt_injection`. The roster check
    belongs on `VectorOutcome.vector`, which is the field the completeness rule reads,
    and it is there. Putting one here would refuse findings the shipped runners
    produce today.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports pydantic and nothing else.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RedTeamFinding(BaseModel):
    """One security finding a red-team attacker produced, frozen on construction.

    The four fields ticket 15's criterion names are `severity`, `attack_vector`,
    `probe_message` and `agent_response`. They are required, so a finding that
    cannot say what was sent or what came back cannot be built at all.

    Args:
        severity:       how bad this landed attack was, one of the four graded
                        values. A finding is a breach, so there is no `none`.
        description:    what the attacker got the agent to do, in a sentence.
        attack_vector:  the category the runner reports under. See the module
                        docstring for why this is free text.
        probe_message:  the exact probe text that triggered the finding.
        agent_response: the deployed agent's response text.
        turn_count:     which turn of the attack sequence this came from.
    """

    # Frozen, so a finding cannot be edited between the attacker producing it and
    # the run storing it. Nothing in the tree assigns to one today, and this is
    # what keeps that true.
    model_config = ConfigDict(frozen=True)

    severity: Literal["low", "medium", "high", "critical"]
    description: str
    attack_vector: str
    probe_message: str
    agent_response: str
    turn_count: int
