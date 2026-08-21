from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backend_cloudbuild_preserves_order_and_security_boundaries() -> None:
    config = (ROOT / "cloudbuild.backend.yaml").read_text(encoding="utf-8")
    ordered_steps = [
        "- id: build-backend-image",
        "- id: push-backend-image",
        "- id: update-migration-job",
        "- id: run-migrations",
        "- id: deploy-control-api",
        "- id: deploy-worker",
    ]

    assert [config.index(step) for step in ordered_steps] == sorted(
        config.index(step) for step in ordered_steps
    )
    assert "deploy/backend.Dockerfile" in config
    assert "--command=caffemate-api" in config
    assert "--args=migrate" in config
    assert "--max-retries=0" in config
    assert "--ingress=all" in config
    assert "--ingress=internal" in config
    assert "--command=uvicorn" in config
    assert "worker.main:app,--host,0.0.0.0,--port,8080" in config
    assert "--allow-unauthenticated" not in config
    assert "set-iam-policy" not in config


def test_backend_deployment_contract_requires_operational_readback() -> None:
    documentation = (ROOT / "docs" / "backend-deployment.md").read_text(encoding="utf-8")

    for required_evidence in (
        "pushed image digest",
        "migration execution success",
        "latest ready revision",
        "HTTP 200",
        "`PUBLISHED`",
    ):
        assert required_evidence in documentation
    assert "`pending`" in documentation
