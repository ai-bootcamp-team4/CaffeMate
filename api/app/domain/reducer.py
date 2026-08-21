from app.domain.errors import StateVersionConflictError
from app.domain.events import DomainEvent, OnboardingConfirmed, ProjectCreated
from app.domain.models import (
    AreaResolutionStatus,
    AreaState,
    CoverageProfile,
    VentureState,
    VentureStatus,
)


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

    raise AssertionError(f"Unhandled event type: {type(event).__name__}")
