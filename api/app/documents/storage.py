import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

import google.auth
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
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

    def read(self, *, object_path: str) -> bytes: ...

    def sign_download(self, *, object_path: str, expires_at: datetime) -> str: ...


class AccessTokenProvider(Protocol):
    def token(self) -> str: ...


class GoogleAccessTokenProvider:
    def __init__(self, credentials: Credentials | None = None) -> None:
        self._credentials = credentials or google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )[0]

    def token(self) -> str:
        if not self._credentials.valid or not self._credentials.token:
            self._credentials.refresh(Request())  # type: ignore[no-untyped-call]
        token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise RuntimeError("Document signed URL access token is unavailable")
        return token


class GoogleCloudDocumentStorage:
    def __init__(
        self,
        bucket_name: str,
        *,
        signing_service_account_email: str,
        client: storage.Client | None = None,
        access_tokens: AccessTokenProvider | None = None,
    ) -> None:
        if not signing_service_account_email:
            raise ValueError("Document signing service account email is required")
        self._bucket = (client or storage.Client()).bucket(bucket_name)
        self._signing_service_account_email = signing_service_account_email
        self._access_tokens = access_tokens or GoogleAccessTokenProvider()

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
            service_account_email=self._signing_service_account_email,
            access_token=self._access_tokens.token(),
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

    def read(self, *, object_path: str) -> bytes:
        blob = self._bucket.get_blob(object_path)
        if blob is None:
            raise FileNotFoundError("Document object does not exist")
        return cast(bytes, blob.download_as_bytes())

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
            service_account_email=self._signing_service_account_email,
            access_token=self._access_tokens.token(),
        ))

    def delete(self, *, object_path: str) -> None:
        self._bucket.blob(object_path).delete()


def short_expiry(minutes: int = 10) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
