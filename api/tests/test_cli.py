import json
import sys
from typing import Any

from app import cli
from app.security.content_protection import (
    ContentBoundary,
    ContentInspection,
)


def test_agent_runtime_iam_verification_does_not_require_execution_hmac(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_PROJECT_ID", "project-1")
    monkeypatch.setenv("AGENT_RUNTIME_RESOURCE_ID", "123456789")
    monkeypatch.delenv("AGENT_RUNTIME_USER_HMAC_SECRET", raising=False)
    monkeypatch.setattr(
        cli,
        "verify_agent_runtime_iam",
        lambda **_: {"query_allowed": True, "mutation_permissions": []},
    )
    monkeypatch.setattr(sys, "argv", ["caffemate-api", "verify-agent-runtime-iam"])

    cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "mutation_permissions": [],
        "query_allowed": True,
        "status": "verified",
    }


def test_model_armor_verification_reports_safe_operational_evidence(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    class FakeProtection:
        def inspect(self, content: str, boundary: ContentBoundary) -> ContentInspection:
            matched = "demo.person@example.com" in content
            return ContentInspection(
                boundary=boundary,
                invocation_result="SUCCESS",
                match_state="MATCH_FOUND" if matched else "NO_MATCH_FOUND",
                finding_count=1 if matched else 0,
                info_types=("EMAIL_ADDRESS",) if matched else (),
                findings_truncated=False,
            )

    monkeypatch.setenv(
        "MODEL_ARMOR_TEMPLATE",
        "projects/project-1/locations/asia-northeast3/templates/template-1",
    )
    monkeypatch.setattr(cli, "_content_protection", lambda _settings: FakeProtection())
    monkeypatch.setattr(sys, "argv", ["caffemate-api", "verify-model-armor"])

    cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "input_safe_match_state": "NO_MATCH_FOUND",
        "model_output_match_state": "NO_MATCH_FOUND",
        "sensitive_finding_count": 1,
        "sensitive_info_types": ["EMAIL_ADDRESS"],
        "sensitive_match_state": "MATCH_FOUND",
        "status": "verified",
        "template": (
            "projects/project-1/locations/asia-northeast3/templates/template-1"
        ),
    }
