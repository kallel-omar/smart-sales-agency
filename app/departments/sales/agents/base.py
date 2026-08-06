from dataclasses import dataclass

from app.config import Settings
from app.services.llm import LLMClient
from app.services.repository import SalesRepository


@dataclass(slots=True)
class AgentContext:
    """Dependencies shared by Sales Department agents."""

    settings: Settings
    repository: SalesRepository
    llm: LLMClient