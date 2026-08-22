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

    def agent_task_result_errors(self, value: dict[str, Any]) -> list[dict[str, str]]: ...


class McpContractValidator(Protocol):
    def validate_mcp_tool_input(self, tool_name: str, value: dict[str, Any]) -> None: ...

    def validate_mcp_tool_result(self, tool_name: str, value: dict[str, Any]) -> None: ...

    def mcp_tool_version(self, tool_name: str) -> str: ...


class EvidencePlanContractValidator(McpContractValidator, Protocol):
    def validate_evidence_plan_result(self, value: dict[str, Any]) -> None: ...


class CandidateContractValidator(Protocol):
    def validate_candidate_result(self, value: dict[str, Any]) -> None: ...


class EvidenceContractValidator(Protocol):
    def validate_evidence_record(self, value: dict[str, Any]) -> None: ...


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
        role_payload_schema = schemas["agent-role-payloads.schema.json"]
        self._evidence_plan_validator = Draft202012Validator(
            {
                "$ref": (
                    f"{role_payload_schema['$id']}#/$defs/evidencePlanResult"
                )
            },
            registry=registry,
            format_checker=FormatChecker(),
        )
        manifest_path = self._schema_directory / "mcp-tool-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_id = (
            "https://github.com/ai-bootcamp-team4/CaffeMate/docs/contracts/"
            "mcp-tool-manifest.json"
        )
        self._mcp_tools = {tool["name"]: tool for tool in manifest["tools"]}
        self._mcp_input_validators = {
            tool["name"]: Draft202012Validator(
                {"$ref": urljoin(manifest_id, tool["input_schema_ref"])},
                registry=registry,
                format_checker=FormatChecker(),
            )
            for tool in manifest["tools"]
        }
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

    def agent_task_result_errors(self, value: dict[str, Any]) -> list[dict[str, str]]:
        errors = sorted(
            self._validators["agent-task-result.schema.json"].iter_errors(value),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        return [
            {
                "code": "AGENT_RESULT_SCHEMA_INVALID",
                "json_pointer": self._json_pointer(error.absolute_path),
                "message": error.message[:500],
            }
            for error in errors[:50]
        ]

    def validate_mcp_tool_input(self, tool_name: str, value: dict[str, Any]) -> None:
        validator = self._mcp_input_validators.get(tool_name)
        if validator is None:
            raise ContractValidationError(f"Unknown MCP tool: {tool_name}")
        self._validate_with(validator, f"MCP tool {tool_name} input", value)

    def validate_mcp_tool_result(self, tool_name: str, value: dict[str, Any]) -> None:
        validator = self._mcp_result_validators.get(tool_name)
        if validator is None:
            raise ContractValidationError(f"Unknown MCP tool: {tool_name}")
        self._validate_with(validator, f"MCP tool {tool_name}", value)

    def mcp_tool_version(self, tool_name: str) -> str:
        tool = self._mcp_tools.get(tool_name)
        if tool is None:
            raise ContractValidationError(f"Unknown MCP tool: {tool_name}")
        return str(tool["version"])

    def validate_evidence_plan_result(self, value: dict[str, Any]) -> None:
        self._validate_with(
            self._evidence_plan_validator,
            "deterministic Evidence Plan result",
            value,
        )

    def validate_candidate_result(self, value: dict[str, Any]) -> None:
        self._validate("candidate-result.schema.json", value)

    def validate_evidence_record(self, value: dict[str, Any]) -> None:
        self._validate("evidence-record.schema.json", value)

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

    @staticmethod
    def _json_pointer(path: Any) -> str:
        encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
        return "" if not encoded else "/" + "/".join(encoded)
