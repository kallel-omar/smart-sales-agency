from types import SimpleNamespace
from uuid import uuid4

from sqlmodel import select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.comment_triggers import InboundCommentChannel
from app.db import get_session
from app.main import app
from app.models import IntegrationAccount, Workspace
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.comment_trigger_rules import CommentTriggerRuleService
from app.services.departments import DepartmentService


def _workspace(client, slug: str) -> Workspace:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.replace("-", " ")})
    assert response.status_code == 201
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        return session.exec(select(Workspace).where(Workspace.slug == slug)).one()


def _configuration(session, workspace: Workspace, provider: str):
    department = DepartmentService(session).ensure_sales_department(workspace)
    employee = AIEmployeeService(session).create_for_department(
        workspace, department, AIEmployeeRoleKey.SALES_CONVERSATION
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace, department, BusinessCapabilityKey.SEND_MESSAGE
    )
    assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace, employee, capability
    )
    account = IntegrationAccount(
        workspace_id=workspace.id,
        provider=provider,
        external_account_id=f"external-{uuid4().hex}",
        secret_reference="INTEGRATION_SECRET_META_TEST",
        credential_hash=uuid4().hex,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    session.refresh(assignment)
    return (
        SimpleNamespace(id=account.id),
        SimpleNamespace(id=assignment.id),
    )


def _payload(account, assignment, **overrides):
    payload = {
        "integration_account_id": str(account.id),
        "channel": "facebook_comment",
        "name": "Interested prospects",
        "enabled": True,
        "keywords": ["Interested", "send details"],
        "content_external_id": "post-1",
        "dm_message": "Thanks for your interest.",
        "send_assignment_id": str(assignment.id),
    }
    payload.update(overrides)
    return payload


def test_authenticated_rule_api_is_workspace_scoped_and_updates(client):
    workspace_a = _workspace(client, "comment-rules-a")
    workspace_b = _workspace(client, "comment-rules-b")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account_a, assignment_a = _configuration(session, workspace_a, "facebook_messenger")

    created = client.post(
        "/api/integrations/comment-trigger-rules",
        headers={"X-Workspace-Slug": workspace_a.slug},
        json=_payload(account_a, assignment_a),
    )
    listed_a = client.get(
        "/api/integrations/comment-trigger-rules",
        headers={"X-Workspace-Slug": workspace_a.slug},
    )
    listed_b = client.get(
        "/api/integrations/comment-trigger-rules",
        headers={"X-Workspace-Slug": workspace_b.slug},
    )
    assert created.status_code == 201
    assert [rule["id"] for rule in listed_a.json()] == [created.json()["id"]]
    assert listed_b.json() == []

    foreign_update = client.patch(
        f"/api/integrations/comment-trigger-rules/{created.json()['id']}",
        headers={"X-Workspace-Slug": workspace_b.slug},
        json={"name": "Foreign update"},
    )
    updated = client.patch(
        f"/api/integrations/comment-trigger-rules/{created.json()['id']}",
        headers={"X-Workspace-Slug": workspace_a.slug},
        json={"keywords": ["  DETAILS  "], "clear_content_external_id": True},
    )
    disabled = client.post(
        f"/api/integrations/comment-trigger-rules/{created.json()['id']}/disable",
        headers={"X-Workspace-Slug": workspace_a.slug},
    )
    assert foreign_update.status_code == 404
    assert updated.status_code == 200
    assert updated.json()["keywords"] == ["DETAILS"]
    assert updated.json()["content_external_id"] is None
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


def test_rule_api_rejects_invalid_account_and_sender_assignment(client):
    workspace_a = _workspace(client, "comment-rules-validation-a")
    workspace_b = _workspace(client, "comment-rules-validation-b")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account_a, assignment_a = _configuration(session, workspace_a, "facebook_messenger")
        account_b, assignment_b = _configuration(session, workspace_b, "facebook_messenger")
        department_a = DepartmentService(session).ensure_sales_department(workspace_a)
        employee_a = AIEmployeeService(session).create_for_department(
            workspace_a, department_a, AIEmployeeRoleKey.LEAD_RESEARCH
        )
        research = CapabilityService(session).ensure_for_department(
            workspace_a, department_a, BusinessCapabilityKey.RESEARCH_COMPANY
        )
        research_assignment = AIEmployeeCapabilityAssignmentService(session).assign(
            workspace_a, employee_a, research
        )

    wrong_provider = client.post(
        "/api/integrations/comment-trigger-rules",
        headers={"X-Workspace-Slug": workspace_a.slug},
        json=_payload(account_a, assignment_a, channel="instagram_comment"),
    )
    foreign_account = client.post(
        "/api/integrations/comment-trigger-rules",
        headers={"X-Workspace-Slug": workspace_a.slug},
        json=_payload(account_b, assignment_a),
    )
    foreign_assignment = client.post(
        "/api/integrations/comment-trigger-rules",
        headers={"X-Workspace-Slug": workspace_a.slug},
        json=_payload(account_a, assignment_b),
    )
    wrong_capability = client.post(
        "/api/integrations/comment-trigger-rules",
        headers={"X-Workspace-Slug": workspace_a.slug},
        json=_payload(account_a, research_assignment),
    )
    assert wrong_provider.status_code == 422
    assert foreign_account.status_code == 422
    assert foreign_assignment.status_code == 422
    assert wrong_capability.status_code == 422


def test_matching_is_casefolded_phrase_scoped_and_ambiguous(client):
    workspace = _workspace(client, "comment-rule-matching")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account, assignment = _configuration(session, workspace, "facebook_messenger")
        service = CommentTriggerRuleService(session)
        scoped = service.create(
            workspace,
            integration_account_id=account.id,
            channel=InboundCommentChannel.FACEBOOK_COMMENT,
            name="Scoped phrase",
            enabled=True,
            keywords=["  SEND DÉTAILS  "],
            content_external_id="post-1",
            dm_message="Hello",
            send_assignment_id=assignment.id,
        )

        assert (
            service.match(
                workspace,
                account,
                InboundCommentChannel.FACEBOOK_COMMENT,
                "Please send détails today",
                "post-1",
            ).rule.id
            == scoped.id
        )
        assert (
            service.match(
                workspace,
                account,
                InboundCommentChannel.FACEBOOK_COMMENT,
                "Please send détails today",
                "post-2",
            ).rule
            is None
        )
        assert (
            service.match(
                workspace, account, InboundCommentChannel.FACEBOOK_COMMENT, "Unrelated", "post-1"
            ).rule
            is None
        )

        service.create(
            workspace,
            integration_account_id=account.id,
            channel=InboundCommentChannel.FACEBOOK_COMMENT,
            name="Second match",
            enabled=True,
            keywords=["détails"],
            content_external_id="post-1",
            dm_message="Hello again",
            send_assignment_id=assignment.id,
        )
        ambiguous = service.match(
            workspace, account, InboundCommentChannel.FACEBOOK_COMMENT, "SEND DÉTAILS", "post-1"
        )
        assert ambiguous.rule is None
        assert ambiguous.ambiguous is True
