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


def test_backend_foundation_scripts_preserve_scope_and_secret_values() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap-backend-foundation.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-backend-foundation.sh").read_text(
        encoding="utf-8"
    )

    assert "CAFFEMATE_GCP_PROJECT_ID" in bootstrap
    assert "asia-northeast3" in bootstrap
    assert "caffemate-backend" in bootstrap
    assert "--immutable-tags" in bootstrap
    assert "openssl rand -base64 48" in bootstrap
    assert "--data-file=-" in bootstrap
    assert "roles/owner" not in bootstrap
    assert "roles/editor" not in bootstrap
    assert "allUsers" not in bootstrap
    assert "gcloud sql" not in bootstrap
    assert "gcloud run deploy" not in bootstrap

    assert "exactly one enabled version" in verifier
    assert "get-iam-policy" in verifier
    assert "secretAccessor" in verifier
    assert "roles/artifactregistry.writer" in verifier
    assert "roles/logging.logWriter" in verifier
    assert "roles/run.admin" in verifier
    assert "roles/iam.serviceAccountUser" in verifier
    assert "versions access" not in verifier


def test_cloud_sql_scripts_lock_region_recovery_and_network_boundary() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap-cloud-sql.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-cloud-sql.sh").read_text(
        encoding="utf-8"
    )

    assert "asia-northeast3" in bootstrap
    assert "POSTGRES_16" in bootstrap
    assert "--edition=enterprise" in bootstrap
    assert "db-g1-small" in bootstrap
    assert "--enable-point-in-time-recovery" in bootstrap
    assert "--deletion-protection" in bootstrap
    assert "--storage-auto-increase" in bootstrap
    assert "--assign-ip" in bootstrap
    assert "authorized-networks" not in bootstrap
    assert "versions access latest" in bootstrap
    assert "roles/cloudsql.client" in bootstrap
    assert "roles/cloudsql.admin" not in bootstrap
    assert "printf.*database_password" not in bootstrap

    assert "public IP has no authorized networks" in verifier
    assert "point-in-time recovery is enabled" in verifier
    assert "deletion protection is enabled" in verifier
    assert "Cloud SQL client" in verifier
