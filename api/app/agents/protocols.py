from typing import Any, Protocol


class AgentRuntime(Protocol):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]: ...
