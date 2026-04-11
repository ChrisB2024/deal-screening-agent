"""Criteria evaluation engine — the rule-based scoring core.

Evaluates a single criterion against an extracted field value.
This is the atomic unit of scoring: one rule, one field, one result.

Spec decision (Appendix B): Rule-based deterministic scoring for v1.
Pluggable interface so v2 can swap in ML-based scoring.
"""

import json
import logging
from typing import Any

from app.models.enums import CriterionType, FieldExtractionStatus

logger = logging.getLogger(__name__)


class CriterionEvalResult:
    """Result of evaluating a single criterion against a field."""

    def __init__(
        self,
        criterion_label: str,
        field_name: str,
        criterion_type: CriterionType,
        matched: bool,
        weight: float,
        detail: str,
        skipped: bool = False,
    ):
        self.criterion_label = criterion_label
        self.field_name = field_name
        self.criterion_type = criterion_type
        self.matched = matched
        self.weight = weight
        self.detail = detail
        self.skipped = skipped  # True when field was MISSING


def evaluate_criterion(
    field_name: str,
    field_value: str | None,
    field_status: FieldExtractionStatus,
    operator: str,
    target_value_json: str,
    criterion_type: CriterionType,
    weight: float,
    label: str,
) -> CriterionEvalResult:
    """Evaluate a single criterion against an extracted field.

    Purpose: Apply one rule (operator + target) to one field value.
    Inputs: Field data from extraction + criterion definition from config.
    Outputs: CriterionEvalResult with match/skip status and explanation.
    Invariants:
        - MISSING fields are never silently skipped — they produce a result with skipped=True.
        - DEALBREAKER match=False is a hard fail regardless of weight.
        - Evaluation is deterministic: same inputs → same output.
    Security: target_value is JSON-decoded here — input was already validated by Pydantic schema.
    """
    # Handle MISSING fields — spec says "reduce confidence, never silently skipped"
    if field_status == FieldExtractionStatus.MISSING or field_value is None:
        return CriterionEvalResult(
            criterion_label=label,
            field_name=field_name,
            criterion_type=criterion_type,
            matched=False,
            weight=weight,
            detail=f"Field '{field_name}' is MISSING — criterion could not be evaluated.",
            skipped=True,
        )

    target = json.loads(target_value_json)
    matched = _apply_operator(field_value, operator, target)

    if matched:
        detail = f"'{label}' matched: {field_name} {operator} {target}"
    else:
        detail = f"'{label}' not matched: {field_name}={field_value}, expected {operator} {target}"

    return CriterionEvalResult(
        criterion_label=label,
        field_name=field_name,
        criterion_type=criterion_type,
        matched=matched,
        weight=weight,
        detail=detail,
    )


def _apply_operator(field_value: str, operator: str, target: Any) -> bool:
    """Apply a comparison operator to a field value and target.

    Handles both numeric and string comparisons.
    Numeric operators (gt, lt, gte, lte) attempt numeric conversion.
    String operators (eq, ne, contains, in, not_in) use case-insensitive comparison.
    """
    if operator in ("gt", "lt", "gte", "lte"):
        return _numeric_compare(field_value, operator, target)
    elif operator == "eq":
        return _str_normalize(field_value) == _str_normalize(str(target))
    elif operator == "ne":
        return _str_normalize(field_value) != _str_normalize(str(target))
    elif operator == "in":
        if not isinstance(target, list):
            logger.warning(f"'in' operator expects list target, got {type(target)}")
            return False
        normalized_targets = [_str_normalize(str(t)) for t in target]
        return _str_normalize(field_value) in normalized_targets
    elif operator == "not_in":
        if not isinstance(target, list):
            logger.warning(f"'not_in' operator expects list target, got {type(target)}")
            return False
        normalized_targets = [_str_normalize(str(t)) for t in target]
        return _str_normalize(field_value) not in normalized_targets
    elif operator == "contains":
        return _str_normalize(str(target)) in _str_normalize(field_value)
    else:
        logger.error(f"Unknown operator: {operator}")
        return False


def _numeric_compare(field_value: str, operator: str, target: Any) -> bool:
    """Compare field value numerically. Returns False if conversion fails."""
    try:
        field_num = float(field_value.replace(",", "").replace("$", "").strip())
        target_num = float(target)
    except (ValueError, TypeError):
        return False

    if operator == "gt":
        return field_num > target_num
    elif operator == "lt":
        return field_num < target_num
    elif operator == "gte":
        return field_num >= target_num
    elif operator == "lte":
        return field_num <= target_num
    return False


def _str_normalize(s: str) -> str:
    """Normalize a string for case-insensitive comparison."""
    return s.strip().lower()
