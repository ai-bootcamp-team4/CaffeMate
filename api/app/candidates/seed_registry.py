import hashlib
import json
from pathlib import Path

import rfc8785
from pydantic import Field, model_validator

from app.domain.models import FounderState, OperationMode, StrictModel


class AllowedParameter(StrictModel):
    field_path: str = Field(min_length=1)
    value_kind: str = Field(
        pattern=r"^(STRING|INTEGER|DECIMAL|BOOLEAN|DATE|DATE_TIME|MONEY_RANGE)$"
    )
    unit: str | None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "AllowedParameter":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("allowed parameter minimum exceeds maximum")
        return self


class IndependentSeedDefinition(StrictModel):
    model_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1)
    allowed_operation_modes: list[OperationMode] = Field(min_length=1)
    minimum_own_funds_krw: int | None = Field(default=None, ge=0)
    allowed_parameters: list[AllowedParameter] = Field(min_length=1)
    support_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_values(self) -> "IndependentSeedDefinition":
        if len(self.allowed_operation_modes) != len(set(self.allowed_operation_modes)):
            raise ValueError("allowed operation modes must be unique")
        paths = [value.field_path for value in self.allowed_parameters]
        if len(paths) != len(set(paths)):
            raise ValueError("allowed parameter paths must be unique")
        if len(self.support_refs) != len(set(self.support_refs)):
            raise ValueError("support refs must be unique")
        return self


class IndependentSeedRegistryDocument(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0\.0$")
    models: list[IndependentSeedDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_models(self) -> "IndependentSeedRegistryDocument":
        model_ids = [value.model_id for value in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("independent seed model ids must be unique")
        return self


class IndependentSeedRegistry:
    def __init__(self, document: IndependentSeedRegistryDocument) -> None:
        self._document = document
        digest = hashlib.sha256(rfc8785.dumps(document.model_dump(mode="json"))).hexdigest()
        self.registry_id = f"independent-seeds-{digest[:40]}"

    @classmethod
    def load(cls, path: Path) -> "IndependentSeedRegistry":
        value = json.loads(path.read_text(encoding="utf-8"))
        return cls(IndependentSeedRegistryDocument.model_validate(value))

    @classmethod
    def load_default(cls) -> "IndependentSeedRegistry":
        return cls.load(Path(__file__).with_name("independent_seed_registry.json"))

    def select(self, founder: FounderState) -> list[IndependentSeedDefinition]:
        return [
            model
            for model in self._document.models
            if founder.operation_mode in model.allowed_operation_modes
            and (
                model.minimum_own_funds_krw is None
                or founder.own_funds_krw >= model.minimum_own_funds_krw
            )
        ]
