from collections.abc import Callable, Mapping
from typing import Protocol, cast

from firebase_admin import App, auth, get_app, initialize_app
from firebase_admin.exceptions import FirebaseError

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
