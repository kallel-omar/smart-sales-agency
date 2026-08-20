import hmac
import json
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.core.comment_triggers import InboundCommentChannel
from app.db import get_session
from app.main import app
from app.models import (
    AIEmployeeCapabilityAssignment,
    AIEmployeeCapabilityToolAccess,
    ApprovalRequest,
    Contact,
    InboundCommentTriggerRule,
    InboundExternalIdentity,
    IntegrationAccount,
    Lead,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    WorkItem,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import (
    AIEmployeeCapabilityToolAccessService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.comment_trigger_rules import CommentTriggerRuleService
from app.services.delivery_adapters import (
    DeliveryAdapterRegistry,
    NoopDeliveryAdapter,
)
from app.services.departments import DepartmentService
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.workspaces import ensure_workspace_lead_capture_foundation

ENDPOINT = "/api/integrations/inbound-events/meta"
META_SECRET_REFERENCE = "INTEGRATION_SECRET_META_TEST"
META_SECRET = "test-meta-comment-secret"


def _setup(
    client,
    *,
    slug: str,
    provider: str = "facebook_messenger",
    enabled: bool = True,
    keyword: str = "interested",
    scope: str | None = None,
):
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.replace("-", " ")})
    assert response.status_code == 201
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        ensure_workspace_lead_capture_foundation(session, workspace)
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
            external_account_id=f"meta-{uuid4().hex}",
            secret_reference=META_SECRET_REFERENCE,
            credential_hash=uuid4().hex,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        channel = (
            InboundCommentChannel.FACEBOOK_COMMENT
            if provider == "facebook_messenger"
            else InboundCommentChannel.INSTAGRAM_COMMENT
        )
        rule = CommentTriggerRuleService(session).create(
            workspace,
            integration_account_id=account.id,
            channel=channel,
            name=f"Rule {slug}",
            enabled=enabled,
            keywords=[keyword],
            content_external_id=scope,
            dm_message="Thanks - here are the details.",
            send_assignment_id=assignment.id,
        )
        session.refresh(workspace)
        session.refresh(account)
        session.refresh(assignment)
        session.refresh(rule)
        return (
            SimpleNamespace(id=workspace.id, slug=workspace.slug),
            SimpleNamespace(
                id=account.id,
                provider=account.provider,
                external_account_id=account.external_account_id,
            ),
            SimpleNamespace(id=assignment.id),
            SimpleNamespace(id=rule.id, dm_message=rule.dm_message),
        )


def _comment(account, *, event_id="comment-1", sender="social-user", text="Interested"):
    if account.provider == "facebook_messenger":
        return {
            "object": "page",
            "entry": [
                {
                    "id": account.external_account_id,
                    "time": 1_720_000_010,
                    "changes": [
                        {
                            "field": "feed",
                            "value": {
                                "item": "comment",
                                "verb": "add",
                                "comment_id": event_id,
                                "post_id": "post-1",
                                "message": text,
                                "created_time": 1_720_000_009,
                                "from": {"id": sender, "name": "Amina"},
                            },
                        }
                    ],
                }
            ],
        }
    return {
        "object": "instagram",
        "entry": [
            {
                "id": account.external_account_id,
                "time": 1_720_000_020,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": event_id,
                            "text": text,
                            "timestamp": 1_720_000_019,
                            "from": {"id": sender, "username": "amina"},
                            "media": {"id": "post-1"},
                        },
                    }
                ],
            }
        ],
    }


def _post(client, account, payload, *, valid=True):
    body = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(META_SECRET.encode(), body, sha256).hexdigest()
    return client.post(
        f"{ENDPOINT}/{account.id}",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={digest if valid else '0' * 64}",
        },
        content=body,
    )


def _grant(workspace, account, assignment, autonomy):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored_workspace = session.get(Workspace, workspace.id)
        stored_account = session.get(IntegrationAccount, account.id)
        stored_assignment = session.get(AIEmployeeCapabilityAssignment, assignment.id)
        AIEmployeeCapabilityToolAccessService(session).grant(
            stored_workspace,
            stored_assignment,
            stored_account,
            OutboundIntegrationActionType.SEND_MESSAGE,
            autonomy,
        )


def _enable_noop_meta_delivery(monkeypatch, provider):
    def from_settings(cls, session, settings, **kwargs):
        del cls, settings, kwargs
        return OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({provider: NoopDeliveryAdapter()}),
        )

    monkeypatch.setattr(
        OutboundIntegrationDeliveryService,
        "from_settings",
        classmethod(from_settings),
    )


@pytest.mark.parametrize("provider", ["facebook_messenger", "instagram_dm"])
@pytest.mark.parametrize("rule_state", ["missing", "disabled", "unmatched"])
def test_ordinary_comments_create_no_business_state(client, monkeypatch, provider, rule_state):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _, account, _, rule = _setup(
        client,
        slug=f"comment-noop-{provider}-{rule_state}",
        provider=provider,
        enabled=rule_state != "disabled",
    )
    session_dependency = app.dependency_overrides[get_session]
    if rule_state == "missing":
        with next(session_dependency()) as session:
            session.delete(session.get(InboundCommentTriggerRule, rule.id))
            session.commit()
    response = _post(
        client,
        account,
        _comment(account, text="Unrelated" if rule_state == "unmatched" else "Interested"),
    )
    assert response.status_code == 200
    assert response.json()["trigger_result"] == "no_match"
    with next(session_dependency()) as session:
        assert session.exec(select(InboundExternalIdentity)).all() == []
        assert session.exec(select(Contact)).all() == []
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(WorkItem)).all() == []
        assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.parametrize("provider", ["facebook_messenger", "instagram_dm"])
def test_matching_comment_captures_identity_lead_and_default_denied_send_work(
    client, monkeypatch, provider
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _, account, assignment, _ = _setup(
        client, slug=f"comment-capture-{provider}", provider=provider
    )

    response = _post(client, account, _comment(account, text="I am INTERESTED today"))

    assert response.status_code == 200
    assert response.json()["trigger_result"] == "tool_access_denied"
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identities = session.exec(select(InboundExternalIdentity)).all()
        contacts = session.exec(select(Contact)).all()
        leads = session.exec(select(Lead)).all()
        capture_items = session.exec(
            select(WorkItem).where(WorkItem.work_type == "lead_capture")
        ).all()
        send_item = session.exec(
            select(WorkItem).where(WorkItem.work_type == "social_comment_dm")
        ).one()
        assert len(identities) == len(contacts) == len(leads) == 1
        assert len(capture_items) == 1
        assert send_item.assignment_id == assignment.id
        assert send_item.status == "failed"
        assert send_item.error_code == "tool_access_denied"
        assert "secret" not in json.dumps(send_item.input).casefold()
        assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.parametrize(
    ("autonomy", "expected", "approval_count", "action_count"),
    [
        (AIEmployeeAutonomyLevel.SUGGEST, "suggested", 0, 0),
        (AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL, "approval_required", 1, 0),
        (AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION, "outbound_delivered", 0, 1),
        (AIEmployeeAutonomyLevel.HIGH_AUTOMATION, "outbound_delivered", 0, 1),
    ],
)
def test_autonomy_governs_comment_dm_delivery(
    client, monkeypatch, autonomy, expected, approval_count, action_count
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    workspace, account, assignment, rule = _setup(client, slug=f"comment-autonomy-{autonomy.value}")
    _grant(workspace, account, assignment, autonomy)
    _enable_noop_meta_delivery(monkeypatch, account.provider)

    response = _post(client, account, _comment(account))

    assert response.status_code == 200
    assert response.json()["trigger_result"] == expected
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        approvals = session.exec(select(ApprovalRequest)).all()
        actions = session.exec(select(OutboundIntegrationAction)).all()
        send_item = session.exec(
            select(WorkItem).where(WorkItem.work_type == "social_comment_dm")
        ).one()
        assert len(approvals) == approval_count
        assert len(actions) == action_count
        assert send_item.assignment_id == assignment.id
        if actions:
            assert actions[0].integration_account_id == account.id
            assert actions[0].content == rule.dm_message
            assert actions[0].status == OutboundIntegrationActionStatus.DELIVERED
            assert actions[0].payload["work_item_id"] == str(send_item.id)
            assert "secret" not in json.dumps(actions[0].payload).casefold()


def test_wrong_account_grant_is_denied_and_duplicate_comment_is_suppressed(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    workspace, account, assignment, _ = _setup(client, slug="comment-wrong-grant")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        wrong_account = IntegrationAccount(
            workspace_id=workspace.id,
            provider="facebook_messenger",
            external_account_id="other-page",
            secret_reference=META_SECRET_REFERENCE,
            credential_hash=uuid4().hex,
        )
        session.add(wrong_account)
        session.commit()
        session.refresh(wrong_account)
    _grant(
        workspace,
        wrong_account,
        assignment,
        AIEmployeeAutonomyLevel.HIGH_AUTOMATION,
    )
    payload = _comment(account)

    first = _post(client, account, payload)
    duplicate = _post(client, account, payload)

    assert first.json()["trigger_result"] == "tool_access_denied"
    assert duplicate.json()["duplicate"] is True
    with next(session_dependency()) as session:
        assert len(session.exec(select(WorkItem)).all()) == 2
        assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_ambiguous_rules_and_invalid_signature_fail_closed(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    workspace, account, assignment, _ = _setup(client, slug="comment-fail-closed")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        CommentTriggerRuleService(session).create(
            session.get(Workspace, workspace.id),
            integration_account_id=account.id,
            channel=InboundCommentChannel.FACEBOOK_COMMENT,
            name="Overlapping rule",
            enabled=True,
            keywords=["interested"],
            content_external_id=None,
            dm_message="Second message",
            send_assignment_id=assignment.id,
        )

    invalid = _post(client, account, _comment(account, event_id="invalid-signature"), valid=False)
    ambiguous = _post(client, account, _comment(account, event_id="ambiguous"))

    assert invalid.status_code == 401
    assert ambiguous.status_code == 200
    assert ambiguous.json()["trigger_result"] == "ambiguous"
    with next(session_dependency()) as session:
        assert session.exec(select(InboundExternalIdentity)).all() == []
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(WorkItem)).all() == []
        assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_identity_and_lead_reused_across_distinct_comments_and_failure_is_durable(
    client, monkeypatch
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    workspace, account, assignment, _ = _setup(client, slug="comment-reuse-failure")
    _grant(
        workspace,
        account,
        assignment,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )

    first = _post(client, account, _comment(account, event_id="comment-a"))
    second = _post(client, account, _comment(account, event_id="comment-b"))

    assert first.json()["trigger_result"] == "outbound_failed"
    assert second.json()["trigger_result"] == "outbound_failed"
    assert first.json()["lead_id"] == second.json()["lead_id"]
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert len(session.exec(select(InboundExternalIdentity)).all()) == 1
        assert len(session.exec(select(Contact)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 4
        actions = session.exec(select(OutboundIntegrationAction)).all()
        assert len(actions) == 2
        assert all(action.status == OutboundIntegrationActionStatus.FAILED for action in actions)
        assert len(session.exec(select(AIEmployeeCapabilityToolAccess)).all()) == 1
