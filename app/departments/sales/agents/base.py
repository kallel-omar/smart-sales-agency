from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config import Settings
from app.services.llm import LLMClient
from app.services.repository import SalesRepository

if TYPE_CHECKING:
    from app.core.ai_execution_attribution import AIExecutionAttribution
    from app.models import Workspace
    from app.services.ai_invocation_gateway import AIInvocationGateway


@dataclass(slots=True)
class AgentContext:
    """Dependencies shared by Sales Department agents."""

    settings: Settings
    repository: SalesRepository
    llm: LLMClient | None
    workspace: "Workspace | None" = None
    ai_invocation_gateway: "AIInvocationGateway | None" = None
    ai_execution_attribution: "AIExecutionAttribution | None" = None
