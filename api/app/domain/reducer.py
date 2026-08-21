from app.domain.errors import StateVersionConflictError
from app.domain.events import (
    DomainEvent,
    FeedbackChangeConfirmed,
    OnboardingConfirmed,
    ProjectCreated,
)
from app.domain.models import (
    AreaResolutionStatus,
    AreaState,
    CoverageProfile,
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

    raise AssertionError(f"Unhandled event type: {type(event).__name__}")
