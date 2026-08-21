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
    PARSING = "PARSING"
    EXTRACTION_READY = "EXTRACTION_READY"
    APPLIED = "APPLIED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
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


class DocumentAnchor(StrictModel):
    document_revision_id: str = Field(min_length=1)
    page_index: int = Field(ge=0)
    section_path: str | None = None
    table_id: str | None = None
    row: int | None = Field(default=None, ge=0)
    column: int | None = Field(default=None, ge=0)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=8)


class ParserBlock(StrictModel):
    block_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=50_000)
    anchor: DocumentAnchor


class ParserResultRequest(StrictModel):
    project_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1, max_length=128)
    blocks: list[ParserBlock] = Field(min_length=1, max_length=1200)
    prompt_injection_flags: list[str] = Field(default_factory=list)


class ExtractionStatus(StrEnum):
    AUTO_FILLED = "AUTO_FILLED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNRESOLVED = "UNRESOLVED"


class EditStatus(StrEnum):
    UNCHANGED = "UNCHANGED"
    EDITED = "EDITED"
    CLEARED = "CLEARED"


class ExtractionField(StrictModel):
    field_id: str
    claim_type: str
    label: str
    raw_value_text: str | None
    extracted_value: str | int | float | bool | None
    current_value: str | int | float | bool | None
    unit: str | None
    materiality: str
    extraction_status: ExtractionStatus
    edit_status: EditStatus = EditStatus.UNCHANGED
    anchor: DocumentAnchor | None
    warnings: list[str]


class DocumentExtractionForm(StrictModel):
    schema_version: str = "1.0.0"
    form_id: str
    project_id: str
    document_id: str
    document_revision_id: str
    expected_state_version: int = Field(ge=1)
    form_status: str
    fields: list[ExtractionField] = Field(min_length=1)
    apply_label: str = "반영하고 다시 계산"
    form_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    applied_state_version: int | None = None


class ExtractionFieldEdit(StrictModel):
    field_id: str = Field(min_length=1)
    value: str | int | float | bool | None


class UpdateExtractionFormRequest(StrictModel):
    expected_state_version: int = Field(ge=1)
    edits: list[ExtractionFieldEdit] = Field(min_length=1)


class ApplyExtractionFormRequest(StrictModel):
    expected_state_version: int = Field(ge=1)
    expected_form_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class AppliedDocumentClaim(StrictModel):
    claim_id: str
    claim_type: str
    value: str | int | float | bool
    unit: str | None
    materiality: str
    document_revision_id: str
    anchor: DocumentAnchor | None


class DocumentClaimConflict(StrictModel):
    conflict_id: str
    claim_type: str
    materiality: str
    competing_claim_ids: list[str] = Field(min_length=2)
    status: str = "OPEN"


class ExtractionFormApplication(StrictModel):
    application_id: str
    project_id: str
    document_revision_id: str
    applied_state_version: int = Field(ge=2)
    recompute_workflow_run_id: str
    claims: list[AppliedDocumentClaim]
    conflicts: list[DocumentClaimConflict]
    requires_human_review: bool


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
