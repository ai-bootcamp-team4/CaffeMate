from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol, cast

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AccessTokenProvider(Protocol):
    def access_token(self) -> str: ...


class GoogleAccessTokenProvider:
    def __init__(self, credentials: Credentials | None = None) -> None:
        resolved = credentials
        if resolved is None:
            resolved, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",)
            )
        self._credentials = resolved

    def access_token(self) -> str:
        if not self._credentials.valid or not self._credentials.token:
            self._credentials.refresh(Request())  # type: ignore[no-untyped-call]
        token = self._credentials.token
        if not token:
            raise RuntimeError("GCP_ACCESS_TOKEN_UNAVAILABLE")
        return cast(str, token)


@dataclass(frozen=True)
class BigQueryLoad:
    table: str
    source_uri: str
    schema: tuple[dict[str, str], ...]


class BigQueryRestClient:
    def __init__(
        self,
        *,
        project_id: str,
        dataset_id: str,
        location: str,
        token_provider: AccessTokenProvider | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._validate_identifier(project_id.replace("-", "_"), "project_id")
        self._validate_identifier(dataset_id, "dataset_id")
        self._project_id = project_id
        self._dataset_id = dataset_id
        self._location = location
        self._token_provider = token_provider or GoogleAccessTokenProvider()
        self._transport = transport

    def load_ndjson(self, *, load: BigQueryLoad, job_id: str) -> None:
        self._validate_identifier(load.table, "table")
        self._validate_identifier(job_id, "job_id")
        body = {
            "jobReference": {
                "projectId": self._project_id,
                "jobId": job_id,
                "location": self._location,
            },
            "configuration": {
                "load": {
                    "sourceUris": [load.source_uri],
                    "destinationTable": {
                        "projectId": self._project_id,
                        "datasetId": self._dataset_id,
                        "tableId": load.table,
                    },
                    "sourceFormat": "NEWLINE_DELIMITED_JSON",
                    "schema": {"fields": list(load.schema)},
                    "createDisposition": "CREATE_IF_NEEDED",
                    "writeDisposition": "WRITE_APPEND",
                    "ignoreUnknownValues": False,
                    "maxBadRecords": 0,
                }
            },
        }
        response = self._request(
            "POST",
            f"/projects/{self._project_id}/jobs",
            params={"location": self._location},
            json_body=body,
            accepted=(200, 409),
        )
        if response.status_code == 409:
            self._wait_for_job(job_id)
            return
        payload = self._json_object(response)
        self._wait_for_job(cast(dict[str, Any], payload["jobReference"])["jobId"])

    def query_scalar(self, query: str) -> str | None:
        response = self._request(
            "POST",
            f"/projects/{self._project_id}/queries",
            json_body={
                "query": query,
                "useLegacySql": False,
                "location": self._location,
                "timeoutMs": 30000,
            },
        )
        payload = self._json_object(response)
        if not payload.get("jobComplete"):
            job_reference = cast(dict[str, Any], payload["jobReference"])
            payload = self._wait_for_query(str(job_reference["jobId"]))
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        fields = cast(list[dict[str, Any]], cast(dict[str, Any], rows[0])["f"])
        value = fields[0].get("v")
        return None if value is None else str(value)

    def _wait_for_job(self, job_id: str) -> dict[str, Any]:
        for _ in range(120):
            response = self._request(
                "GET",
                f"/projects/{self._project_id}/jobs/{job_id}",
                params={"location": self._location},
            )
            payload = self._json_object(response)
            status = cast(dict[str, Any], payload.get("status", {}))
            if status.get("state") == "DONE":
                error = status.get("errorResult")
                if error:
                    raise RuntimeError(f"BIGQUERY_JOB_FAILED:{self._safe_error_reason(error)}")
                return payload
            time.sleep(1)
        raise TimeoutError("BIGQUERY_JOB_TIMED_OUT")

    def _wait_for_query(self, job_id: str) -> dict[str, Any]:
        for _ in range(120):
            response = self._request(
                "GET",
                f"/projects/{self._project_id}/queries/{job_id}",
                params={"location": self._location, "timeoutMs": 1000},
            )
            payload = self._json_object(response)
            if payload.get("jobComplete"):
                return payload
        raise TimeoutError("BIGQUERY_QUERY_TIMED_OUT")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_body: dict[str, Any] | None = None,
        accepted: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        with httpx.Client(
            base_url="https://bigquery.googleapis.com/bigquery/v2",
            timeout=45,
            transport=self._transport,
        ) as client:
            response = client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {self._token_provider.access_token()}"},
            )
        if response.status_code not in accepted:
            reason = "UNKNOWN"
            try:
                error = response.json().get("error", {})
                reason = self._safe_error_reason(error)
            except (json.JSONDecodeError, AttributeError):
                pass
            raise RuntimeError(f"BIGQUERY_HTTP_{response.status_code}:{reason}")
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("BIGQUERY_RESPONSE_INVALID")
        return cast(dict[str, Any], payload)

    @staticmethod
    def _safe_error_reason(error: object) -> str:
        if not isinstance(error, dict):
            return "UNKNOWN"
        reason = error.get("reason") or error.get("status") or "UNKNOWN"
        return re.sub(r"[^A-Za-z0-9_.-]", "_", str(reason))[:80]

    @staticmethod
    def _validate_identifier(value: str, label: str) -> None:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {label}")
