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
