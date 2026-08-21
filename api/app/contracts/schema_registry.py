import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from app.domain.errors import ContractValidationError


class VentureStateValidator(Protocol):
    def validate_venture_state(self, value: dict[str, Any]) -> None: ...


class AgentContractValidator(Protocol):
    def validate_agent_task(self, value: dict[str, Any]) -> None: ...

    def validate_agent_task_result(self, value: dict[str, Any]) -> None: ...


class McpContractValidator(Protocol):
    def validate_mcp_tool_result(self, tool_name: str, value: dict[str, Any]) -> None: ...


class CandidateContractValidator(Protocol):
    def validate_candidate_result(self, value: dict[str, Any]) -> None: ...


class ContractRegistry:
    def __init__(self, schema_directory: Path | None = None) -> None:
        self._schema_directory = schema_directory or (
            Path(__file__).resolve().parents[3] / "docs" / "contracts"
        )
        schemas = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in self._schema_directory.glob("*.schema.json")
        }
        registry = Registry()
        for schema in schemas.values():
            Draft202012Validator.check_schema(schema)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        self._validators = {
            name: Draft202012Validator(
                schema,
                registry=registry,
                format_checker=FormatChecker(),
            )
            for name, schema in schemas.items()
        }
        manifest_path = self._schema_directory / "mcp-tool-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_id = (
            "https://github.com/ai-bootcamp-team4/CaffeMate/docs/contracts/"
            "mcp-tool-manifest.json"
        )
        self._mcp_result_validators = {
            tool["name"]: Draft202012Validator(
                {"$ref": urljoin(manifest_id, tool["output_schema_ref"])},
                registry=registry,
                format_checker=FormatChecker(),
            )
            for tool in manifest["tools"]
        }

    def validate_venture_state(self, value: dict[str, Any]) -> None:
        self._validate("venture-state.schema.json", value)

    def validate_agent_task(self, value: dict[str, Any]) -> None:
        self._validate("agent-task.schema.json", value)

    def validate_agent_task_result(self, value: dict[str, Any]) -> None:
        self._validate("agent-task-result.schema.json", value)

    def validate_mcp_tool_result(self, tool_name: str, value: dict[str, Any]) -> None:
        validator = self._mcp_result_validators.get(tool_name)
        if validator is None:
            raise ContractValidationError(f"Unknown MCP tool: {tool_name}")
        self._validate_with(validator, f"MCP tool {tool_name}", value)

    def validate_candidate_result(self, value: dict[str, Any]) -> None:
        self._validate("candidate-result.schema.json", value)

    def _validate(self, contract_name: str, value: dict[str, Any]) -> None:
        self._validate_with(self._validators[contract_name], contract_name, value)

    def _validate_with(
        self,
        validator: Draft202012Validator,
        contract_name: str,
        value: dict[str, Any],
    ) -> None:
        try:
            validator.validate(value)
        except ValidationError as error:
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            raise ContractValidationError(
                f"{contract_name} rejected {location}: {error.message}"
            ) from error
