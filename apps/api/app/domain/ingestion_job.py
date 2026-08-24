"""IngestionJob, the four ids the ingestion chain hands from hop to hop (ticket #43, issue #7).

WHY A TYPE RATHER THAN THE DICT IT REPLACES
    parse, chunk, metadata and embed passed `{"tenant_id", "agent_id",
    "job_id", "document_ids"}` between them, and three of the four opened with
    the same four-clause guard over it:

        if not tenant_id or not agent_id or not job_id or document_ids is None:

    One rule, spelled three times, in three files that are edited separately.
    Here it is spelled once, in construction, so a job that exists is a job
    whose four ids are present and no task asks again.

WHY THE WIRE IS STILL A DICT
    Celery serialises task arguments as JSON, so the chain cannot carry a
    dataclass between workers. `to_dict` and `from_dict` are that conversion,
    and each task converts at its Celery edge while its body takes and returns
    the type. The dict on the wire is byte for byte the one the chain has always
    sent, which is what keeps a chain re-dispatched mid-flight readable by the
    hop that receives it.

    `from_dict` IGNORES KEYS IT DOES NOT KNOW. A dict from an older revision of
    the pipeline may carry more than these four keys, and a hop that refused it
    would strand a job that is otherwise complete.

WHY document_ids IS A TUPLE
    The record is frozen, so what it holds is immutable too: the list a caller
    passes in is copied, and a later append to that list cannot reach a job that
    four tasks are reading. An EMPTY tuple is a whole job. Nothing to do is not
    the same as nothing given, which is why None is refused and () is not.

WHAT IS NOT HERE
    No connection string, and no field that could hold one (project rule 1). A
    task receives ids, then fetches and decrypts the tenant connection string
    from the control DB at runtime. That promise used to live in four task
    docstrings and at every return statement; it lives in the field set now.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

_REQUIRED_IDS = ("tenant_id", "agent_id", "job_id")


@dataclass(frozen=True)
class IngestionJob:
    """One run of the ingestion chain, as every hop in it receives the run.

    Args:
        tenant_id:    UUID string of the owning tenant.
        agent_id:     UUID string of the agent being ingested for. Every task
                      fetches the tenant connection string by this id.
        job_id:       UUID string of the job row, and the SSE channel every
                      event in this run is published to.
        document_ids: The documents this run covers. A list is accepted and
                      copied; the job holds a tuple. Empty is allowed.

    Raises:
        ValueError: any of the three ids is empty, or document_ids is None.
    """

    tenant_id: str
    agent_id: str
    job_id: str
    document_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in _REQUIRED_IDS:
            if not getattr(self, name):
                raise ValueError(
                    f"IngestionJob needs a {name}, got {getattr(self, name)!r}"
                )
        if self.document_ids is None:
            raise ValueError(
                "IngestionJob needs document_ids, got None. An empty list is a whole job."
            )
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "document_ids", tuple(self.document_ids))

    def to_dict(self) -> dict[str, Any]:
        """The wire form: the four keys, with document_ids as a JSON list."""
        return {
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "job_id": self.job_id,
            "document_ids": list(self.document_ids),
        }

    @classmethod
    def from_dict(cls, mapping: Mapping[str, Any]) -> IngestionJob:
        """Read a job off the wire, ignoring any key this type does not name.

        A missing key reads as absent and fails construction, which is the same
        outcome the tasks reached by asking `result.get(...)` and testing the
        answer.
        """
        return cls(**{field.name: mapping.get(field.name) for field in fields(cls)})

