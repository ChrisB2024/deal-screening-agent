"""Pydantic schemas for criteria configuration API contracts.

These define how the user configures their fund's screening criteria.
The scoring engine consumes these to evaluate deals.
"""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.enums import CriterionType

VALID_OPERATORS = {"eq", "ne", "gt", "lt", "gte", "lte", "in", "not_in", "contains"}


class CriterionCreateSchema(BaseModel):
    field_name: str = Field(
        description="Which extraction field this criterion evaluates",
        examples=["sector", "revenue", "ebitda", "geography", "ask_price", "deal_type"],
    )
    criterion_type: CriterionType
    operator: str = Field(description="Comparison operator", examples=["eq", "gt", "in"])
    target_value: str = Field(
        description="JSON-encoded target value",
        examples=['"healthcare"', "1000000", '["US", "Canada"]'],
    )
    weight: float = Field(ge=0.0, le=1.0, default=1.0)
    label: str = Field(description="Human-readable description", examples=["Sector is healthcare"])

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, v: str) -> str:
        if v not in VALID_OPERATORS:
            raise ValueError(f"operator must be one of {VALID_OPERATORS}")
        return v


class CriterionResponseSchema(CriterionCreateSchema):
    id: UUID


class CriteriaConfigCreateSchema(BaseModel):
    name: str = Field(default="Default", max_length=256)
    criteria: list[CriterionCreateSchema] = Field(min_length=1)


class CriteriaConfigResponseSchema(BaseModel):
    id: UUID
    tenant_id: UUID
    version: int
    is_active: bool
    name: str
    criteria: list[CriterionResponseSchema]
