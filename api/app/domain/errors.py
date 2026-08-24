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


class ExternalExecutionUnavailableError(DomainError):
    code = "EXTERNAL_EXECUTION_UNAVAILABLE"


class ResultNotFoundError(DomainError):
    code = "RESULT_NOT_FOUND"


class FeedbackPreviewNotFoundError(DomainError):
    code = "FEEDBACK_PREVIEW_NOT_FOUND"


class FeedbackPreconditionError(DomainError):
    code = "FEEDBACK_PRECONDITION_FAILED"


class ResultExplanationPreconditionError(DomainError):
    code = "RESULT_EXPLANATION_PRECONDITION_FAILED"


class CandidateSelectionPreconditionError(DomainError):
    code = "CANDIDATE_SELECTION_PRECONDITION_FAILED"


class DocumentNotFoundError(DomainError):
    code = "DOCUMENT_NOT_FOUND"


class DocumentPreconditionError(DomainError):
    code = "DOCUMENT_PRECONDITION_FAILED"
