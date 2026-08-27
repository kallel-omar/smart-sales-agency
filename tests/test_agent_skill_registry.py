from dataclasses import FrozenInstanceError, replace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.agent_skills import (
    AgentSkillCapabilityNotEligibleError,
    AgentSkillDefinition,
    AgentSkillDefinitionError,
    AgentSkillDepartmentNotEligibleError,
    AgentSkillNotFoundError,
    AgentSkillRegistry,
    AgentSkillRoleNotEligibleError,
    AgentSkillVersionNotFoundError,
    DuplicateAgentSkillDefinitionError,
    effective_agent_skill_tools,
)
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department
from app.departments.sales.skills import (
    SALES_AGENT_SKILL_DEFINITIONS,
    sales_agent_skill_registry,
)
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    AIEmployeeCapabilityToolAccess,
    ApprovalRequest,
    IntegrationAccount,
    OutboundIntegrationAction,
    WorkItem,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService


def definition(
    *,
    key: str = "pricing_explanation",
    version: str = "v1",
    department: Department = Department.SALES,
    roles: frozenset[AIEmployeeRoleKey] = frozenset(
        {AIEmployeeRoleKey.SALES_CONVERSATION}
    ),
    capability: BusinessCapabilityKey = BusinessCapabilityKey.ANSWER_CUSTOMER,
    tools: frozenset[str] = frozenset(),
) -> AgentSkillDefinition:
    return AgentSkillDefinition(
        key=key,
        version=version,
        department=department,
        eligible_roles=roles,
        required_capability=capability,
        input_contract=f"test.{key}.input.v1",
        output_contract=f"test.{key}.output.v1",
        allowed_tool_ceiling=tools,
        validator=f"test.{key}.validator.v1",
        instruction_component=f"test.{key}.instruction.v1",
    )


def test_exact_registration_resolution_and_deterministic_listing() -> None:
    v2 = definition(version="v2")
    v1 = definition()
    research = definition(
        key="account_research",
        roles=frozenset({AIEmployeeRoleKey.LEAD_RESEARCH}),
        capability=BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    registry = AgentSkillRegistry((v2, v1, research))

    assert registry.resolve("pricing_explanation", "v1") is v1
    assert registry.resolve("pricing_explanation", "v2") is v2
    assert registry.list_definitions() == (research, v1, v2)


def test_duplicate_unknown_key_and_unknown_version_fail_closed() -> None:
    registered = definition()
    registry = AgentSkillRegistry((registered,))

    with pytest.raises(DuplicateAgentSkillDefinitionError, match="already registered"):
        registry.register(registered)
    with pytest.raises(AgentSkillNotFoundError, match="not registered"):
        registry.resolve("unknown_skill", "v1")
    with pytest.raises(AgentSkillVersionNotFoundError, match="version"):
        registry.resolve(registered.key, "v2")


def test_exact_version_is_never_silently_replaced_or_fallback_resolved() -> None:
    v1 = definition()
    registry = AgentSkillRegistry((v1,))

    with pytest.raises(AgentSkillVersionNotFoundError):
        registry.resolve(v1.key, "v999")
    with pytest.raises(DuplicateAgentSkillDefinitionError):
        registry.register(replace(v1, output_contract="test.replacement.output.v1"))
    assert registry.resolve(v1.key, "v1") is v1


def test_definition_and_nested_collections_are_immutable() -> None:
    registered = definition(tools=frozenset({"send_message"}))

    with pytest.raises(FrozenInstanceError):
        registered.version = "v2"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        registered.eligible_roles.add(AIEmployeeRoleKey.QUALIFICATION)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        registered.allowed_tool_ceiling.add("another_tool")  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"key": "Pricing Explanation"}, "key"),
        ({"version": "latest"}, "version"),
        ({"input_contract": ""}, "input contract"),
        ({"output_contract": "invalid-contract"}, "output contract"),
        ({"validator": ""}, "validator"),
        ({"instruction_component": ""}, "instruction component"),
        ({"eligible_roles": frozenset()}, "eligible roles"),
        ({"allowed_tool_ceiling": {"send_message"}}, "tool ceiling"),
    ],
)
def test_invalid_or_mutable_definition_contracts_are_rejected(changes, message) -> None:
    values = {
        "key": "pricing_explanation",
        "version": "v1",
        "department": Department.SALES,
        "eligible_roles": frozenset({AIEmployeeRoleKey.SALES_CONVERSATION}),
        "required_capability": BusinessCapabilityKey.ANSWER_CUSTOMER,
        "input_contract": "test.pricing.input.v1",
        "output_contract": "test.pricing.output.v1",
        "allowed_tool_ceiling": frozenset(),
        "validator": "test.pricing.validator.v1",
        "instruction_component": "test.pricing.instruction.v1",
    }
    values.update(changes)

    with pytest.raises(AgentSkillDefinitionError, match=message):
        AgentSkillDefinition(**values)


def test_role_capability_and_department_eligibility_are_exact() -> None:
    registry = AgentSkillRegistry((definition(),))
    eligible = registry.require_eligible(
        "pricing_explanation",
        "v1",
        department=Department.SALES,
        role=AIEmployeeRoleKey.SALES_CONVERSATION,
        capability=BusinessCapabilityKey.ANSWER_CUSTOMER,
    )
    assert eligible.key == "pricing_explanation"

    with pytest.raises(AgentSkillRoleNotEligibleError):
        registry.require_eligible(
            eligible.key,
            eligible.version,
            department=Department.SALES,
            role=AIEmployeeRoleKey.QUALIFICATION,
            capability=BusinessCapabilityKey.ANSWER_CUSTOMER,
        )
    with pytest.raises(AgentSkillCapabilityNotEligibleError):
        registry.require_eligible(
            eligible.key,
            eligible.version,
            department=Department.SALES,
            role=AIEmployeeRoleKey.SALES_CONVERSATION,
            capability=BusinessCapabilityKey.QUALIFY_LEAD,
        )
    with pytest.raises(AgentSkillDepartmentNotEligibleError):
        registry.require_eligible(
            eligible.key,
            eligible.version,
            department=Department.MARKETING,
            role=AIEmployeeRoleKey.SALES_CONVERSATION,
            capability=BusinessCapabilityKey.ANSWER_CUSTOMER,
        )


def test_unknown_role_and_capability_values_fail_with_safe_eligibility_errors() -> None:
    registry = AgentSkillRegistry((definition(),))

    with pytest.raises(AgentSkillRoleNotEligibleError):
        registry.require_eligible(
            "pricing_explanation",
            "v1",
            department=Department.SALES,
            role="unknown_role",  # type: ignore[arg-type]
            capability=BusinessCapabilityKey.ANSWER_CUSTOMER,
        )
    with pytest.raises(AgentSkillCapabilityNotEligibleError):
        registry.require_eligible(
            "pricing_explanation",
            "v1",
            department=Department.SALES,
            role=AIEmployeeRoleKey.SALES_CONVERSATION,
            capability="unknown_capability",  # type: ignore[arg-type]
        )


def test_core_registry_is_department_neutral_and_sales_definitions_are_scoped() -> None:
    business_definition = definition(
        key="business_procedure",
        department=Department.BUSINESS,
    )
    registry = AgentSkillRegistry((business_definition,))

    assert registry.require_eligible(
        business_definition.key,
        business_definition.version,
        department=Department.BUSINESS,
        role=AIEmployeeRoleKey.SALES_CONVERSATION,
        capability=BusinessCapabilityKey.ANSWER_CUSTOMER,
    ) is business_definition
    assert all(item.department is Department.SALES for item in SALES_AGENT_SKILL_DEFINITIONS)


def test_tool_ceiling_only_intersects_and_never_expands_authorization() -> None:
    send_only = definition(tools=frozenset({"send_message"}))

    assert effective_agent_skill_tools(
        {"send_message", "delete_customer"},
        send_only,
    ) == {"send_message"}
    assert effective_agent_skill_tools(set(), send_only) == frozenset()
    assert effective_agent_skill_tools(
        {"send_message"},
        definition(),
    ) == frozenset()


def test_representative_sales_definitions_use_canonical_contracts_and_no_tools() -> None:
    registry = sales_agent_skill_registry()
    expected = {
        "account_research": (
            AIEmployeeRoleKey.LEAD_RESEARCH,
            BusinessCapabilityKey.RESEARCH_COMPANY,
        ),
        "buying_signal_detection": (
            AIEmployeeRoleKey.LEAD_RESEARCH,
            BusinessCapabilityKey.RESEARCH_COMPANY,
        ),
        "qualification_gap_detector": (
            AIEmployeeRoleKey.QUALIFICATION,
            BusinessCapabilityKey.QUALIFY_LEAD,
        ),
        "icp_scoring": (
            AIEmployeeRoleKey.QUALIFICATION,
            BusinessCapabilityKey.QUALIFY_LEAD,
        ),
        "pricing_explanation": (
            AIEmployeeRoleKey.SALES_CONVERSATION,
            BusinessCapabilityKey.ANSWER_CUSTOMER,
        ),
        "needs_discovery": (
            AIEmployeeRoleKey.SALES_CONVERSATION,
            BusinessCapabilityKey.ANSWER_CUSTOMER,
        ),
        "objection_handling": (
            AIEmployeeRoleKey.SALES_CONVERSATION,
            BusinessCapabilityKey.ANSWER_CUSTOMER,
        ),
        "buyer_indecision": (
            AIEmployeeRoleKey.SALES_CONVERSATION,
            BusinessCapabilityKey.ANSWER_CUSTOMER,
        ),
        "followup_planner": (
            AIEmployeeRoleKey.FOLLOW_UP,
            BusinessCapabilityKey.FOLLOW_UP_LEAD,
        ),
        "followup_message_generation": (
            AIEmployeeRoleKey.FOLLOW_UP,
            BusinessCapabilityKey.FOLLOW_UP_LEAD,
        ),
    }

    assert {item.key for item in registry.list_definitions()} == set(expected)
    for key, (role, capability) in expected.items():
        item = registry.require_eligible(
            key,
            "v1",
            department=Department.SALES,
            role=role,
            capability=capability,
        )
        assert item.allowed_tool_ceiling == frozenset()
        assert item.input_contract == f"sales.{key}.input.v1"
        assert item.output_contract == f"sales.{key}.output.v1"
        assert item.validator == f"sales.{key}.output_validator.v1"
        assert item.instruction_component == f"sales.{key}.instruction.v1"


def test_custom_employee_with_canonical_role_remains_eligible_after_assignment() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            workspace = Workspace(slug="custom-skill-employee", name="Custom Skill Employee")
            session.add(workspace)
            session.commit()
            session.refresh(workspace)
            department = DepartmentService(session).ensure_sales_department(workspace)
            employee = AIEmployeeService(session).create_for_department(
                workspace,
                department,
                AIEmployeeRoleKey.SALES_CONVERSATION,
                name="Customer-configured Conversation Specialist",
            )
            capability = CapabilityService(session).ensure_for_department(
                workspace,
                department,
                BusinessCapabilityKey.ANSWER_CUSTOMER,
            )
            assignment = AIEmployeeCapabilityAssignmentService(session).assign(
                workspace,
                employee,
                capability,
            )

            resolved = sales_agent_skill_registry().require_eligible(
                "pricing_explanation",
                "v1",
                department=department.kind,
                role=employee.role_key,
                capability=capability.key,
            )

            assert resolved.key == "pricing_explanation"
            assert assignment.ai_employee_id == employee.id
            assert session.exec(select(AIEmployee)).all() == [employee]
            assert session.exec(select(AIEmployeeCapabilityAssignment)).all() == [assignment]
            assert session.exec(select(AIEmployeeCapabilityToolAccess)).all() == []
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def test_registry_resolution_has_no_domain_or_provider_side_effects() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            registry = sales_agent_skill_registry()
            registry.require_eligible(
                "pricing_explanation",
                "v1",
                department=Department.SALES,
                role=AIEmployeeRoleKey.SALES_CONVERSATION,
                capability=BusinessCapabilityKey.ANSWER_CUSTOMER,
            )

            for model in (
                AIEmployeeCapabilityAssignment,
                AIEmployeeCapabilityToolAccess,
                WorkItem,
                ApprovalRequest,
                OutboundIntegrationAction,
                IntegrationAccount,
            ):
                assert list(session.exec(select(model)).all()) == []
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()
