from app.models.enums import (
    AuditAction,
    ConfidenceLevel,
    CORE_EXTRACTION_FIELDS,
    CriterionType,
    DealStatus,
    DecisionType,
    FieldExtractionStatus,
    MIN_FIELDS_FOR_EXTRACTION,
)


def test_deal_statuses_match_spec_state_machine():
    assert {status.value for status in DealStatus} == {
        "UPLOADED",
        "EXTRACTED",
        "FAILED",
        "SCORED",
        "DECIDED",
    }


def test_decision_and_confidence_enums_cover_documented_values():
    assert {decision.value for decision in DecisionType} == {"PASSED", "PURSUING"}
    assert {level.value for level in ConfidenceLevel} == {"HIGH", "MEDIUM", "LOW", "NONE"}
    assert {status.value for status in FieldExtractionStatus} == {
        "FOUND",
        "INFERRED",
        "MISSING",
    }
    assert {criterion.value for criterion in CriterionType} == {
        "MUST_HAVE",
        "NICE_TO_HAVE",
        "DEALBREAKER",
    }


def test_core_extraction_fields_and_threshold_match_spec():
    assert CORE_EXTRACTION_FIELDS == [
        "sector",
        "revenue",
        "ebitda",
        "geography",
        "ask_price",
        "deal_type",
    ]
    assert MIN_FIELDS_FOR_EXTRACTION == 3


def test_audit_actions_cover_expected_lifecycle_events():
    assert {action.value for action in AuditAction} == {
        "DEAL_UPLOADED",
        "EXTRACTION_STARTED",
        "EXTRACTION_COMPLETED",
        "EXTRACTION_FAILED",
        "SCORING_COMPLETED",
        "DECISION_MADE",
        "CRITERIA_UPDATED",
        "DEAL_RETRIED",
    }
