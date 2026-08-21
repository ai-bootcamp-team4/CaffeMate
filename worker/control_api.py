from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
from app.workflows.models import StageLease
from google.auth.transport.requests import Request
from google.oauth2 import id_token


class IdentityTokenProvider(Protocol):
    def token_for(self, audience: str) -> str: ...


class GoogleIdentityTokenProvider:
    def __init__(self, fetch: Callable[[Request, str], str | None] | None = None) -> None:
        self._fetch = fetch or id_token.fetch_id_token

    def token_for(self, audience: str) -> str:
        token = self._fetch(Request(), audience)
        if not token:
            raise RuntimeError("Google service identity token was not returned")
        return token


class ControlApiStageProcessor:
    def __init__(
        self,
        *,
        base_url: str,
        audience: str,
        token_provider: IdentityTokenProvider,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not base_url or not audience:
            raise ValueError("Control API URL and audience are required")
        self._base_url = base_url.rstrip("/")
        self._audience = audience
        self._token_provider = token_provider
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self._now = now or (lambda: datetime.now(UTC))

    def process(self, lease: StageLease) -> dict[str, object]:
        remaining = (lease.lease_expires_at - self._now()).total_seconds()
        if remaining <= 2:
            raise TimeoutError("Stage lease has insufficient time for Control API execution")
        timeout = min(40.0, remaining - 2)
        token = self._token_provider.token_for(self._audience)
        response = self._client.post(
            (
                f"{self._base_url}/internal/v1/workflows/{lease.workflow_run_id}"
                f"/stages/{lease.stage_run_id}:execute"
            ),
            headers={"Authorization": f"Bearer {token}"},
            json={"lease": lease.model_dump(mode="json")},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
            raise TypeError("Control API stage response is invalid")
        return cast(dict[str, object], body["result"])

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
