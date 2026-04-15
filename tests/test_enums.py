from app.models.enums import (
    ConfidenceLevel,
    CORE_EXTRACTION_FIELDS,
    CriterionType,
    DealStatus,
    DecisionType,
    FieldExtractionStatus,
    MIN_FIELDS_FOR_EXTRACTION,
    VALID_STATE_TRANSITIONS,
    validate_transition,
    InvalidStateTransition,
)
import pytest


def test_deal_statuses_match_spec_state_machine():
    assert {status.value for status in DealStatus} == {
        "UPLOADED",
        "EXTRACTED",
        "FAILED",
        "SCORED",
        "DECIDED",
        "ARCHIVED",
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


def test_valid_state_transitions_match_spec():
    assert VALID_STATE_TRANSITIONS[DealStatus.UPLOADED] == {DealStatus.EXTRACTED, DealStatus.FAILED}
    assert VALID_STATE_TRANSITIONS[DealStatus.EXTRACTED] == {DealStatus.SCORED}
    assert VALID_STATE_TRANSITIONS[DealStatus.FAILED] == {DealStatus.UPLOADED}
    assert VALID_STATE_TRANSITIONS[DealStatus.SCORED] == {DealStatus.DECIDED}
    assert VALID_STATE_TRANSITIONS[DealStatus.DECIDED] == {DealStatus.ARCHIVED}
    assert VALID_STATE_TRANSITIONS[DealStatus.ARCHIVED] == set()


def test_validate_transition_allows_valid():
    validate_transition(DealStatus.UPLOADED, DealStatus.EXTRACTED)
    validate_transition(DealStatus.SCORED, DealStatus.DECIDED)


def test_validate_transition_rejects_invalid():
    with pytest.raises(InvalidStateTransition):
        validate_transition(DealStatus.SCORED, DealStatus.UPLOADED)
    with pytest.raises(InvalidStateTransition):
        validate_transition(DealStatus.DECIDED, DealStatus.SCORED)
    with pytest.raises(InvalidStateTransition):
        validate_transition(DealStatus.ARCHIVED, DealStatus.UPLOADED)
