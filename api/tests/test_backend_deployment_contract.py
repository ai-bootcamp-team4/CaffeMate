import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backend_cloudbuild_preserves_order_and_security_boundaries() -> None:
    config = (ROOT / "cloudbuild.backend.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "deploy" / "backend.Dockerfile").read_text(encoding="utf-8")
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
    assert (
        "COPY --chown=caffemate:caffemate agents/release-manifest.json "
        "./agents/release-manifest.json"
    ) in dockerfile
    assert (
        "COPY --chown=caffemate:caffemate agents/fixtures ./agents/fixtures"
        in dockerfile
    )


def test_backend_runtime_files_are_readable_by_non_root_runtime_user() -> None:
    dockerfile = (ROOT / "deploy" / "backend.Dockerfile").read_text(encoding="utf-8")

    for copy_instruction in (
        "COPY --chown=caffemate:caffemate api/app ./api/app",
        "COPY --chown=caffemate:caffemate api/migrations ./api/migrations",
        (
            "COPY --chown=caffemate:caffemate agents/release-manifest.json "
            "./agents/release-manifest.json"
        ),
        "COPY --chown=caffemate:caffemate agents/fixtures ./agents/fixtures",
        "COPY --chown=caffemate:caffemate docs/contracts ./docs/contracts",
        "COPY --chown=caffemate:caffemate worker ./worker",
    ):
        assert copy_instruction in dockerfile


def test_main_deploy_scope_selects_only_changed_runtime(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "output"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )

    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)

    def commit_and_resolve(path: str, content: str) -> tuple[str, str]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", path], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-qm", f"change {path}"],
            check=True,
        )
        subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "resolve-main-deploy-scope.sh"),
                str(repo),
                str(output),
                "auto",
            ],
            check=True,
        )
        return (
            (output / "deploy-web").read_text(encoding="utf-8").strip(),
            (output / "deploy-backend").read_text(encoding="utf-8").strip(),
        )

    assert commit_and_resolve("src/App.tsx", "web\n") == ("true", "false")
    assert commit_and_resolve("api/app/main.py", "backend\n") == ("false", "true")
    assert commit_and_resolve("src/App.test.tsx", "web test\n") == ("false", "false")
    assert commit_and_resolve("api/tests/test_main.py", "api test\n") == (
        "false",
        "false",
    )
    assert commit_and_resolve("docs/notes.md", "docs\n") == ("false", "false")


def test_main_cloudbuild_guards_web_and_backend_deployments() -> None:
    config = (ROOT / "cloudbuild.main-webhook.yaml").read_text(encoding="utf-8")

    assert "--depth=2" in config
    assert "resolve-main-deploy-scope.sh" in config
    assert "_DEPLOY_SCOPE: auto" in config
    assert config.count("/workspace/deploy-web") >= 3
    assert config.count("/workspace/deploy-backend") >= 6
    assert "\nimages:\n" not in config


def test_selected_candidate_canary_reads_operational_revision_digest() -> None:
    verifier = (ROOT / "scripts" / "verify-selected-candidate-runtime.sh").read_text(
        encoding="utf-8"
    )

    assert "latestReadyRevisionName" in verifier
    assert 'gcloud run revisions describe "$api_revision"' in verifier
    assert 'gcloud run revisions describe "$worker_revision"' in verifier
    assert "data['status']['imageDigest']" in verifier
    assert "data['spec']['template']['spec']['containers'][0]['image']" not in verifier


def test_selected_candidate_canary_tracks_the_single_recompute_stage() -> None:
    verifier = (ROOT / "scripts" / "verify-selected-candidate-runtime.sh").read_text(
        encoding="utf-8"
    )

    assert 'recomputed == {"RUN_PROPOSAL"}' in verifier
    assert 'report.get("reused_stage_count") == 0' in verifier
    assert '"CALCULATE_GATE_RANK"' not in verifier
    assert '"CANDIDATE_AUDIT"' not in verifier
    assert '"COMMIT_RESULT"' not in verifier


def test_backend_verifier_reads_operational_revision_digests() -> None:
    verifier = (ROOT / "scripts" / "verify-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )

    assert 'gcloud run revisions describe "$ready_revision"' in verifier
    assert "status.imageDigest" in verifier
    assert 'spec.template.spec.containers[0].image' not in verifier


def test_backend_deployment_contract_requires_operational_readback() -> None:
    documentation = (ROOT / "docs" / "backend-deployment.md").read_text(encoding="utf-8")

    for required_evidence in (
        "pushed image digest",
        "migration execution success",
        "latest ready revision",
        "HTTP 200",
        "`RUN_PROPOSAL`",
    ):
        assert required_evidence in documentation
    assert "`pending`" in documentation


def test_document_storage_deployment_is_pinned_and_verified_end_to_end() -> None:
    deploy = (ROOT / "scripts" / "deploy-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )
    cors = json.loads(
        (ROOT / "deploy" / "gcs" / "document-cors.json").read_text(encoding="utf-8")
    )

    assert "CAFFEMATE_DOCUMENT_BUCKET" in deploy
    assert 'expected_document_bucket="${project_id}-caffemate-documents"' in deploy
    assert "--uniform-bucket-level-access" in deploy
    assert "--public-access-prevention" in deploy
    assert "caffemateDocumentUrlSigner" in deploy
    assert "iam.serviceAccounts.signBlob" in deploy
    assert "caffemateDocumentObjectAccess" in deploy
    assert "storage.objects.create,storage.objects.delete,storage.objects.get" in deploy
    assert "DOCUMENT_SIGNING_SERVICE_ACCOUNT_EMAIL=${api_sa}" in deploy
    assert 'bucket["uniform_bucket_level_access"] is True' in verifier
    assert 'bucket["public_access_prevention"] == "enforced"' in verifier
    assert 'bucket.get("cors_config", [])' in verifier
    assert 'bucket["iamConfiguration"]' not in verifier
    assert "--args=verify-document-storage" in verifier
    assert "DOCUMENT_EXTRACT" in verifier
    assert "service-account-key" not in verifier
    assert set(cors[0]["method"]) == {"GET", "HEAD", "PUT"}
    assert set(cors[0]["responseHeader"]) == {
        "Content-Type",
        "x-goog-meta-caffemate-sha256",
    }


def test_model_armor_is_inspect_only_and_verified_with_the_deployed_api_image() -> None:
    deploy = (ROOT / "scripts" / "deploy-model-armor.sh").read_text(encoding="utf-8")
    runtime_deploy = (ROOT / "scripts" / "deploy-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-model-armor.sh").read_text(encoding="utf-8")

    assert 'region="asia-northeast3"' in deploy
    assert '"enforcementType": "INSPECT_ONLY"' in deploy
    assert '"logSanitizeOperations": false' in deploy
    assert ".templateMetadata.logSanitizeOperations != true" in deploy
    assert '"filterEnforcement": "ENABLED"' in deploy
    assert "roles/modelarmor.user" in deploy
    assert "roles/modelarmor.viewer" in deploy
    assert "roles/modelarmor.admin" not in deploy
    assert '--update-env-vars="MODEL_ARMOR_TEMPLATE=' in deploy
    assert "CAFFEMATE_MODEL_ARMOR_TEMPLATE_ID" in runtime_deploy
    assert "MODEL_ARMOR_TEMPLATE=${model_armor_template}" in runtime_deploy

    assert "latestReadyRevisionName" in verifier
    assert "status.imageDigest" in verifier
    assert '--args="verify-model-armor"' in verifier
    assert "--max-retries=0" in verifier
    assert 'template["templateMetadata"]["enforcementType"] == "INSPECT_ONLY"' in verifier
    assert (
        'template["templateMetadata"].get("logSanitizeOperations", False) is False'
        in verifier
    )
    assert "MODEL_ARMOR_TEMPLATE" in verifier
    assert "roles/modelarmor.user" in verifier
    assert "roles/modelarmor.viewer" in verifier


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
    assert "roles/firebaseauth.viewer" in bootstrap
    assert "roles/firebaseauth.viewer" in verifier
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
    assert "caffemate-scheduler" in deploy
    assert "--oidc-service-account-email" in deploy
    assert "WORKER_ID=caffemate-worker" in deploy
    assert "/internal/v1/agent-sessions:cleanup" in deploy
    assert "WORKFLOW_STAGE_TOPIC_RESOURCE" not in deploy
    assert "PUBSUB_SUBSCRIPTION" not in deploy
    assert "caffemate-pubsub-push" not in deploy
    assert "/internal/v1/pubsub/workflow-stages" not in deploy
    assert "/internal/v1/outbox:publish" not in deploy
    assert 'existing_api_url=$(gcloud run services describe caffemate-api' in deploy
    assert 'CONTROL_API_AUDIENCE=${existing_api_url}' in deploy
    assert '--update-env-vars="CONTROL_API_AUDIENCE=${api_url}"' in deploy
    assert "Control API internal identity audience matches canonical service URL" in verifier
    assert "CAFFEMATE_AGENT_RUNTIME_RESOURCE_ID" in deploy
    assert "AGENT_RUNTIME_PROJECT_ID=${project_id}" in deploy
    assert "AGENT_RUNTIME_RESOURCE_ID=${agent_runtime_resource_id}" in deploy
    assert '"${agent_runtime_url}:getIamPolicy"' in deploy
    assert '"${agent_runtime_url}:setIamPolicy"' in deploy
    assert "caffemateAgentRuntimeInvoker" in deploy
    assert "caffemateAgentSessionManager" in deploy
    assert "caffemateReleaseVerifier" in deploy
    for permission in (
        "aiplatform.reasoningEngines.query",
        "run.services.get",
        "storage.objects.get",
    ):
        assert permission in deploy
        assert permission in verifier
    assert "remove_project_role_binding" in deploy
    assert "roles/serviceusage.serviceUsageConsumer" in deploy
    assert 'member="serviceAccount:${api_sa}"' in deploy
    assert 'agent_runtime_identity="principal://${agent_runtime_identity}"' in deploy
    assert 'agent_runtime_identity="principal://${agent_runtime_identity}"' in verifier
    assert "--header=" not in deploy
    assert "--header=" not in verifier
    assert "--data=" not in deploy
    assert "--data=" not in verifier
    assert "MCP_SCOPE_HMAC_SECRET" not in deploy.split("gcloud run deploy caffemate-worker", 1)[1]
    assert "API unauthenticated business request returned HTTP 401" in verifier
    assert '"${api_url}/health"' in verifier
    assert '"${worker_url}/health"' in verifier
    assert "/healthz" not in verifier
    assert "Worker unauthenticated internet request rejected" in verifier
    assert "API and Worker use the same image digest" in verifier
    assert "verify-mcp-preflight" in verifier
    assert "Control API SDK manifest preflight against deployed MCP" in verifier
    assert "verify-agent-runtime" in verifier
    assert "resource-scoped Agent Runtime query IAM" in verifier
    assert (
        "Agent Runtime identity uses managed default access plus trace-only project access"
        in verifier
    )
    assert 'direct_roles == {"roles/cloudtrace.agent"}' in verifier
    assert "verify-agent-runtime-iam" in verifier
    assert "runtime identity has query-only effective access" in verifier
    assert "shared Agent GCP release preflight" in verifier
    assert "Control API has project service usage permission" in verifier
    assert (
        "completed one ephemeral Agent Runtime stream with create, execute, validate and delete"
        in verifier
    )
    assert "caffemate-agent-runtime-intent-preflight" in verifier
    assert "--agent-fixture-id,intent_delta-complete,--repeat,3" in verifier
    assert 'jsonPayload.preflight=true' in verifier
    assert 'payload.get("repair_attempt") == 0' in verifier
    assert 'payload.get("finish_reason") == "STOP"' in verifier
    assert "expected exactly three INTENT_DELTA generations" in verifier
    assert "INTENT_DELTA completed three managed Agent Runtime sessions without repair" in verifier
    cli = (ROOT / "api" / "app" / "cli.py").read_text(encoding="utf-8")
    assert "Agent Runtime probe operations differ from fixture" in cli
    assert "verify-first-proposal" in verifier
    assert "caffemate-first-proposal-canary" in verifier
    assert "caffemate-franchise-proposal-canary" in verifier
    assert "--cafe-type-preference=${cafe_type_preference}" in verifier
    assert "--task-timeout=25m" in verifier
    assert "FIRST_PROPOSAL completed the single RUN_PROPOSAL stage" in verifier
    assert 'jsonPayload.status=\\"verified\\"' in verifier
    assert 'rows[0]["jsonPayload"]' in verifier
    assert "expected one FIRST_PROPOSAL canary report" in verifier
    assert 'report["workflow_status"] == "SUCCEEDED"' in verifier
    assert 'report["stage_count"] == 1' in verifier
    assert 'report["max_stage_attempt"] == 1' in verifier
    assert 'report["elapsed_ms"] <= 120_000' in verifier
    assert 'report["result_freshness"] == "CURRENT"' in verifier
    assert 'report["requested_cafe_type_preference"] == "FRANCHISE_ONLY"' in verifier
    assert 'set(report.get("candidate_case_types", [])) == {"FRANCHISE"}' in verifier
    assert 'report.get("franchise_candidate_brand_ids")' in verifier
    assert 'report.get("franchise_official_citations"' in verifier
    # 사용자 의도: 추천 후보의 인용 완전성은 Python 제품 검증기가 한 번만 판정한다.
    # 배포 셸은 조건부 후보의 근거 부족을 전체 실행 실패로 다시 판정하지 않는다.
    assert '"FRANCHISE_INDIVIDUAL_ELIGIBILITY"' not in verifier
    assert '"FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE"' not in verifier
    assert 'jsonPayload.event=\\"VERTEX_AGENT_GENERATION\\"' in verifier
    assert 'jsonPayload.event=\\"AGENT_RESULT_VALIDATION\\"' in verifier
    assert "Worker has public invoker policy" in verifier
    assert "Scheduler reached Agent session cleanup with HTTP 200" in verifier


def test_agent_runtime_release_is_source_and_digest_bound() -> None:
    deploy = (ROOT / "scripts" / "deploy-agent-runtime.sh").read_text(encoding="utf-8")
    build = (ROOT / "scripts" / "build-agent-runtime-release.sh").read_text(
        encoding="utf-8"
    )
    approve = (ROOT / "scripts" / "approve-agent-runtime-release.sh").read_text(
        encoding="utf-8"
    )
    cloudbuild = (ROOT / "agents" / "cloudbuild.runtime.yaml").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-agent-runtime-deployment.sh").read_text(
        encoding="utf-8"
    )
    provenance = (ROOT / "scripts" / "build-provenance-helpers.sh").read_text(
        encoding="utf-8"
    )

    assert '"$(git rev-parse HEAD)" = "$source_revision"' in deploy
    assert "git status --porcelain" in deploy
    assert "agents/cloudbuild.runtime.yaml" in build
    assert "checkout-reviewed-source" in cloudbuild
    assert "build-agent-runtime-image" in cloudbuild
    assert "fetch --depth=1 origin '${_SOURCE_REVISION}'" in cloudbuild
    assert "/workspace/source/agents/Dockerfile.runtime" in cloudbuild
    assert "image_summary.fully_qualified_digest" in deploy
    assert "approved-${source_revision}" in approve
    assert "approved-${source_revision}" in deploy
    assert 'image=$(gcloud artifacts docker images describe "$approved_tag"' in deploy
    assert 'verified_build_id_for_image' in deploy
    assert '"$tagged_image" "$digest" "$source_revision" "$build_sa"' in deploy
    assert '[ "$approved_image" = "$image" ]' not in deploy
    assert 'expected_image=$(gcloud artifacts docker images describe "$approved_tag"' in verifier
    assert '[ "$built_image" = "$approved_image" ]' not in verifier
    assert "approval tag already points at a different digest" in approve
    assert "updateMask=description,labels,spec.classMethods" in deploy
    assert "pinned Agent Runtime GET failed with HTTP" in deploy
    assert "bootstrap must create and approve a new manifest resource" in deploy
    assert "verified_build_id_for_image" in deploy
    assert "_SOURCE_REVISION=${source_revision}" in build
    assert "verify-agent-runtime-deployment.sh" in deploy
    assert "build-id" in verifier
    assert "classMethods" in verifier
    assert "effectiveIdentity" in verifier
    assert "git-sha" in verifier
    assert 'direct_project_roles == {"roles/cloudtrace.agent"}' in verifier
    assert "def checkout_is_exact" in provenance
    assert 'args[1].strip() == expected_checkout_script' in provenance
    assert 'if len(steps) != 2' in provenance
    assert 'steps[1].get("id") != "build-agent-runtime-image"' in provenance
    assert '"/workspace/source/agents/Dockerfile.runtime"' in provenance


def test_mcp_build_provenance_uses_only_reviewed_checkout() -> None:
    cloudbuild = (ROOT / "cloudbuild.mcp-image.yaml").read_text(encoding="utf-8")
    build_preflight = (ROOT / "scripts" / "build-agent-gcp-preflight.sh").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / "scripts" / "deploy-private-mcp.sh").read_text(
        encoding="utf-8"
    )
    provenance = (ROOT / "scripts" / "build-provenance-helpers.sh").read_text(
        encoding="utf-8"
    )

    ordered_steps = [
        "checkout-reviewed-source",
        "build-mcp-image",
        "build-agent-release-preflight-image",
        "push-mcp-image",
        "push-agent-release-preflight-image",
    ]
    assert [cloudbuild.index(step) for step in ordered_steps] == sorted(
        cloudbuild.index(step) for step in ordered_steps
    )
    assert "--target, runtime" in cloudbuild
    assert "--target, release-preflight" in cloudbuild
    assert "agent-release-preflight" in cloudbuild
    assert (
        "serviceAccount: projects/${PROJECT_ID}/serviceAccounts/"
        "caffemate-backend-build@${PROJECT_ID}.iam.gserviceaccount.com"
        in cloudbuild
    )
    assert "/workspace/source/deploy/mcp.Dockerfile" in cloudbuild
    assert cloudbuild.count("/workspace/source") >= 2
    assert "def mcp_build_shape_is_exact" in provenance
    assert 'if len(steps) != 5' in provenance
    assert '"build-agent-release-preflight-image"' in provenance
    assert '"push-agent-release-preflight-image"' in provenance
    assert '"release-preflight"' in provenance
    assert '"$(git rev-parse HEAD)" != "$source_revision"' in build_preflight
    assert "git status --porcelain" in build_preflight
    assert "git ls-remote origin refs/heads/main" in build_preflight
    assert "gcloud builds submit --no-source" in build_preflight
    assert "--config=cloudbuild.mcp-image.yaml" in build_preflight
    assert "verified_build_id_for_image" in build_preflight
    assert "immutable source tag exists without trusted provenance" in build_preflight
    assert "gcloud run deploy" not in build_preflight
    assert "build-agent-gcp-preflight.sh" in deploy
    assert "gcloud builds submit" not in deploy


def test_effective_iam_verification_runs_as_the_deployed_identities() -> None:
    verifier = (ROOT / "scripts" / "verify-private-mcp.sh").read_text(
        encoding="utf-8"
    )
    smoke = (ROOT / "deploy" / "runtime-iam-smoke.mjs").read_text(encoding="utf-8")

    assert '--service-account="$mcp_sa"' in verifier
    assert "deploy/runtime-iam-smoke.mjs" in verifier
    assert "CAFFEMATE_GCP_PROJECT_ID=${project_id}" in verifier
    assert (
        "cloudresourcemanager.googleapis.com/v1/projects/${projectId}:testIamPermissions"
        in smoke
    )
    assert "aiplatform.reasoningEngines.update" in smoke
    assert "aiplatform.endpoints.predict" in smoke
    assert "aiplatform.ragCorpora.get" in smoke
    assert "aiplatform.ragCorpora.query" in smoke
    assert "aiplatform.ragFiles.get" in smoke
    assert "discoveryengine.rankingConfigs.rank" in smoke
    assert "aiplatform.ragCorpora.delete" in smoke
    assert "aiplatform.ragFiles.delete" in smoke
    assert "MCP_EFFECTIVE_IAM_OK" in smoke


def test_backend_verifier_uses_the_requested_release_source_for_mcp_provenance() -> None:
    verifier = (ROOT / "scripts" / "verify-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )

    assert 'agents/release-manifest.json' in verifier
    assert 'mcp_release_source_revision' not in verifier
    assert 'mcp_release_image' not in verifier
    assert '[ "$mcp_source_revision" = "$source_revision" ]' in verifier
    assert '[ "$mcp_image" = "$mcp_tagged_digest" ]' in verifier
    assert 'mcp:${source_revision}' in verifier
    assert '[ "$agent_release_preflight_build_id" = "$mcp_verified_build_id" ]' not in verifier


def test_shared_agent_preflight_uses_a_non_self_referential_verifier_image() -> None:
    dockerfile = (ROOT / "deploy" / "mcp.Dockerfile").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )

    runtime_section = dockerfile.split("FROM base AS runtime", 1)[1].split(
        "FROM base AS release-preflight", 1
    )[0]
    preflight_section = dockerfile.split("FROM base AS release-preflight", 1)[1]
    assert "agents/" not in runtime_section
    assert "agents/release-manifest.json ./agents/release-manifest.json" in preflight_section
    assert "agents/fixtures ./agents/fixtures" in preflight_section
    assert "agent-release-preflight:${source_revision}" in verifier
    assert '--image="$agent_release_preflight_image"' in verifier
    assert "agents/src/control-cli.ts,gcp-preflight,--json" in verifier
    assert "CAFFEMATE_AGENT_RUNTIME_RESOURCE_NAME" not in verifier
    assert "CAFFEMATE_AGENT_RUNTIME_IMAGE_URI" not in verifier


def test_build_provenance_handles_large_realistic_cloudbuild_payload(tmp_path: Path) -> None:
    revision = "d" * 40
    digest = "sha256:" + "a" * 64
    project_id = "proj-aj20-211200020328"
    region = "asia-northeast3"
    image = (
        f"{region}-docker.pkg.dev/{project_id}/caffemate-agents/"
        f"caffemate-agent-runtime:{revision}"
    )
    build_sa = (
        f"projects/{project_id}/serviceAccounts/"
        f"caffemate-backend-build@{project_id}.iam.gserviceaccount.com"
    )
    repository = "https://github.com/ai-bootcamp-team4/CaffeMate.git"
    checkout_script = "\n".join(
        [
            "git init /workspace/source",
            f"git -C /workspace/source remote add origin '{repository}'",
            f"git -C /workspace/source fetch --depth=1 origin '{revision}'",
            "git -C /workspace/source checkout --detach FETCH_HEAD",
            f'test "$(git -C /workspace/source rev-parse HEAD)" = \'{revision}\'',
        ]
    )
    payload = [
        {
            "id": "large-build-id",
            "substitutions": {"_SOURCE_REVISION": revision},
            "serviceAccount": build_sa,
            "options": {},
            "steps": [
                {
                    "id": "checkout-reviewed-source",
                    "name": "gcr.io/cloud-builders/git",
                    "entrypoint": "sh",
                    "args": ["-ceu", checkout_script],
                },
                {
                    "id": "build-agent-runtime-image",
                    "name": "gcr.io/cloud-builders/docker",
                    "args": [
                        "build",
                        "--file",
                        "/workspace/source/agents/Dockerfile.runtime",
                        "--tag",
                        image,
                        "/workspace/source",
                    ],
                },
            ],
            "results": {"images": [{"name": image, "digest": digest}]},
            "largeLogMetadata": "x" * 500_000,
        }
    ]
    builds_file = tmp_path / "builds.json"
    builds_file.write_text(json.dumps(payload), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/bin/sh\ncat "$FAKE_BUILDS_JSON_FILE"\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_BUILDS_JSON_FILE"] = str(builds_file)
    command = f"""
project_id={project_id}
region={region}
. ./scripts/build-provenance-helpers.sh
verified_build_id_for_image \\
  '{image}' \\
  '{digest}' \\
  '{revision}' \\
  '{build_sa}'
"""
    result = subprocess.run(
        ["sh", "-c", command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "large-build-id"
