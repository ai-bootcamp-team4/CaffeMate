class DomainError(Exception):
    """Base class for errors safe to map to a public error code."""

    code = "DOMAIN_ERROR"


class ProjectNotFoundError(DomainError):
    code = "PROJECT_NOT_FOUND"


class StateVersionConflictError(DomainError):
    code = "STATE_VERSION_CONFLICT"


class IdempotencyKeyReusedError(DomainError):
    code = "IDEMPOTENCY_KEY_REUSED"


class ContractValidationError(DomainError):
    code = "CONTRACT_VALIDATION_FAILED"


class AuthenticationUnavailableError(DomainError):
    code = "AUTHENTICATION_UNAVAILABLE"


class UnauthenticatedError(DomainError):
    code = "UNAUTHENTICATED"


class PersistenceUnavailableError(DomainError):
    code = "PERSISTENCE_UNAVAILABLE"


class WorkflowNotFoundError(DomainError):
    code = "WORKFLOW_NOT_FOUND"


class WorkflowPreconditionError(DomainError):
    code = "WORKFLOW_PRECONDITION_FAILED"


class FirstProposalConfigurationUnavailableError(DomainError):
    code = "FIRST_PROPOSAL_CONFIGURATION_UNAVAILABLE"

    def __init__(self, missing_stage_codes: list[str]) -> None:
        super().__init__("FIRST_PROPOSAL stage composition is incomplete")
        self.missing_stage_codes = list(missing_stage_codes)


class StageLeaseRejectedError(DomainError):
    code = "STAGE_LEASE_REJECTED"


class ExternalExecutionUnavailableError(DomainError):
    code = "EXTERNAL_EXECUTION_UNAVAILABLE"


class ResultNotFoundError(DomainError):
    code = "RESULT_NOT_FOUND"


class FeedbackPreviewNotFoundError(DomainError):
    code = "FEEDBACK_PREVIEW_NOT_FOUND"


class FeedbackPreconditionError(DomainError):
    code = "FEEDBACK_PRECONDITION_FAILED"
