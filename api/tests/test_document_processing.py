from datetime import UTC, datetime

from app.documents.models import (
    DocumentRevision,
    DocumentRevisionStatus,
    DocumentType,
    ParserBlock,
    ParserResultRequest,
)
from app.documents.processing import DocumentProcessingService


def revision(status: DocumentRevisionStatus) -> DocumentRevision:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    return DocumentRevision(
        document_id="document-1",
        document_revision_id="revision-1",
        project_id="project-1",
        revision_number=1,
        document_type=DocumentType.PROPERTY_LISTING,
        original_filename="real-listing.pdf",
        content_type="application/pdf",
        size_bytes=100,
        sha256="a" * 64,
        status=status,
        failure_codes=[],
        created_at=now,
        updated_at=now,
    )


class RecordingDocuments:
    def __init__(self) -> None:
        self.current = revision(DocumentRevisionStatus.SCAN_PENDING)
        self.scan_calls: list[dict[str, object]] = []
        self.failure_codes: list[str] = []

    def record_scan_result(self, **kwargs: object) -> DocumentRevision:
        self.scan_calls.append(kwargs)
        self.current = revision(DocumentRevisionStatus.READY_FOR_PARSING)
        return self.current

    def mark_extraction_failed(self, **kwargs: object) -> DocumentRevision:
        self.failure_codes.append(str(kwargs["failure_code"]))
        self.current = revision(DocumentRevisionStatus.EXTRACTION_FAILED).model_copy(
            update={"failure_codes": self.failure_codes}
        )
        return self.current

    def get_revision(self, **_kwargs: object) -> DocumentRevision:
        return self.current


class RecordingStorage:
    def read(self, *, object_path: str) -> bytes:
        assert object_path.endswith("/source.pdf")
        return b"%PDF-safe"


class RecordingParser:
    def parse(self, value: DocumentRevision, content: bytes) -> ParserResultRequest:
        assert value.document_revision_id == "revision-1"
        assert content == b"%PDF-safe"
        return ParserResultRequest(
            project_id=value.project_id,
            document_id=value.document_id,
            parser_version="test.v1",
            blocks=[
                ParserBlock.model_validate(
                    {
                        "block_id": "block-1",
                        "text": "보증금 3,000만원",
                        "anchor": {
                            "document_revision_id": value.document_revision_id,
                            "page_index": 0,
                        },
                    }
                )
            ],
        )


class RecordingExtraction:
    def __init__(self, documents: RecordingDocuments) -> None:
        self.documents = documents
        self.calls: list[dict[str, object]] = []

    def accept_parser_result(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        self.documents.current = revision(DocumentRevisionStatus.EXTRACTION_READY)
        return object()


def test_real_document_runs_scan_parse_and_extraction_in_one_public_flow() -> None:
    documents = RecordingDocuments()
    extraction = RecordingExtraction(documents)
    service = DocumentProcessingService(
        documents=documents,
        extraction=extraction,
        storage=RecordingStorage(),  # type: ignore[arg-type]
        parser=RecordingParser(),  # type: ignore[arg-type]
    )

    result = service.process(revision=documents.current, user_id="user-1")

    assert result.status == DocumentRevisionStatus.EXTRACTION_READY
    assert documents.scan_calls[0]["clean"] is True
    assert documents.scan_calls[0]["enqueue_parsing"] is False
    assert len(extraction.calls) == 1
    request = extraction.calls[0]["request"]
    assert isinstance(request, ParserResultRequest)
    assert request.blocks[0].text == "보증금 3,000만원"
