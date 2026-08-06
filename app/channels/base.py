from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class DeliveryResult:
    success: bool
    external_id: str | None = None
    error: str | None = None


class ChannelAdapter(ABC):
    @abstractmethod
    async def send(self, recipient: str, content: str) -> DeliveryResult:
        raise NotImplementedError
