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
            return ContentInspection(
                boundary=boundary,
                invocation_result="SUCCESS",
                match_state="NOT_REPORTED",
                finding_count=0,
                info_types=(),
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
        "attack_case_inspected": True,
        "input_safe_inspected": True,
        "model_output_inspected": True,
        "pii_case_inspected": True,
        "result_visibility": "NOT_REPORTED",
        "status": "verified",
        "template": (
            "projects/project-1/locations/asia-northeast3/templates/template-1"
        ),
    }
