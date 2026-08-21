import pytest
from firebase_admin.exceptions import FirebaseError

from app.auth import FirebaseIdentityVerifier
from app.domain.errors import AuthenticationUnavailableError, UnauthenticatedError


def test_identity_verifier_returns_authoritative_uid() -> None:
    verifier = FirebaseIdentityVerifier(
        project_id="test-project",
        decode_token=lambda _token: {"uid": "founder-123"},
    )

    assert verifier.verify("signed-token") == "founder-123"


@pytest.mark.parametrize("claims", [{}, {"uid": ""}, {"uid": 123}])
def test_identity_verifier_rejects_token_without_string_uid(claims: dict[str, object]) -> None:
    verifier = FirebaseIdentityVerifier(
        project_id="test-project",
        decode_token=lambda _token: claims,
    )

    with pytest.raises(UnauthenticatedError):
        verifier.verify("signed-token")


def test_identity_verifier_maps_invalid_token_to_unauthenticated() -> None:
    def reject(_token: str) -> dict[str, object]:
        raise ValueError("malformed")

    verifier = FirebaseIdentityVerifier(project_id="test-project", decode_token=reject)

    with pytest.raises(UnauthenticatedError):
        verifier.verify("malformed-token")


def test_identity_verifier_maps_provider_failure_to_unavailable() -> None:
    def fail(_token: str) -> dict[str, object]:
        raise FirebaseError("UNAVAILABLE", "certificate endpoint failed")

    verifier = FirebaseIdentityVerifier(project_id="test-project", decode_token=fail)

    with pytest.raises(AuthenticationUnavailableError):
        verifier.verify("signed-token")
