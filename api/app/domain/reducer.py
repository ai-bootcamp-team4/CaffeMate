from app.domain.errors import StateVersionConflictError
from app.domain.events import (
    CandidateSelected,
    DocumentClaimsApplied,
    DomainEvent,
    FeedbackChangeConfirmed,
    OnboardingConfirmed,
    ProjectCreated,
)
from app.domain.models import (
    AreaResolutionStatus,
    AreaState,
    CaseMaturity,
    CaseStatus,
    CaseType,
    CoverageProfile,
    FranchiseEligibility,
    VentureCase,
    VentureState,
    VentureStatus,
)
from app.feedback.intent import apply_feedback_operations


def reduce_venture_state(
    current: VentureState | None,
    event: DomainEvent,
) -> VentureState | None:
    """The only domain function allowed to create or change authoritative State."""
    if isinstance(event, ProjectCreated):
        if current is not None:
            raise StateVersionConflictError("Project creation cannot replace existing State")
        return None

    if isinstance(event, OnboardingConfirmed):
        if current is not None:
            raise StateVersionConflictError("Onboarding is already confirmed")
        return VentureState(
            project_id=event.project_id,
            user_id=event.user_id,
            state_version=1,
            status=VentureStatus.ANALYZING,
            founder=event.founder,
            area=AreaState(
                resolution_status=AreaResolutionStatus.UNRESOLVED,
                coverage_profile=CoverageProfile.N0_NATIONWIDE_FACTS,
                unavailable_fields=[],
            ),
            venture_cases=[],
            updated_at=event.occurred_at,
        )

    if isinstance(event, FeedbackChangeConfirmed):
        if current is None or current.state_version != event.expected_state_version:
            raise StateVersionConflictError("Feedback expected State version does not match")
        if current.project_id != event.project_id or current.user_id != event.user_id:
            raise StateVersionConflictError("Feedback crossed the State aggregate boundary")
        return current.model_copy(
            update={
                "state_version": current.state_version + 1,
                "status": VentureStatus.RECOMPUTE_REQUIRED,
                "founder": apply_feedback_operations(current.founder, event.operations),
                "updated_at": event.occurred_at,
            },
            deep=True,
        )

    if isinstance(event, CandidateSelected):
        if current is None or current.state_version != event.expected_state_version:
            raise StateVersionConflictError("Candidate selection State version does not match")
        if current.project_id != event.project_id or current.user_id != event.user_id:
            raise StateVersionConflictError("Candidate selection crossed State boundary")
        candidate_id = event.candidate.get("candidate_id")
        case_type = event.candidate.get("case_type")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise StateVersionConflictError("Candidate selection has no candidate id")
        if case_type not in {"INDEPENDENT", "FRANCHISE"}:
            raise StateVersionConflictError("Candidate selection case type is invalid")
        franchise = event.candidate.get("franchise")
        eligibility = FranchiseEligibility.NOT_APPLICABLE
        if case_type == "FRANCHISE":
            raw_eligibility = franchise.get("eligibility") if isinstance(franchise, dict) else None
            if not isinstance(raw_eligibility, str):
                raise StateVersionConflictError(
                    "Franchise candidate eligibility is missing"
                )
            eligibility = FranchiseEligibility(raw_eligibility)
        replacement = VentureCase(
            case_id=candidate_id,
            case_type=CaseType(case_type),
            maturity=CaseMaturity.CANDIDATE,
            status=CaseStatus.SELECTED,
            display_name=event.candidate.get("display_name"),
            franchise_eligibility=eligibility,
            confirmed_claim_ids=list(event.candidate.get("evidence_refs", [])),
            assumption_ids=list(event.candidate.get("assumption_refs", [])),
            missing_fields=[
                value["field"]
                for value in event.candidate.get("missing_fields", [])
                if isinstance(value, dict) and isinstance(value.get("field"), str)
            ],
        )
        cases = []
        replaced = False
        for venture_case in current.venture_cases:
            if venture_case.case_id == candidate_id:
                cases.append(replacement)
                replaced = True
            elif venture_case.status == CaseStatus.SELECTED:
                cases.append(
                    venture_case.model_copy(
                        update={"status": CaseStatus.CONDITIONALLY_REVIEWABLE}
                    )
                )
            else:
                cases.append(venture_case)
        if not replaced:
            cases.append(replacement)
        return current.model_copy(
            update={
                "state_version": current.state_version + 1,
                "status": VentureStatus.WAITING_FOR_HUMAN,
                "active_case_id": candidate_id,
                "venture_cases": cases,
                "updated_at": event.occurred_at,
            },
            deep=True,
        )

    if isinstance(event, DocumentClaimsApplied):
        if current is None or current.state_version != event.expected_state_version:
            raise StateVersionConflictError("Document claims expected State version does not match")
        if current.project_id != event.project_id or current.user_id != event.user_id:
            raise StateVersionConflictError("Document claims crossed State boundary")
        if current.active_case_id != event.active_case_id:
            raise StateVersionConflictError("Document claims crossed the active Venture Case")
        cases = []
        found = False
        for venture_case in current.venture_cases:
            if venture_case.case_id != event.active_case_id:
                cases.append(venture_case)
                continue
            found = True
            cases.append(
                venture_case.model_copy(
                    update={
                        "maturity": CaseMaturity.DOCUMENT_LINKED,
                        "confirmed_claim_ids": sorted(
                            set(venture_case.confirmed_claim_ids) | set(event.confirmed_claim_ids)
                        ),
                    }
                )
            )
        if not found:
            raise StateVersionConflictError("Active Venture Case does not exist")
        return current.model_copy(
            update={
                "state_version": current.state_version + 1,
                "status": (
                    VentureStatus.WAITING_FOR_HUMAN
                    if event.conflict_ids
                    else VentureStatus.RECOMPUTE_REQUIRED
                ),
                "venture_cases": cases,
                "conflict_ids": sorted(set(current.conflict_ids) | set(event.conflict_ids)),
                "updated_at": event.occurred_at,
            },
            deep=True,
        )

    raise AssertionError(f"Unhandled event type: {type(event).__name__}")
