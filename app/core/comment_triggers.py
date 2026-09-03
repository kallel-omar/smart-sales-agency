from enum import StrEnum


class InboundCommentChannel(StrEnum):
    FACEBOOK_COMMENT = "facebook_comment"
    INSTAGRAM_COMMENT = "instagram_comment"
    TIKTOK_COMMENT = "tiktok_comment"


class CommentTriggerResult(StrEnum):
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    SUGGESTED = "suggested"
    APPROVAL_REQUIRED = "approval_required"
    TOOL_ACCESS_DENIED = "tool_access_denied"
    OUTBOUND_DELIVERED = "outbound_delivered"
    OUTBOUND_FAILED = "outbound_failed"
    PROVIDER_INELIGIBLE = "provider_ineligible"
    HANDOFF_ACTIVE = "handoff_active"
