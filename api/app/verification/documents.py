import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import httpx
from google.api_core.exceptions import NotFound
from sqlalchemy import Engine, text

from app.documents.extraction import DocumentExtractionService
from app.documents.models import (
    BeginDocumentUploadRequest,
    DocumentRevisionStatus,
    DocumentType,
    ParserBlock,
    ParserResultRequest,
)
from app.documents.service import DocumentService
from app.documents.storage import GoogleCloudDocumentStorage


class SignedUrlTransport(Protocol):
    def put(self, url: str, *, content: bytes, headers: Mapping[str, str]) -> int: ...

    def get(self, url: str) -> tuple[int, bytes]: ...


class HttpxSignedUrlTransport:
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds

    def put(self, url: str, *, content: bytes, headers: Mapping[str, str]) -> int:
        return httpx.put(
            url,
            content=content,
            headers=dict(headers),
            timeout=self._timeout_seconds,
        ).status_code

    def get(self, url: str) -> tuple[int, bytes]:
        response = httpx.get(url, timeout=self._timeout_seconds)
        return response.status_code, response.content


class DocumentStorageCanaryError(RuntimeError):
    def __init__(self, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class DocumentStorageCanaryReport:
    status: str
    upload_status: str
    scan_status: str
    extraction_status: str
    download_bytes: int
    agent_result_statuses: tuple[str, ...]
    extracted_field_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "upload_status": self.upload_status,
            "scan_status": self.scan_status,
            "extraction_status": self.extraction_status,
            "download_bytes": self.download_bytes,
            "agent_result_statuses": list(self.agent_result_statuses),
            "extracted_field_count": self.extracted_field_count,
        }


class DocumentStorageCanary:
    """Exercise the deployed bucket and DOCUMENT_EXTRACT boundary with disposable state."""

    def __init__(
        self,
        *,
        engine: Engine,
        documents: DocumentService,
        extraction: DocumentExtractionService,
        storage: GoogleCloudDocumentStorage,
        policy_snapshot_id: str,
        seed_registry_id: str,
        transport: SignedUrlTransport | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._documents = documents
        self._extraction = extraction
        self._storage = storage
        self._policy_snapshot_id = policy_snapshot_id
        self._seed_registry_id = seed_registry_id
        self._transport = transport or HttpxSignedUrlTransport()
        self._new_id = new_id or (lambda: uuid4().hex)

    def run(self) -> DocumentStorageCanaryReport:
        canary_id = self._new_id()
        project_id = f"document-canary-{canary_id}"
        user_id = f"document-canary-user-{canary_id}"
        active_case_id = f"document-canary-case-{canary_id}"
        object_path: str | None = None
        revision_id: str | None = None
        content = b"%PDF-1.7\nCaffeMate canary lease deposit 50000000 KRW\n%%EOF\n"
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            self._seed_project(
                project_id=project_id,
                user_id=user_id,
                active_case_id=active_case_id,
            )
            upload = self._documents.begin_upload(
                project_id=project_id,
                user_id=user_id,
                idempotency_key=f"{canary_id}:upload",
                request=BeginDocumentUploadRequest(
                    document_type=DocumentType.COMMERCIAL_LEASE,
                    filename="canary-lease.pdf",
                    content_type="application/pdf",
                    size_bytes=len(content),
                    sha256=sha256,
                ),
            )
            object_path = upload.object_path
            revision_id = upload.document_revision_id
            upload_http_status = self._transport.put(
                upload.upload_url,
                content=content,
                headers=upload.required_headers,
            )
            if upload_http_status not in {200, 201}:
                raise DocumentStorageCanaryError(
                    "SIGNED_UPLOAD_FAILED", {"http_status": upload_http_status}
                )
            completed = self._documents.complete_upload(
                project_id=project_id,
                user_id=user_id,
                document_revision_id=revision_id,
            )
            if completed.status != DocumentRevisionStatus.SCAN_PENDING:
                raise DocumentStorageCanaryError(
                    "UPLOAD_COMPLETION_INVALID", {"status": completed.status.value}
                )
            scanned = self._documents.record_scan_result(
                project_id=project_id,
                document_revision_id=revision_id,
                clean=True,
                threat_codes=[],
            )
            if scanned.status != DocumentRevisionStatus.READY_FOR_PARSING:
                raise DocumentStorageCanaryError(
                    "SCAN_RESULT_INVALID", {"status": scanned.status.value}
                )
            form = self._extraction.accept_parser_result(
                document_revision_id=revision_id,
                request=ParserResultRequest(
                    project_id=project_id,
                    document_id=upload.document_id,
                    parser_version="caffemate-canary-parser.v1",
                    blocks=[
                        ParserBlock.model_validate(
                            {
                                "block_id": "canary-block-1",
                                "text": "임대차 보증금 50,000,000원",
                                "anchor": {
                                    "document_revision_id": revision_id,
                                    "page_index": 0,
                                    "section_path": "임대 조건",
                                    "table_id": None,
                                    "row": None,
                                    "column": None,
                                    "bbox": None,
                                },
                            }
                        )
                    ],
                    prompt_injection_flags=[],
                ),
            )
            download = self._documents.get_download(
                project_id=project_id,
                user_id=user_id,
                document_revision_id=revision_id,
            )
            download_http_status, downloaded = self._transport.get(download.download_url)
            if download_http_status != 200 or downloaded != content:
                raise DocumentStorageCanaryError(
                    "SIGNED_DOWNLOAD_FAILED",
                    {
                        "http_status": download_http_status,
                        "content_matches": downloaded == content,
                    },
                )
            agent_statuses = self._load_agent_result_statuses(revision_id)
            if not agent_statuses:
                raise DocumentStorageCanaryError("DOCUMENT_AGENT_RESULT_MISSING")
            return DocumentStorageCanaryReport(
                status="verified",
                upload_status=completed.status.value,
                scan_status=scanned.status.value,
                extraction_status=DocumentRevisionStatus.EXTRACTION_READY.value,
                download_bytes=len(downloaded),
                agent_result_statuses=tuple(agent_statuses),
                extracted_field_count=sum(
                    field.extracted_value is not None for field in form.fields
                ),
            )
        finally:
            self._cleanup(
                project_id=project_id,
                user_id=user_id,
                revision_id=revision_id,
                object_path=object_path,
            )

    def _seed_project(self, *, project_id: str, user_id: str, active_case_id: str) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO venture_projects(project_id, owner_user_id, "
                    "current_state_version, workflow_generation, created_at) "
                    "VALUES (:project_id, :user_id, NULL, 1, :created_at)"
                ),
                {"project_id": project_id, "user_id": user_id, "created_at": now},
            )
            connection.execute(
                text(
                    "INSERT INTO venture_states(project_id, state_version, state_json, created_at) "
                    "VALUES (:project_id, 1, CAST(:state_json AS JSONB), :created_at)"
                ),
                {
                    "project_id": project_id,
                    "state_json": json.dumps({"active_case_id": active_case_id}),
                    "created_at": now,
                },
            )
            connection.execute(
                text(
                    "UPDATE venture_projects SET current_state_version=1 "
                    "WHERE project_id=:project_id"
                ),
                {"project_id": project_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO project_heads(
                        project_id, workflow_generation, state_version,
                        founder_snapshot_id, area_snapshot_id, evidence_snapshot_id,
                        policy_snapshot_id, index_generation_id, seed_registry_id, updated_at
                    ) VALUES (
                        :project_id, 1, 1, NULL, NULL, NULL,
                        :policy_snapshot_id, NULL, :seed_registry_id, :updated_at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "policy_snapshot_id": self._policy_snapshot_id,
                    "seed_registry_id": self._seed_registry_id,
                    "updated_at": now,
                },
            )

    def _load_agent_result_statuses(self, revision_id: str) -> list[str]:
        with self._engine.connect() as connection:
            results = connection.execute(
                text(
                    "SELECT agent_results_json FROM document_extraction_forms "
                    "WHERE document_revision_id=:revision_id"
                ),
                {"revision_id": revision_id},
            ).scalar_one()
        return [
            str(result["status"])
            for result in results
            if isinstance(result, dict) and isinstance(result.get("status"), str)
        ]

    def _cleanup(
        self,
        *,
        project_id: str,
        user_id: str,
        revision_id: str | None,
        object_path: str | None,
    ) -> None:
        if object_path is not None:
            try:
                self._storage.delete(object_path=object_path)
            except NotFound:
                pass
            except Exception as error:
                raise DocumentStorageCanaryError("CANARY_OBJECT_CLEANUP_FAILED") from error
        with self._engine.begin() as connection:
            if revision_id is not None:
                connection.execute(
                    text(
                        "DELETE FROM workflow_outbox WHERE aggregate_id=:revision_id "
                        "OR payload_json->>'project_id'=:project_id"
                    ),
                    {"revision_id": revision_id, "project_id": project_id},
                )
            connection.execute(
                text(
                    "DELETE FROM venture_projects "
                    "WHERE project_id=:project_id AND owner_user_id=:user_id"
                ),
                {"project_id": project_id, "user_id": user_id},
            )
        with self._engine.connect() as connection:
            remaining = connection.execute(
                text(
                    "SELECT COUNT(*) FROM venture_projects "
                    "WHERE project_id=:project_id OR owner_user_id=:user_id"
                ),
                {"project_id": project_id, "user_id": user_id},
            ).scalar_one()
        if remaining != 0:
            raise DocumentStorageCanaryError("CANARY_DATABASE_CLEANUP_FAILED")
