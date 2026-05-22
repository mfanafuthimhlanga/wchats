"""
Pydantic v2 schemas for document endpoints.

DocumentUploadResponse — response body for POST /agents/{id}/documents (202 Accepted)
                         Fields: job_id, document_ids, status, events_url
DocumentResponse       — response body for GET /agents/{id}/documents/{doc_id}
                         Fields: id, source_uri, source_type, title, parse_status,
                                 chunk_count, created_at
DocumentListResponse   — response body for GET /agents/{id}/documents
                         Fields: documents (list of DocumentResponse)
DocumentDetailResponse — response body for GET /agents/{id}/documents/{id}/detail
                         Fields: document metadata + ordered chunks, each with
                         optional chunk_metadata and a list of entities.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentUploadResponse(BaseModel):
    job_id: UUID
    document_ids: list[UUID]
    status: str
    events_url: str


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_uri: str
    source_type: str
    title: str | None
    parse_status: str
    chunk_count: int | None
    created_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]


class ChunkMetadataResponse(BaseModel):
    """Per-chunk LLM-extracted metadata (chunk_metadata table).

    All fields default to safe empties so a chunk row whose metadata extraction
    never ran (or partially ran) still serialises without nulls in the arrays.
    """

    summary: str | None = None
    keywords: list[str] = []
    questions: list[str] = []


class ChunkEntityResponse(BaseModel):
    """A single entity linked to a chunk (entities joined via chunk_entities)."""

    name: str
    type: str
    normalized: str


class ChunkDetailResponse(BaseModel):
    """One chunk with its metadata (nullable) and zero-or-more entities.

    chunk_index maps to the tenant schema's chunks.ordinal column; text maps to
    chunks.content. metadata is None when no chunk_metadata row exists.
    """

    id: UUID
    chunk_index: int
    text: str
    metadata: ChunkMetadataResponse | None = None
    entities: list[ChunkEntityResponse] = []


class DocumentDetailResponse(BaseModel):
    id: UUID
    title: str | None
    source_uri: str
    source_type: str
    parse_status: str
    created_at: datetime
    chunks: list[ChunkDetailResponse]
