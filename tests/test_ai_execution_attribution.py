from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_execution_attribution import AIExecutionAttribution
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.departments.sales.agents.qualifier import (
    QualificationAgent,
    QualificationResult,
)
from app.departments.sales.services import SalesWorkItemExecutionService
from app.models import AIInvocationUsage, Department, Lead, Workspace
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.ai_execution_attribution import (
    AIExecutionAttributionConflictError,
    AIExecutionAttributionScopeError,
    AIExecutionAttributionService,
)
from app.services.ai_invocation_gateway import (
    AIInvocationGateway,
    AIInvocationGatewayRequest,
)
from app.services.ai_model_routing import AIModelRoutingTask
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.llm import LLMClient, LLMCompletion
from app.services.work_items import WorkItemService


class FakeLLM(LLMClient):
    def __init__(
        self,
        *,
        content: str = "Attributed completion",
        input_tokens: int = 10,
        output_tokens: int = 5,
    ) -> None:
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return (await self.complete_with_metadata(system_prompt, user_prompt)).content

    async def complete_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMCompletion:
        self.calls.append((system_prompt, user_prompt))
        return LLMCompletion(
            content=self.content,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="openai_compatible",
        llm_api_key="test-key",
        require_human_approval=True,
        ai_model_tier_mappings={
            "economy": {"provider": "provider-a", "model": "economy-model"},
            "standard": {"provider": "provider-b", "model": "standard-model"},
        },
        ai_model_pricing=[
            {
                "provider": "provider-a",
                "model": "economy-model",
                "input_cost_per_million_tokens": "2.00",
                "output_cost_per_million_tokens": "4.00",
            },
            {
                "provider": "provider-b",
                "model": "standard-model",
                "input_cost_per_million_tokens": "2.00",
                "output_cost_per_million_tokens": "4.00",
            },
        ],
    )


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug.replace("-", " ").title())
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def _assigned_work_item(
    session: Session,
    workspace: Workspace,
    capability_key: BusinessCapabilityKey,
):
    department = DepartmentService(session).ensure_sales_department(workspace)
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        capability_key,
    )
    role_key = {
        BusinessCapabilityKey.RESEARCH_COMPANY: AIEmployeeRoleKey.LEAD_RESEARCH,
        BusinessCapabilityKey.QUALIFY_LEAD: AIEmployeeRoleKey.QUALIFICATION,
        BusinessCapabilityKey.ANSWER_CUSTOMER: AIEmployeeRoleKey.SALES_CONVERSATION,
    }[capability_key]
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        role_key,
        name=f"{capability_key.value} specialist",
    )
    assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )
    work_item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type=capability_key.value,
        title=f"Execute {capability_key.value}",
        input={},
        capability=capability,
    )
    work_item = WorkItemService(session).assign_work_item(
        workspace,
        work_item.id,
        assignment,
    )
    return department, capability, employee, assignment, work_item


def _gateway(session: Session, fake: FakeLLM) -> AIInvocationGateway:
    return AIInvocationGateway(
        session,
        _settings(),
        llm_builder=lambda _settings, *, model: fake,
    )


def _request(
    workspace: Workspace,
    attribution: AIExecutionAttribution | None = None,
) -> AIInvocationGatewayRequest:
    return AIInvocationGatewayRequest(
        workspace=workspace,
        task=AIModelRoutingTask.SALES_CONVERSATION,
        task_identifier="sales.conversation.reply",
        agent_identifier="sales_conversation",
        system_prompt="system",
        user_prompt="user",
        attribution=attribution,
    )


@pytest.mark.asyncio
async def test_legacy_gateway_call_preserves_identifiers_cost_and_null_attribution(
    session: Session,
) -> None:
    workspace = _workspace(session, "attribution-legacy")

    result = await _gateway(session, FakeLLM()).invoke(_request(workspace))

    assert result.usage is not None
    assert result.usage.task_identifier == "sales.conversation.reply"
    assert result.usage.agent_identifier == "sales_conversation"
    assert result.usage.department_id is None
    assert result.usage.ai_employee_id is None
    assert result.usage.capability_id is None
    assert result.usage.work_item_id is None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (10, 5)
    assert result.usage.total_tokens == 15
    assert result.usage.estimated_cost == Decimal("0.00004000")


@pytest.mark.asyncio
async def test_gateway_records_all_hiri_attribution_without_changing_accounting(
    session: Session,
) -> None:
    workspace = _workspace(session, "attribution-all")
    department, capability, employee, _, work_item = _assigned_work_item(
        session,
        workspace,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    )
    attribution = AIExecutionAttributionService(session).from_work_item(
        workspace,
        work_item,
    )

    result = await _gateway(session, FakeLLM()).invoke(
        _request(workspace, attribution)
    )

    assert result.usage is not None
    assert result.usage.department_id == department.id
    assert result.usage.ai_employee_id == employee.id
    assert result.usage.capability_id == capability.id
    assert result.usage.work_item_id == work_item.id
    assert result.usage.task_identifier == "sales.conversation.reply"
    assert result.usage.agent_identifier == "sales_conversation"
    assert result.usage.total_tokens == 15
    assert result.usage.estimated_cost == Decimal("0.00004000")


def test_work_item_attribution_derives_only_persisted_values(session: Session) -> None:
    workspace = _workspace(session, "attribution-derive")
    department, capability, employee, _, assigned = _assigned_work_item(
        session,
        workspace,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    unassigned = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type="unassigned",
        title="Unassigned",
        input={},
    )
    service = AIExecutionAttributionService(session)

    assigned_attribution = service.from_work_item(workspace, assigned)
    unassigned_attribution = service.from_work_item(workspace, unassigned)

    assert assigned_attribution == AIExecutionAttribution(
        department_id=department.id,
        ai_employee_id=employee.id,
        capability_id=capability.id,
        work_item_id=assigned.id,
    )
    assert unassigned_attribution == AIExecutionAttribution(
        department_id=department.id,
        work_item_id=unassigned.id,
    )


def test_persisted_work_item_values_cannot_be_contradicted(session: Session) -> None:
    workspace = _workspace(session, "attribution-conflict")
    department, _, _, _, work_item = _assigned_work_item(
        session,
        workspace,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    other_employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.LEAD_RESEARCH,
        name="Other researcher",
    )
    other_capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.QUALIFY_LEAD,
    )
    other_department = Department(
        workspace_id=workspace.id,
        kind=DepartmentKind.BUSINESS,
    )
    session.add(other_department)
    session.commit()
    session.refresh(other_department)
    contradictions = (
        AIExecutionAttribution(
            department_id=other_department.id,
            work_item_id=work_item.id,
        ),
        AIExecutionAttribution(
            ai_employee_id=other_employee.id,
            work_item_id=work_item.id,
        ),
        AIExecutionAttribution(
            capability_id=other_capability.id,
            work_item_id=work_item.id,
        ),
    )

    for attribution in contradictions:
        with pytest.raises(AIExecutionAttributionConflictError, match="contradicts"):
            AIExecutionAttributionService(session).validate(
                workspace,
                attribution,
            )


def test_cross_workspace_attribution_is_rejected_for_every_entity(session: Session) -> None:
    workspace_a = _workspace(session, "attribution-scope-a")
    workspace_b = _workspace(session, "attribution-scope-b")
    department, capability, employee, _, work_item = _assigned_work_item(
        session,
        workspace_a,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    attributions = (
        AIExecutionAttribution(department_id=department.id),
        AIExecutionAttribution(ai_employee_id=employee.id),
        AIExecutionAttribution(capability_id=capability.id),
        AIExecutionAttribution(work_item_id=work_item.id),
    )

    for attribution in attributions:
        with pytest.raises(AIExecutionAttributionScopeError, match="workspace"):
            AIExecutionAttributionService(session).validate(
                workspace_b,
                attribution,
            )


def test_cross_department_employee_capability_attribution_is_rejected(
    session: Session,
) -> None:
    workspace = _workspace(session, "attribution-department")
    sales_department = DepartmentService(session).ensure_sales_department(workspace)
    other_department = Department(
        workspace_id=workspace.id,
        kind=DepartmentKind.BUSINESS,
    )
    session.add(other_department)
    session.commit()
    session.refresh(other_department)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        sales_department,
        AIEmployeeRoleKey.LEAD_RESEARCH,
        name="Researcher",
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        other_department,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )

    with pytest.raises(AIExecutionAttributionScopeError, match="same Department"):
        AIExecutionAttributionService(session).validate(
            workspace,
            AIExecutionAttribution(
                ai_employee_id=employee.id,
                capability_id=capability.id,
            ),
        )


@pytest.mark.asyncio
async def test_invalid_attribution_fails_before_provider_and_usage_write(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "attribution-gateway-a")
    workspace_b = _workspace(session, "attribution-gateway-b")
    department = DepartmentService(session).ensure_sales_department(workspace_a)
    fake = FakeLLM()

    with pytest.raises(AIExecutionAttributionScopeError, match="workspace"):
        await _gateway(session, fake).invoke(
            _request(
                workspace_b,
                AIExecutionAttribution(department_id=department.id),
            )
        )

    assert fake.calls == []
    assert list(session.exec(select(AIInvocationUsage))) == []


def _sales_work_item(
    session: Session,
    capability_key: BusinessCapabilityKey,
    *,
    slug: str,
):
    workspace = _workspace(session, slug)
    department, capability, employee, _, work_item = _assigned_work_item(
        session,
        workspace,
        capability_key,
    )
    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Sarra Ben Ali",
        company_name="Example Co",
        email="sarra@example.com",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    source = {"lead_id": str(lead.id)}
    if capability_key == BusinessCapabilityKey.QUALIFY_LEAD:
        source["research"] = {"opportunities": ["Improve response speed"]}
    if capability_key == BusinessCapabilityKey.ANSWER_CUSTOMER:
        source.update(
            {
                "channel": "website",
                "customer_message": "What is the price?",
            }
        )
    work_item.input = source
    session.add(work_item)
    session.commit()
    session.refresh(work_item)
    return workspace, department, capability, employee, work_item


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability_key",
    [
        BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    ],
)
async def test_sales_ai_work_item_usage_receives_derived_attribution(
    session: Session,
    capability_key: BusinessCapabilityKey,
) -> None:
    workspace, department, capability, employee, work_item = _sales_work_item(
        session,
        capability_key,
        slug=f"attribution-sales-{capability_key.value}",
    )
    fake = FakeLLM()

    await SalesWorkItemExecutionService(
        session,
        _settings(),
        ai_invocation_gateway=_gateway(session, fake),
    ).execute(workspace, work_item.id)

    usage = session.exec(
        select(AIInvocationUsage).where(
            AIInvocationUsage.workspace_id == workspace.id
        )
    ).one()
    assert usage.department_id == department.id
    assert usage.ai_employee_id == employee.id
    assert usage.capability_id == capability.id
    assert usage.work_item_id == work_item.id
    assert fake.calls


@pytest.mark.asyncio
async def test_qualification_work_item_receives_attribution_without_new_ai_cost(
    session: Session,
    monkeypatch,
) -> None:
    workspace, department, capability, employee, work_item = _sales_work_item(
        session,
        BusinessCapabilityKey.QUALIFY_LEAD,
        slug="attribution-sales-qualification",
    )
    captured: list[AIExecutionAttribution | None] = []

    async def qualify(self, lead, research):
        captured.append(self.context.ai_execution_attribution)
        return QualificationResult(score=60, qualified=True, reasons=["Qualified"])

    monkeypatch.setattr(QualificationAgent, "run", qualify)

    await SalesWorkItemExecutionService(
        session,
        _settings(),
        ai_invocation_gateway=_gateway(session, FakeLLM()),
    ).execute(workspace, work_item.id)

    assert captured == [
        AIExecutionAttribution(
            department_id=department.id,
            ai_employee_id=employee.id,
            capability_id=capability.id,
            work_item_id=work_item.id,
        )
    ]
    assert list(session.exec(select(AIInvocationUsage))) == []
