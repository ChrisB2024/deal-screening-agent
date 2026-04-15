"""Scoring service — orchestrates deal scoring against user criteria.

Takes extracted deal fields + active criteria config → produces a score (0-100),
per-criterion breakdown, natural language rationale, and confidence level.

This transitions deals from EXTRACTED → SCORED with an audit log entry.

Spec invariants:
- Score is deterministic for same inputs.
- Rationale must cite specific criteria matches/misses.
- Missing fields reduce confidence, never silently skipped.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.criteria import CriteriaConfig, Criterion
from app.models.deal import DealAuditLog, Deal, DealScore, ExtractedField
from app.models.enums import (
    ConfidenceLevel,
    CriterionType,
    DealStatus,
    FieldExtractionStatus,
)
from app.services.criteria_evaluator import CriterionEvalResult, evaluate_criterion

logger = logging.getLogger(__name__)


class ScoringResult:
    """Result of scoring a deal."""

    def __init__(
        self,
        success: bool,
        deal_id: uuid.UUID,
        score: int = 0,
        confidence: ConfidenceLevel = ConfidenceLevel.NONE,
        rationale: str = "",
        criterion_results: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ):
        self.success = success
        self.deal_id = deal_id
        self.score = score
        self.confidence = confidence
        self.rationale = rationale
        self.criterion_results = criterion_results or []
        self.error = error


async def score_deal(
    db: AsyncSession,
    deal_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ScoringResult:
    """Score a deal against the tenant's active criteria config.

    Purpose: Evaluate extracted fields against screening criteria, produce score + rationale.
    Inputs: Database session, deal_id, tenant_id.
    Outputs: ScoringResult with score (0-100), confidence, rationale, per-criterion breakdown.
    Invariants:
        - Deal must be in EXTRACTED status.
        - Tenant must have an active criteria config.
        - State transition (EXTRACTED → SCORED) always produces an audit log.
        - Score is deterministic for same inputs.
    Security: Tenant-scoped queries prevent cross-tenant scoring.
    """
    # 1. Load deal and verify state
    deal = await _load_deal(db, deal_id, tenant_id)

    # 2. Load active criteria config
    config = await _load_active_config(db, tenant_id)
    if config is None:
        return ScoringResult(
            success=False,
            deal_id=deal_id,
            error="No active criteria config found for tenant. Configure screening criteria first.",
        )

    # 3. Load latest extracted fields
    fields_map = await _load_extracted_fields(db, deal_id)

    # 4. Evaluate each criterion
    eval_results = _evaluate_all_criteria(config.criteria, fields_map)

    # 5. Compute score
    score = _compute_score(eval_results)

    # 6. Compute confidence
    confidence = _compute_scoring_confidence(eval_results, fields_map)

    # 7. Generate rationale
    rationale = _generate_rationale(eval_results, score, confidence)

    # 8. Serialize criterion results for storage
    criterion_results_json = [
        {
            "criterion_label": r.criterion_label,
            "field_name": r.field_name,
            "matched": r.matched,
            "detail": r.detail,
            "weight": r.weight,
            "skipped": r.skipped,
        }
        for r in eval_results
    ]

    # 9. Persist score
    deal_score = DealScore(
        deal_id=deal.id,
        criteria_config_id=config.id,
        score=score,
        confidence=confidence,
        rationale=rationale,
        criterion_results=criterion_results_json,
    )
    db.add(deal_score)

    # 10. Transition deal status
    import uuid as _uuid
    old_status = deal.status
    deal.status = DealStatus.SCORED
    audit = DealAuditLog(
        audit_id=str(_uuid.uuid4()),
        deal_id=deal.id,
        tenant_id=str(deal.tenant_id),
        actor_type="worker",
        action="SCORING_COMPLETED",
        before_state=old_status.value,
        after_state=DealStatus.SCORED.value,
        metadata_={"score": score, "confidence": confidence.value},
    )
    db.add(audit)
    await db.flush()

    return ScoringResult(
        success=True,
        deal_id=deal.id,
        score=score,
        confidence=confidence,
        rationale=rationale,
        criterion_results=criterion_results_json,
    )


async def _load_deal(
    db: AsyncSession,
    deal_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Deal:
    """Load deal, verify tenant and status."""
    stmt = select(Deal).where(Deal.id == deal_id, Deal.tenant_id == tenant_id)
    result = await db.execute(stmt)
    deal = result.scalar_one_or_none()

    if deal is None:
        raise ValueError(f"Deal {deal_id} not found for tenant {tenant_id}")

    if deal.status != DealStatus.EXTRACTED:
        raise ValueError(
            f"Deal {deal_id} is in status {deal.status} — scoring requires EXTRACTED status"
        )

    return deal


async def _load_active_config(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> CriteriaConfig | None:
    """Load the active criteria config for a tenant, with criteria eagerly loaded."""
    stmt = (
        select(CriteriaConfig)
        .options(selectinload(CriteriaConfig.criteria))
        .where(
            CriteriaConfig.tenant_id == tenant_id,
            CriteriaConfig.is_active == True,  # noqa: E712
        )
        .order_by(CriteriaConfig.version.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _load_extracted_fields(
    db: AsyncSession,
    deal_id: uuid.UUID,
) -> dict[str, ExtractedField]:
    """Load the latest extraction run's fields as a name→field map."""
    # Get the latest extraction run number
    stmt = (
        select(ExtractedField)
        .where(ExtractedField.deal_id == deal_id)
        .order_by(ExtractedField.extraction_run.desc())
    )
    result = await db.execute(stmt)
    all_fields = result.scalars().all()

    if not all_fields:
        return {}

    latest_run = max(f.extraction_run for f in all_fields)
    return {
        f.field_name: f for f in all_fields if f.extraction_run == latest_run
    }


def _evaluate_all_criteria(
    criteria: list[Criterion],
    fields_map: dict[str, ExtractedField],
) -> list[CriterionEvalResult]:
    """Evaluate all criteria against the extracted fields."""
    results: list[CriterionEvalResult] = []

    for criterion in criteria:
        field = fields_map.get(criterion.field_name)

        if field is None:
            # Field not in extraction output at all — treat as MISSING
            result = CriterionEvalResult(
                criterion_label=criterion.label,
                field_name=criterion.field_name,
                criterion_type=criterion.criterion_type,
                matched=False,
                weight=criterion.weight,
                detail=f"Field '{criterion.field_name}' not available in extraction results.",
                skipped=True,
            )
        else:
            result = evaluate_criterion(
                field_name=criterion.field_name,
                field_value=field.field_value,
                field_status=field.field_status,
                operator=criterion.operator,
                target_value_json=criterion.target_value,
                criterion_type=criterion.criterion_type,
                weight=criterion.weight,
                label=criterion.label,
            )

        results.append(result)

    return results


def _compute_score(eval_results: list[CriterionEvalResult]) -> int:
    """Compute a 0-100 score from criterion evaluation results.

    Scoring algorithm:
    1. If ANY dealbreaker is not matched (and not skipped) → score = 0.
    2. Otherwise, weighted average of matched criteria:
       - Each matched criterion contributes its weight.
       - Skipped criteria are excluded from the denominator (not penalized, but reduce confidence).
       - Score = (sum of matched weights / sum of evaluable weights) * 100.

    This is deterministic: same inputs → same score.
    """
    if not eval_results:
        return 0

    # Check for dealbreaker failures (only non-skipped — missing field dealbreakers
    # don't auto-fail, they just reduce confidence)
    for r in eval_results:
        if (
            r.criterion_type == CriterionType.DEALBREAKER
            and not r.matched
            and not r.skipped
        ):
            return 0

    # Weighted average of evaluable (non-skipped) criteria
    total_weight = 0.0
    matched_weight = 0.0

    for r in eval_results:
        if r.skipped:
            continue
        total_weight += r.weight
        if r.matched:
            matched_weight += r.weight

    if total_weight == 0:
        return 0

    raw_score = (matched_weight / total_weight) * 100
    return round(raw_score)


def _compute_scoring_confidence(
    eval_results: list[CriterionEvalResult],
    fields_map: dict[str, ExtractedField],
) -> ConfidenceLevel:
    """Compute scoring confidence based on how many criteria could be evaluated.

    Rules:
    - All criteria evaluated (none skipped) + extraction was HIGH confidence → HIGH
    - >= 75% of criteria evaluated → MEDIUM
    - >= 50% of criteria evaluated → LOW
    - < 50% → NONE
    """
    if not eval_results:
        return ConfidenceLevel.NONE

    total = len(eval_results)
    skipped = sum(1 for r in eval_results if r.skipped)
    evaluated = total - skipped

    if evaluated == 0:
        return ConfidenceLevel.NONE

    eval_ratio = evaluated / total

    if eval_ratio == 1.0:
        # Check if underlying extraction was high confidence
        all_high = all(
            f.confidence == ConfidenceLevel.HIGH
            for f in fields_map.values()
            if f.field_status != FieldExtractionStatus.MISSING
        )
        if all_high:
            return ConfidenceLevel.HIGH
        return ConfidenceLevel.MEDIUM

    if eval_ratio >= 0.75:
        return ConfidenceLevel.MEDIUM

    if eval_ratio >= 0.50:
        return ConfidenceLevel.LOW

    return ConfidenceLevel.NONE


def _generate_rationale(
    eval_results: list[CriterionEvalResult],
    score: int,
    confidence: ConfidenceLevel,
) -> str:
    """Generate a natural language rationale citing specific criteria matches/misses.

    Spec invariant: Rationale must cite specific criteria matches/misses.
    """
    if not eval_results:
        return "No criteria configured — unable to evaluate deal."

    lines: list[str] = []
    lines.append(f"Deal scored {score}/100 (confidence: {confidence.value}).\n")

    # Dealbreaker failures
    dealbreaker_fails = [
        r for r in eval_results
        if r.criterion_type == CriterionType.DEALBREAKER and not r.matched and not r.skipped
    ]
    if dealbreaker_fails:
        lines.append("DEALBREAKER TRIGGERED:")
        for r in dealbreaker_fails:
            lines.append(f"  - {r.detail}")
        lines.append("")

    # Matched criteria
    matched = [r for r in eval_results if r.matched]
    if matched:
        lines.append("Criteria met:")
        for r in matched:
            lines.append(f"  + {r.detail}")
        lines.append("")

    # Unmatched criteria (non-dealbreaker, non-skipped)
    unmatched = [
        r for r in eval_results
        if not r.matched and not r.skipped and r.criterion_type != CriterionType.DEALBREAKER
    ]
    if unmatched:
        lines.append("Criteria not met:")
        for r in unmatched:
            lines.append(f"  - {r.detail}")
        lines.append("")

    # Skipped criteria (missing fields)
    skipped = [r for r in eval_results if r.skipped]
    if skipped:
        lines.append("Could not evaluate (missing data):")
        for r in skipped:
            lines.append(f"  ? {r.detail}")
        lines.append("")

    return "\n".join(lines)
