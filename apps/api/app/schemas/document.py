"""
Pydantic v2 schemas for document endpoints.

DocumentUploadResponse — response body for POST /agents/{id}/documents (202 Accepted)
                         Fields: job_id, document_ids, status, events_url
DocumentResponse       — response body for GET /agents/{id}/documents/{doc_id}
                         Fields: id, source_uri, source_type, title, parse_status,
                                 chunk_count, created_at
DocumentListResponse   — response body for GET /agents/{id}/documents
                         Fields: documents (list of DocumentResponse)
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
