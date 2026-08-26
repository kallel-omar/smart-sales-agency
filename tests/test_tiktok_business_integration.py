import hmac
import json
import time
from hashlib import sha256
from uuid import UUID

import pytest
from sqlmodel import select

from app.config import get_settings
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.core.comment_triggers import InboundCommentChannel
from app.db import get_session
from app.main import app
from app.models import (
    AIEmployeeCapabilityAssignment,
    ApprovalRequest,
    Capability,
    InboundExternalIdentity,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    IntegrationAccountAuditEvent,
    IntegrationAccountConnectionStatus,
    Lead,
    OutboundIntegrationAction,
    OutboundIntegrationActionType,
    OutboundIntegrationAuditEvent,
    OutboundIntegrationDeliveryAttempt,
    WorkItem,
    Workspace,
    utc_now,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import AIEmployeeCapabilityToolAccessService
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.comment_trigger_rules import (
    CommentTriggerRuleService,
    CommentTriggerRuleValidationError,
)
from app.services.delivery_adapters import (
    HttpxTikTokBusinessHttpTransport,
    TikTokBusinessHttpResponse,
)
from app.services.departments import DepartmentService

ENDPOINT = "/api/integrations/inbound-events/tiktok"
SECRET_REFERENCE = "INTEGRATION_SECRET_GENERIC_HMAC_TEST"
APP_SECRET = "test-generic-hmac-secret"
APP_ID = "test-tiktok-client-key"


@pytest.fixture(autouse=True)
def tiktok_settings_and_transport(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.tiktok_business_app_id = APP_ID
    calls = []

    def fake_post(self, url, *, payload, headers, timeout):
        del self
        calls.append(
            {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
        )
        return TikTokBusinessHttpResponse(
            status_code=200,
            headers={},
            body={
                "code": 0,
                "data": {"message": {"message_id": f"tt-delivery-{len(calls)}"}},
            },
        )

    monkeypatch.setattr(HttpxTikTokBusinessHttpTransport, "post", fake_post)
    return calls


def _create_workspace(client, slug, *, workforce=True):
    response = client.post(
        "/api/workspaces", json={"slug": slug, "name": slug.replace("-", " ")}
    )
    assert response.status_code == 201
    if workforce:
        _add_workforce(slug)


def _add_workforce(slug):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        department = DepartmentService(session).ensure_sales_department(workspace)
        capabilities = {
            item.key: item
            for item in CapabilityService(session).ensure_sales_capabilities(
                workspace, department
            )
        }
        employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.SALES_CONVERSATION,
            name="TikTok Sales",
        )
        assignments = AIEmployeeCapabilityAssignmentService(session)
        assignments.assign(
            workspace, employee, capabilities[BusinessCapabilityKey.ANSWER_CUSTOMER]
        )
        assignments.assign(
            workspace, employee, capabilities[BusinessCapabilityKey.SEND_MESSAGE]
        )


def _mark_test_account_connected(account_id: str, *, active: bool = True) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account = session.get(IntegrationAccount, UUID(account_id))
        assert account is not None
        account.connection_status = IntegrationAccountConnectionStatus.CONNECTED
        account.last_validated_at = utc_now()
        account.active = active
        session.add(account)
        session.commit()


def _create_account(
    client,
    slug,
    business_id,
    *,
    eligible=False,
    grant=True,
    autonomy=AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
):
    response = client.post(
        "/api/integrations/accounts",
        headers={"X-Workspace-Slug": slug},
        json={
            "provider": "tiktok_dm",
            "external_account_id": business_id,
            "secret_reference": SECRET_REFERENCE,
        },
    )
    assert response.status_code == 201
    account_data = response.json()
    for purpose in ("webhook_app_secret", "api_access_token"):
        configured = client.put(
            (
                f"/api/integrations/accounts/{account_data['id']}"
                f"/credential-references/{purpose}"
            ),
            headers={"X-Workspace-Slug": slug},
            json={"secret_reference": SECRET_REFERENCE},
        )
        assert configured.status_code == 200
    _mark_test_account_connected(account_data["id"])
    if grant:
        _grant(slug, UUID(account_data["id"]), autonomy)
    if eligible:
        updated = client.put(
            f"/api/integrations/accounts/{account_data['id']}/comment-to-message-eligibility",
            headers={"X-Workspace-Slug": slug},
            json={"eligible": True},
        )
        assert updated.status_code == 200
    return account_data


def _grant(slug, account_id, autonomy):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        account = session.get(IntegrationAccount, account_id)
        capability = session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.key == BusinessCapabilityKey.SEND_MESSAGE,
            )
        ).one()
        assignment = session.exec(
            select(AIEmployeeCapabilityAssignment).where(
                AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
                AIEmployeeCapabilityAssignment.capability_id == capability.id,
            )
        ).one()
        assert account is not None
        AIEmployeeCapabilityToolAccessService(session).grant(
            workspace,
            assignment,
            account,
            OutboundIntegrationActionType.SEND_MESSAGE,
            autonomy,
        )


def _direct_event(business_id, *, event_id="tt-message-1", sender="tt-person-1"):
    content = {
        "from_user": {"role": "personal_account"},
        "to_user": {"role": "business_account"},
        "unique_identifier": sender,
        "from": "TikTok Prospect",
        "type": "text",
        "text": {"body": "Can you share the price?"},
        "message_id": event_id,
        "conversation_id": f"conversation-{sender}",
        "timestamp": int(time.time()),
    }
    return {
        "client_key": APP_ID,
        "event": "im_receive_msg",
        "user_openid": business_id,
        "content": json.dumps(content, separators=(",", ":")),
    }


def _comment_event(business_id, *, event_id="tt-comment-1", sender="tt-person-1"):
    content = {
        "from_user": {"role": "personal_account"},
        "to_user": {"role": "business_account"},
        "unique_identifier": sender,
        "from": "TikTok Prospect",
        "comment_id": event_id,
        "comment_text": "Interested",
        "timestamp": int(time.time()),
    }
    return {
        "client_key": APP_ID,
        "event": "im_receive_high_intent_comment",
        "user_openid": business_id,
        "content": json.dumps(content, separators=(",", ":")),
    }


def _post(client, payload, *, valid=True):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        APP_SECRET.encode(), str(timestamp).encode() + b"." + body, sha256
    ).hexdigest()
    if not valid:
        signature = "0" * 64
    return client.post(
        ENDPOINT,
        headers={
            "Content-Type": "application/json",
            "TikTok-Signature": f"t={timestamp},s={signature}",
        },
        content=body,
    )


def _create_comment_rule(slug, account_id, *, scope=None):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        capability = session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.key == BusinessCapabilityKey.SEND_MESSAGE,
            )
        ).one()
        assignment = session.exec(
            select(AIEmployeeCapabilityAssignment).where(
                AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
                AIEmployeeCapabilityAssignment.capability_id == capability.id,
            )
        ).one()
        return CommentTriggerRuleService(session).create(
            workspace,
            integration_account_id=account_id,
            channel=InboundCommentChannel.TIKTOK_COMMENT,
            name="TikTok high-intent comments",
            enabled=True,
            keywords=["interested"],
            content_external_id=scope,
            dm_message="Thanks — here are the details.",
            send_assignment_id=assignment.id,
        )


def test_tiktok_active_ownership_allows_disconnect_reconnect_history(client):
    _create_workspace(client, "tt-owner-a")
    _create_workspace(client, "tt-owner-b")
    first = _create_account(client, "tt-owner-a", "shared-business")
    assert first["comment_to_message_eligible"] is False

    configured = client.post(
        "/api/integrations/accounts",
        headers={"X-Workspace-Slug": "tt-owner-b"},
        json={
            "provider": "tiktok_dm",
            "external_account_id": "shared-business",
            "secret_reference": SECRET_REFERENCE,
        },
    )
    assert configured.status_code == 201
    assert configured.json()["active"] is False
    _mark_test_account_connected(configured.json()["id"], active=False)
    conflict = client.post(
        f"/api/integrations/accounts/{configured.json()['id']}/reactivate",
        headers={"X-Workspace-Slug": "tt-owner-b"},
    )
    assert conflict.status_code == 409

    deactivated = client.post(
        f"/api/integrations/accounts/{first['id']}/deactivate",
        headers={"X-Workspace-Slug": "tt-owner-a"},
    )
    assert deactivated.status_code == 200
    second = configured.json()
    assert second["id"] != first["id"]

    reactivation = client.post(
        f"/api/integrations/accounts/{second['id']}/reactivate",
        headers={"X-Workspace-Slug": "tt-owner-b"},
    )
    assert reactivation.status_code == 200

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        rows = session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.provider == "tiktok_dm",
                IntegrationAccount.external_account_id == "shared-business",
            )
        ).all()
        assert len(rows) == 2
        assert sum(account.active for account in rows) == 1


def test_tiktok_direct_sales_path_is_native_idempotent_isolated_and_secret_safe(
    client, tiktok_settings_and_transport
):
    _create_workspace(client, "tt-direct")
    account = _create_account(client, "tt-direct", "business-direct")
    payload = _direct_event("business-direct")

    first = _post(client, payload)
    duplicate = _post(client, payload)

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200
    assert duplicate.json() == {"duplicate": True, "correlation_id": first.json()["correlation_id"]}
    assert len(tiktok_settings_and_transport) == 1
    provider_call = tiktok_settings_and_transport[0]
    assert provider_call["url"].startswith("https://business-api.tiktok.com/")
    assert provider_call["payload"]["recipient"] == "conversation-tt-person-1"

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored_account = session.get(IntegrationAccount, UUID(account["id"]))
        assert stored_account is not None
        actions = session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.integration_account_id == stored_account.id
            )
        ).all()
        attempts = session.exec(select(OutboundIntegrationDeliveryAttempt)).all()
        receipts = session.exec(select(InboundIntegrationEventReceipt)).all()
        identities = session.exec(select(InboundExternalIdentity)).all()
        assert len(actions) == len(attempts) == len(receipts) == len(identities) == 1
        assert actions[0].provider_delivery_id == "tt-delivery-1"
        persisted = " ".join(
            repr(item)
            for model in (
                IntegrationAccountAuditEvent,
                OutboundIntegrationAction,
                OutboundIntegrationAuditEvent,
                OutboundIntegrationDeliveryAttempt,
            )
            for item in session.exec(select(model)).all()
        )
        assert APP_SECRET not in persisted
        assert provider_call["headers"]["Access-Token"] == APP_SECRET
        assert stored_account.secret_reference == SECRET_REFERENCE

    unknown_account = _post(client, _direct_event("business-not-owned", event_id="other"))
    assert unknown_account.status_code == 401

    _create_workspace(client, "tt-direct-other")
    _create_account(client, "tt-direct-other", "business-other")
    isolated = _post(client, _direct_event("business-other"))
    assert isolated.status_code == 200, isolated.text
    assert len(tiktok_settings_and_transport) == 2
    with next(session_dependency()) as session:
        workspace_ids = {
            identity.workspace_id
            for identity in session.exec(select(InboundExternalIdentity)).all()
        }
        assert len(workspace_ids) == 2
        assert len(session.exec(select(Lead)).all()) == 2


def test_failed_tiktok_business_processing_releases_receipt_for_retry(
    client, tiktok_settings_and_transport
):
    _create_workspace(client, "tt-retry", workforce=False)
    _create_account(client, "tt-retry", "business-retry", grant=False)
    payload = _direct_event("business-retry", event_id="retry-event")

    failed = _post(client, payload)
    assert failed.status_code == 409
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []

    _add_workforce("tt-retry")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account = session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.external_account_id == "business-retry"
            )
        ).one()
        _account_id = account.id
    _grant("tt-retry", _account_id, AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION)
    retried = _post(client, payload)

    assert retried.status_code == 200, retried.text
    assert len(tiktok_settings_and_transport) == 1
    with next(session_dependency()) as session:
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1


def test_comment_to_message_is_default_off_then_uses_existing_social_sales_path(
    client, tiktok_settings_and_transport
):
    _create_workspace(client, "tt-comment")
    account = _create_account(client, "tt-comment", "business-comment")
    _create_comment_rule("tt-comment", UUID(account["id"]))

    ineligible = _post(client, _comment_event("business-comment", event_id="comment-off"))
    assert ineligible.status_code == 200, ineligible.text
    assert ineligible.json()["trigger_result"] == "provider_ineligible"
    assert ineligible.json()["lead_id"] is None
    assert tiktok_settings_and_transport == []

    enabled = client.put(
        f"/api/integrations/accounts/{account['id']}/comment-to-message-eligibility",
        headers={"X-Workspace-Slug": "tt-comment"},
        json={"eligible": True},
    )
    assert enabled.status_code == 200
    delivered = _post(client, _comment_event("business-comment", event_id="comment-on"))

    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["trigger_result"] == "outbound_delivered"
    assert delivered.json()["lead_id"] is not None
    assert tiktok_settings_and_transport[0]["payload"]["direct_reply"] == {
        "reply_type": "COMMENT_REPLY",
        "comment_reply": {"comment_id": "comment-on"},
    }


@pytest.mark.parametrize(
    ("grant", "autonomy", "expected", "approvals", "actions"),
    [
        (False, AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION, "tool_access_denied", 0, 0),
        (True, AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL, "approval_required", 1, 0),
    ],
)
def test_tiktok_comment_to_message_preserves_tool_and_approval_governance(
    client,
    tiktok_settings_and_transport,
    grant,
    autonomy,
    expected,
    approvals,
    actions,
):
    slug = f"tt-governance-{expected}"
    business_id = f"business-{expected}"
    _create_workspace(client, slug)
    account = _create_account(
        client,
        slug,
        business_id,
        eligible=True,
        grant=grant,
        autonomy=autonomy,
    )
    _create_comment_rule(slug, UUID(account["id"]))

    response = _post(client, _comment_event(business_id))

    assert response.status_code == 200, response.text
    assert response.json()["trigger_result"] == expected
    assert tiktok_settings_and_transport == []
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert len(session.exec(select(ApprovalRequest)).all()) == approvals
        assert len(session.exec(select(OutboundIntegrationAction)).all()) == actions
        assert len(session.exec(select(WorkItem)).all()) >= 1


def test_tiktok_comment_rules_are_account_wide_and_bad_signature_fails_closed(client):
    _create_workspace(client, "tt-rules")
    account = _create_account(client, "tt-rules", "business-rules")

    with pytest.raises(CommentTriggerRuleValidationError):
        _create_comment_rule("tt-rules", UUID(account["id"]), scope="video-1")

    rejected = _post(client, _direct_event("business-rules"), valid=False)
    assert rejected.status_code == 401

    unsafe_provisioning = client.post(
        "/api/integrations/accounts",
        headers={"X-Workspace-Slug": "tt-rules"},
        json={
            "provider": "tiktok_dm",
            "external_account_id": "cannot-enable-at-provisioning",
            "secret_reference": SECRET_REFERENCE,
            "comment_to_message_eligible": True,
        },
    )
    assert unsafe_provisioning.status_code == 422

    meta = client.post(
        "/api/integrations/accounts",
        headers={"X-Workspace-Slug": "tt-rules"},
        json={
            "provider": "facebook_messenger",
            "external_account_id": "page-rules",
            "secret_reference": SECRET_REFERENCE,
        },
    )
    assert meta.status_code == 201
    invalid_provider_gate = client.put(
        f"/api/integrations/accounts/{meta.json()['id']}/comment-to-message-eligibility",
        headers={"X-Workspace-Slug": "tt-rules"},
        json={"eligible": True},
    )
    assert invalid_provider_gate.status_code == 422

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []
