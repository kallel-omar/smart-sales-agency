from enum import StrEnum

from app.core.capabilities import BusinessCapabilityKey


class AIEmployeeAutonomyLevel(StrEnum):
    """Platform-defined autonomy levels for AI employee tool access."""

    SUGGEST = "suggest"
    DRAFT_REQUIRES_APPROVAL = "draft_requires_approval"
    CONTROLLED_AUTOMATION = "controlled_automation"
    HIGH_AUTOMATION = "high_automation"


CAPABILITY_ACTION_COMPATIBILITY: dict[BusinessCapabilityKey, frozenset[str]] = {
    BusinessCapabilityKey.SEND_MESSAGE: frozenset({"send_message"}),
}

CONTROLLED_AUTOMATION_SAFE_ACTION_TYPES = frozenset({"send_message"})
