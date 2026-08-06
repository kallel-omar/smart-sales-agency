from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.event_factory import create_business_event
from app.core.event_payloads import LeadGeneratedPayload
from app.core.event_types import EventType
from app.core.events import Department
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService
from app.models import SalesStage


@pytest.mark.asyncio
async def test_sales_department_service_handles_inbound_message_with_approval(
    monkeypatch,
) -> None:
    settings = Mock()
    settings.require_human_approval = True

    repository = Mock()

    approval_id = uuid4()
    approval = Mock()
    approval.id = approval_id

    repository.create_approval.return_value = approval

    context = AgentContext(
        settings=settings,
        repository=repository,
        llm=Mock(),
    )

    lead = Mock()
    lead.id = uuid4()
    lead.email = "customer@example.com"
    lead.phone = None
    lead.full_name = "Example Customer"

    draft_reply = AsyncMock(
        return_value=(
            SalesStage.QUALIFICATION,
            "The product costs 100.",
        )
    )

    monkeypatch.setattr(
        "app.departments.sales.services.department_service."
        "SalesConversationAgent.draft_reply",
        draft_reply,
    )

    service = SalesDepartmentService(context)

    result = await service.draft_sales_reply(
        lead=lead,
        channel="web",
        content="How much does it cost?",
    )

    assert result.detected_stage == SalesStage.QUALIFICATION
    assert result.draft_reply == "The product costs 100."
    assert result.approval_id == approval_id

    assert repository.add_message.call_count == 1
    repository.create_approval.assert_called_once()


@pytest.mark.asyncio
async def test_sales_department_service_persists_outbound_when_approval_disabled(
    monkeypatch,
) -> None:
    settings = Mock()
    settings.require_human_approval = False

    repository = Mock()

    context = AgentContext(
        settings=settings,
        repository=repository,
        llm=Mock(),
    )

    lead = Mock()
    lead.id = uuid4()
    lead.email = "customer@example.com"
    lead.phone = None
    lead.full_name = "Example Customer"

    draft_reply = AsyncMock(
        return_value=(
            SalesStage.DISCOVERY,
            "What problem would you like to solve?",
        )
    )

    monkeypatch.setattr(
        "app.departments.sales.services.department_service."
        "SalesConversationAgent.draft_reply",
        draft_reply,
    )

    service = SalesDepartmentService(context)

    result = await service.draft_sales_reply(
        lead=lead,
        channel="web",
        content="I need help with my sales process.",
    )

    assert result.detected_stage == SalesStage.DISCOVERY
    assert result.approval_id is None

    assert repository.add_message.call_count == 2
    repository.create_approval.assert_not_called()

    inbound_message = repository.add_message.call_args_list[0].args[0]
    outbound_message = repository.add_message.call_args_list[1].args[0]

    assert inbound_message.direction == "inbound"
    assert outbound_message.direction == "outbound"


@pytest.mark.asyncio
async def test_sales_department_service_runs_new_lead_workflow(
    monkeypatch,
) -> None:
    context = AgentContext(
        settings=Mock(),
        repository=Mock(),
        llm=Mock(),
    )

    expected_result = {
        "status": "awaiting_approval",
        "qualified": True,
    }

    workflow_run = AsyncMock(
        return_value=expected_result
    )

    monkeypatch.setattr(
        "app.departments.sales.services.department_service."
        "NewLeadWorkflow.run",
        workflow_run,
    )

    service = SalesDepartmentService(context)

    lead_id = uuid4()

    result = await service.run_new_lead_workflow(
        lead_id
    )

    assert result == expected_result

    workflow_run.assert_awaited_once_with(
        lead_id
    )


def test_sales_department_service_uses_sales_supervisor() -> None:
    context = AgentContext(
        settings=Mock(),
        repository=Mock(),
        llm=Mock(),
    )

    service = SalesDepartmentService(context)

    assert service.supervisor is not None


@pytest.mark.asyncio
async def test_sales_department_service_handles_lead_generated_event(
    monkeypatch,
) -> None:
    context = AgentContext(
        settings=Mock(),
        repository=Mock(),
        llm=Mock(),
    )

    service = SalesDepartmentService(context)

    lead_id = uuid4()

    expected_result = {
        "status": "awaiting_approval",
        "qualified": True,
    }

    run_new_lead_workflow = AsyncMock(
        return_value=expected_result
    )

    monkeypatch.setattr(
        service,
        "run_new_lead_workflow",
        run_new_lead_workflow,
    )

    event = create_business_event(
        workspace_id=uuid4(),
        event_type=EventType.LEAD_GENERATED,
        source_department=Department.PLATFORM,
        destination_department=Department.SALES,
        payload=LeadGeneratedPayload(
            lead_id=str(lead_id),
            source="api",
        ),
    )

    result = await service.handle_event(event)

    assert result == expected_result

    run_new_lead_workflow.assert_awaited_once_with(
        lead_id
    )


@pytest.mark.asyncio
async def test_sales_department_service_rejects_unknown_event() -> None:
    context = AgentContext(
        settings=Mock(),
        repository=Mock(),
        llm=Mock(),
    )

    service = SalesDepartmentService(context)

    event = Mock()
    event.event_type = "order.created"
    event.payload = {}

    with pytest.raises(
        ValueError,
        match="Unsupported Sales Department event",
    ):
        await service.handle_event(event)


@pytest.mark.asyncio
async def test_sales_department_service_rejects_invalid_lead_id_event() -> None:
    context = AgentContext(
        settings=Mock(),
        repository=Mock(),
        llm=Mock(),
    )

    service = SalesDepartmentService(context)

    event = Mock()
    event.event_type = EventType.LEAD_GENERATED.value
    event.payload = {
        "lead_id": "not-a-valid-uuid",
    }

    with pytest.raises(
        ValueError,
        match="invalid lead_id",
    ):
        await service.handle_event(event)