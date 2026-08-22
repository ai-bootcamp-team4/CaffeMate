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
    assert "DOCKER_BUILDKIT=1" in config
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
    assert "openssl rand -hex 48 | tr -d '\\n'" in bootstrap
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
    assert "roles/storage.objectViewer" in verifier
    assert "storage buckets get-iam-policy" in verifier
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


def test_migration_runtime_build_and_job_contracts() -> None:
    image_build = (ROOT / "cloudbuild.backend-image.yaml").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy-migration-job.sh").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify-migration-job.sh").read_text(encoding="utf-8")

    assert "deploy/backend.Dockerfile" in image_build
    assert "DOCKER_BUILDKIT=1" in image_build
    assert "${_IMAGE_TAG}" in image_build
    assert "CLOUD_LOGGING_ONLY" in image_build
    assert "CAFFEMATE_SOURCE_REVISION" in deploy
    assert "--set-cloudsql-instances" in deploy
    assert "DB_PASS=caffemate-db-password:latest" in deploy
    assert "--max-retries=0" in deploy
    assert "--args=verify-migrations" in deploy
    assert "image_summary.fully_qualified_digest" in deploy
    assert "backend@sha256:" in deploy
    assert "--allow-unauthenticated" not in deploy
    assert "versions access" not in deploy
    assert "digest-pinned image" in verifier
    assert "latest migration verification execution" in verifier


def test_api_worker_runtime_deployment_preserves_auth_boundaries() -> None:
    deploy = (ROOT / "scripts" / "deploy-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )

    assert "caffemate-api" in deploy
    assert "caffemate-worker" in deploy
    assert "--ingress=all" in deploy
    assert "--ingress=internal" in deploy
    assert "--default-url" in deploy
    assert "--invoker-iam-check" in deploy
    assert "--allow-unauthenticated" in deploy.split("gcloud run deploy caffemate-worker", 1)[0]
    assert "--allow-unauthenticated" not in deploy.split("gcloud run deploy caffemate-worker", 1)[1]
    assert "caffemate-pubsub-push" in deploy
    assert "caffemate-scheduler" in deploy
    assert "--push-auth-service-account" in deploy
    assert "--oidc-service-account-email" in deploy
    assert "WORKER_ID=caffemate-worker" in deploy
    assert "roles/iam.serviceAccountTokenCreator" in deploy
    assert "CAFFEMATE_AGENT_RUNTIME_RESOURCE_ID" in deploy
    assert "AGENT_RUNTIME_PROJECT_ID=${project_id}" in deploy
    assert "AGENT_RUNTIME_RESOURCE_ID=${agent_runtime_resource_id}" in deploy
    assert '"${agent_runtime_url}:getIamPolicy"' in deploy
    assert '"${agent_runtime_url}:setIamPolicy"' in deploy
    assert "roles/aiplatform.user" in deploy
    assert "roles/aiplatform.expressUser" in deploy
    assert "roles/serviceusage.serviceUsageConsumer" in deploy
    assert 'agent_runtime_identity="principal://${agent_runtime_identity}"' in deploy
    assert 'agent_runtime_identity="principal://${agent_runtime_identity}"' in verifier
    assert "--header=" not in deploy
    assert "--header=" not in verifier
    assert "MCP_SCOPE_HMAC_SECRET" not in deploy.split("gcloud run deploy caffemate-worker", 1)[1]
    assert "API unauthenticated business request returned HTTP 401" in verifier
    assert '"${api_url}/health"' in verifier
    assert '"${worker_url}/health"' in verifier
    assert "/healthz" not in verifier
    assert "Worker unauthenticated internet request rejected" in verifier
    assert "authenticated Pub/Sub push configuration" in verifier
    assert "API and Worker use the same image digest" in verifier
    assert "verify-mcp-preflight" in verifier
    assert "Control API SDK manifest preflight against deployed MCP" in verifier
    assert "verify-agent-runtime" in verifier
    assert "resource-scoped Agent Runtime query IAM" in verifier
    assert "Agent Runtime identity has model and service usage permissions" in verifier
    assert "created, executed, validated and deleted an Agent Runtime session" in verifier
    assert "Worker has public invoker policy" in verifier
    assert "Scheduler reached internal Worker with HTTP 200" in verifier
