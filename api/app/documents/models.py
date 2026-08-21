from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.domain.models import StrictModel


class DocumentType(StrEnum):
    COMMERCIAL_LEASE = "COMMERCIAL_LEASE"
    FRANCHISE_DISCLOSURE = "FRANCHISE_DISCLOSURE"
    FRANCHISE_AGREEMENT = "FRANCHISE_AGREEMENT"
    INTERIOR_QUOTE = "INTERIOR_QUOTE"
    EQUIPMENT_QUOTE = "EQUIPMENT_QUOTE"
    PROPERTY_LISTING = "PROPERTY_LISTING"
    LOAN_TERMS = "LOAN_TERMS"
    BUSINESS_PROCEDURE = "BUSINESS_PROCEDURE"
    OTHER = "OTHER"


class DocumentRevisionStatus(StrEnum):
    UPLOAD_PENDING = "UPLOAD_PENDING"
    VALIDATING = "VALIDATING"
    SCAN_PENDING = "SCAN_PENDING"
    READY_FOR_PARSING = "READY_FOR_PARSING"
    QUARANTINED = "QUARANTINED"
    DELETED = "DELETED"


class BeginDocumentUploadRequest(StrictModel):
    document_id: str | None = Field(default=None, min_length=1)
    document_type: DocumentType
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=52_428_800)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SignedUpload(StrictModel):
    document_id: str
    document_revision_id: str
    revision_number: int = Field(ge=1)
    object_path: str
    upload_url: str
    method: str = "PUT"
    required_headers: dict[str, str]
    expires_at: datetime
    status: DocumentRevisionStatus


class CompleteDocumentUploadRequest(StrictModel):
    document_revision_id: str = Field(min_length=1)


class DocumentScanResultRequest(StrictModel):
    project_id: str = Field(min_length=1)
    clean: bool
    threat_codes: list[str] = Field(default_factory=list)


class DocumentRevision(StrictModel):
    document_id: str
    document_revision_id: str
    project_id: str
    revision_number: int = Field(ge=1)
    document_type: DocumentType
    original_filename: str
    content_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: DocumentRevisionStatus
    failure_codes: list[str]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class DocumentDownload(StrictModel):
    document_revision_id: str
    download_url: str
    expires_at: datetime
