from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
from app.workflows.models import StageLease
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from worker.errors import StageExecutionError


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
        timeout = remaining - 2
        try:
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
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise StageExecutionError(
                "CONTROL_API_TRANSPORT_FAILED", retryable=True
            ) from error
        if response.is_error:
            raise self._stage_error(response)
        try:
            body = response.json()
        except ValueError as error:
            raise StageExecutionError(
                "CONTROL_API_RESPONSE_INVALID", retryable=False
            ) from error
        if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
            raise StageExecutionError("CONTROL_API_RESPONSE_INVALID", retryable=False)
        return cast(dict[str, object], body["result"])

    @staticmethod
    def _stage_error(response: httpx.Response) -> StageExecutionError:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            code = body.get("code")
            retryable = body.get("retryable")
            if (
                isinstance(code, str)
                and code
                and len(code) <= 64
                and isinstance(retryable, bool)
            ):
                return StageExecutionError(code, retryable=retryable)
        status = response.status_code
        terminal_code = {
            400: "CONTROL_API_REQUEST_INVALID",
            401: "CONTROL_API_UNAUTHENTICATED",
            403: "CONTROL_API_FORBIDDEN",
            409: "STAGE_LEASE_REJECTED",
            422: "CONTRACT_VALIDATION_FAILED",
        }.get(status, "CONTROL_API_HTTP_TERMINAL")
        return StageExecutionError(
            (
                "CONTROL_API_TRANSPORT_FAILED"
                if status in {408, 429} or 500 <= status <= 599
                else terminal_code
            ),
            retryable=status in {408, 429} or 500 <= status <= 599,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
