from pydantic import Field

from app.domain.models import StrictModel


class ResultExplanationRequest(StrictModel):
    result_bundle_id: str = Field(min_length=1, max_length=128)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=500)


class ExplanationEvidence(StrictModel):
    evidence_id: str
    label: str
    value: str | None = None
    source_title: str | None = None
    source_ref: str | None = None
    data_date: str | None = None
    caveat: str | None = None


class ResultExplanation(StrictModel):
    explanation_id: str
    result_bundle_id: str
    candidate_id: str
    intent: str
    conclusion: str
    reasons: list[str]
    evidence: list[ExplanationEvidence]
    unknowns: list[str]
    decision_change_conditions: list[str]
    suggested_action: str
    state_changed: bool = False
