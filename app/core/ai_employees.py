from enum import StrEnum


class AIEmployeeRoleKey(StrEnum):
    """Platform-defined AI employee specialist roles assignable within a Department."""

    LEAD_RESEARCH = "lead_research"
    QUALIFICATION = "qualification"
    SALES_CONVERSATION = "sales_conversation"
    FOLLOW_UP = "follow_up"


SALES_AI_EMPLOYEE_ROLE_KEYS = (
    AIEmployeeRoleKey.LEAD_RESEARCH,
    AIEmployeeRoleKey.QUALIFICATION,
    AIEmployeeRoleKey.SALES_CONVERSATION,
    AIEmployeeRoleKey.FOLLOW_UP,
)

SUPPORTED_AI_EMPLOYEE_ROLE_KEYS = frozenset(
    {
        *SALES_AI_EMPLOYEE_ROLE_KEYS,
    }
)

AI_EMPLOYEE_ROLE_DEFAULT_NAMES = {
    AIEmployeeRoleKey.LEAD_RESEARCH: "Lead Research",
    AIEmployeeRoleKey.QUALIFICATION: "Qualification",
    AIEmployeeRoleKey.SALES_CONVERSATION: "Sales Conversation",
    AIEmployeeRoleKey.FOLLOW_UP: "Follow-up",
}
