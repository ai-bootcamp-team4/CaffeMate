from collections.abc import Callable, Mapping
from typing import Protocol, cast

from firebase_admin import App, auth, get_app, initialize_app
from firebase_admin.exceptions import FirebaseError
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from app.domain.errors import AuthenticationUnavailableError, UnauthenticatedError


class IdentityVerifier(Protocol):
    def verify(self, bearer_token: str) -> str: ...


class UnconfiguredIdentityVerifier:
    def verify(self, bearer_token: str) -> str:
        del bearer_token
        raise AuthenticationUnavailableError(
            "Identity Platform verifier is not configured for this deployment"
        )


class FirebaseIdentityVerifier:
    """Verify Identity Platform client ID tokens and return their authoritative uid."""

    def __init__(
        self,
        *,
        project_id: str,
        decode_token: Callable[[str], Mapping[str, object]] | None = None,
    ) -> None:
        self._app: App | None = None
        self._decode_token: Callable[[str], Mapping[str, object]]
        if decode_token is None:
            app_name = f"caffemate-{project_id}"
            try:
                self._app = get_app(app_name)
            except ValueError:
                self._app = initialize_app(
                    options={"projectId": project_id},
                    name=app_name,
                )
            self._decode_token = self._decode_with_admin_sdk
        else:
            self._decode_token = decode_token

    def verify(self, bearer_token: str) -> str:
        if not bearer_token:
            raise UnauthenticatedError("ID token is empty")
        try:
            claims = self._decode_token(bearer_token)
        except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError):
            raise UnauthenticatedError("ID token is invalid") from None
        except (auth.UserDisabledError, ValueError):
            raise UnauthenticatedError("Identity Platform user cannot authenticate") from None
        except FirebaseError:
            raise AuthenticationUnavailableError("Identity Platform verification failed") from None

        uid = claims.get("uid") or claims.get("sub")
        if not isinstance(uid, str) or not uid:
            raise UnauthenticatedError("ID token has no uid")
        return uid

    def _decode_with_admin_sdk(self, bearer_token: str) -> Mapping[str, object]:
        assert self._app is not None
        return cast(
            Mapping[str, object],
            auth.verify_id_token(bearer_token, app=self._app, check_revoked=True),
        )


class GoogleServiceIdentityVerifier:
    """Verify a Cloud Run caller ID token and allow exactly one service account."""

    def __init__(
        self,
        *,
        audience: str,
        allowed_service_account_email: str,
        decode_token: Callable[[str, str], Mapping[str, object]] | None = None,
    ) -> None:
        if not audience or not allowed_service_account_email:
            raise ValueError("Internal audience and worker service account email are required")
        self._audience = audience
        self._allowed_email = allowed_service_account_email
        self._decode_token = decode_token or self._decode_with_google_auth

    def verify(self, bearer_token: str) -> str:
        if not bearer_token:
            raise UnauthenticatedError("Service identity token is empty")
        try:
            claims = self._decode_token(bearer_token, self._audience)
        except ValueError:
            raise UnauthenticatedError("Service identity token is invalid") from None
        except GoogleAuthError:
            raise AuthenticationUnavailableError("Service identity verification failed") from None

        email = claims.get("email")
        verified = claims.get("email_verified")
        if email != self._allowed_email or verified is not True:
            raise UnauthenticatedError("Caller service identity is not allowed")
        return self._allowed_email

    @staticmethod
    def _decode_with_google_auth(token: str, audience: str) -> Mapping[str, object]:
        return cast(
            Mapping[str, object],
            id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                token,
                Request(),
                audience=audience,
            ),
        )
