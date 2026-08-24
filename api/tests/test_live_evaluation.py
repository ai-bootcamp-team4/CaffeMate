"""운영 평가는 15개의 실제 시나리오를 개별 결과로 남겨야 한다."""

from app.domain.models import CafeTypePreference
from app.verification.first_proposal import FirstProposalCanaryReport
from app.verification.live_evaluation import (
    LIVE_EVALUATION_SCENARIOS,
    GoogleCloudLiveEvaluationReportStore,
    LiveEvaluationRunner,
)


class RecordingCanary:
    def __init__(self) -> None:
        self.preferences: list[CafeTypePreference] = []

    def run(self, **kwargs: object) -> FirstProposalCanaryReport:
        founder = kwargs["founder"]
        preference = founder.cafe_type_preference  # type: ignore[union-attr]
        self.preferences.append(preference)
        expected = {
            CafeTypePreference.OPEN_TO_BOTH: ("FRANCHISE", "INDEPENDENT"),
            CafeTypePreference.INDEPENDENT_ONLY: ("INDEPENDENT",),
            CafeTypePreference.FRANCHISE_ONLY: ("FRANCHISE",),
        }[preference]
        return FirstProposalCanaryReport(
            status="verified",
            requested_cafe_type_preference=preference.value,
            workflow_status="SUCCEEDED",
            stage_count=1,
            max_stage_attempt=1,
            elapsed_ms=100,
            candidate_count=len(expected),
            candidate_case_types=expected,
            franchise_candidate_brand_ids=(
                ("kr-compose-coffee",) if "FRANCHISE" in expected else ()
            ),
            market_signals=(),
            result_freshness="CURRENT",
        )


def test_live_evaluation_defines_fifteen_unique_operational_scenarios() -> None:
    assert len(LIVE_EVALUATION_SCENARIOS) == 15
    assert len({item.case_id for item in LIVE_EVALUATION_SCENARIOS}) == 15
    assert {item.founder.cafe_type_preference for item in LIVE_EVALUATION_SCENARIOS} == set(
        CafeTypePreference
    )


def test_live_evaluation_runs_every_scenario_and_requires_every_case_to_pass() -> None:
    canary = RecordingCanary()

    report = LiveEvaluationRunner(canary=canary, source_revision="a" * 40).run()

    assert report.summary.total_cases == 15
    assert report.summary.passed_cases == 15
    assert report.summary.failed_cases == 0
    assert report.summary.pass_rate == 1.0
    assert report.passed is True
    assert len(report.cases) == 15
    assert all(item.status == "PASSED" for item in report.cases)
    assert all(item.workflow_status == "SUCCEEDED" for item in report.cases)
    assert all(item.result_freshness == "CURRENT" for item in report.cases)
    assert report.source_revision == "a" * 40
    assert len(canary.preferences) == 15


def test_live_evaluation_report_has_human_readable_markdown() -> None:
    report = LiveEvaluationRunner(canary=RecordingCanary(), source_revision="b" * 40).run()

    markdown = report.as_markdown()

    assert "15/15" in markdown
    assert "실제 운영 경로" in markdown
    assert "E2E-015" in markdown


class RecordingBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.uploads: list[tuple[str, str]] = []

    def upload_from_string(self, value: str, *, content_type: str) -> None:
        self.uploads.append((value, content_type))


class RecordingBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, RecordingBlob] = {}

    def blob(self, name: str) -> RecordingBlob:
        return self.blobs.setdefault(name, RecordingBlob(name))


class RecordingStorage:
    def __init__(self) -> None:
        self.bucket_name: str | None = None
        self.recording_bucket = RecordingBucket()

    def bucket(self, name: str) -> RecordingBucket:
        self.bucket_name = name
        return self.recording_bucket


def test_live_evaluation_writes_json_and_markdown_to_one_gcs_revision() -> None:
    client = RecordingStorage()
    report = LiveEvaluationRunner(canary=RecordingCanary(), source_revision="c" * 40).run()

    uris = GoogleCloudLiveEvaluationReportStore(client=client).write(  # type: ignore[arg-type]
        report=report,
        report_uri="gs://evaluation/reports/run-1.json",
    )

    assert uris == (
        "gs://evaluation/reports/run-1.json",
        "gs://evaluation/reports/run-1.md",
    )
    assert client.bucket_name == "evaluation"
    assert set(client.recording_bucket.blobs) == {
        "reports/run-1.json",
        "reports/run-1.md",
    }
    assert '"total_cases": 15' in client.recording_bucket.blobs["reports/run-1.json"].uploads[0][0]
    assert "15/15" in client.recording_bucket.blobs["reports/run-1.md"].uploads[0][0]
