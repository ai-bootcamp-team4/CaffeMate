import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import jwt
import rfc8785
from pydantic import Field

from app.domain.models import StrictModel
from app.workflows.models import HeadFence


class ScopeTokenError(ValueError):
    pass


class ScopeClaims(StrictModel):
    iss: str
    aud: str
    venture_project_id: str = Field(min_length=1, max_length=128)
    workflow_run_id: str = Field(min_length=1, max_length=128)
    full_head_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    jti: str = Field(min_length=1, max_length=128)
    iat: int
    exp: int


class ScopeTokenSigner:
    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        clock: Callable[[], datetime] | None = None,
        jti_factory: Callable[[], str] | None = None,
    ) -> None:
        if len(secret.encode()) < 32:
            raise ValueError("MCP scope signing secret must contain at least 32 bytes")
        if not issuer or not audience:
            raise ValueError("MCP scope issuer and audience are required")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._clock = clock or (lambda: datetime.now(UTC))
        self._jti_factory = jti_factory or (lambda: str(uuid4()))

    def issue(
        self,
        *,
        venture_project_id: str,
        workflow_run_id: str,
        head: HeadFence,
        ttl_seconds: int = 300,
    ) -> str:
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("MCP scope token TTL must be between 1 and 300 seconds")
        now = int(self._clock().timestamp())
        claims = ScopeClaims(
            iss=self._issuer,
            aud=self._audience,
            venture_project_id=venture_project_id,
            workflow_run_id=workflow_run_id,
            full_head_digest=digest_head(head),
            jti=self._jti_factory(),
            iat=now,
            exp=now + ttl_seconds,
        )
        return jwt.encode(claims.model_dump(), self._secret, algorithm="HS256")

    def verify(self, token: str) -> ScopeClaims:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"verify_exp": False, "verify_iat": False},
            )
            claims = ScopeClaims.model_validate(payload)
        except (jwt.InvalidTokenError, ValueError) as error:
            raise ScopeTokenError("Invalid MCP scope token") from error
        now = int(self._clock().timestamp())
        if claims.exp <= now:
            raise ScopeTokenError("Expired MCP scope token")
        if claims.iat > now + 30:
            raise ScopeTokenError("Invalid MCP scope token issued-at time")
        if claims.exp - claims.iat > 300 or claims.exp <= claims.iat:
            raise ScopeTokenError("Invalid MCP scope token lifetime")
        return claims


def digest_head(head: HeadFence) -> str:
    canonical = rfc8785.dumps(head.model_dump(mode="json"))
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
