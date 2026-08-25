from enum import StrEnum
from typing import Protocol


class FirstProposalProgressStage(StrEnum):
    EVIDENCE_RETRIEVAL = "EVIDENCE_RETRIEVAL"
    EVIDENCE_ASSESS = "EVIDENCE_ASSESS"
    PROPOSAL_GENERATION = "PROPOSAL_GENERATION"
    FINANCE_AND_RANK = "FINANCE_AND_RANK"
    CANDIDATE_AUDIT = "CANDIDATE_AUDIT"
    COMMIT_RESULT = "COMMIT_RESULT"


FIRST_PROPOSAL_PROGRESS_STAGES: tuple[FirstProposalProgressStage, ...] = (
    FirstProposalProgressStage.EVIDENCE_RETRIEVAL,
    FirstProposalProgressStage.EVIDENCE_ASSESS,
    FirstProposalProgressStage.PROPOSAL_GENERATION,
    FirstProposalProgressStage.FINANCE_AND_RANK,
    FirstProposalProgressStage.CANDIDATE_AUDIT,
    FirstProposalProgressStage.COMMIT_RESULT,
)


class WorkflowProgressSink(Protocol):
    def start(self, stage: FirstProposalProgressStage) -> None: ...

    def complete(self, stage: FirstProposalProgressStage) -> None: ...


class NullWorkflowProgressSink:
    def start(self, stage: FirstProposalProgressStage) -> None:
        del stage

    def complete(self, stage: FirstProposalProgressStage) -> None:
        del stage