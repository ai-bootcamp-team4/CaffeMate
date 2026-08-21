import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from google.cloud import storage  # type: ignore[attr-defined]


@dataclass(frozen=True)
class StoredObject:
    content_type: str
    size_bytes: int
    sha256: str


class DocumentStorage(Protocol):
    def sign_upload(
        self,
        *,
        object_path: str,
        content_type: str,
        sha256: str,
        expires_at: datetime,
    ) -> str: ...

    def inspect(self, *, object_path: str) -> StoredObject | None: ...

    def sign_download(self, *, object_path: str, expires_at: datetime) -> str: ...


class GoogleCloudDocumentStorage:
    def __init__(self, bucket_name: str, *, client: storage.Client | None = None) -> None:
        self._bucket = (client or storage.Client()).bucket(bucket_name)

    def sign_upload(
        self,
        *,
        object_path: str,
        content_type: str,
        sha256: str,
        expires_at: datetime,
    ) -> str:
        return cast(str, self._bucket.blob(object_path).generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="PUT",
            content_type=content_type,
            headers={"x-goog-meta-caffemate-sha256": sha256},
        ))

    def inspect(self, *, object_path: str) -> StoredObject | None:
        blob = self._bucket.get_blob(object_path)
        if blob is None:
            return None
        content = blob.download_as_bytes()
        return StoredObject(
            content_type=self._detect_content_type(content),
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    @staticmethod
    def _detect_content_type(content: bytes) -> str:
        if content.startswith(b"%PDF-"):
            return "application/pdf"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    if "[Content_Types].xml" in archive.namelist() and any(
                        name.startswith("word/") for name in archive.namelist()
                    ):
                        return (
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        )
            except zipfile.BadZipFile:
                pass
        return "application/octet-stream"

    def sign_download(self, *, object_path: str, expires_at: datetime) -> str:
        return cast(str, self._bucket.blob(object_path).generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="GET",
        ))


def short_expiry(minutes: int = 10) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
