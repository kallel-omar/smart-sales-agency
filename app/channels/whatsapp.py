from app.channels.base import ChannelAdapter, DeliveryResult


class WhatsAppCloudChannel(ChannelAdapter):
    """Integration boundary for Meta WhatsApp Cloud API.

    Intentionally left disabled until credentials, webhook validation, templates,
    consent rules, retry handling, and tenant isolation are implemented.
    """

    async def send(self, recipient: str, content: str) -> DeliveryResult:
        return DeliveryResult(
            success=False,
            error="WhatsApp adapter is a safe stub. Configure the official Cloud API before use.",
        )
