import hashlib
import hmac
import json
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
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
        client: httpx.Client | None = None,
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
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=False)
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
        session_id: str | None = None
        primary_error: Exception | None = None
        try:
            session_id = self._create_session(user_id, timeout=self._request_timeout(task))
            events = self._stream_query(
                user_id=user_id,
                session_id=session_id,
                task=task,
                timeout=self._request_timeout(task),
            )
            self._ensure_before_deadline(task)
            response_text = self._select_final_text(events, expected_author=task["agent_name"])
            result = self._parse_and_validate_result(response_text)
            self._ensure_before_deadline(task)
            return result
        except Exception as error:
            primary_error = error
            raise
        finally:
            if session_id is not None:
                try:
                    self._delete_session(
                        user_id=user_id,
                        session_id=session_id,
                        timeout=self._request_timeout(task),
                    )
                except Exception:
                    try:
                        self._cleanup_sink.enqueue_session_delete(
                            runtime_resource=self._resource,
                            user_id=user_id,
                            session_id=session_id,
                        )
                    except Exception as enqueue_error:
                        if primary_error is None:
                            raise AgentRuntimeError(
                                "RUNTIME_CLEANUP_ENQUEUE_FAILED"
                            ) from enqueue_error

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

    def _create_session(self, user_id: str, *, timeout: float) -> str:
        response = self._post_json(
            f"{self._base_url}:query",
            {"class_method": "async_create_session", "input": {"user_id": user_id}},
            timeout=timeout,
        )
        output = response.get("output", response)
        session_id = output.get("id") if isinstance(output, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise AgentRuntimeError("RUNTIME_SESSION_CREATE_INVALID")
        return session_id

    def _stream_query(
        self,
        *,
        user_id: str,
        session_id: str,
        task: dict[str, Any],
        timeout: float,
    ) -> list[dict[str, Any]]:
        body = {
            "class_method": "async_stream_query",
            "input": {
                "user_id": user_id,
                "session_id": session_id,
                "message": rfc8785.dumps(task).decode(),
            },
        }
        try:
            with self._client.stream(
                "POST",
                f"{self._base_url}:streamQuery?alt=sse",
                headers=self._headers(),
                json=body,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                events = []
                for line in response.iter_lines():
                    if not line:
                        continue
                    encoded = line[5:].strip() if line.startswith("data:") else line
                    value = json.loads(encoded)
                    if not isinstance(value, dict):
                        continue
                    output = value.get("output")
                    events.append(output if isinstance(output, dict) else value)
        except json.JSONDecodeError as error:
            raise AgentRuntimeError("RUNTIME_STREAM_PROTOCOL_INVALID") from error
        except httpx.HTTPStatusError as error:
            self._raise_for_http_status(error.response, stage="STREAM")
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise _RetryableTransportError("RUNTIME_STREAM_TRANSPORT_FAILED") from error
        except httpx.HTTPError as error:
            raise AgentRuntimeError("RUNTIME_STREAM_TRANSPORT_FAILED") from error
        return events

    def _delete_session(self, *, user_id: str, session_id: str, timeout: float) -> None:
        self._post_json(
            f"{self._base_url}:query",
            {
                "class_method": "async_delete_session",
                "input": {"user_id": user_id, "session_id": session_id},
            },
            timeout=timeout,
        )

    def _post_json(self, url: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        try:
            response = self._client.post(
                url,
                headers=self._headers(),
                json=body,
                timeout=timeout,
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

    @staticmethod
    def _select_final_text(
        events: list[dict[str, Any]],
        *,
        expected_author: str,
    ) -> str:
        candidates: list[str] = []
        for event in events:
            observed_error = event.get("error_code", event.get("errorCode"))
            if observed_error == "SAFETY_BLOCKED":
                raise AgentRuntimeError("SAFETY_BLOCKED")
            if event.get("author") != expected_author or event.get("partial") is True:
                continue
            content = event.get("content")
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list) or len(parts) != 1:
                continue
            part = parts[0]
            if not isinstance(part, dict) or set(part) != {"text"}:
                continue
            value = part.get("text")
            if not isinstance(value, str) or not value.strip() or "```" in value:
                continue
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
        code = {
            400: "RUNTIME_REQUEST_INVALID",
            401: "RUNTIME_UNAUTHENTICATED",
            403: "RUNTIME_FORBIDDEN",
        }.get(status, "RUNTIME_HTTP_TERMINAL")
        raise AgentRuntimeError(code)

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

    def _request_timeout(self, task: dict[str, Any]) -> float:
        remaining = self._remaining_seconds(task)
        if remaining <= 0:
            raise AgentRuntimeError("RUNTIME_TIMED_OUT")
        return min(30.0, remaining)

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
