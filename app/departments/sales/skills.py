"""Sales-owned AgentSkill definitions; procedures are implemented in later tasks."""

from app.core.agent_skills import AgentSkillDefinition, AgentSkillRegistry
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department


def _sales_skill(
    *,
    key: str,
    role: AIEmployeeRoleKey,
    capability: BusinessCapabilityKey,
) -> AgentSkillDefinition:
    return AgentSkillDefinition(
        key=key,
        version="v1",
        department=Department.SALES,
        eligible_roles=frozenset({role}),
        required_capability=capability,
        input_contract=f"sales.{key}.input.v1",
        output_contract=f"sales.{key}.output.v1",
        allowed_tool_ceiling=frozenset(),
        validator=f"sales.{key}.output_validator.v1",
        instruction_component=f"sales.{key}.instruction.v1",
    )


SALES_AGENT_SKILL_DEFINITIONS = (
    _sales_skill(
        key="account_research",
        role=AIEmployeeRoleKey.LEAD_RESEARCH,
        capability=BusinessCapabilityKey.RESEARCH_COMPANY,
    ),
    _sales_skill(
        key="qualification_gap_detector",
        role=AIEmployeeRoleKey.QUALIFICATION,
        capability=BusinessCapabilityKey.QUALIFY_LEAD,
    ),
    _sales_skill(
        key="pricing_explanation",
        role=AIEmployeeRoleKey.SALES_CONVERSATION,
        capability=BusinessCapabilityKey.ANSWER_CUSTOMER,
    ),
    _sales_skill(
        key="followup_planner",
        role=AIEmployeeRoleKey.FOLLOW_UP,
        capability=BusinessCapabilityKey.FOLLOW_UP_LEAD,
    ),
)


def sales_agent_skill_registry() -> AgentSkillRegistry:
    """Build the application-owned Sales registry with exact v1 definitions."""

    return AgentSkillRegistry(SALES_AGENT_SKILL_DEFINITIONS)
