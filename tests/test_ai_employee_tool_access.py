from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.integrations.providers import GENERIC_HMAC_PROVIDER
from app.models import (
    AIEmployeeCapabilityAssignment,
    IntegrationAccount,
    OutboundIntegrationActionType,
    Workspace,
)
from app.schemas import AIEmployeeCapabilityToolAccessRead
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentScopeError,
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import (
    AIEmployeeCapabilityToolAccessScopeError,
    AIEmployeeCapabilityToolAccessService,
    DuplicateAIEmployeeCapabilityToolAccessError,
    IncompatibleAIEmployeeCapabilityActionError,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService


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


def _integration_account(
    session: Session,
    workspace: Workspace,
    slug: str,
) -> IntegrationAccount:
    account = IntegrationAccount(
        workspace_id=workspace.id,
        provider=GENERIC_HMAC_PROVIDER,
        credential_hash=f"{slug}-credential-hash",
        active=True,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _assignment(
    session: Session,
    workspace: Workspace,
    capability_key: BusinessCapabilityKey,
) -> AIEmployeeCapabilityAssignment:
    department = DepartmentService(session).ensure_sales_department(workspace)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        capability_key,
    )
    return AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )


def test_valid_send_message_grant_for_send_message_capability(session: Session) -> None:
    workspace = _workspace(session, "tool-access-valid")
    assignment = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    account = _integration_account(session, workspace, "tool-access-valid")
    service = AIEmployeeCapabilityToolAccessService(session)

    grant = service.grant(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )
    decision = service.evaluate(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
    )
    read = AIEmployeeCapabilityToolAccessRead.model_validate(grant)

    assert read.workspace_id == workspace.id
    assert read.assignment_id == assignment.id
    assert read.integration_account_id == account.id
    assert read.action_type == OutboundIntegrationActionType.SEND_MESSAGE
    assert read.autonomy_level == AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION
    assert decision.allowed is True
    assert decision.requires_human_approval is False
    assert decision.may_execute_automatically is True


def test_default_deny_when_no_active_grant_exists(session: Session) -> None:
    workspace = _workspace(session, "tool-access-default-deny")
    assignment = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    account = _integration_account(session, workspace, "tool-access-default-deny")

    decision = AIEmployeeCapabilityToolAccessService(session).evaluate(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
    )

    assert decision.allowed is False
    assert decision.autonomy_level is None
    assert decision.requires_human_approval is False
    assert decision.may_execute_automatically is False
    assert decision.denial_reason == "no_active_grant"


def test_inactive_grant_denies(session: Session) -> None:
    workspace = _workspace(session, "tool-access-inactive")
    assignment = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    account = _integration_account(session, workspace, "tool-access-inactive")
    service = AIEmployeeCapabilityToolAccessService(session)
    grant = service.grant(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
        AIEmployeeAutonomyLevel.HIGH_AUTOMATION,
    )
    grant.active = False
    session.add(grant)
    session.commit()

    decision = service.evaluate(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
    )

    assert decision.allowed is False
    assert decision.autonomy_level == AIEmployeeAutonomyLevel.HIGH_AUTOMATION
    assert decision.requires_human_approval is False
    assert decision.may_execute_automatically is False
    assert decision.denial_reason == "grant_inactive"


def test_duplicate_grant_is_rejected(session: Session) -> None:
    workspace = _workspace(session, "tool-access-duplicate")
    assignment = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    account = _integration_account(session, workspace, "tool-access-duplicate")
    service = AIEmployeeCapabilityToolAccessService(session)
    service.grant(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
        AIEmployeeAutonomyLevel.SUGGEST,
    )

    with pytest.raises(
        DuplicateAIEmployeeCapabilityToolAccessError,
        match="already has this tool access grant",
    ):
        service.grant(
            workspace,
            assignment,
            account,
            OutboundIntegrationActionType.SEND_MESSAGE,
            AIEmployeeAutonomyLevel.HIGH_AUTOMATION,
        )


def test_cross_workspace_assignment_is_rejected(session: Session) -> None:
    workspace_a = _workspace(session, "tool-access-assignment-a")
    workspace_b = _workspace(session, "tool-access-assignment-b")
    assignment_b = _assignment(session, workspace_b, BusinessCapabilityKey.SEND_MESSAGE)
    account_a = _integration_account(session, workspace_a, "tool-access-assignment-a")

    with pytest.raises(
        AIEmployeeCapabilityAssignmentScopeError,
        match="does not belong to this workspace",
    ):
        AIEmployeeCapabilityToolAccessService(session).grant(
            workspace_a,
            assignment_b,
            account_a,
            OutboundIntegrationActionType.SEND_MESSAGE,
            AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
        )


def test_cross_workspace_integration_account_is_rejected(session: Session) -> None:
    workspace_a = _workspace(session, "tool-access-account-a")
    workspace_b = _workspace(session, "tool-access-account-b")
    assignment_a = _assignment(session, workspace_a, BusinessCapabilityKey.SEND_MESSAGE)
    account_b = _integration_account(session, workspace_b, "tool-access-account-b")

    with pytest.raises(
        AIEmployeeCapabilityToolAccessScopeError,
        match="Integration account does not belong to this workspace",
    ):
        AIEmployeeCapabilityToolAccessService(session).grant(
            workspace_a,
            assignment_a,
            account_b,
            OutboundIntegrationActionType.SEND_MESSAGE,
            AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
        )


def test_incompatible_capability_action_is_rejected(session: Session) -> None:
    workspace = _workspace(session, "tool-access-incompatible")
    assignment = _assignment(session, workspace, BusinessCapabilityKey.RESEARCH_COMPANY)
    account = _integration_account(session, workspace, "tool-access-incompatible")

    with pytest.raises(
        IncompatibleAIEmployeeCapabilityActionError,
        match="not compatible",
    ):
        AIEmployeeCapabilityToolAccessService(session).grant(
            workspace,
            assignment,
            account,
            OutboundIntegrationActionType.SEND_MESSAGE,
            AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
        )


def test_same_integration_action_can_be_granted_to_different_employees(
    session: Session,
) -> None:
    workspace = _workspace(session, "tool-access-different-employees")
    assignment_a = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    assignment_b = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    account = _integration_account(session, workspace, "tool-access-different-employees")
    service = AIEmployeeCapabilityToolAccessService(session)

    grant_a = service.grant(
        workspace,
        assignment_a,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )
    grant_b = service.grant(
        workspace,
        assignment_b,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )

    assert assignment_a.ai_employee_id != assignment_b.ai_employee_id
    assert assignment_a.capability_id == assignment_b.capability_id
    assert grant_a.id != grant_b.id


def test_one_employee_can_use_different_allowed_integrations(session: Session) -> None:
    workspace = _workspace(session, "tool-access-different-integrations")
    assignment = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    account_a = _integration_account(
        session,
        workspace,
        "tool-access-different-integrations-a",
    )
    account_b = _integration_account(
        session,
        workspace,
        "tool-access-different-integrations-b",
    )
    service = AIEmployeeCapabilityToolAccessService(session)

    grant_a = service.grant(
        workspace,
        assignment,
        account_a,
        OutboundIntegrationActionType.SEND_MESSAGE,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )
    grant_b = service.grant(
        workspace,
        assignment,
        account_b,
        OutboundIntegrationActionType.SEND_MESSAGE,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )

    assert grant_a.integration_account_id == account_a.id
    assert grant_b.integration_account_id == account_b.id
    assert grant_a.id != grant_b.id
    assert service.list_for_assignment(workspace, assignment) == [grant_a, grant_b]


@pytest.mark.parametrize(
    ("autonomy_level", "requires_approval", "may_execute"),
    [
        (AIEmployeeAutonomyLevel.SUGGEST, False, False),
        (AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL, True, False),
        (AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION, False, True),
        (AIEmployeeAutonomyLevel.HIGH_AUTOMATION, False, True),
    ],
)
def test_autonomy_policy_decision(
    session: Session,
    autonomy_level: AIEmployeeAutonomyLevel,
    requires_approval: bool,
    may_execute: bool,
) -> None:
    workspace = _workspace(session, f"tool-access-autonomy-{autonomy_level.value}")
    assignment = _assignment(session, workspace, BusinessCapabilityKey.SEND_MESSAGE)
    account = _integration_account(
        session,
        workspace,
        f"tool-access-autonomy-{autonomy_level.value}",
    )
    service = AIEmployeeCapabilityToolAccessService(session)
    service.grant(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
        autonomy_level,
    )

    decision = service.evaluate(
        workspace,
        assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
    )

    assert decision.allowed is True
    assert decision.autonomy_level == autonomy_level
    assert decision.requires_human_approval is requires_approval
    assert decision.may_execute_automatically is may_execute
