import pytest

from app.models.enums import CriterionType, FieldExtractionStatus
from app.services.criteria_evaluator import _apply_operator, evaluate_criterion


def test_evaluate_criterion_skips_missing_fields():
    result = evaluate_criterion(
        field_name="revenue",
        field_value=None,
        field_status=FieldExtractionStatus.MISSING,
        operator="gte",
        target_value_json="5000000",
        criterion_type=CriterionType.MUST_HAVE,
        weight=1.0,
        label="Revenue threshold",
    )

    assert result.skipped is True
    assert result.matched is False
    assert "MISSING" in result.detail


@pytest.mark.parametrize(
    ("field_value", "operator", "target", "expected"),
    [
        ("Healthcare", "eq", '"healthcare"', True),
        ("Healthcare", "ne", '"technology"', True),
        ("5,000,000", "gt", "4000000", True),
        ("5,000,000", "lt", "6000000", True),
        ("5000000", "gte", "5000000", True),
        ("5000000", "lte", "5000000", True),
        ("US Southeast", "contains", '"southeast"', True),
        ("healthcare", "in", '["technology", "healthcare"]', True),
        ("canada", "not_in", '["us", "mexico"]', True),
    ],
)
def test_evaluate_criterion_supports_documented_operators(
    field_value: str, operator: str, target: str, expected: bool
):
    result = evaluate_criterion(
        field_name="test_field",
        field_value=field_value,
        field_status=FieldExtractionStatus.FOUND,
        operator=operator,
        target_value_json=target,
        criterion_type=CriterionType.NICE_TO_HAVE,
        weight=0.5,
        label="Test criterion",
    )

    assert result.matched is expected
    assert result.skipped is False


def test_numeric_operators_fail_closed_for_non_numeric_values():
    assert _apply_operator("not-a-number", "gt", 10) is False
    assert _apply_operator("10", "gte", "not-a-number") is False


def test_in_and_not_in_fail_closed_for_non_list_targets():
    assert _apply_operator("healthcare", "in", "healthcare") is False
    assert _apply_operator("healthcare", "not_in", "technology") is False


def test_string_comparisons_are_case_insensitive():
    assert _apply_operator("Healthcare", "eq", "healthcare") is True
    assert _apply_operator("US Southeast", "contains", "southeast") is True
