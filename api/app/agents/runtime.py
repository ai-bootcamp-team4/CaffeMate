import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, TypeVar
from uuid import uuid4

import google.auth
import httpx
import rfc8785
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from sqlalchemy import Engine, text

from app.agents.task_factory import compute_agent_input_digest
from app.contracts.schema_registry import AgentContractValidator, ContractRegistry
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
STREAM_CLEANUP_RESERVE_SECONDS = 2.0
T = TypeVar("T")


class AccessTokenProvider(Protocol):
    def token(self) -> str: ...


class AgentCleanupSink(Protocol):
    def enqueue_session_delete(
        self,
        *,
        runtime_resource: str,
        user_id: str,
        session_id: str,
    ) -> None: ...


class GoogleAccessTokenProvider:
    def __init__(self, credentials: Credentials | None = None) -> None:
        if credentials is None:
            credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        self._credentials = credentials

    def token(self) -> str:
        if not self._credentials.valid:
            credentials: Any = self._credentials
            credentials.refresh(Request())
        token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise AgentRuntimeError("RUNTIME_AUTH_TOKEN_MISSING")
        return token


class AgentRuntimeError(ExternalExecutionUnavailableError):
    def __init__(self, runtime_code: str) -> None:
        super().__init__(runtime_code)
        self.runtime_code = runtime_code


def verify_agent_runtime_iam(
    *,
    gcp_project_id: str,
    resource_id: str,
    access_tokens: AccessTokenProvider,
    location: str = "asia-northeast3",
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    if location != "asia-northeast3":
        raise ValueError("Agent Runtime location must be asia-northeast3")
    if not gcp_project_id or not resource_id:
        raise ValueError("Agent Runtime project and resource id are required")
    resource = (
        f"projects/{gcp_project_id}/locations/{location}/reasoningEngines/{resource_id}"
    )
    required = "aiplatform.reasoningEngines.query"
    prohibited = {
        "aiplatform.reasoningEngines.update",
        "aiplatform.reasoningEngines.delete",
    }
    try:
        with httpx.Client(timeout=30.0, transport=transport) as client:
            response = client.post(
                f"https://{location}-aiplatform.googleapis.com/v1/"
                f"{resource}:testIamPermissions",
                headers={"Authorization": f"Bearer {access_tokens.token()}"},
                json={"permissions": [required, *sorted(prohibited)]},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise AgentRuntimeError("RUNTIME_IAM_VERIFICATION_FAILED") from error
    granted = payload.get("permissions") if isinstance(payload, dict) else None
    if not isinstance(granted, list) or not all(
        isinstance(permission, str) for permission in granted
    ):
        raise AgentRuntimeError("RUNTIME_IAM_VERIFICATION_INVALID")
    granted_set = set(granted)
    if required not in granted_set:
        raise AgentRuntimeError("RUNTIME_QUERY_PERMISSION_MISSING")
    unexpected = sorted(granted_set & prohibited)
    if unexpected:
        raise AgentRuntimeError("RUNTIME_MUTATION_PERMISSION_PRESENT")
    return {"resource": resource, "granted_permissions": sorted(granted_set)}


class _RetryableTransportError(Exception):
    def __init__(self, runtime_code: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(runtime_code)
        self.runtime_code = runtime_code
        self.retry_after_seconds = retry_after_seconds


class _RepairableResultError(Exception):
    def __init__(self, response_text: str, validator_errors: list[dict[str, str]]) -> None:
        super().__init__("RUNTIME_RESULT_REPAIR_REQUIRED")
        self.response_text = response_text
        self.validator_errors = validator_errors
        self.invocation_id: str | None = None


class _HardRuntimeDeadlineError(AgentRuntimeError):
    pass


class _RuntimeCleanupUncertainError(AgentRuntimeError):
    pass


class PostgresAgentCleanupSink:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def enqueue_session_delete(
        self,
        *,
        runtime_resource: str,
        user_id: str,
        session_id: str,
    ) -> None:
        payload = {
            "runtime_resource": runtime_resource,
            "user_id": user_id,
            "session_id": session_id,
        }
        payload_bytes = rfc8785.dumps(payload)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO workflow_outbox(
                        topic, aggregate_id, payload_json, payload_digest,
                        available_at, created_at
                    ) VALUES (
                        'AGENT_SESSION_CLEANUP', :aggregate_id,
                        CAST(:payload_json AS JSONB), :payload_digest, NOW(), NOW()
                    )
                    ON CONFLICT (topic, aggregate_id, payload_digest) DO NOTHING
                    """
                ),
                {
                    "aggregate_id": session_id,
                    "payload_json": payload_bytes.decode(),
                    "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
                },
            )


class AgentRuntimeHttpClient:
    def __init__(
        self,
        *,
        gcp_project_id: str,
        resource_id: str,
        user_hmac_secret: str,
        access_tokens: AccessTokenProvider,
        cleanup_sink: AgentCleanupSink,
        location: str = "asia-northeast3",
        transport: httpx.AsyncBaseTransport | None = None,
        async_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        contracts: AgentContractValidator | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        new_invocation_id: Callable[[], str] | None = None,
    ) -> None:
        if location != "asia-northeast3":
            raise ValueError("Agent Runtime location must be asia-northeast3")
        if not gcp_project_id or not resource_id:
            raise ValueError("Agent Runtime project and resource id are required")
        if len(user_hmac_secret.encode()) < 32:
            raise ValueError("Agent Runtime user HMAC secret must contain at least 32 bytes")
        self._resource = (
            f"projects/{gcp_project_id}/locations/{location}/reasoningEngines/{resource_id}"
        )
        self._base_url = (
            f"https://{location}-aiplatform.googleapis.com/v1/{self._resource}"
        )
        self._secret = user_hmac_secret.encode()
        self._access_tokens = access_tokens
        self._cleanup_sink = cleanup_sink
        if transport is not None and async_client_factory is not None:
            raise ValueError("Provide transport or async_client_factory, not both")
        self._async_client_factory = async_client_factory or (
            lambda: httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=False,
                transport=transport,
            )
        )
        self._contracts = contracts or ContractRegistry()
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._new_invocation_id = new_invocation_id or (lambda: str(uuid4()))

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self._contracts.validate_agent_task(task)
        if task["input_digest"] != compute_agent_input_digest(task):
            raise AgentRuntimeError("RUNTIME_TASK_DIGEST_MISMATCH")
        if task["repair_attempt"] != 0:
            raise AgentRuntimeError("RUNTIME_REPAIR_TASK_NOT_ALLOWED")

        try:
            return self._invoke_logical(task)
        except _RepairableResultError as first_error:
            repair_task = self._build_repair_task(task, first_error)
            try:
                return self._invoke_with_transport_retries(repair_task)
            except _RepairableResultError as second_error:
                raise AgentRuntimeError("RUNTIME_RESULT_SCHEMA_INVALID") from second_error

    def _invoke_logical(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._invoke_with_transport_retries(task)

    def _invoke_with_transport_retries(self, task: dict[str, Any]) -> dict[str, Any]:
        current_task = deepcopy(task)
        retry_after_seconds: float | None = None
        for attempt in range(1, 4):
            if attempt > 1:
                self._wait_before_retry(
                    current_task,
                    attempt,
                    retry_after_seconds=retry_after_seconds,
                )
                current_task["invocation_id"] = self._new_invocation_id()
            current_task["transport_attempt"] = attempt
            self._contracts.validate_agent_task(current_task)
            try:
                return self._invoke_once(current_task)
            except _RepairableResultError as error:
                error.invocation_id = current_task["invocation_id"]
                raise
            except _RetryableTransportError as error:
                if attempt == 3 or self._remaining_seconds(current_task) < 2:
                    raise AgentRuntimeError(error.runtime_code) from error
                retry_after_seconds = error.retry_after_seconds
        raise AgentRuntimeError("RUNTIME_TRANSPORT_FAILED")

    def _invoke_once(self, task: dict[str, Any]) -> dict[str, Any]:
        self._ensure_before_deadline(task)
        user_id = self._runtime_user_id(task["venture_project_id"])
        session_id = self._runtime_session_id(task["invocation_id"])
        try:
            events = self._stream_query(
                user_id=user_id,
                session_id=session_id,
                task=task,
                timeout=self._stream_timeout(task),
            )
        except Exception:
            # The Runtime owns normal deletion in the ephemeral stream's finally
            # block. A transport interruption makes that acknowledgement
            # uncertain, so enqueue the deterministic session id for idempotent
            # cleanup before propagating the original failure.
            self._enqueue_uncertain_cleanup(user_id=user_id, session_id=session_id)
            raise
        self._ensure_before_deadline(task)
        try:
            response_text = self._select_final_text(
                events,
                expected_author=task["agent_name"],
            )
        except _RuntimeCleanupUncertainError:
            self._enqueue_uncertain_cleanup(user_id=user_id, session_id=session_id)
            raise
        result = self._parse_and_validate_result(response_text)
        self._ensure_before_deadline(task)
        return result

    def _enqueue_uncertain_cleanup(self, *, user_id: str, session_id: str) -> None:
        try:
            self._cleanup_sink.enqueue_session_delete(
                runtime_resource=self._resource,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception:
            # Preserve the primary execution failure. The deterministic session
            # id remains available in the invocation trace for operator cleanup.
            pass

    def _parse_and_validate_result(self, response_text: str) -> dict[str, Any]:
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as error:
            raise _RepairableResultError(
                response_text,
                [
                    {
                        "code": "JSON_PARSE_FAILED",
                        "json_pointer": "",
                        "message": "Response is not one valid JSON object",
                    }
                ],
            ) from error
        if not isinstance(result, dict):
            raise _RepairableResultError(
                response_text,
                [
                    {
                        "code": "JSON_OBJECT_REQUIRED",
                        "json_pointer": "",
                        "message": "Response root must be an object",
                    }
                ],
            )
        try:
            self._contracts.validate_agent_task_result(result)
        except ContractValidationError as error:
            validation_errors = self._result_validation_errors(result, error)
            raise _RepairableResultError(response_text, validation_errors) from error
        return result

    def _result_validation_errors(
        self,
        result: dict[str, Any],
        fallback_error: ContractValidationError,
    ) -> list[dict[str, str]]:
        collector = getattr(self._contracts, "agent_task_result_errors", None)
        if callable(collector):
            errors = collector(result)
            if errors:
                return list(errors[:50])
        return [
            {
                "code": "AGENT_RESULT_SCHEMA_INVALID",
                "json_pointer": "",
                "message": str(fallback_error)[:500] or "Agent result Schema validation failed",
            }
        ]

    def _build_repair_task(
        self,
        original_task: dict[str, Any],
        error: _RepairableResultError,
    ) -> dict[str, Any]:
        self._ensure_retry_budget(original_task)
        previous_text = error.response_text
        if not previous_text:
            previous_text = "null"
        previous_text = previous_text[:65536]
        repair_task = deepcopy(original_task)
        repair_task["repair_attempt"] = 1
        repair_task["transport_attempt"] = 1
        repair_task["repair_of_invocation_id"] = (
            error.invocation_id or original_task["invocation_id"]
        )
        repair_task["invocation_id"] = self._new_invocation_id()
        repair_task["repair_context"] = {
            "previous_response_text": previous_text,
            "previous_response_digest": (
                f"sha256:{hashlib.sha256(previous_text.encode()).hexdigest()}"
            ),
            "validator_errors": error.validator_errors[:50],
        }
        if repair_task["input_digest"] != compute_agent_input_digest(repair_task):
            raise AgentRuntimeError("RUNTIME_REPAIR_DIGEST_CHANGED")
        self._contracts.validate_agent_task(repair_task)
        return repair_task

    def _stream_query(
        self,
        *,
        user_id: str,
        session_id: str,
        task: dict[str, Any],
        timeout: float,
    ) -> list[dict[str, Any]]:
        return self._run_with_hard_timeout(
            lambda client: self._stream_query_transport(
                client,
                user_id=user_id,
                session_id=session_id,
                task=task,
                request_timeout=timeout,
            ),
            timeout=timeout,
        )

    async def _stream_query_transport(
        self,
        client: httpx.AsyncClient,
        *,
        user_id: str,
        session_id: str,
        task: dict[str, Any],
        request_timeout: float,
    ) -> list[dict[str, Any]]:
        body = {
            "class_method": "async_ephemeral_stream_query",
            "input": {
                "user_id": user_id,
                "session_id": session_id,
                "message": rfc8785.dumps(task).decode(),
            },
        }
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}:streamQuery?alt=sse",
                headers=self._headers(),
                json=body,
                timeout=request_timeout,
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    await response.aread()
                    raise
                events = []
                async for line in response.aiter_lines():
                    self._ensure_stream_budget(task)
                    if not line:
                        continue
                    encoded = line[5:].strip() if line.startswith("data:") else line
                    value = json.loads(encoded)
                    if not isinstance(value, dict):
                        raise AgentRuntimeError("RUNTIME_STREAM_PROTOCOL_INVALID")
                    if "output" in value:
                        if set(value) != {"output"} or not isinstance(value["output"], dict):
                            raise AgentRuntimeError("RUNTIME_STREAM_PROTOCOL_INVALID")
                        events.append(value["output"])
                    elif self._is_direct_runtime_event(value):
                        events.append(value)
                    else:
                        raise AgentRuntimeError("RUNTIME_STREAM_PROTOCOL_INVALID")
        except json.JSONDecodeError as error:
            raise AgentRuntimeError("RUNTIME_STREAM_PROTOCOL_INVALID") from error
        except httpx.HTTPStatusError as error:
            self._raise_for_http_status(error.response, stage="STREAM")
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise _RetryableTransportError("RUNTIME_STREAM_TRANSPORT_FAILED") from error
        except httpx.HTTPError as error:
            raise AgentRuntimeError("RUNTIME_STREAM_TRANSPORT_FAILED") from error
        return events

    def _run_with_hard_timeout(
        self,
        action: Callable[[httpx.AsyncClient], Awaitable[T]],
        *,
        timeout: float,
    ) -> T:
        async def invoke() -> T:
            async with self._async_client_factory() as client:
                async with asyncio.timeout(timeout):
                    return await action(client)

        try:
            return asyncio.run(invoke())
        except TimeoutError as error:
            raise _HardRuntimeDeadlineError("RUNTIME_TIMED_OUT") from error

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        body: dict[str, Any],
        *,
        request_timeout: float,
    ) -> dict[str, Any]:
        try:
            response = await client.post(
                url,
                headers=self._headers(),
                json=body,
                timeout=request_timeout,
            )
            response.raise_for_status()
            value = response.json()
        except json.JSONDecodeError as error:
            raise AgentRuntimeError("RUNTIME_RESPONSE_INVALID") from error
        except httpx.HTTPStatusError as error:
            self._raise_for_http_status(error.response, stage="QUERY")
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise _RetryableTransportError("RUNTIME_TRANSPORT_FAILED") from error
        except httpx.HTTPError as error:
            raise AgentRuntimeError("RUNTIME_TRANSPORT_FAILED") from error
        if not isinstance(value, dict):
            raise AgentRuntimeError("RUNTIME_RESPONSE_INVALID")
        return value

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_tokens.token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _runtime_user_id(self, venture_project_id: str) -> str:
        digest = hmac.new(self._secret, venture_project_id.encode(), hashlib.sha256).hexdigest()
        return f"p-{digest}"

    def _runtime_session_id(self, invocation_id: str) -> str:
        digest = hashlib.sha256(invocation_id.encode()).hexdigest()
        return f"caffemate-{digest[:48]}"

    @staticmethod
    def _is_direct_runtime_event(value: dict[str, Any]) -> bool:
        return bool(
            {"author", "content"} <= set(value)
            or "error_code" in value
            or "errorCode" in value
        )

    @staticmethod
    def _select_final_text(
        events: list[dict[str, Any]],
        *,
        expected_author: str,
    ) -> str:
        candidates: list[str] = []
        for event in events:
            observed_error = event.get("error_code", event.get("errorCode"))
            if observed_error is not None:
                if observed_error == "RUNTIME_SESSION_CLEANUP_FAILED":
                    raise _RuntimeCleanupUncertainError(observed_error)
                if observed_error == "SAFETY_BLOCKED":
                    raise AgentRuntimeError("SAFETY_BLOCKED")
                raise AgentRuntimeError("RUNTIME_PROTOCOL_INVALID")
            if event.get("author") != expected_author:
                raise AgentRuntimeError("RUNTIME_PROTOCOL_INVALID")
            content = event.get("content")
            parts = content.get("parts") if isinstance(content, dict) else None
            if event.get("partial") is True:
                if not isinstance(parts, list) or not parts:
                    raise AgentRuntimeError("RUNTIME_PROTOCOL_INVALID")
                continue
            if not isinstance(parts, list) or len(parts) != 1:
                raise AgentRuntimeError("RUNTIME_PROTOCOL_INVALID")
            part = parts[0]
            if not isinstance(part, dict) or set(part) != {"text"}:
                raise AgentRuntimeError("RUNTIME_PROTOCOL_INVALID")
            value = part.get("text")
            if not isinstance(value, str) or not value.strip() or "```" in value:
                raise AgentRuntimeError("RUNTIME_PROTOCOL_INVALID")
            candidates.append(value)
        if len(candidates) != 1:
            raise AgentRuntimeError("RUNTIME_PROTOCOL_INVALID")
        return candidates[0]

    def _raise_for_http_status(self, response: httpx.Response, *, stage: str) -> None:
        status = response.status_code
        if status in {408, 429} or 500 <= status <= 599:
            raise _RetryableTransportError(
                f"RUNTIME_{stage}_TRANSPORT_FAILED",
                retry_after_seconds=self._retry_after_seconds(response),
            )
        provider_code = self._response_error_code(response)
        if provider_code in {
            "MODEL_JSON_INVALID",
            "RESULT_SCHEMA_INVALID",
            "RESULT_SEMANTIC_INVALID",
            "RUNTIME_AGENT_OUTPUT_INVALID",
            "VERTEX_MODEL_RESPONSE_INCOMPLETE",
            "VERTEX_MODEL_RESPONSE_INVALID",
        }:
            raise AgentRuntimeError("RUNTIME_AGENT_OUTPUT_INVALID")
        code = {
            400: "RUNTIME_REQUEST_INVALID",
            401: "RUNTIME_UNAUTHENTICATED",
            403: "RUNTIME_FORBIDDEN",
            422: "RUNTIME_AGENT_OUTPUT_INVALID",
        }.get(status, "RUNTIME_HTTP_TERMINAL")
        raise AgentRuntimeError(code)

    @staticmethod
    def _response_error_code(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        return error if isinstance(error, str) else None

    def _wait_before_retry(
        self,
        task: dict[str, Any],
        attempt: int,
        *,
        retry_after_seconds: float | None,
    ) -> None:
        self._ensure_retry_budget(task)
        base = (0.25, 0.75)[attempt - 2]
        jitter_seed = hashlib.sha256(task["invocation_id"].encode()).digest()
        jitter = int.from_bytes(jitter_seed[:2], "big") % 101 / 1000
        delay = retry_after_seconds if retry_after_seconds is not None else base + jitter
        if delay > self._remaining_seconds(task) - 2:
            raise AgentRuntimeError("RUNTIME_TIMED_OUT")
        self._sleep(delay)

    def _ensure_retry_budget(self, task: dict[str, Any]) -> None:
        if self._remaining_seconds(task) < 2:
            raise AgentRuntimeError("RUNTIME_TIMED_OUT")

    def _ensure_before_deadline(self, task: dict[str, Any]) -> None:
        if self._remaining_seconds(task) <= 0:
            raise AgentRuntimeError("RUNTIME_TIMED_OUT")

    def _remaining_seconds(self, task: dict[str, Any]) -> float:
        deadline = datetime.fromisoformat(task["deadline_at"].replace("Z", "+00:00"))
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return (deadline - now).total_seconds()

    def _stream_timeout(self, task: dict[str, Any]) -> float:
        available = self._remaining_seconds(task) - STREAM_CLEANUP_RESERVE_SECONDS
        if available <= 0:
            raise AgentRuntimeError("RUNTIME_TIMED_OUT")
        return available

    def _ensure_stream_budget(self, task: dict[str, Any]) -> None:
        if self._remaining_seconds(task) <= STREAM_CLEANUP_RESERVE_SECONDS:
            raise AgentRuntimeError("RUNTIME_TIMED_OUT")

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                seconds = (target - self._now()).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        if 0 <= seconds <= 2:
            return seconds
        return None
