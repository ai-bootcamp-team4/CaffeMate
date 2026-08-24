from kfp import dsl
from kfp.dsl import InputPath, OutputPath


GOOGLE_CLOUD_CLI_IMAGE = "gcr.io/google.com/cloudsdktool/google-cloud-cli:slim"


@dsl.container_component
def run_live_e2e_evaluation(
    project_id: str,
    region: str,
    job_name: str,
    report_uri: str,
    live_e2e_report: OutputPath(str),
) -> dsl.ContainerSpec:
    # 사용자 의도: Vertex 평가가 로컬 테스트 결과를 재사용하지 않고 배포된 Cloud Run
    # Job을 실행한 뒤 그 실행이 남긴 운영 보고서를 직접 품질 판정에 사용해야 한다.
    return dsl.ContainerSpec(
        image=GOOGLE_CLOUD_CLI_IMAGE,
        command=["bash", "-ceu"],
        args=[
            """
project_id="$1"
region="$2"
job_name="$3"
report_uri="$4"
report_path="$5"
gcloud run jobs execute "$job_name" \
  --project="$project_id" \
  --region="$region" \
  --update-env-vars="CAFFEMATE_EVALUATION_REPORT_URI=$report_uri" \
  --wait \
  --quiet
gcloud storage cp "$report_uri" "$report_path"
""",
            "--",
            project_id,
            region,
            job_name,
            report_uri,
            live_e2e_report,
        ],
    )


@dsl.component(base_image="python:3.13-slim")
def apply_quality_gate(
    live_e2e_report: InputPath(str),
    gate_report: OutputPath(str),
) -> None:
    import json

    with open(live_e2e_report, encoding="utf-8") as stream:
        live_e2e = json.load(stream)
    passed = (
        live_e2e["summary"]["total_cases"] == 15
        and live_e2e["summary"]["failed_cases"] == 0
        and live_e2e["passed"] is True
    )
    result = {
        "passed": passed,
        "live_e2e_pass_rate": live_e2e["summary"]["pass_rate"],
        "live_e2e_source_revision": live_e2e["source_revision"],
    }
    with open(gate_report, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    if not passed:
        raise RuntimeError("CaffeMate evaluation gate failed")


@dsl.pipeline(name="caffemate-operational-evaluation")
def caffemate_evaluation_pipeline(
    project_id: str = "proj-aj20-211200020328",
    region: str = "asia-northeast3",
    live_job_name: str = "caffemate-live-e2e-evaluation",
    live_report_uri: str = "gs://proj-aj20-211200020328-caffemate-evaluation/reports/manual.json",
) -> None:
    live = run_live_e2e_evaluation(
        project_id=project_id,
        region=region,
        job_name=live_job_name,
        report_uri=live_report_uri,
    )
    apply_quality_gate(
        live_e2e_report=live.outputs["live_e2e_report"],
    )
