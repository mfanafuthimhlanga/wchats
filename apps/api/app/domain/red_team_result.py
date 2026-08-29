"""RedTeamResult, what one red-team run measured (ticket 15, issue #52).

WHY THE RUN HAD NO RECORD TYPE
    `run_red_team` writes four loose columns into `red_team_runs`: `findings`,
    `max_severity`, `deployment_blocked` and `coverage`. Nothing holds the four
    to one run. `max_severity` is recomputed from the findings blob at the
    write, `coverage` is assembled from a separate ledger of VectorObservation,
    and a reader asking "what did this run test, and what got through" joins
    four columns and trusts that one pass wrote them all.

    This record answers that in one frozen object. Its rows are the seven
    vectors a run dispatches, and each row says how many independent attempts
    ran, how many of them landed an attack, and how bad the worst one was.

WHY k IS A FIELD ON THE RECORD
    `settings.RED_TEAM_ATTACK_SEQUENCES` is a live setting. A reader that goes
    and looks it up compares today's configuration against a run that happened
    under yesterday's, so raising the setting silently turns every stored run
    incomplete and lowering it silently turns a truncated run complete. Neither
    run changed. The run writes `k` onto its own result, so the completeness
    rule below always measures a run against the requirement it ran under.

    `k` counts INDEPENDENT ATTEMPTS, and one attempt is a whole attack
    sequence. It is not `RED_TEAM_ATTACK_SEQUENCES`, which is 3 today and means
    three sequences inside ONE attacker loop under one shared 120 second
    timeout. The shipped dispatcher calls each runner once, so a result built
    from a run of it carries `attempts=1` on every vector. At `k=3` that result
    reports `complete` False and names all seven vectors, which is the honest
    reading of a dispatcher that has not been rebuilt yet.

WHY EACH NUMBER IS STORED RATHER THAN DERIVED
    `attempts` is the measurement and nothing else in the record implies it.
    `breaches` counts landed attacks, which `max_severity` cannot supply: the
    worst severity says how bad one attempt was, never how many got through.
    `max_severity` cannot be recovered from `breaches` either. The run-level
    `max_severity` and `breaches` ARE derived, once, here on the record, so two
    readers cannot arrive at two different totals for one run.

WHY AN ABSENT VECTOR IS NOT AN EXCUSED VECTOR
    A vector with no row counts as zero attempts, so it is incomplete at any k
    of one or above. A runner that raised before recording anything, or a
    caller that assembled a short result, must not be able to shrink the
    denominator into agreement with itself. Missing data is never passing data,
    the same rule `run_coverage` applies to a missing VectorObservation.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Every attack vector a full red-team run dispatches, in dispatch order.
#: `app.services.red_team_service` imported this tuple from here in ticket 15 and
#: re-exports it under the same name, so its own readers are unaffected. It lives
#: on this rung because the completeness rule below needs the roster it is
#: measuring against, and a second copy of seven strings is a second copy that
#: can disagree.
RED_TEAM_VECTORS: tuple[str, ...] = (
    "conversation_injection",
    "content_injection",
    "data_leakage",
    "hallucination",
    "confused_deputy",
    "value_bound_evasion",
    "identity_bypass",
)


class Severity(StrEnum):
    """How bad a landed attack was, with `none` for a vector nothing breached.

    The four graded values are the ones `RedTeamFinding.severity` already
    carries. `none` is the fifth because a vector that breached nothing still
    has to report something, and the shipped task already spells that state
    "none" when it writes `red_team_runs.max_severity`.
    """

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Worst last. The order is the whole reason this is an enum and not a string:
#: sorted as text, "critical" comes before "high", so a plain `max()` over the
#: severity strings reports a run's worst finding as its mildest one.
_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.NONE,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)
_SEVERITY_RANK: dict[Severity, int] = {
    severity: rank for rank, severity in enumerate(_SEVERITY_ORDER)
}


class InvalidRedTeamResult(ValueError):
    """A run record that would misreport what a run covered, refused on construction.

    A ValueError, so callers that already catch ValueError keep catching it, the
    same choice `InvalidModelCall` and `InvalidRetrievedContext` made.
    """


def _require_count(name: str, value: Any) -> None:
    """A count is a non-negative int. bool is checked first: True counts as one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRedTeamResult(
            f"RedTeamResult needs {name} as an int, got {type(value).__name__}"
        )
    if value < 0:
        raise InvalidRedTeamResult(f"RedTeamResult needs {name} at zero or above, got {value}")


def _as_severity(value: Any) -> Severity:
    """Coerce a stored string to the enum, refusing a grade nobody defined."""
    if isinstance(value, Severity):
        return value
    try:
        return Severity(value)
    except ValueError:
        raise InvalidRedTeamResult(
            f"RedTeamResult needs a severity from {[s.value for s in Severity]}, got {value!r}"
        ) from None


def worst_severity(severities: Iterable[Severity | str]) -> Severity:
    """The worst of several severities, `none` over nothing at all.

    THE ordering, exported so nobody keeps a second copy of it. The red-team
    task carried `SEVERITY_ORDER = ["low", "medium", "high", "critical"]` and
    ranked `red_team_runs.max_severity` by `.index()` into it, which is a second
    copy that can disagree with this one and a ValueError inside the completion
    write the day a grade is added here and not there.

    Raises:
        InvalidRedTeamResult: a grade outside Severity. A run's worst finding is
            not something to guess at, so an unknown string stops the write
            rather than being ranked as the mildest one.
    """
    return max(
        (_as_severity(value) for value in severities),
        key=_SEVERITY_RANK.__getitem__,
        default=Severity.NONE,
    )


@dataclass(frozen=True)
class VectorOutcome:
    """What ONE attack vector produced across its independent attempts.

    Args:
        vector:       one of RED_TEAM_VECTORS. A name outside that roster is a
                      row the completeness rule can never match, so it is
                      refused rather than carried and ignored.
        attempts:     independent attempts that ran to completion for this
                      vector. One attempt is one whole attack sequence.
        breaches:     how many of those attempts landed an attack. Never above
                      `attempts`, because an attack cannot land in an attempt
                      that never ran.
        max_severity: the worst severity across this vector's breaches, or
                      `none` when it breached nothing. A Severity, or its string
                      value, which is how a stored row reads back.

    Raises:
        InvalidRedTeamResult: unknown vector, a count that is negative or is not
            an int, more breaches than attempts, or a severity that disagrees
            with the breach count.
    """

    vector: str
    attempts: int
    breaches: int = 0
    # The init input, not what the record holds. __post_init__ coerces a string
    # to the enum.
    max_severity: Severity | str = Severity.NONE

    def __post_init__(self) -> None:
        if self.vector not in RED_TEAM_VECTORS:
            raise InvalidRedTeamResult(
                f"{self.vector!r} is not one of the {len(RED_TEAM_VECTORS)} vectors a "
                f"run dispatches: {', '.join(RED_TEAM_VECTORS)}"
            )
        _require_count("attempts", self.attempts)
        _require_count("breaches", self.breaches)
        if self.breaches > self.attempts:
            raise InvalidRedTeamResult(
                f"{self.vector} reports {self.breaches} breach(es) over "
                f"{self.attempts} attempt(s). An attack cannot land in an attempt "
                "that never ran."
            )
        severity = _as_severity(self.max_severity)
        if (severity is Severity.NONE) != (self.breaches == 0):
            # Both directions are the same defect. A breach with no grade cannot
            # be triaged, and a grade with no breach blocks a deploy over
            # nothing.
            raise InvalidRedTeamResult(
                f"{self.vector} reports {self.breaches} breach(es) at severity "
                f"'{severity.value}'. A breach carries a graded severity, and a "
                "vector that breached nothing carries 'none'."
            )
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "max_severity", severity)


@dataclass(frozen=True)
class RedTeamResult:
    """One red-team run's measurement: k, and one row per vector that reported.

    Args:
        k:       independent attempts each vector was required to make, as the
                 run that produced this record was configured. One or above; at
                 zero every vector is complete having attempted nothing.
        vectors: the per-vector rows. A list is accepted and copied; the record
                 holds a tuple. A vector may appear at most once, and a vector
                 may be absent, which reads as zero attempts.

    Raises:
        InvalidRedTeamResult: k below one or not an int, vectors is not a list
            or a tuple, a row is not a VectorOutcome, or one vector has two rows.
    """

    k: int
    # The init input, not what the record holds. __post_init__ copies whatever
    # sequence it is handed into a tuple.
    vectors: Sequence[VectorOutcome]

    def __post_init__(self) -> None:
        _require_count("k", self.k)
        if self.k < 1:
            raise InvalidRedTeamResult(
                "RedTeamResult needs k at one or above. At k=0 every vector is "
                "complete having attempted nothing, which is the one reading a "
                "coverage rule may never produce."
            )
        if not isinstance(self.vectors, (list, tuple)):
            # A string is the expensive case. tuple("abc") raises nothing and
            # builds three rows that name no vector.
            raise InvalidRedTeamResult(
                "RedTeamResult needs vectors as a list or a tuple, got "
                f"{type(self.vectors).__name__}"
            )
        wrong = [
            type(row).__name__ for row in self.vectors if not isinstance(row, VectorOutcome)
        ]
        if wrong:
            raise InvalidRedTeamResult(
                "RedTeamResult needs every row to be a VectorOutcome, got "
                + ", ".join(wrong)
            )
        named = [row.vector for row in self.vectors]
        repeated = sorted({name for name in named if named.count(name) > 1})
        if repeated:
            # Two rows for one vector are two answers to one question. Picking
            # one silently is how the weaker measurement wins an argument
            # nobody sees.
            raise InvalidRedTeamResult(
                "RedTeamResult needs at most one row per vector, got two or more "
                "for " + ", ".join(repeated)
            )
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "vectors", tuple(self.vectors))

    def attempts_for(self, vector: str) -> int:
        """Independent attempts this vector completed. An absent vector ran none."""
        for row in self.vectors:
            if row.vector == vector:
                return row.attempts
        return 0

    @property
    def breaches(self) -> int:
        """Attempts that landed an attack, across every vector."""
        return sum(row.breaches for row in self.vectors)

    @property
    def max_severity(self) -> Severity:
        """The worst severity any vector reported. `none` when nothing landed."""
        return worst_severity(row.max_severity for row in self.vectors)

    @property
    def incomplete_vectors(self) -> tuple[str, ...]:
        """THE completeness rule: every vector short of k attempts, in dispatch order.

        A vector with no row is short by all k of them. That is the case the
        shipped `run_coverage` had to be taught as well, and it is the one a
        rule written over `self.vectors` alone gets wrong, because a run that
        dispatched nothing has no rows to be short.
        """
        return tuple(
            vector for vector in RED_TEAM_VECTORS if self.attempts_for(vector) < self.k
        )

    @property
    def coverage(self) -> dict:
        """What this run covered, as `red_team_runs.coverage` reads it back.

        `complete` is the claim a deploy gate acts on, and it is False whenever
        any of the seven is short of k attempts. `incomplete_reason` names each
        one and its shortfall, because a person reading a blocked run out of the
        column needs to know which vector went untested and by how much.

        Returns:
            {"k", "vectors_attempted", "vectors_complete", "incomplete_vectors",
             "incomplete_reason", "complete"}. `vectors_attempted` is how many
            vectors a full run dispatches, which is the denominator, not how
            many this record happens to hold.
        """
        short = self.incomplete_vectors
        return {
            "k": self.k,
            "vectors_attempted": len(RED_TEAM_VECTORS),
            "vectors_complete": len(RED_TEAM_VECTORS) - len(short),
            "incomplete_vectors": list(short),
            "incomplete_reason": self._incomplete_reason(short),
            "complete": not short,
        }

    @property
    def payload(self) -> dict:
        """The whole record as JSON, which is how `red_team_runs.result` holds it.

        One place decides the stored shape, so the task that writes the column
        and any reader that grows a parser for it are looking at the same keys.
        The two run-level totals are written out rather than left to be derived
        again by whoever reads the row.

        Returns:
            {"k", "vectors": [{"vector", "attempts", "breaches", "max_severity"}],
             "breaches", "max_severity", "coverage"}.
        """
        return {
            "k": self.k,
            "vectors": [
                {
                    "vector": row.vector,
                    "attempts": row.attempts,
                    "breaches": row.breaches,
                    "max_severity": Severity(row.max_severity).value,
                }
                for row in self.vectors
            ],
            "breaches": self.breaches,
            "max_severity": self.max_severity.value,
            "coverage": self.coverage,
        }

    def _incomplete_reason(self, short: tuple[str, ...]) -> str | None:
        """One clause per short vector, or None when every vector met k."""
        if not short:
            return None
        return "; ".join(
            f"{vector}: {self.attempts_for(vector)} of {self.k} attempt(s) ran"
            for vector in short
        )
