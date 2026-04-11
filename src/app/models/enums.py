import enum


class DealStatus(str, enum.Enum):
    """Deal lifecycle states per spec state machine."""

    UPLOADED = "UPLOADED"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"
    SCORED = "SCORED"
    DECIDED = "DECIDED"


class DecisionType(str, enum.Enum):
    PASSED = "PASSED"
    PURSUING = "PURSUING"


class FieldExtractionStatus(str, enum.Enum):
    """Per-field extraction confidence."""

    FOUND = "FOUND"
    INFERRED = "INFERRED"
    MISSING = "MISSING"


class ConfidenceLevel(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class CriterionType(str, enum.Enum):
    """How a criterion affects scoring."""

    MUST_HAVE = "MUST_HAVE"
    NICE_TO_HAVE = "NICE_TO_HAVE"
    DEALBREAKER = "DEALBREAKER"


class AuditAction(str, enum.Enum):
    DEAL_UPLOADED = "DEAL_UPLOADED"
    EXTRACTION_STARTED = "EXTRACTION_STARTED"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    SCORING_COMPLETED = "SCORING_COMPLETED"
    DECISION_MADE = "DECISION_MADE"
    CRITERIA_UPDATED = "CRITERIA_UPDATED"
    DEAL_RETRIED = "DEAL_RETRIED"


# The 6 core extraction fields referenced in the spec
CORE_EXTRACTION_FIELDS = [
    "sector",
    "revenue",
    "ebitda",
    "geography",
    "ask_price",
    "deal_type",
]

MIN_FIELDS_FOR_EXTRACTION = 3
