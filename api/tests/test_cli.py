import json
import sys
from typing import Any

from app import cli


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
