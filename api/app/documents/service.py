import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from app.documents.models import (
    BeginDocumentUploadRequest,
    DocumentDownload,
    DocumentRevision,
    DocumentRevisionStatus,
    SignedUpload,
)
from app.documents.storage import DocumentStorage
from app.domain.errors import (
    DocumentNotFoundError,
    DocumentPreconditionError,
    IdempotencyKeyReusedError,
    PersistenceUnavailableError,
    ProjectNotFoundError,
)

ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


class DocumentService:
    def __init__(
        self,
        engine: Engine,
        storage: DocumentStorage,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))

    def begin_upload(
        self,
        *,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        request: BeginDocumentUploadRequest,
    ) -> SignedUpload:
        if request.content_type not in ALLOWED_CONTENT_TYPES:
            raise DocumentPreconditionError("Document content type is not allowed")
        digest_value = {
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        request_digest = hashlib.sha256(
            rfc8785.dumps(cast(Any, digest_value))
        ).digest()
        with self._engine.begin() as connection:
            project = connection.execute(
                text(
                    """
                    SELECT s.state_json FROM venture_projects p
                    JOIN venture_states s
                      ON s.project_id=p.project_id
                     AND s.state_version=p.current_state_version
                    WHERE p.project_id=:project_id AND p.owner_user_id=:user_id
                    FOR UPDATE OF p
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).mappings().one_or_none()
            if project is None:
                raise ProjectNotFoundError("Project does not exist")
            state_json = project["state_json"]
            if isinstance(state_json, str):
                state_json = json.loads(state_json)
            active_case_id = (
                state_json.get("active_case_id") if isinstance(state_json, dict) else None
            )
            if not isinstance(active_case_id, str) or not active_case_id:
                raise DocumentPreconditionError(
                    "A current candidate must be selected before document upload"
                )
            existing = connection.execute(
                text(
                    """
                    SELECT r.*, d.document_type FROM document_revisions r
                    JOIN documents d ON d.document_id=r.document_id
                    WHERE r.project_id=:project_id AND r.idempotency_key=:idempotency_key
                    """
                ),
                {"project_id": project_id, "idempotency_key": idempotency_key},
            ).mappings().one_or_none()
            expires_at = self._now() + timedelta(minutes=10)
            if existing is not None:
                if bytes(existing["request_digest"]) != request_digest:
                    raise IdempotencyKeyReusedError(
                        "Idempotency key was used with another document upload"
                    )
                if existing["status"] != DocumentRevisionStatus.UPLOAD_PENDING.value:
                    raise DocumentPreconditionError("Document upload is already completed")
                return self._signed_upload(existing, expires_at=expires_at)
            document_id = request.document_id or self._new_id()
            revision_id = self._new_id()
            revision_number = 1
            if request.document_id is not None:
                document = connection.execute(
                    text(
                        """
                        SELECT * FROM documents
                        WHERE document_id=:document_id AND project_id=:project_id
                          AND owner_user_id=:user_id AND status='ACTIVE'
                        FOR UPDATE
                        """
                    ),
                    {
                        "document_id": document_id,
                        "project_id": project_id,
                        "user_id": user_id,
                    },
                ).mappings().one_or_none()
                if document is None:
                    raise DocumentNotFoundError("Document does not exist")
                if (
                    document["active_case_id"] != active_case_id
                    or document["document_type"] != request.document_type.value
                ):
                    raise DocumentPreconditionError(
                        "Document revision crossed its case or document type"
                    )
                revision_number = int(document["current_revision_number"]) + 1
            object_path = (
                f"projects/{project_id}/documents/{document_id}/revisions/"
                f"{revision_id}/source{ALLOWED_CONTENT_TYPES[request.content_type]}"
            )
            occurred_at = self._now()
            if request.document_id is None:
                connection.execute(
                    text(
                        """
                        INSERT INTO documents(
                            document_id, project_id, owner_user_id, active_case_id,
                            document_type, status, current_revision_number, created_at, updated_at
                        ) VALUES (
                            :document_id, :project_id, :owner_user_id, :active_case_id,
                            :document_type, 'ACTIVE', 1, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "document_id": document_id,
                        "project_id": project_id,
                        "owner_user_id": user_id,
                        "active_case_id": active_case_id,
                        "document_type": request.document_type.value,
                        "created_at": occurred_at,
                        "updated_at": occurred_at,
                    },
                )
            else:
                connection.execute(
                    text(
                        """
                        UPDATE documents
                        SET current_revision_number=:revision_number, updated_at=:updated_at
                        WHERE document_id=:document_id
                        """
                    ),
                    {
                        "revision_number": revision_number,
                        "updated_at": occurred_at,
                        "document_id": document_id,
                    },
                )
            connection.execute(
                text(
                    """
                    INSERT INTO document_revisions(
                        document_revision_id, document_id, project_id, revision_number,
                        object_path, original_filename, declared_content_type,
                        declared_size_bytes, declared_sha256, status, idempotency_key,
                        request_digest, created_at, updated_at
                    ) VALUES (
                        :revision_id, :document_id, :project_id, :revision_number, :object_path,
                        :filename, :content_type, :size_bytes, :sha256, 'UPLOAD_PENDING',
                        :idempotency_key, :request_digest, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "revision_id": revision_id,
                    "document_id": document_id,
                    "project_id": project_id,
                    "revision_number": revision_number,
                    "object_path": object_path,
                    "filename": Path(request.filename).name,
                    "content_type": request.content_type,
                    "size_bytes": request.size_bytes,
                    "sha256": request.sha256,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                },
            )
            row = connection.execute(
                text(
                    """
                    SELECT r.*, d.document_type FROM document_revisions r
                    JOIN documents d ON d.document_id=r.document_id
                    WHERE r.document_revision_id=:revision_id
                    """
                ),
                {"revision_id": revision_id},
            ).mappings().one()
            return self._signed_upload(row, expires_at=expires_at)

    def complete_upload(
        self,
        *,
        project_id: str,
        user_id: str,
        document_revision_id: str,
        enqueue_processing: bool = True,
    ) -> DocumentRevision:
        current = self._load_owned(
            project_id=project_id,
            user_id=user_id,
            document_revision_id=document_revision_id,
        )
        if current["status"] != DocumentRevisionStatus.UPLOAD_PENDING.value:
            return self._revision(current)
        observed = self._storage.inspect(object_path=current["object_path"])
        failure_codes: list[str] = []
        if observed is None:
            failure_codes.append("OBJECT_NOT_FOUND")
        else:
            if observed.content_type != current["declared_content_type"]:
                failure_codes.append("MIME_MISMATCH")
            if observed.size_bytes != current["declared_size_bytes"]:
                failure_codes.append("SIZE_MISMATCH")
            if observed.sha256 != current["declared_sha256"]:
                failure_codes.append("CHECKSUM_MISMATCH")
        final_status = (
            DocumentRevisionStatus.QUARANTINED
            if failure_codes
            else DocumentRevisionStatus.SCAN_PENDING
        )
        occurred_at = self._now()
        with self._engine.begin() as connection:
            row = self._load_owned_connection(
                connection,
                project_id=project_id,
                user_id=user_id,
                document_revision_id=document_revision_id,
                for_update=True,
            )
            if row["status"] != DocumentRevisionStatus.UPLOAD_PENDING.value:
                return self._revision(row)
            connection.execute(
                text(
                    """
                    UPDATE document_revisions
                    SET observed_content_type=:content_type,
                        observed_size_bytes=:size_bytes, observed_sha256=:sha256,
                        status=:status, failure_codes=CAST(:failure_codes AS JSONB),
                        updated_at=:updated_at, completed_at=:completed_at
                    WHERE document_revision_id=:revision_id
                    """
                ),
                {
                    "content_type": observed.content_type if observed else None,
                    "size_bytes": observed.size_bytes if observed else None,
                    "sha256": observed.sha256 if observed else None,
                    "status": final_status.value,
                    "failure_codes": json.dumps(failure_codes),
                    "updated_at": occurred_at,
                    "completed_at": occurred_at,
                    "revision_id": document_revision_id,
                },
            )
            if final_status == DocumentRevisionStatus.SCAN_PENDING and enqueue_processing:
                self._insert_outbox(
                    connection,
                    topic="DOCUMENT_SCAN_REQUESTED",
                    aggregate_id=document_revision_id,
                    payload={
                        "project_id": project_id,
                        "document_id": row["document_id"],
                        "document_revision_id": document_revision_id,
                        "object_path": row["object_path"],
                    },
                    occurred_at=occurred_at,
                )
            return self._revision(
                self._load_owned_connection(
                    connection,
                    project_id=project_id,
                    user_id=user_id,
                    document_revision_id=document_revision_id,
                )
            )

    def record_scan_result(
        self,
        *,
        project_id: str,
        document_revision_id: str,
        clean: bool,
        threat_codes: list[str],
        enqueue_parsing: bool = True,
    ) -> DocumentRevision:
        occurred_at = self._now()
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT r.*, d.document_type, d.owner_user_id
                    FROM document_revisions r JOIN documents d ON d.document_id=r.document_id
                    WHERE r.project_id=:project_id
                      AND r.document_revision_id=:revision_id
                    FOR UPDATE OF r
                    """
                ),
                {"project_id": project_id, "revision_id": document_revision_id},
            ).mappings().one_or_none()
            if row is None:
                raise DocumentNotFoundError("Document revision does not exist")
            if row["status"] != DocumentRevisionStatus.SCAN_PENDING.value:
                return self._revision(row)
            status = (
                DocumentRevisionStatus.READY_FOR_PARSING
                if clean and not threat_codes
                else DocumentRevisionStatus.QUARANTINED
            )
            codes = [] if status == DocumentRevisionStatus.READY_FOR_PARSING else (
                threat_codes or ["MALWARE_DETECTED"]
            )
            connection.execute(
                text(
                    """
                    UPDATE document_revisions
                    SET status=:status, failure_codes=CAST(:failure_codes AS JSONB),
                        updated_at=:updated_at
                    WHERE document_revision_id=:revision_id
                    """
                ),
                {
                    "status": status.value,
                    "failure_codes": json.dumps(sorted(set(codes))),
                    "updated_at": occurred_at,
                    "revision_id": document_revision_id,
                },
            )
            if status == DocumentRevisionStatus.READY_FOR_PARSING and enqueue_parsing:
                self._insert_outbox(
                    connection,
                    topic="DOCUMENT_PARSE_REQUESTED",
                    aggregate_id=document_revision_id,
                    payload={
                        "project_id": project_id,
                        "document_id": row["document_id"],
                        "document_revision_id": document_revision_id,
                        "document_type": row["document_type"],
                        "object_path": row["object_path"],
                        "sha256": row["declared_sha256"],
                    },
                    occurred_at=occurred_at,
                )
            updated = dict(row)
            updated.update(
                status=status.value,
                failure_codes=sorted(set(codes)),
                updated_at=occurred_at,
            )
            return self._revision(cast(RowMapping, updated))

    def mark_extraction_failed(
        self,
        *,
        project_id: str,
        document_revision_id: str,
        failure_code: str,
    ) -> DocumentRevision:
        occurred_at = self._now()
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT r.*, d.document_type, d.owner_user_id
                    FROM document_revisions r JOIN documents d ON d.document_id=r.document_id
                    WHERE r.project_id=:project_id
                      AND r.document_revision_id=:revision_id
                    FOR UPDATE OF r
                    """
                ),
                {"project_id": project_id, "revision_id": document_revision_id},
            ).mappings().one_or_none()
            if row is None:
                raise DocumentNotFoundError("Document revision does not exist")
            if row["status"] in {
                DocumentRevisionStatus.EXTRACTION_READY.value,
                DocumentRevisionStatus.APPLIED.value,
                DocumentRevisionStatus.QUARANTINED.value,
                DocumentRevisionStatus.DELETED.value,
            }:
                return self._revision(row)
            connection.execute(
                text(
                    """
                    UPDATE document_revisions
                    SET status='EXTRACTION_FAILED', failure_codes=CAST(:codes AS JSONB),
                        updated_at=:updated_at
                    WHERE document_revision_id=:revision_id
                    """
                ),
                {
                    "codes": json.dumps([failure_code]),
                    "updated_at": occurred_at,
                    "revision_id": document_revision_id,
                },
            )
            updated = dict(row)
            updated.update(
                status=DocumentRevisionStatus.EXTRACTION_FAILED.value,
                failure_codes=[failure_code],
                updated_at=occurred_at,
            )
            return self._revision(cast(RowMapping, updated))

    def get_download(
        self,
        *,
        project_id: str,
        user_id: str,
        document_revision_id: str,
    ) -> DocumentDownload:
        row = self._load_owned(
            project_id=project_id,
            user_id=user_id,
            document_revision_id=document_revision_id,
        )
        if row["status"] in {
            DocumentRevisionStatus.QUARANTINED.value,
            DocumentRevisionStatus.DELETED.value,
        }:
            raise DocumentPreconditionError("Quarantined or deleted document cannot be downloaded")
        expires_at = self._now() + timedelta(minutes=5)
        return DocumentDownload(
            document_revision_id=document_revision_id,
            download_url=self._storage.sign_download(
                object_path=row["object_path"], expires_at=expires_at
            ),
            expires_at=expires_at,
        )

    def get_revision(
        self,
        *,
        project_id: str,
        user_id: str,
        document_revision_id: str,
    ) -> DocumentRevision:
        return self._revision(
            self._load_owned(
                project_id=project_id,
                user_id=user_id,
                document_revision_id=document_revision_id,
            )
        )

    def _signed_upload(self, row: RowMapping, *, expires_at: datetime) -> SignedUpload:
        content_type = row["declared_content_type"]
        sha256 = row["declared_sha256"]
        return SignedUpload(
            document_id=row["document_id"],
            document_revision_id=row["document_revision_id"],
            revision_number=row["revision_number"],
            object_path=row["object_path"],
            upload_url=self._storage.sign_upload(
                object_path=row["object_path"],
                content_type=content_type,
                sha256=sha256,
                expires_at=expires_at,
            ),
            required_headers={
                "Content-Type": content_type,
                "x-goog-meta-caffemate-sha256": sha256,
            },
            expires_at=expires_at,
            status=row["status"],
        )

    def _load_owned(
        self, *, project_id: str, user_id: str, document_revision_id: str
    ) -> RowMapping:
        with self._engine.connect() as connection:
            return self._load_owned_connection(
                connection,
                project_id=project_id,
                user_id=user_id,
                document_revision_id=document_revision_id,
            )

    @staticmethod
    def _load_owned_connection(
        connection: Any,
        *,
        project_id: str,
        user_id: str,
        document_revision_id: str,
        for_update: bool = False,
    ) -> RowMapping:
        suffix = " FOR UPDATE OF r" if for_update else ""
        row = connection.execute(
            text(
                """
                SELECT r.*, d.document_type, d.owner_user_id
                FROM document_revisions r JOIN documents d ON d.document_id=r.document_id
                WHERE r.project_id=:project_id AND r.document_revision_id=:revision_id
                  AND d.owner_user_id=:user_id
                """
                + suffix
            ),
            {
                "project_id": project_id,
                "revision_id": document_revision_id,
                "user_id": user_id,
            },
        ).mappings().one_or_none()
        if row is None:
            raise DocumentNotFoundError("Document revision does not exist")
        return cast(RowMapping, row)

    @staticmethod
    def _revision(row: RowMapping) -> DocumentRevision:
        return DocumentRevision(
            document_id=row["document_id"],
            document_revision_id=row["document_revision_id"],
            project_id=row["project_id"],
            revision_number=row["revision_number"],
            document_type=row["document_type"],
            original_filename=row["original_filename"],
            content_type=row["observed_content_type"] or row["declared_content_type"],
            size_bytes=row["observed_size_bytes"] or row["declared_size_bytes"],
            sha256=row["observed_sha256"] or row["declared_sha256"],
            status=row["status"],
            failure_codes=list(row["failure_codes"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _insert_outbox(
        connection: Any,
        *,
        topic: str,
        aggregate_id: str,
        payload: dict[str, str],
        occurred_at: datetime,
    ) -> None:
        payload_bytes = rfc8785.dumps(payload)
        connection.execute(
            text(
                """
                INSERT INTO workflow_outbox(
                    topic, aggregate_id, payload_json, payload_digest,
                    available_at, created_at
                ) VALUES (
                    :topic, :aggregate_id, CAST(:payload_json AS JSONB),
                    :payload_digest, :available_at, :created_at
                )
                ON CONFLICT (topic, aggregate_id, payload_digest) DO NOTHING
                """
            ),
            {
                "topic": topic,
                "aggregate_id": aggregate_id,
                "payload_json": payload_bytes.decode(),
                "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
                "available_at": occurred_at,
                "created_at": occurred_at,
            },
        )


class UnavailableDocumentService:
    def begin_upload(self, **_: Any) -> SignedUpload:
        raise PersistenceUnavailableError("Document storage is unavailable")

    def complete_upload(self, **_: Any) -> DocumentRevision:
        raise PersistenceUnavailableError("Document storage is unavailable")

    def record_scan_result(self, **_: Any) -> DocumentRevision:
        raise PersistenceUnavailableError("Document storage is unavailable")

    def mark_extraction_failed(self, **_: Any) -> DocumentRevision:
        raise PersistenceUnavailableError("Document storage is unavailable")

    def get_download(self, **_: Any) -> DocumentDownload:
        raise PersistenceUnavailableError("Document storage is unavailable")

    def get_revision(self, **_: Any) -> DocumentRevision:
        raise PersistenceUnavailableError("Document storage is unavailable")
