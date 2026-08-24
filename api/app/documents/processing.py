"""업로드 완료 문서를 검사하고 실제 추출 폼까지 한 경로로 연결한다."""

from typing import Protocol

from app.documents.models import (
    DocumentExtractionForm,
    DocumentRevision,
    DocumentRevisionStatus,
    ParserResultRequest,
)
from app.documents.parser import (
    DocumentParseError,
    OperationalDocumentParser,
    active_content_threats,
)
from app.documents.storage import DocumentStorage
from app.domain.errors import ExternalExecutionUnavailableError


class DocumentLifecycle(Protocol):
    def record_scan_result(
        self,
        *,
        project_id: str,
        document_revision_id: str,
        clean: bool,
        threat_codes: list[str],
        enqueue_parsing: bool = True,
    ) -> DocumentRevision: ...

    def mark_extraction_failed(
        self,
        *,
        project_id: str,
        document_revision_id: str,
        failure_code: str,
    ) -> DocumentRevision: ...

    def get_revision(
        self,
        *,
        project_id: str,
        user_id: str,
        document_revision_id: str,
    ) -> DocumentRevision: ...


class ExtractionReceiver(Protocol):
    def accept_parser_result(
        self,
        *,
        document_revision_id: str,
        request: ParserResultRequest,
    ) -> DocumentExtractionForm: ...


class DocumentProcessingService:
    def __init__(
        self,
        *,
        documents: DocumentLifecycle,
        extraction: ExtractionReceiver,
        storage: DocumentStorage,
        parser: OperationalDocumentParser,
    ) -> None:
        self._documents = documents
        self._extraction = extraction
        self._storage = storage
        self._parser = parser

    def process(self, *, revision: DocumentRevision, user_id: str) -> DocumentRevision:
        if revision.status != DocumentRevisionStatus.SCAN_PENDING:
            return revision
        try:
            content = self._storage.read(object_path=self._object_path(revision))
        except FileNotFoundError:
            return self._fail(revision, user_id=user_id, code="OBJECT_NOT_FOUND")
        threats = active_content_threats(content=content, content_type=revision.content_type)
        scanned = self._documents.record_scan_result(
            project_id=revision.project_id,
            document_revision_id=revision.document_revision_id,
            clean=not threats,
            threat_codes=threats,
            enqueue_parsing=False,
        )
        if scanned.status != DocumentRevisionStatus.READY_FOR_PARSING:
            return scanned
        try:
            parser_request = self._parser.parse(revision, content)
            self._extraction.accept_parser_result(
                document_revision_id=revision.document_revision_id,
                request=parser_request,
            )
        except DocumentParseError as error:
            return self._fail(revision, user_id=user_id, code=error.code)
        except Exception as error:
            self._fail(revision, user_id=user_id, code="DOCUMENT_EXTRACTION_FAILED")
            raise ExternalExecutionUnavailableError("Document extraction failed") from error
        return self._documents.get_revision(
            project_id=revision.project_id,
            user_id=user_id,
            document_revision_id=revision.document_revision_id,
        )

    def _fail(self, revision: DocumentRevision, *, user_id: str, code: str) -> DocumentRevision:
        self._documents.mark_extraction_failed(
            project_id=revision.project_id,
            document_revision_id=revision.document_revision_id,
            failure_code=code,
        )
        return self._documents.get_revision(
            project_id=revision.project_id,
            user_id=user_id,
            document_revision_id=revision.document_revision_id,
        )

    @staticmethod
    def _object_path(revision: DocumentRevision) -> str:
        extension = {
            "application/pdf": ".pdf",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }[revision.content_type]
        return (
            f"projects/{revision.project_id}/documents/{revision.document_id}/revisions/"
            f"{revision.document_revision_id}/source{extension}"
        )
