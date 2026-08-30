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

WHY THE RECORD HOLDS THE FINDINGS
    The rows above are counts. They say a vector was attacked three times and
    that one of those landed at `high`, and they cannot say what was sent or what
    came back. A reader holding only counts has to go to a second column,
    `red_team_runs.findings`, and trust that the same pass wrote both, which is
    the join this record exists to remove. So `findings` is a field, each one
    keeping its severity, its vector, the probe and the response, and `payload`
    writes them into `red_team_runs.result` with everything else.

    Holding both halves is not the same as them agreeing. A record could carry a
    high finding beside seven rows that all report zero breaches, and `payload`
    would write `breaches=0, max_severity="none"` beside that finding.
    `_require_findings_agree` refuses that record on construction.

WHY THE STORED RECORD IS RE-CHECKED ON THE WAY OUT
    `from_payload` is `payload`'s inverse and it refuses a stored shape on every
    rule a fresh record is refused on. Already being written down is not evidence
    that a shape is honest: `red_team_runs.result` is jsonb, so nothing in the
    column stops a hand-edited row, a row an older build wrote, or a row written
    before a key meant what it means now.

    A MISSING KEY IS A REFUSAL, NEVER A DEFAULT. A defaulted count is a number,
    and a number is indistinguishable from a measurement one reader later. An
    unknown key is a refusal too, so adding a field to this record is a breaking
    read of older rows on purpose rather than a silent half-read. The stored
    `breaches`, `max_severity` and `coverage` are derived, and `from_payload`
    re-derives them from the rows and refuses a payload that disagrees, because a
    column holding two answers to one question answers neither.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library and one sibling,
`app.domain.red_team_finding`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.domain.red_team_finding import RedTeamFinding

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


#: The keys `VectorOutcome`'s stored row carries, and the only ones it reads.
_VECTOR_KEYS: tuple[str, ...] = ("vector", "attempts", "breaches", "max_severity")

#: The keys `RedTeamResult.payload` writes, and the only ones it reads back. The
#: last three are derived from `vectors`; they are stored so a reader of the
#: column never re-derives them, and re-derived on the way out so the column
#: cannot hold two answers.
_RESULT_KEYS: tuple[str, ...] = (
    "k",
    "vectors",
    "findings",
    "breaches",
    "max_severity",
    "coverage",
)


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


def _as_findings(value: Any) -> tuple[RedTeamFinding, ...]:
    """Copy a caller's findings into the tuple the record holds.

    Raises:
        InvalidRedTeamResult: findings is not a list or a tuple, or a row is not a
            RedTeamFinding. The string case is the expensive one: `tuple("abc")`
            raises nothing and builds three findings that say nothing.
    """
    if not isinstance(value, (list, tuple)):
        raise InvalidRedTeamResult(
            f"RedTeamResult needs findings as a list or a tuple, got {type(value).__name__}"
        )
    wrong = [type(row).__name__ for row in value if not isinstance(row, RedTeamFinding)]
    if wrong:
        raise InvalidRedTeamResult(
            "RedTeamResult needs every finding to be a RedTeamFinding, got " + ", ".join(wrong)
        )
    return tuple(value)


def _require_exact_keys(payload: Mapping, keys: tuple[str, ...], where: str) -> None:
    """The stored shape names every key and no others.

    A MISSING key is refused rather than defaulted, because a default is a number
    and a number is indistinguishable from a measurement one reader later. An
    UNKNOWN key is refused because something this build does not know about wrote
    it, and reading the rest of the row would be reading half of whatever that
    was. `app.domain.verdict` reads its own stored shape under the same rule.

    Raises:
        InvalidRedTeamResult: the payload is not a mapping, misses a key, or
            carries one this build does not read.
    """
    if not isinstance(payload, Mapping):
        raise InvalidRedTeamResult(f"{where} needs a mapping, got {type(payload).__name__}")
    missing = sorted(set(keys) - set(payload))
    if missing:
        raise InvalidRedTeamResult(
            f"{where} needs {', '.join(missing)} in the stored shape. A default in "
            "its place would report a measurement nobody made."
        )
    unknown = sorted(set(payload) - set(keys))
    if unknown:
        raise InvalidRedTeamResult(
            f"{where} was stored with {', '.join(unknown)}, which this build does "
            f"not read. Its keys are {', '.join(keys)}."
        )


def _finding_from_payload(stored: Any) -> RedTeamFinding:
    """One stored finding, with pydantic doing the checking.

    `RedTeamFinding.model_dump()` is what `payload` wrote, so this is its inverse
    and nothing here restates the six fields. A missing field and a seventh key
    are both refusals, which is the model's required fields plus its
    `extra="forbid"`, and both arrive as ValidationError.

    Raises:
        InvalidRedTeamResult: the stored finding is not a mapping, or pydantic
            refuses it. ValidationError IS a ValueError, so this rung catches it
            without importing pydantic to name it.
    """
    if not isinstance(stored, Mapping):
        raise InvalidRedTeamResult(
            "RedTeamResult needs every stored finding to be a mapping, got "
            f"{type(stored).__name__}"
        )
    try:
        return RedTeamFinding(**stored)
    except (TypeError, ValueError) as exc:
        raise InvalidRedTeamResult(
            f"RedTeamResult cannot rebuild a stored finding ({type(exc).__name__}: {exc})"
        ) from exc


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


def _require_findings_agree(
    findings: Sequence[RedTeamFinding], breaches: int, max_severity: Severity
) -> None:
    """Refuse a record whose findings and whose vector rows describe different runs.

    THE INVARIANT `_attempt_every_vector` ALREADY SATISFIES, written down. It runs
    one vector at a time; `run_vector_attempts` extends that vector's finding list
    with every attempt's findings, counts a `breach` for each attempt whose list
    came back non-empty, and grades the row `worst_severity` over that same list.
    The task then extends the record's findings with the identical list. Fold the
    seven rows together and two equalities hold over the whole run:

        the worst grade over every finding IS the run's max_severity
        there are never fewer findings than there are breached attempts

    Break either one and the record was not built by a run. The case this exists
    for is `findings=[one high finding]` beside seven rows reporting zero
    breaches: `payload` wrote `breaches=0, max_severity="none"` into
    `red_team_runs.result` next to the high finding the same record held, and a
    reader acting on the counts read that run as clean.

    RUN LEVEL, NEVER PER VECTOR. `RedTeamFinding.attack_vector` is whatever the
    attacker model typed rather than the dispatch vector its row is keyed by, so
    this rung cannot match one finding to one row. `red_team_finding`'s module
    docstring carries that argument. The totals are matchable, and the totals are
    what a reader of the column acts on.

    Args:
        findings:     the record's findings, already type-checked and copied.
        breaches:     the run's total, summed across the vector rows.
        max_severity: the run's worst grade, taken across the vector rows.

    Raises:
        InvalidRedTeamResult: naming both numbers, so a reader of the message
            knows which half of the record to distrust.
    """
    worst = worst_severity(finding.severity for finding in findings)
    if worst is not max_severity:
        raise InvalidRedTeamResult(
            f"RedTeamResult holds {len(findings)} finding(s) whose worst grade is "
            f"'{worst.value}', beside vector rows reporting "
            f"'{max_severity.value}'. One run has one worst finding."
        )
    if len(findings) < breaches:
        raise InvalidRedTeamResult(
            f"RedTeamResult holds {len(findings)} finding(s) beside vector rows "
            f"reporting {breaches} breached attempt(s). An attempt that landed an "
            "attack produced at least one finding, so a run can never hold fewer "
            "findings than breaches."
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

    @classmethod
    def from_payload(cls, payload: Mapping) -> VectorOutcome:
        """Rebuild one vector's row from its stored form.

        Raises:
            InvalidRedTeamResult: the stored shape is not a mapping, misses a
                key, carries a key this build does not read, or breaks one of the
                construction rules above.
        """
        _require_exact_keys(payload, _VECTOR_KEYS, "VectorOutcome")
        return cls(
            vector=payload["vector"],
            attempts=payload["attempts"],
            breaches=payload["breaches"],
            max_severity=_as_severity(payload["max_severity"]),
        )


def _require_derived_agree(payload: Mapping, record: RedTeamResult) -> None:
    """The stored totals say what the stored rows add up to.

    `payload` writes `breaches`, `max_severity` and `coverage` beside the rows
    they came off, so a reader of the column never re-derives them.
    `from_payload` rebuilds the record from the rows alone, so a payload claiming
    `breaches: 0` over rows reporting two reads back as two, and the column held
    two answers with nothing choosing between them. Each stored total therefore
    has to equal what the rebuilt record derives, and a payload where one does
    not is refused rather than quietly corrected.

    This is the read-time twin of `_require_findings_agree`, which holds the
    findings to the rows at construction. Together they leave one number per
    question in the column.

    Args:
        payload: the stored shape, already checked for its exact keys.
        record:  what the rows in it rebuilt.

    Raises:
        InvalidRedTeamResult: a stored total disagrees with the rows under it.
    """
    for name, derived in (
        ("breaches", record.breaches),
        ("max_severity", record.max_severity.value),
        ("coverage", record.coverage),
    ):
        if payload[name] != derived:
            raise InvalidRedTeamResult(
                f"RedTeamResult stores {name}={payload[name]!r} over vector rows "
                f"that derive {derived!r}. One run cannot have two answers to one "
                "question, and the rows are where the attempts were counted."
            )


@dataclass(frozen=True)
class RedTeamResult:
    """One red-team run's measurement: k, and one row per vector that reported.

    Args:
        k:        independent attempts each vector was required to make, as the
                  run that produced this record was configured. One or above; at
                  zero every vector is complete having attempted nothing.
        vectors:  the per-vector rows. A list is accepted and copied; the record
                  holds a tuple. A vector may appear at most once, and a vector
                  may be absent, which reads as zero attempts.
        findings: every attack that landed, in dispatch order, each one keeping
                  its severity, its vector, the probe that was sent and the
                  response that came back. Defaults to none, which is what a run
                  that breached nothing produced. The rows above stay the
                  authority on HOW MANY attempts and breaches a vector had; these
                  are what those breaches were.

    Raises:
        InvalidRedTeamResult: k below one or not an int, vectors is not a list
            or a tuple, a row is not a VectorOutcome, one vector has two rows,
            findings is not a list or a tuple, a finding is not a
            RedTeamFinding, or the findings and the rows disagree about what
            this run measured (`_require_findings_agree`).
    """

    k: int
    # The init input, not what the record holds. __post_init__ copies whatever
    # sequence it is handed into a tuple. Same for `findings` below.
    vectors: Sequence[VectorOutcome]
    findings: Sequence[RedTeamFinding] = field(default_factory=tuple)

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
        object.__setattr__(self, "findings", _as_findings(self.findings))
        # Last, because it reads `breaches` and `max_severity`, and both are
        # derived over the rows the two lines above just normalised.
        _require_findings_agree(self.findings, self.breaches, self.max_severity)

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

        The findings ride along, which is what makes the column answer "what got
        through, and with what" on its own. `red_team_runs.findings` holds the
        same dumps in its own column and keeps doing so, because the deploy gate
        and the ops room read that one; a reader of `result` should not have to
        join to a second column to finish the sentence the record starts.
        `RedTeamFinding.model_dump()` is the shape both columns store, so the two
        cannot describe one finding differently.

        Returns:
            {"k", "vectors": [{"vector", "attempts", "breaches", "max_severity"}],
             "findings": [{"severity", "description", "attack_vector",
             "probe_message", "agent_response", "turn_count"}], "breaches",
             "max_severity", "coverage"}.
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
            "findings": [finding.model_dump() for finding in self.findings],
            "breaches": self.breaches,
            "max_severity": self.max_severity.value,
            "coverage": self.coverage,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> RedTeamResult:
        """Rebuild the record from a stored `red_team_runs.result`.

        The round trip is the contract: `RedTeamResult.from_payload(r.payload) == r`,
        and `from_payload(p).payload == p` back the other way. A stored row is
        validated on the way out as it was on the way in.

        EVERY WAY A STORED SHAPE CAN BE WRONG LEAVES HERE AS InvalidRedTeamResult.
        A reader that catches this module's refusal alone would otherwise take a
        pydantic ValidationError out of one malformed finding, or a TypeError out
        of a row that is not a mapping, as a 500 for the whole route.

        Args:
            payload: the stored shape, as `red_team_runs.result` holds it.

        Raises:
            InvalidRedTeamResult: the stored shape is not a mapping, misses a
                key, carries a key this build does not read, breaks a
                construction rule, or cannot be read as this record at all.
        """
        _require_exact_keys(payload, _RESULT_KEYS, "RedTeamResult")
        rows = payload["vectors"]
        if not isinstance(rows, (list, tuple)):
            raise InvalidRedTeamResult(
                f"RedTeamResult needs stored vectors as a list, got {type(rows).__name__}"
            )
        stored_findings = payload["findings"]
        if not isinstance(stored_findings, (list, tuple)):
            raise InvalidRedTeamResult(
                "RedTeamResult needs stored findings as a list, got "
                f"{type(stored_findings).__name__}"
            )
        try:
            record = cls(
                k=payload["k"],
                vectors=[VectorOutcome.from_payload(row) for row in rows],
                findings=[_finding_from_payload(row) for row in stored_findings],
            )
        except InvalidRedTeamResult:
            # Already this module's refusal, carrying which rule it broke.
            raise
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            raise InvalidRedTeamResult(
                "RedTeamResult cannot be rebuilt from this stored shape "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        _require_derived_agree(payload, record)
        return record

    def _incomplete_reason(self, short: tuple[str, ...]) -> str | None:
        """One clause per short vector, or None when every vector met k."""
        if not short:
            return None
        return "; ".join(
            f"{vector}: {self.attempts_for(vector)} of {self.k} attempt(s) ran"
            for vector in short
        )
