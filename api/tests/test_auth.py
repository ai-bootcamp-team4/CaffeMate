import pytest
from firebase_admin.exceptions import FirebaseError

from app.auth import FirebaseIdentityVerifier, GoogleServiceIdentityVerifier
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


def test_google_service_identity_requires_exact_verified_worker_email() -> None:
    verifier = GoogleServiceIdentityVerifier(
        audience="https://control-api.example",
        allowed_service_account_email="worker@example.iam.gserviceaccount.com",
        decode_token=lambda token, audience: {
            "email": "worker@example.iam.gserviceaccount.com",
            "email_verified": True,
            "token_seen": token,
            "audience_seen": audience,
        },
    )

    assert verifier.verify("worker-token") == "worker@example.iam.gserviceaccount.com"


@pytest.mark.parametrize(
    "claims",
    [
        {"email": "attacker@example.iam.gserviceaccount.com", "email_verified": True},
        {"email": "worker@example.iam.gserviceaccount.com", "email_verified": False},
        {},
    ],
)
def test_google_service_identity_rejects_other_or_unverified_callers(
    claims: dict[str, object],
) -> None:
    verifier = GoogleServiceIdentityVerifier(
        audience="https://control-api.example",
        allowed_service_account_email="worker@example.iam.gserviceaccount.com",
        decode_token=lambda _token, _audience: claims,
    )

    with pytest.raises(UnauthenticatedError):
        verifier.verify("worker-token")
