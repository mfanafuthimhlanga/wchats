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

WHY attack_vector IS FREE TEXT AND NOT A RED_TEAM_VECTORS MEMBER
    The attacker model picks it. `_TOOL_REPORT_FINDING`
    (`app/services/red_team_service.py:245`) declares `attack_vector` as a bare string
    with no enum beside it, and `_classify_reported_findings` (`:1115`) reads
    `raw.get("attack_vector")` ahead of `session.attack_vector`, so the finding carries
    whatever the model typed. A roster check here would throw away the probe and the
    response over one word the model chose, which costs more than an unmatchable name.

    EXPECT THE TWO NAMES TO DIFFER. `run_identity_bypass_agent` is dispatched as
    `identity_bypass` and records that on its VectorObservation
    (`red_team_service.py:2147`); the findings it returns say
    `identity_verification_bypass` (`:2122`). That is the pair a reader will meet
    first.

    WHAT A MODEL-TYPED VECTOR COSTS. `run_red_team` groups the findings by this field
    and upserts one `red_team_strategies` row per distinct value
    (`app/worker/tasks/runtime/red_team.py:719-735`), so a name the model invented
    becomes a strategy row of its own and stays there. Nothing downstream corrects it:
    `RedTeamResult.coverage` reads the vector rows alone and never matches a finding to
    a row. The roster check belongs on `VectorOutcome.vector`, which is the field the
    completeness rule does read, and it is there.

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
        attack_vector:  the category the attacker model reported under, which is
                        not always the vector that was dispatched. See the module
                        docstring.
        probe_message:  the exact probe text that triggered the finding.
        agent_response: the deployed agent's response text.
        turn_count:     which turn of the attack sequence this came from.
    """

    # Frozen, so a finding cannot be edited between the attacker producing it and
    # the run storing it. Nothing in the tree assigns to one today, and this is
    # what keeps that true.
    #
    # extra="forbid" so a seventh key cannot ride along. `model_dump()` is the
    # stored shape at two write sites, and pydantic's default would carry a
    # misspelt key into `red_team_runs.findings` and `red_team_runs.result` with
    # neither column's reader told it was there. The one place a raw dict reaches
    # this type, `_classify_reported_findings`, names all six keys itself, so a
    # key the attacker model invented is dropped at that boundary and never
    # reaches this refusal.
    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: Literal["low", "medium", "high", "critical"]
    description: str
    attack_vector: str
    probe_message: str
    agent_response: str
    turn_count: int
