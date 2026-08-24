"""운영 평가는 revision 고정 Job과 실제 Vertex PipelineJob으로 배포되어야 한다."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_live_evaluation_deployment_pins_source_and_required_services() -> None:
    deploy = (ROOT / "scripts" / "deploy-live-evaluation.sh").read_text(encoding="utf-8")

    assert "caffemate-live-e2e-evaluation" in deploy
    assert "verify-live-evaluation" in deploy
    assert "CAFFEMATE_SOURCE_REVISION" in deploy
    assert "CAFFEMATE_EVALUATION_REPORT_URI" in deploy
    assert "--max-retries=0" in deploy
    assert "--task-timeout=60m" in deploy
    assert "roles/run.invoker" in deploy
    assert "roles/storage.objectAdmin" in deploy
    assert "roles/aiplatform.user" in deploy


def test_vertex_submission_waits_for_success_and_verifies_fifteen_case_report() -> None:
    submit = (ROOT / "scripts" / "submit-operational-evaluation.sh").read_text(encoding="utf-8")

    assert "pipelineJobs" in submit
    assert "caffemate-operational-evaluation.json" in submit
    assert "PIPELINE_STATE_SUCCEEDED" in submit
    assert 'summary["total_cases"] == 15' in submit
    assert 'summary["failed_cases"] == 0' in submit
    assert "console.cloud.google.com/vertex-ai/pipelines" in submit
