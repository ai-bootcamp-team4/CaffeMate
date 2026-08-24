"""Vertex 평가 파이프라인은 실제 Cloud Run 평가 결과를 품질 판정에 사용한다."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pipeline_invokes_live_job_and_checks_exactly_fifteen_cases() -> None:
    source = (ROOT / "pipelines" / "vertex_evaluation_pipeline.py").read_text(encoding="utf-8")

    assert "caffemate-live-e2e-evaluation" in source
    assert "gcloud run jobs execute" in source
    assert 'total_cases"] == 15' in source
    assert 'failed_cases"] == 0' in source
    assert "live_e2e_report" in source
