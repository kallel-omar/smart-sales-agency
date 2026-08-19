from enum import StrEnum


class BusinessCapabilityKey(StrEnum):
    """Platform-defined business capabilities assignable within a Department."""

    CAPTURE_LEAD = "capture_lead"
    RESEARCH_COMPANY = "research_company"
    QUALIFY_LEAD = "qualify_lead"
    ANSWER_CUSTOMER = "answer_customer"
    SEND_MESSAGE = "send_message"
    FOLLOW_UP_LEAD = "follow_up_lead"


SALES_BUSINESS_CAPABILITY_KEYS = (
        BusinessCapabilityKey.CAPTURE_LEAD,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.QUALIFY_LEAD,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
        BusinessCapabilityKey.SEND_MESSAGE,
        BusinessCapabilityKey.FOLLOW_UP_LEAD,
)

SUPPORTED_BUSINESS_CAPABILITY_KEYS = frozenset(
    {
        *SALES_BUSINESS_CAPABILITY_KEYS,
    }
)
