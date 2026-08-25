import hashlib
import json
from enum import StrEnum
from pathlib import Path

import rfc8785
from pydantic import Field, model_validator

from app.domain.models import FounderState, OperationMode, StrictModel
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CostCategory,
    MoneyRange,
)


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


class CommercialPropertyClass(StrEnum):
    SMALL_RETAIL = "SMALL_RETAIL"
    MEDIUM_LARGE_RETAIL = "MEDIUM_LARGE_RETAIL"
    STRATA_RETAIL = "STRATA_RETAIL"


class SpaceProfile(StrictModel):
    low: int = Field(gt=0)
    base: int = Field(gt=0)
    high: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "SpaceProfile":
        if not self.low <= self.base <= self.high:
            raise ValueError("space profile must satisfy low <= base <= high")
        return self


class IndependentFinanceProfile(StrictModel):
    """Versioned, explicitly provisional finance inputs for a seed model.

    These values keep the first proposal calculable before a user supplies an
    actual lease or quote. They are assumptions, never external Evidence.
    """

    cost_ranges: dict[CostCategory, MoneyRange]
    contribution_margin_bps: int = Field(ge=1, le=10_000)
    operating_days_per_month: int = Field(ge=1, le=31)
    average_ticket_krw: int = Field(ge=1)
    space_profile_sqm: SpaceProfile
    commercial_property_class: CommercialPropertyClass
    management_fee_ratio_bps: int = Field(ge=0, le=5_000)

    @model_validator(mode="after")
    def validate_complete_profile(self) -> "IndependentFinanceProfile":
        required = (
            INITIAL_COST_CATEGORIES | MONTHLY_FIXED_COST_CATEGORIES
        ) - {CostCategory.FRANCHISE_INITIAL_FEES}
        if set(self.cost_ranges) != required:
            raise ValueError("independent finance profile must cover every non-franchise cost")
        for category, amount in self.cost_ranges.items():
            if None in (amount.low, amount.base, amount.high):
                raise ValueError(f"finance profile cost range is incomplete: {category.value}")
        return self


class IndependentSeedDefinition(StrictModel):
    model_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1)
    allowed_operation_modes: list[OperationMode] = Field(min_length=1)
    minimum_own_funds_krw: int | None = Field(default=None, ge=0)
    allowed_parameters: list[AllowedParameter] = Field(min_length=1)
    finance_profile: IndependentFinanceProfile | None = None
    support_refs: list[str] = Field(min_length=1)
    selection_keywords: list[str] = Field(default_factory=list)
    requires_explicit_interest: bool = False

    @model_validator(mode="after")
    def validate_unique_values(self) -> "IndependentSeedDefinition":
        if len(self.allowed_operation_modes) != len(set(self.allowed_operation_modes)):
            raise ValueError("allowed operation modes must be unique")
        paths = [value.field_path for value in self.allowed_parameters]
        if len(paths) != len(set(paths)):
            raise ValueError("allowed parameter paths must be unique")
        if len(self.support_refs) != len(set(self.support_refs)):
            raise ValueError("support refs must be unique")
        if len(self.selection_keywords) != len(set(self.selection_keywords)):
            raise ValueError("selection keywords must be unique")
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
        # 사용자가 말한 운영 방향을 먼저 보되, 말하지 않은 고비용 조건부 모델은 열지 않는다.
        preference_text = " ".join(
            [*founder.preferences, founder.prior_cafe_experience or ""]
        ).casefold()
        eligible: list[tuple[int, int, IndependentSeedDefinition]] = []
        for index, model in enumerate(self._document.models):
            if founder.operation_mode not in model.allowed_operation_modes:
                continue
            if (
                model.minimum_own_funds_krw is not None
                and founder.own_funds_krw < model.minimum_own_funds_krw
            ):
                continue
            preference_matches = sum(
                keyword.casefold() in preference_text
                for keyword in model.selection_keywords
            )
            if model.requires_explicit_interest and preference_matches == 0:
                continue
            eligible.append((-preference_matches, index, model))
        eligible.sort(key=lambda value: (value[0], value[1]))
        return [model for _, _, model in eligible]

    def get(self, model_id: str) -> IndependentSeedDefinition | None:
        return next(
            (model for model in self._document.models if model.model_id == model_id),
            None,
        )
