"""15개 합성 창업자 조건으로 실제 운영 제안 경로를 반복 검증한다."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol

from google.cloud import storage  # type: ignore[attr-defined]

from app.domain.models import (
    AreaMappingStatus,
    AreaResolutionStatus,
    AreaScopeType,
    AreaState,
    BorrowingIntent,
    CafeTypePreference,
    CandidateSetCompleteness,
    CoverageProfile,
    FounderState,
    OperationMode,
)
from app.verification.first_proposal import (
    FirstProposalCanaryError,
    FirstProposalCanaryReport,
)


class FirstProposalRunner(Protocol):
    def run(
        self,
        *,
        founder: FounderState,
        area: AreaState,
    ) -> FirstProposalCanaryReport: ...


@dataclass(frozen=True)
class LiveEvaluationScenario:
    case_id: str
    profile_id: str
    founder: FounderState
    area: AreaState


@dataclass(frozen=True)
class LiveEvaluationCaseResult:
    case_id: str
    profile_id: str
    cafe_type_preference: str
    status: str
    workflow_status: str | None
    candidate_count: int
    candidate_case_types: tuple[str, ...]
    franchise_candidate_count: int
    result_freshness: str | None
    elapsed_ms: int
    failure_code: str | None


@dataclass(frozen=True)
class LiveEvaluationSummary:
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float


@dataclass(frozen=True)
class LiveEvaluationReport:
    schema_version: str
    generated_at: str
    source_revision: str
    execution_mode: str
    passed: bool
    summary: LiveEvaluationSummary
    cases: tuple[LiveEvaluationCaseResult, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def as_markdown(self) -> str:
        rows = "\n".join(
            f"| {item.case_id} | {item.profile_id} | {item.cafe_type_preference} | "
            f"{item.status} | {item.candidate_count} | "
            f"{item.elapsed_ms}ms |"
            for item in self.cases
        )
        return (
            "# CaffeMate 실제 운영 평가 보고서\n\n"
            f"- 생성 시각: {self.generated_at}\n"
            f"- 소스 revision: `{self.source_revision}`\n"
            "- 실행 범위: 실제 운영 경로(Database → MCP → Agent Runtime → Gemini → Result)\n"
            f"- 통과: **{self.summary.passed_cases}/{self.summary.total_cases}**\n\n"
            "| 사례 | 프로필 | 비교 유형 | 결과 | 후보 수 | 시간 |\n"
            "|---|---|---|---:|---:|---:|\n"
            f"{rows}\n"
        )


def _area() -> AreaState:
    return AreaState(
        resolution_status=AreaResolutionStatus.RESOLVED,
        area_id="legal-dong:1144012300",
        scope_type=AreaScopeType.LEGAL_DONG,
        administrative_code="1144012300",
        legal_dong_code="1144012300",
        administrative_dong_codes=[],
        mapping_status=AreaMappingStatus.UNVERIFIED,
        candidate_set_completeness=CandidateSetCompleteness.UNVERIFIED,
        source_revision="MOIS_LEGAL_DONG_20260301",
        display_name="서울특별시 마포구 망원동",
        boundary_version=None,
        coverage_profile=CoverageProfile.R2_REGIONAL_CONNECTOR,
        unavailable_fields=[],
    )


def _scenario(
    case_number: int,
    profile_id: str,
    *,
    funds: int,
    borrowing: BorrowingIntent,
    operation: OperationMode,
    preference: CafeTypePreference,
) -> LiveEvaluationScenario:
    return LiveEvaluationScenario(
        case_id=f"E2E-{case_number:03d}",
        profile_id=profile_id,
        founder=FounderState(
            target_area_input="서울특별시 마포구 망원동",
            own_funds_krw=funds,
            borrowing_intent=borrowing,
            cafe_type_preference=preference,
            operation_mode=operation,
            preferences=[profile_id],
        ),
        area=_area(),
    )


_PROFILES = (
    ("low-funds-direct", 50_000_000, BorrowingIntent.NO, OperationMode.DIRECT_FULL_TIME),
    ("mid-funds-direct", 70_000_000, BorrowingIntent.UNDECIDED, OperationMode.DIRECT_FULL_TIME),
    ("loan-ready-direct", 100_000_000, BorrowingIntent.YES, OperationMode.DIRECT_FULL_TIME),
    ("staff-led", 150_000_000, BorrowingIntent.YES, OperationMode.EMPLOYEE_LED),
    ("part-time-owner", 200_000_000, BorrowingIntent.NO, OperationMode.DIRECT_PART_TIME),
)

LIVE_EVALUATION_SCENARIOS = tuple(
    _scenario(
        index,
        profile_id,
        funds=funds,
        borrowing=borrowing,
        operation=operation,
        preference=preference,
    )
    for index, (preference, profile) in enumerate(
        ((preference, profile) for preference in CafeTypePreference for profile in _PROFILES),
        start=1,
    )
    for profile_id, funds, borrowing, operation in (profile,)
)


class LiveEvaluationRunner:
    def __init__(self, *, canary: FirstProposalRunner, source_revision: str) -> None:
        self._canary = canary
        self._source_revision = source_revision

    def run(self) -> LiveEvaluationReport:
        # 사용자 의도: 하나가 실패해도 나머지 평가를 실행해 전체 결함 분포를 보고하되,
        # 최종 품질 판정은 15개가 모두 통과한 경우에만 성공으로 둔다.
        cases: list[LiveEvaluationCaseResult] = []
        for scenario in LIVE_EVALUATION_SCENARIOS:
            try:
                result = self._canary.run(founder=scenario.founder, area=scenario.area)
                cases.append(
                    LiveEvaluationCaseResult(
                        case_id=scenario.case_id,
                        profile_id=scenario.profile_id,
                        cafe_type_preference=scenario.founder.cafe_type_preference.value,
                        status="PASSED",
                        workflow_status=result.workflow_status,
                        candidate_count=result.candidate_count,
                        candidate_case_types=result.candidate_case_types,
                        franchise_candidate_count=len(result.franchise_candidate_brand_ids),
                        result_freshness=result.result_freshness,
                        elapsed_ms=result.elapsed_ms,
                        failure_code=None,
                    )
                )
            except FirstProposalCanaryError as error:
                failed_candidate_count = error.details.get("candidate_count")
                cases.append(
                    LiveEvaluationCaseResult(
                        case_id=scenario.case_id,
                        profile_id=scenario.profile_id,
                        cafe_type_preference=scenario.founder.cafe_type_preference.value,
                        status="FAILED",
                        workflow_status=(
                            str(error.details["workflow_status"])
                            if "workflow_status" in error.details
                            else None
                        ),
                        candidate_count=(
                            failed_candidate_count
                            if isinstance(failed_candidate_count, int)
                            else 0
                        ),
                        candidate_case_types=(),
                        franchise_candidate_count=0,
                        result_freshness=None,
                        elapsed_ms=0,
                        failure_code=error.code,
                    )
                )
        passed_cases = sum(item.status == "PASSED" for item in cases)
        summary = LiveEvaluationSummary(
            total_cases=len(cases),
            passed_cases=passed_cases,
            failed_cases=len(cases) - passed_cases,
            pass_rate=passed_cases / len(cases),
        )
        return LiveEvaluationReport(
            schema_version="1.0.0",
            generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            source_revision=self._source_revision,
            execution_mode="PRODUCTION_E2E",
            passed=summary.failed_cases == 0 and summary.total_cases == 15,
            summary=summary,
            cases=tuple(cases),
        )


class GoogleCloudLiveEvaluationReportStore:
    def __init__(self, client: storage.Client | None = None) -> None:
        self._client = client or storage.Client()

    def write(self, *, report: LiveEvaluationReport, report_uri: str) -> tuple[str, str]:
        # 사용자 의도: 발표와 운영 검증에서 같은 실행 결과를 다시 읽을 수 있도록
        # 기계 판독 JSON과 사람이 읽는 Markdown을 한 revision 경로에 함께 저장한다.
        if not report_uri.startswith("gs://") or not report_uri.endswith(".json"):
            raise ValueError("evaluation report URI must be a gs:// URI ending in .json")
        bucket_and_name = report_uri.removeprefix("gs://")
        bucket_name, separator, blob_name = bucket_and_name.partition("/")
        if not separator or not bucket_name or not blob_name:
            raise ValueError("evaluation report URI must include bucket and object name")
        bucket = self._client.bucket(bucket_name)
        bucket.blob(blob_name).upload_from_string(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            content_type="application/json; charset=utf-8",
        )
        markdown_uri = report_uri.removesuffix(".json") + ".md"
        markdown_name = blob_name.removesuffix(".json") + ".md"
        bucket.blob(markdown_name).upload_from_string(
            report.as_markdown(),
            content_type="text/markdown; charset=utf-8",
        )
        return report_uri, markdown_uri
