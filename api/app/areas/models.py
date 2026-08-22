from pydantic import Field

from app.domain.models import (
    AreaIdentity,
    CandidateSetCompleteness,
    StrictModel,
)


class AreaSearchCandidate(AreaIdentity):
    selection_token: str = Field(min_length=1)


class AreaSearchRequest(StrictModel):
    query: str = Field(min_length=2, max_length=100)
    limit: int = Field(default=10, ge=1, le=20)


class AreaSearchResult(StrictModel):
    query: str
    status: str
    completeness: CandidateSetCompleteness
    candidates: list[AreaSearchCandidate]
    missing_fields: list[str] = Field(default_factory=list)
    source_trace: list[dict[str, object]] = Field(default_factory=list)
