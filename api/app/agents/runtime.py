import hashlib
import hmac
import json
from typing import Any, Protocol

import google.auth
import httpx
import rfc8785
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from sqlalchemy import Engine, text

from app.agents.task_factory import compute_agent_input_digest
from app.contracts.schema_registry import AgentContractValidator, ContractRegistry
from app.domain.errors import ExternalExecutionUnavailableError

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

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self._contracts.validate_agent_task(task)
        if task["input_digest"] != compute_agent_input_digest(task):
            raise AgentRuntimeError("RUNTIME_TASK_DIGEST_MISMATCH")
        user_id = self._runtime_user_id(task["venture_project_id"])
        session_id: str | None = None
        primary_error: Exception | None = None
        try:
            session_id = self._create_session(user_id)
            events = self._stream_query(user_id=user_id, session_id=session_id, task=task)
            result = self._select_final_result(events, expected_author=task["agent_name"])
            self._contracts.validate_agent_task_result(result)
            return result
        except Exception as error:
            primary_error = error
            raise
        finally:
            if session_id is not None:
                try:
                    self._delete_session(user_id=user_id, session_id=session_id)
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

    def _create_session(self, user_id: str) -> str:
        response = self._post_json(
            f"{self._base_url}:query",
            {"class_method": "async_create_session", "input": {"user_id": user_id}},
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
            ) as response:
                response.raise_for_status()
                events = []
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    value = json.loads(line[5:].strip())
                    if isinstance(value, dict):
                        events.append(value)
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise AgentRuntimeError("RUNTIME_STREAM_QUERY_FAILED") from error
        return events

    def _delete_session(self, *, user_id: str, session_id: str) -> None:
        self._post_json(
            f"{self._base_url}:query",
            {
                "class_method": "async_delete_session",
                "input": {"user_id": user_id, "session_id": session_id},
            },
        )

    def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(url, headers=self._headers(), json=body)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as error:
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
    def _select_final_result(
        events: list[dict[str, Any]],
        *,
        expected_author: str,
    ) -> dict[str, Any]:
        candidates: list[str] = []
        for event in events:
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
        try:
            result = json.loads(candidates[0])
        except json.JSONDecodeError as error:
            raise AgentRuntimeError("RUNTIME_RESULT_JSON_INVALID") from error
        if not isinstance(result, dict):
            raise AgentRuntimeError("RUNTIME_RESULT_JSON_INVALID")
        return result
