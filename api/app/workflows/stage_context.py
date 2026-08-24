from typing import Any

from app.domain.models import StrictModel, VentureState
from app.workflows.models import StageLease


class StageContext(StrictModel):
    """Compatibility DTO for Agent task builders outside the workflow controller."""

    lease: StageLease
    project_id: str
    state: VentureState
    dependency_results: dict[str, dict[str, Any]]
    document_claims: list[dict[str, Any]] = []
