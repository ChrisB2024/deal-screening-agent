import uuid

import pytest
from pydantic import ValidationError

from app.models.enums import ConfidenceLevel, CriterionType, DealStatus, DecisionType
from app.schemas.criteria import CriteriaConfigCreateSchema, CriterionCreateSchema
from app.schemas.deal import (
    CriterionResultSchema,
    DealDecisionRequest,
    DealUploadResponse,
    ExtractedDealSchema,
    ExtractedFieldSchema,
    ScoredDealSchema,
)


def test_criterion_create_schema_accepts_supported_operator():
    criterion = CriterionCreateSchema(
        field_name="revenue",
        criterion_type=CriterionType.MUST_HAVE,
        operator="gte",
        target_value="5000000",
        weight=1.0,
        label="Revenue above threshold",
    )

    assert criterion.operator == "gte"


@pytest.mark.parametrize("operator", ["", "GTE", "between", "drop table"])
def test_criterion_create_schema_rejects_unsupported_operator(operator: str):
    with pytest.raises(ValidationError):
        CriterionCreateSchema(
            field_name="revenue",
            criterion_type=CriterionType.MUST_HAVE,
            operator=operator,
            target_value="5000000",
            weight=1.0,
            label="Revenue above threshold",
        )


def test_criteria_config_requires_at_least_one_criterion():
    with pytest.raises(ValidationError):
        CriteriaConfigCreateSchema(name="Default", criteria=[])


def test_extracted_deal_schema_enforces_core_field_count_bounds():
    base_payload = {
        "deal_id": uuid.uuid4(),
        "fields": [
            ExtractedFieldSchema(
                field_name="sector",
                field_value="healthcare",
                field_status="FOUND",
                confidence="HIGH",
            )
        ],
        "overall_confidence": "MEDIUM",
        "extraction_run": 1,
    }

    valid = ExtractedDealSchema(fields_found_count=3, **base_payload)
    assert valid.fields_found_count == 3

    with pytest.raises(ValidationError):
        ExtractedDealSchema(fields_found_count=7, **base_payload)


def test_scored_deal_schema_enforces_score_bounds():
    payload = {
        "deal_id": uuid.uuid4(),
        "confidence": ConfidenceLevel.MEDIUM,
        "rationale": "Two must-haves met, one field missing.",
        "criterion_results": [
            CriterionResultSchema(
                criterion_label="Revenue above threshold",
                field_name="revenue",
                matched=True,
                detail="Revenue is above 5M",
                weight=1.0,
            )
        ],
    }

    scored = ScoredDealSchema(score=87, **payload)
    assert scored.score == 87

    with pytest.raises(ValidationError):
        ScoredDealSchema(score=101, **payload)


def test_request_response_schemas_accept_expected_enums():
    response = DealUploadResponse(
        deal_id=uuid.uuid4(),
        status=DealStatus.UPLOADED,
        message="Queued for extraction",
        is_duplicate=False,
    )
    request = DealDecisionRequest(decision=DecisionType.PASSED, notes="Outside thesis")

    assert response.status is DealStatus.UPLOADED
    assert request.decision is DecisionType.PASSED
