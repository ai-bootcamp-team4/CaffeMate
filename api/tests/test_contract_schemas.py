import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_all_shared_contracts_are_valid_draft_2020_12_schemas() -> None:
    contract_directory = Path(__file__).resolve().parents[2] / "docs" / "contracts"
    schemas = sorted(contract_directory.glob("*.schema.json"))

    assert schemas
    for schema_path in schemas:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
