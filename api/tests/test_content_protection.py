import json

import httpx
import pytest

from app.domain.errors import ExternalExecutionUnavailableError
from app.security.content_protection import (
    ContentBoundary,
    ModelArmorContentProtection,
)


class FakeTokens:
    def token(self) -> str:
        return "access-token"


def test_model_armor_inspects_user_payload_and_returns_only_safe_summary() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "sanitizationResult": {
                    "filterMatchState": "MATCH_FOUND",
                    "invocationResult": "SUCCESS",
                    "filterResults": {
                        "sdp": {
                            "sdpFilterResult": {
                                "inspectResult": {
                                    "executionState": "EXECUTION_SUCCESS",
                                    "matchState": "MATCH_FOUND",
                                    "findings": [
                                        {
                                            "infoType": "EMAIL_ADDRESS",
                                            "likelihood": "LIKELY",
                                            "location": {"byteRange": {"start": "0", "end": "4"}},
                                        },
                                        {
                                            "infoType": "EMAIL_ADDRESS",
                                            "likelihood": "LIKELY",
                                            "location": {"byteRange": {"start": "8", "end": "12"}},
                                        },
                                    ],
                                    "findingsTruncated": False,
                                }
                            }
                        }
                    },
                }
            },
        )

    protector = ModelArmorContentProtection(
        template_resource=(
            "projects/proj-aj20-211200020328/locations/asia-northeast3/"
            "templates/caffemate-sdp-inspect-v1"
        ),
        access_tokens=FakeTokens(),
        transport=httpx.MockTransport(handler),
    )

    result = protector.inspect("demo.user@example.com", ContentBoundary.AGENT_INPUT)

    assert result.match_state == "MATCH_FOUND"
    assert result.invocation_result == "SUCCESS"
    assert result.finding_count == 2
    assert result.info_types == ("EMAIL_ADDRESS",)
    assert result.findings_truncated is False
    assert len(requests) == 1
    assert requests[0].url.host == "modelarmor.asia-northeast3.rep.googleapis.com"
    assert requests[0].url.path.endswith(":sanitizeUserPrompt")
    assert json.loads(requests[0].content) == {
        "userPromptData": {"text": "demo.user@example.com"}
    }
    assert requests[0].headers["Authorization"] == "Bearer access-token"


def test_model_armor_uses_the_model_response_operation() -> None:
    observed_path = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_path
        observed_path = request.url.path
        assert json.loads(request.content) == {"modelResponseData": {"text": "안내 결과"}}
        return httpx.Response(
            200,
            json={
                "sanitizationResult": {
                    "filterMatchState": "NO_MATCH_FOUND",
                    "invocationResult": "SUCCESS",
                    "filterResults": {
                        "sdp": {
                            "sdpFilterResult": {
                                "inspectResult": {
                                    "executionState": "EXECUTION_SUCCESS",
                                    "matchState": "NO_MATCH_FOUND",
                                    "findings": [],
                                    "findingsTruncated": False,
                                }
                            }
                        }
                    },
                }
            },
        )

    result = ModelArmorContentProtection(
        template_resource=(
            "projects/proj/locations/asia-northeast3/templates/caffemate-sdp-inspect-v1"
        ),
        access_tokens=FakeTokens(),
        transport=httpx.MockTransport(handler),
    ).inspect("안내 결과", ContentBoundary.AGENT_OUTPUT)

    assert observed_path.endswith(":sanitizeModelResponse")
    assert result.match_state == "NO_MATCH_FOUND"
    assert result.finding_count == 0


def test_model_armor_accepts_inspect_only_success_without_disclosed_findings() -> None:
    result = ModelArmorContentProtection(
        template_resource=(
            "projects/proj/locations/asia-northeast3/templates/caffemate-sdp-inspect-v1"
        ),
        access_tokens=FakeTokens(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"sanitizationResult": {"invocationResult": "SUCCESS"}},
            )
        ),
    ).inspect("검사 전용 입력", ContentBoundary.AGENT_INPUT)

    assert result.invocation_result == "SUCCESS"
    assert result.match_state == "NOT_REPORTED"
    assert result.finding_count == 0
    assert result.info_types == ()
    assert result.findings_truncated is False


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, json={}),
        httpx.Response(
            200,
            json={
                "sanitizationResult": {
                    "filterMatchState": "NO_MATCH_FOUND",
                    "invocationResult": "PARTIAL",
                    "filterResults": {},
                }
            },
        ),
    ],
)
def test_model_armor_fails_loudly_when_inspection_is_not_complete(
    response: httpx.Response,
) -> None:
    protector = ModelArmorContentProtection(
        template_resource="projects/proj/locations/asia-northeast3/templates/template-1",
        access_tokens=FakeTokens(),
        transport=httpx.MockTransport(lambda _request: response),
    )

    with pytest.raises(ExternalExecutionUnavailableError):
        protector.inspect("검사할 내용", ContentBoundary.AGENT_INPUT)


def test_template_resource_must_be_an_exact_seoul_regional_resource() -> None:
    with pytest.raises(ValueError, match="asia-northeast3"):
        ModelArmorContentProtection(
            template_resource="projects/proj/locations/global/templates/template-1",
            access_tokens=FakeTokens(),
        )
