import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from app.domain.errors import ContractValidationError


class ContractRegistry:
    def __init__(self, schema_directory: Path | None = None) -> None:
        self._schema_directory = schema_directory or (
            Path(__file__).resolve().parents[3] / "docs" / "contracts"
        )
        schema_path = self._schema_directory / "venture-state.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self._venture_state = Draft202012Validator(schema, format_checker=FormatChecker())

    def validate_venture_state(self, value: dict[str, Any]) -> None:
        try:
            self._venture_state.validate(value)
        except ValidationError as error:
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            raise ContractValidationError(
                f"venture-state.schema.json rejected {location}: {error.message}"
            ) from error
