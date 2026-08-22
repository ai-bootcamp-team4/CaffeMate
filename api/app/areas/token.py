from collections.abc import Callable
from datetime import UTC, datetime

import jwt
from pydantic import Field

from app.domain.models import AreaIdentity, StrictModel


class AreaSelectionTokenError(ValueError):
    pass


class AreaSelectionClaims(StrictModel):
    iss: str
    aud: str
    venture_project_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=2, max_length=100)
    area: AreaIdentity
    iat: int
    exp: int


class AreaSelectionTokenSigner:
    def __init__(
        self,
        *,
        secret: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret.encode()) < 32:
            raise ValueError("Area selection signing secret must contain at least 32 bytes")
        self._secret = secret
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue(
        self,
        *,
        venture_project_id: str,
        query: str,
        area: AreaIdentity,
        ttl_seconds: int = 900,
    ) -> str:
        if not 60 <= ttl_seconds <= 1800:
            raise ValueError("Area selection token TTL must be between 60 and 1800 seconds")
        now = int(self._clock().timestamp())
        claims = AreaSelectionClaims(
            iss="caffemate-control-api",
            aud="caffemate-area-selection",
            venture_project_id=venture_project_id,
            query=query,
            area=area,
            iat=now,
            exp=now + ttl_seconds,
        )
        return jwt.encode(claims.model_dump(mode="json"), self._secret, algorithm="HS256")

    def verify(self, token: str) -> AreaSelectionClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience="caffemate-area-selection",
                issuer="caffemate-control-api",
                options={"verify_exp": False, "verify_iat": False},
            )
            claims = AreaSelectionClaims.model_validate(payload)
        except (jwt.InvalidTokenError, ValueError) as error:
            raise AreaSelectionTokenError("Area selection token is invalid or expired") from error
        now = int(self._clock().timestamp())
        if claims.exp <= now or claims.iat > now + 30 or claims.exp - claims.iat > 1800:
            raise AreaSelectionTokenError("Area selection token is invalid or expired")
        return claims
