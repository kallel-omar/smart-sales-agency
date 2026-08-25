"""Provider-neutral normalized social messaging event contract."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SocialInboundEvent:
    kind: Literal["direct_message", "comment"]
    channel: str
    provider_event_id: str
    sender_external_id: str
    recipient_account_id: str
    content: str
    display_name: str | None = None
    timestamp: int | None = None
    post_or_media_id: str | None = None
    parent_comment_id: str | None = None
    message_type: str | None = None
    # Some providers, including TikTok Business Messaging, address outbound
    # replies by conversation rather than by the sender identity.
    external_conversation_id: str | None = None
