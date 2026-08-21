from typing import Protocol

from app.domain.errors import AuthenticationUnavailableError


class IdentityVerifier(Protocol):
    def verify(self, bearer_token: str) -> str: ...


class UnconfiguredIdentityVerifier:
    def verify(self, bearer_token: str) -> str:
        del bearer_token
        raise AuthenticationUnavailableError(
            "Identity Platform verifier is not configured for this deployment"
        )
