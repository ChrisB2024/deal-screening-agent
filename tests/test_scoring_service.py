import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import AuditAction, ConfidenceLevel, CriterionType, DealStatus, FieldExtractionStatus
from app.services.criteria_evaluator import CriterionEvalResult
from app.services.scoring_service import (
    ScoringResult,
    _compute_score,
    _compute_scoring_confidence,
    _evaluate_all_criteria,
    _generate_rationale,
    score_deal,
)


def _field(name: str, value: str | None, status, confidence, run: int = 1):
    return SimpleNamespace(
        field_name=name,
        field_value=value,
        field_status=status,
        confidence=confidence,
        extraction_run=run,
    )


def _criterion(field_name: str, operator: str, target_value: str, criterion_type, weight: float, label: str):
    return SimpleNamespace(
        field_name=field_name,
        operator=operator,
        target_value=target_value,
        criterion_type=criterion_type,
        weight=weight,
        label=label,
    )


class _FakeDB:
    def __init__(self):
        self.added = []
        self.flushed = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


def test_compute_score_short_circuits_on_dealbreaker_failure():
    results = [
        CriterionEvalResult("Wrong sector", "sector", CriterionType.DEALBREAKER, False, 1.0, "bad", False),
        CriterionEvalResult("Revenue", "revenue", CriterionType.MUST_HAVE, True, 1.0, "good", False),
    ]

    assert _compute_score(results) == 0


def test_compute_score_uses_weighted_average_and_skips_missing():
    results = [
        CriterionEvalResult("Revenue", "revenue", CriterionType.MUST_HAVE, True, 2.0, "good", False),
        CriterionEvalResult("Geography", "geography", CriterionType.NICE_TO_HAVE, False, 1.0, "bad", False),
        CriterionEvalResult("EBITDA", "ebitda", CriterionType.NICE_TO_HAVE, False, 5.0, "missing", True),
    ]

    assert _compute_score(results) == 67


def test_compute_score_returns_zero_for_empty_or_all_skipped():
    assert _compute_score([]) == 0
    skipped_only = [
        CriterionEvalResult("Sector", "sector", CriterionType.MUST_HAVE, False, 1.0, "missing", True)
    ]
    assert _compute_score(skipped_only) == 0


def test_compute_scoring_confidence_matches_thresholds():
    high_results = [
        CriterionEvalResult("A", "sector", CriterionType.MUST_HAVE, True, 1.0, "ok", False),
        CriterionEvalResult("B", "revenue", CriterionType.MUST_HAVE, True, 1.0, "ok", False),
    ]
    high_fields = {
        "sector": _field("sector", "healthcare", FieldExtractionStatus.FOUND, ConfidenceLevel.HIGH),
        "revenue": _field("revenue", "5000000", FieldExtractionStatus.FOUND, ConfidenceLevel.HIGH),
    }
    assert _compute_scoring_confidence(high_results, high_fields) is ConfidenceLevel.HIGH

    medium_results = high_results + [
        CriterionEvalResult("C", "geography", CriterionType.NICE_TO_HAVE, False, 1.0, "missing", True),
        CriterionEvalResult("D", "deal_type", CriterionType.NICE_TO_HAVE, False, 1.0, "missing", True),
    ]
    assert _compute_scoring_confidence(medium_results, high_fields) is ConfidenceLevel.LOW

    medium_ratio_results = high_results + [
        CriterionEvalResult("C", "geography", CriterionType.NICE_TO_HAVE, True, 1.0, "ok", False),
        CriterionEvalResult("D", "deal_type", CriterionType.NICE_TO_HAVE, False, 1.0, "missing", True),
    ]
    assert _compute_scoring_confidence(medium_ratio_results, high_fields) is ConfidenceLevel.MEDIUM

    none_results = [
        CriterionEvalResult("A", "sector", CriterionType.MUST_HAVE, False, 1.0, "missing", True),
        CriterionEvalResult("B", "revenue", CriterionType.MUST_HAVE, False, 1.0, "missing", True),
        CriterionEvalResult("C", "geography", CriterionType.NICE_TO_HAVE, True, 1.0, "ok", False),
    ]
    assert _compute_scoring_confidence(none_results, high_fields) is ConfidenceLevel.NONE


def test_generate_rationale_lists_matches_misses_and_skips():
    results = [
        CriterionEvalResult("Wrong sector", "sector", CriterionType.DEALBREAKER, False, 1.0, "sector mismatch", False),
        CriterionEvalResult("Revenue", "revenue", CriterionType.MUST_HAVE, True, 1.0, "revenue ok", False),
        CriterionEvalResult("Geography", "geography", CriterionType.NICE_TO_HAVE, False, 1.0, "geo miss", False),
        CriterionEvalResult("EBITDA", "ebitda", CriterionType.NICE_TO_HAVE, False, 1.0, "missing ebitda", True),
    ]

    rationale = _generate_rationale(results, 0, ConfidenceLevel.LOW)

    assert "DEALBREAKER TRIGGERED" in rationale
    assert "Criteria met:" in rationale
    assert "Criteria not met:" in rationale
    assert "Could not evaluate" in rationale


def test_evaluate_all_criteria_treats_absent_fields_as_skipped():
    criteria = [
        _criterion("sector", "eq", '"healthcare"', CriterionType.MUST_HAVE, 1.0, "Sector fit"),
        _criterion("revenue", "gte", "5000000", CriterionType.MUST_HAVE, 1.0, "Revenue fit"),
    ]
    fields_map = {
        "sector": _field("sector", "healthcare", FieldExtractionStatus.FOUND, ConfidenceLevel.HIGH)
    }

    results = _evaluate_all_criteria(criteria, fields_map)

    assert len(results) == 2
    assert results[0].matched is True
    assert results[1].skipped is True
    assert "not available" in results[1].detail


def test_score_deal_persists_score_and_transitions_status(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid.uuid4()
    deal = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, status=DealStatus.EXTRACTED)
    config = SimpleNamespace(
        id=uuid.uuid4(),
        criteria=[
            _criterion("sector", "eq", '"healthcare"', CriterionType.MUST_HAVE, 2.0, "Sector fit"),
            _criterion("revenue", "gte", "5000000", CriterionType.NICE_TO_HAVE, 1.0, "Revenue fit"),
        ],
    )
    fields_map = {
        "sector": _field("sector", "healthcare", FieldExtractionStatus.FOUND, ConfidenceLevel.HIGH),
        "revenue": _field("revenue", "6000000", FieldExtractionStatus.FOUND, ConfidenceLevel.HIGH),
    }
    db = _FakeDB()

    async def _load_deal(*args, **kwargs):
        return deal

    async def _load_config(*args, **kwargs):
        return config

    async def _load_fields(*args, **kwargs):
        return fields_map

    monkeypatch.setattr("app.services.scoring_service._load_deal", _load_deal)
    monkeypatch.setattr("app.services.scoring_service._load_active_config", _load_config)
    monkeypatch.setattr("app.services.scoring_service._load_extracted_fields", _load_fields)

    result = asyncio.run(score_deal(db, deal.id, tenant_id))

    assert isinstance(result, ScoringResult)
    assert result.success is True
    assert result.score == 100
    assert result.confidence is ConfidenceLevel.HIGH
    assert deal.status is DealStatus.SCORED
    assert db.flushed == 1
    assert [entry.action for entry in db.added if hasattr(entry, "action")] == [
        AuditAction.SCORING_COMPLETED
    ]
    stored_scores = [entry for entry in db.added if entry.__class__.__name__ == "DealScore"]
    assert len(stored_scores) == 1
    assert stored_scores[0].criteria_config_id == config.id


def test_score_deal_returns_error_when_no_active_config(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid.uuid4()
    deal = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, status=DealStatus.EXTRACTED)
    db = _FakeDB()

    async def _load_deal(*args, **kwargs):
        return deal

    async def _load_config(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.scoring_service._load_deal", _load_deal)
    monkeypatch.setattr("app.services.scoring_service._load_active_config", _load_config)

    result = asyncio.run(score_deal(db, deal.id, tenant_id))

    assert result.success is False
    assert "No active criteria config found" in result.error
    assert db.added == []
    assert deal.status is DealStatus.EXTRACTED


def test_score_deal_is_deterministic_for_same_inputs(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid.uuid4()
    deal = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, status=DealStatus.EXTRACTED)
    config = SimpleNamespace(
        id=uuid.uuid4(),
        criteria=[_criterion("sector", "eq", '"healthcare"', CriterionType.MUST_HAVE, 1.0, "Sector fit")],
    )
    fields_map = {
        "sector": _field("sector", "healthcare", FieldExtractionStatus.FOUND, ConfidenceLevel.HIGH)
    }

    async def _load_deal(*args, **kwargs):
        return deal

    async def _load_config(*args, **kwargs):
        return config

    async def _load_fields(*args, **kwargs):
        return fields_map

    monkeypatch.setattr("app.services.scoring_service._load_deal", _load_deal)
    monkeypatch.setattr("app.services.scoring_service._load_active_config", _load_config)
    monkeypatch.setattr("app.services.scoring_service._load_extracted_fields", _load_fields)

    first = asyncio.run(score_deal(_FakeDB(), deal.id, tenant_id))
    deal.status = DealStatus.EXTRACTED
    second = asyncio.run(score_deal(_FakeDB(), deal.id, tenant_id))

    assert (first.score, first.confidence, first.rationale) == (
        second.score,
        second.confidence,
        second.rationale,
    )
