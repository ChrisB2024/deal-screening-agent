import enum


class DealStatus(str, enum.Enum):
    """Deal lifecycle states per spec state machine."""

    UPLOADED = "UPLOADED"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"
    SCORED = "SCORED"
    DECIDED = "DECIDED"
    ARCHIVED = "ARCHIVED"


VALID_STATE_TRANSITIONS: dict[DealStatus, set[DealStatus]] = {
    DealStatus.UPLOADED: {DealStatus.EXTRACTED, DealStatus.FAILED},
    DealStatus.EXTRACTED: {DealStatus.SCORED},
    DealStatus.FAILED: {DealStatus.UPLOADED},
    DealStatus.SCORED: {DealStatus.DECIDED},
    DealStatus.DECIDED: {DealStatus.ARCHIVED},
    DealStatus.ARCHIVED: set(),
}


class InvalidStateTransition(ValueError):
    def __init__(self, from_status: DealStatus, to_status: DealStatus):
        super().__init__(
            f"Invalid state transition: {from_status.value} -> {to_status.value}"
        )
        self.from_status = from_status
        self.to_status = to_status


def validate_transition(from_status: DealStatus, to_status: DealStatus) -> None:
    allowed = VALID_STATE_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise InvalidStateTransition(from_status, to_status)


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
