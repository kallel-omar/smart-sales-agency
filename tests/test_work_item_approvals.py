from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import BusinessEvent
from app.core.work_items import WorkItemInvalidStateTransitionError, WorkItemStatus
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    IntegrationAccount,
    Lead,
    OutboundIntegrationAction,
    Workspace,
    WorkspaceMemberRole,
)
from app.schemas import ApprovalRead
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.approval_decisions import (
    ApprovalDecisionActor,
    ApprovalDecisionNotFoundError,
    ApprovalDecisionService,
)
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.send_message_work_items import SendMessageWorkItemService
from app.services.work_item_approvals import (
    WORK_ITEM_APPROVAL_REQUESTED_EVENT,
    WORK_ITEM_APPROVAL_RESUME_BLOCKED_EVENT,
    WORK_ITEM_APPROVAL_RESUME_PERMITTED_EVENT,
    WorkItemApprovalInvalidStateError,
    WorkItemApprovalNotFoundError,
    WorkItemApprovalNotPermittedError,
    WorkItemApprovalScopeError,
    WorkItemApprovalService,
)
from app.services.work_items import WorkItemNotFoundError, WorkItemService


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


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug.replace("-", " ").title())
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def _running_work_item(session: Session, workspace: Workspace):
    department = DepartmentService(session).ensure_sales_department(workspace)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.SEND_MESSAGE,
    )
    assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )
    work_item_service = WorkItemService(session)
    work_item = work_item_service.create_work_item(
        workspace,
        department,
        work_type="approval_test",
        title="Approval test",
        input={"safe": True},
    )
    work_item_service.assign_work_item(workspace, work_item.id, assignment)
    return work_item_service.transition_work_item(
        workspace,
        work_item.id,
        WorkItemStatus.RUNNING,
    )


def _actor(workspace: Workspace) -> ApprovalDecisionActor:
    return ApprovalDecisionActor(
        user_id=uuid4(),
        membership_id=uuid4(),
        workspace_id=workspace.id,
        role=WorkspaceMemberRole.ADMIN,
    )


async def _approve(
    session: Session,
    workspace: Workspace,
    approval: ApprovalRequest,
) -> ApprovalRequest:
    return await ApprovalDecisionService(session).approve(
        workspace=workspace,
        approval_id=approval.id,
        reviewer_note="approved",
        actor=_actor(workspace),
    )


def _reject(
    session: Session,
    workspace: Workspace,
    approval: ApprovalRequest,
) -> ApprovalRequest:
    return ApprovalDecisionService(session).reject(
        workspace=workspace,
        approval_id=approval.id,
        reviewer_note="rejected",
        actor=_actor(workspace),
    )


def test_valid_work_item_approval_request_links_approval_and_emits_event(
    session: Session,
) -> None:
    workspace = _workspace(session, "work-item-approval-request")
    work_item = _running_work_item(session, workspace)
    events: list[BusinessEvent] = []

    result = WorkItemApprovalService(
        session,
        event_recorder=events.append,
    ).request_approval(
        workspace,
        work_item.id,
        payload={"reason": "needs human approval"},
    )

    assert result.work_item.status == WorkItemStatus.APPROVAL_REQUIRED
    assert result.approval.work_item_id == work_item.id
    assert result.approval.status == ApprovalStatus.PENDING
    assert ApprovalRead.model_validate(result.approval).work_item_id == work_item.id
    assert [event.event_type for event in events] == [WORK_ITEM_APPROVAL_REQUESTED_EVENT]
    assert events[0].workspace_id == workspace.id
    assert events[0].correlation_id == result.work_item.correlation_id


@pytest.mark.anyio
async def test_existing_approval_without_work_item_still_works(session: Session) -> None:
    workspace = _workspace(session, "legacy-approval")
    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Legacy Lead",
        company_name="Legacy Co",
        source="manual",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    approval = ApprovalRequest(
        lead_id=lead.id,
        action_type="send_message",
        channel="console",
        payload={"recipient": "customer", "content": "hello"},
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)

    approved = await _approve(session, workspace, approval)

    assert approved.work_item_id is None
    assert approved.status == ApprovalStatus.EXECUTED


@pytest.mark.anyio
async def test_approved_linked_approval_permits_resume_without_completion(
    session: Session,
) -> None:
    workspace = _workspace(session, "work-item-approval-approved")
    work_item = _running_work_item(session, workspace)
    events: list[BusinessEvent] = []
    service = WorkItemApprovalService(session, event_recorder=events.append)
    requested = service.request_approval(workspace, work_item.id)

    approved = await _approve(session, workspace, requested.approval)
    resumed = service.resume_after_approval(
        workspace,
        work_item.id,
        approved.id,
    )

    assert approved.status == ApprovalStatus.APPROVED
    assert resumed.work_item.status == WorkItemStatus.RUNNING
    assert resumed.work_item.completed_at is None
    assert [event.event_type for event in events] == [
        WORK_ITEM_APPROVAL_REQUESTED_EVENT,
        WORK_ITEM_APPROVAL_RESUME_PERMITTED_EVENT,
    ]


def test_rejected_approval_cannot_resume_work_item(session: Session) -> None:
    workspace = _workspace(session, "work-item-approval-rejected")
    work_item = _running_work_item(session, workspace)
    events: list[BusinessEvent] = []
    service = WorkItemApprovalService(session, event_recorder=events.append)
    requested = service.request_approval(workspace, work_item.id)
    rejected = _reject(session, workspace, requested.approval)

    with pytest.raises(WorkItemApprovalInvalidStateError, match="must require approval"):
        service.resume_after_approval(workspace, work_item.id, rejected.id)

    assert rejected.status == ApprovalStatus.REJECTED
    terminal = WorkItemService(session).get_work_item(workspace, work_item.id)
    assert terminal.status == WorkItemStatus.CANCELLED
    assert terminal.result == {
        "outcome": "approval_rejected",
        "approval_id": str(rejected.id),
    }
    assert rejected.decided_by_user_id is not None
    assert events[-1].event_type == WORK_ITEM_APPROVAL_REQUESTED_EVENT


def test_rejected_send_work_item_cannot_execute_an_outbound_action(
    session: Session,
) -> None:
    workspace = _workspace(session, "work-item-approval-no-send")
    work_item = _running_work_item(session, workspace)
    requested = WorkItemApprovalService(session).request_approval(
        workspace,
        work_item.id,
    )
    rejected = _reject(session, workspace, requested.approval)
    account = IntegrationAccount(
        workspace_id=workspace.id,
        provider="facebook_messenger",
        external_account_id="approval-rejection-page",
        credential_hash=uuid4().hex,
    )
    session.add(account)
    session.commit()
    session.refresh(account)

    with pytest.raises(ValueError, match="must require approval"):
        SendMessageWorkItemService(
            session,
            Settings(environment="test", database_url="sqlite://", llm_mode="demo"),
        ).execute_work_item(
            workspace,
            work_item.id,
            account,
            approval_id=rejected.id,
        )

    assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_cross_workspace_approval_rejection_is_impossible(session: Session) -> None:
    workspace_a = _workspace(session, "work-item-decision-scope-a")
    workspace_b = _workspace(session, "work-item-decision-scope-b")
    work_item = _running_work_item(session, workspace_a)
    requested = WorkItemApprovalService(session).request_approval(
        workspace_a,
        work_item.id,
    )

    with pytest.raises(ApprovalDecisionNotFoundError, match="not found"):
        ApprovalDecisionService(session).reject(
            workspace=workspace_b,
            approval_id=requested.approval.id,
            reviewer_note="cross-workspace rejection",
            actor=_actor(workspace_b),
        )

    assert requested.approval.status == ApprovalStatus.PENDING
    assert WorkItemService(session).get_work_item(workspace_a, work_item.id).status == (
        WorkItemStatus.APPROVAL_REQUIRED
    )


def test_pending_approval_cannot_resume_work_item(session: Session) -> None:
    workspace = _workspace(session, "work-item-approval-pending")
    work_item = _running_work_item(session, workspace)
    events: list[BusinessEvent] = []
    service = WorkItemApprovalService(session, event_recorder=events.append)
    requested = service.request_approval(workspace, work_item.id)

    with pytest.raises(WorkItemApprovalNotPermittedError, match="does not permit"):
        service.resume_after_approval(workspace, work_item.id, requested.approval.id)

    assert events[-1].event_type == WORK_ITEM_APPROVAL_RESUME_BLOCKED_EVENT


def test_cross_workspace_work_item_approval_request_is_rejected(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "work-item-approval-scope-a")
    workspace_b = _workspace(session, "work-item-approval-scope-b")
    work_item_a = _running_work_item(session, workspace_a)

    with pytest.raises(WorkItemNotFoundError, match="not found"):
        WorkItemApprovalService(session).request_approval(
            workspace_b,
            work_item_a.id,
        )


def test_cross_workspace_approval_lookup_is_rejected(session: Session) -> None:
    workspace_a = _workspace(session, "work-item-approval-lookup-a")
    workspace_b = _workspace(session, "work-item-approval-lookup-b")
    work_item_a = _running_work_item(session, workspace_a)
    requested = WorkItemApprovalService(session).request_approval(
        workspace_a,
        work_item_a.id,
    )

    with pytest.raises(WorkItemApprovalNotFoundError, match="not found"):
        WorkItemApprovalService(session).get_scoped_work_item_approval(
            workspace_b,
            requested.approval.id,
        )


def test_unrelated_work_item_cannot_use_another_work_items_approval(
    session: Session,
) -> None:
    workspace = _workspace(session, "work-item-approval-unrelated")
    work_item_a = _running_work_item(session, workspace)
    work_item_b = _running_work_item(session, workspace)
    service = WorkItemApprovalService(session)
    approval_a = service.request_approval(workspace, work_item_a.id).approval
    service.request_approval(workspace, work_item_b.id)

    with pytest.raises(WorkItemApprovalScopeError, match="not linked"):
        service.resume_after_approval(workspace, work_item_b.id, approval_a.id)


@pytest.mark.anyio
async def test_approval_decision_service_remains_human_authority(
    session: Session,
) -> None:
    workspace = _workspace(session, "work-item-approval-authority")
    work_item = _running_work_item(session, workspace)
    service = WorkItemApprovalService(session)
    approval = service.request_approval(workspace, work_item.id).approval

    approved = await _approve(session, workspace, approval)
    resumed = service.resume_after_approval(workspace, work_item.id, approved.id)

    assert approved.decided_by_user_id is not None
    assert resumed.work_item.status == WorkItemStatus.RUNNING


def test_terminal_work_item_cannot_request_approval(session: Session) -> None:
    workspace = _workspace(session, "work-item-approval-terminal")
    work_item = _running_work_item(session, workspace)
    work_item_service = WorkItemService(session)
    completed = work_item_service.transition_work_item(
        workspace,
        work_item.id,
        WorkItemStatus.COMPLETED,
        result={"done": True},
    )

    with pytest.raises(WorkItemApprovalInvalidStateError, match="must be running"):
        WorkItemApprovalService(session).request_approval(workspace, completed.id)


def test_invalid_work_item_transition_remains_rejected(session: Session) -> None:
    workspace = _workspace(session, "work-item-approval-invalid-transition")
    work_item = _running_work_item(session, workspace)
    service = WorkItemApprovalService(session)
    requested = service.request_approval(workspace, work_item.id)

    with pytest.raises(WorkItemInvalidStateTransitionError):
        WorkItemService(session).transition_work_item(
            workspace,
            requested.work_item.id,
            WorkItemStatus.ASSIGNED,
        )
