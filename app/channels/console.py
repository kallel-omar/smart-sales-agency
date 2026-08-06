from uuid import uuid4

from app.channels.base import ChannelAdapter, DeliveryResult


class ConsoleChannel(ChannelAdapter):
    async def send(self, recipient: str, content: str) -> DeliveryResult:
        print(f"[CONSOLE DELIVERY] recipient={recipient}\n{content}")
        return DeliveryResult(success=True, external_id=f"console-{uuid4()}")
