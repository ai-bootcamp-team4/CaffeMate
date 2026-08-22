class StageExecutionError(RuntimeError):
    """Stable stage failure safe to persist without provider details."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
