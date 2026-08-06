from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService


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