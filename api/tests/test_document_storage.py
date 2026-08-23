from datetime import UTC, datetime, timedelta
from typing import Any

from app.documents.storage import GoogleCloudDocumentStorage


class FixedAccessTokens:
    def __init__(self) -> None:
        self.calls = 0

    def token(self) -> str:
        self.calls += 1
        return "access-token"


class RecordingBlob:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_signed_url(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "https://storage.invalid/signed"


class RecordingBucket:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.blob_value = RecordingBlob()

    def blob(self, object_path: str) -> RecordingBlob:
        self.paths.append(object_path)
        return self.blob_value


class RecordingClient:
    def __init__(self) -> None:
        self.bucket_names: list[str] = []
        self.bucket_value = RecordingBucket()

    def bucket(self, bucket_name: str) -> RecordingBucket:
        self.bucket_names.append(bucket_name)
        return self.bucket_value


def test_signed_urls_use_iam_sign_blob_credentials_from_runtime_identity() -> None:
    client = RecordingClient()
    tokens = FixedAccessTokens()
    storage = GoogleCloudDocumentStorage(
        "documents-bucket",
        signing_service_account_email="api@example.iam.gserviceaccount.com",
        client=client,  # type: ignore[arg-type]
        access_tokens=tokens,
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    upload = storage.sign_upload(
        object_path="projects/p/documents/d/source.pdf",
        content_type="application/pdf",
        sha256="a" * 64,
        expires_at=expires_at,
    )
    download = storage.sign_download(
        object_path="projects/p/documents/d/source.pdf",
        expires_at=expires_at,
    )

    assert upload == download == "https://storage.invalid/signed"
    assert client.bucket_names == ["documents-bucket"]
    assert client.bucket_value.paths == [
        "projects/p/documents/d/source.pdf",
        "projects/p/documents/d/source.pdf",
    ]
    assert tokens.calls == 2
    upload_call, download_call = client.bucket_value.blob_value.calls
    for call in (upload_call, download_call):
        assert call["service_account_email"] == "api@example.iam.gserviceaccount.com"
        assert call["access_token"] == "access-token"
        assert call["version"] == "v4"
    assert upload_call["method"] == "PUT"
    assert upload_call["content_type"] == "application/pdf"
    assert upload_call["headers"] == {"x-goog-meta-caffemate-sha256": "a" * 64}
    assert download_call["method"] == "GET"


def test_document_storage_requires_explicit_signing_identity() -> None:
    client = RecordingClient()

    try:
        GoogleCloudDocumentStorage(
            "documents-bucket",
            signing_service_account_email="",
            client=client,  # type: ignore[arg-type]
        )
    except ValueError as error:
        assert str(error) == "Document signing service account email is required"
    else:
        raise AssertionError("missing signer must fail closed")
