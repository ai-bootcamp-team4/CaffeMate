"""Model Armor 검사 결과를 제품 판단과 분리해 안전한 요약으로만 보존한다."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

from app.domain.errors import ExternalExecutionUnavailableError
from app.observability import record_safe_metric


class ContentBoundary(StrEnum):
    AGENT_INPUT = "AGENT_INPUT"
    AGENT_OUTPUT = "AGENT_OUTPUT"


@dataclass(frozen=True)
class ContentInspection:
    boundary: ContentBoundary
    invocation_result: str
    match_state: str
    finding_count: int
    info_types: tuple[str, ...]
    findings_truncated: bool


class ContentProtection(Protocol):
    def inspect(self, content: str, boundary: ContentBoundary) -> ContentInspection: ...


class AccessTokenProvider(Protocol):
    def token(self) -> str: ...


class GoogleAccessTokenProvider:
    def __init__(self, credentials: Credentials | None = None) -> None:
        self._credentials = credentials or google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )[0]

    def token(self) -> str:
        if not self._credentials.valid or not self._credentials.token:
            self._credentials.refresh(Request())  # type: ignore[no-untyped-call]
        token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_AUTH_TOKEN_MISSING")
        return token


class ModelArmorContentProtection:
    def __init__(
        self,
        *,
        template_resource: str,
        access_tokens: AccessTokenProvider,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parts = template_resource.split("/")
        if (
            len(parts) != 6
            or parts[0] != "projects"
            or parts[2] != "locations"
            or parts[3] != "asia-northeast3"
            or parts[4] != "templates"
            or not parts[1]
            or not parts[5]
        ):
            raise ValueError(
                "Model Armor template must be an exact asia-northeast3 template resource"
            )
        self._template_resource = template_resource
        self._access_tokens = access_tokens
        self._transport = transport

    def inspect(self, content: str, boundary: ContentBoundary) -> ContentInspection:
        """사용자 의도: 원문은 바꾸거나 기록하지 않고 검사 결과만 관측한다."""

        method, field = {
            ContentBoundary.AGENT_INPUT: ("sanitizeUserPrompt", "userPromptData"),
            ContentBoundary.AGENT_OUTPUT: ("sanitizeModelResponse", "modelResponseData"),
        }[boundary]
        url = (
            "https://modelarmor.asia-northeast3.rep.googleapis.com/v1/"
            f"{self._template_resource}:{method}"
        )
        try:
            with httpx.Client(timeout=30.0, transport=self._transport) as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {self._access_tokens.token()}"},
                    json={field: {"text": content}},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_INSPECTION_FAILED") from error

        inspection = self._parse(payload, boundary=boundary)
        record_safe_metric(
            "CAFFEMATE_MODEL_ARMOR_INSPECTION",
            content_boundary=boundary.value,
            invocation_result=inspection.invocation_result,
            match_state=inspection.match_state,
            finding_count=inspection.finding_count,
        )
        return inspection

    @staticmethod
    def _parse(payload: Any, *, boundary: ContentBoundary) -> ContentInspection:
        result = payload.get("sanitizationResult") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_RESULT_INVALID")
        invocation_result = result.get("invocationResult")
        if invocation_result != "SUCCESS":
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_RESULT_INCOMPLETE")
        match_state = result.get("filterMatchState")
        filter_results = result.get("filterResults")
        if match_state is None and filter_results is None:
            return ContentInspection(
                boundary=boundary,
                invocation_result=invocation_result,
                match_state="NOT_REPORTED",
                finding_count=0,
                info_types=(),
                findings_truncated=False,
            )
        if match_state not in {
            "NO_MATCH_FOUND",
            "MATCH_FOUND",
        }:
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_RESULT_INCOMPLETE")
        sdp = filter_results.get("sdp") if isinstance(filter_results, dict) else None
        sdp_result = sdp.get("sdpFilterResult") if isinstance(sdp, dict) else None
        inspect_result = (
            sdp_result.get("inspectResult") if isinstance(sdp_result, dict) else None
        )
        if (
            not isinstance(inspect_result, dict)
            or inspect_result.get("executionState") != "EXECUTION_SUCCESS"
            or inspect_result.get("matchState") != match_state
        ):
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_SDP_RESULT_INVALID")
        findings = inspect_result.get("findings")
        if not isinstance(findings, list):
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_SDP_FINDINGS_INVALID")
        info_types: set[str] = set()
        for finding in findings:
            info_type = finding.get("infoType") if isinstance(finding, dict) else None
            if not isinstance(info_type, str) or not info_type:
                raise ExternalExecutionUnavailableError("MODEL_ARMOR_SDP_FINDING_INVALID")
            info_types.add(info_type)
        truncated = inspect_result.get("findingsTruncated", False)
        if not isinstance(truncated, bool):
            raise ExternalExecutionUnavailableError("MODEL_ARMOR_SDP_TRUNCATION_INVALID")
        return ContentInspection(
            boundary=boundary,
            invocation_result=invocation_result,
            match_state=match_state,
            finding_count=len(findings),
            info_types=tuple(sorted(info_types)),
            findings_truncated=truncated,
        )
