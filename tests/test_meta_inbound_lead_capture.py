import hmac
import json
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.db import get_session
from app.main import app
from app.models import (
    AIEmployeeCapabilityAssignment,
    ApprovalRequest,
    ApprovalStatus,
    Capability,
    Contact,
    ConversationMessage,
    Department,
    InboundExternalIdentity,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    IntegrationCredentialReference,
    Lead,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    OutboundIntegrationAuditEvent,
    OutboundIntegrationDeliveryAttempt,
    WorkItem,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import AIEmployeeCapabilityToolAccessService
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.delivery_adapters import (
    HttpxMetaGraphHttpTransport,
    MetaGraphHttpResponse,
)
from app.services.departments import DepartmentService
from app.services.lead_capture import LeadCaptureService
from app.services.meta_inbound import (
    InboundExternalIdentityBindingError,
    InboundExternalIdentityService,
)

ENDPOINT = "/api/integrations/inbound-events/meta"
META_SECRET_REFERENCE = "INTEGRATION_SECRET_META_TEST"
META_SECRET = "test-meta-app-secret"


@pytest.fixture(autouse=True)
def fake_meta_graph_transport(monkeypatch):
    calls: list[dict] = []

    def fake_post(self, url, *, payload, headers, timeout):
        del self
        calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return MetaGraphHttpResponse(
            status_code=200,
            headers={},
            body={"message_id": f"mid.fake-{len(calls)}"},
        )

    monkeypatch.setattr(HttpxMetaGraphHttpTransport, "post", fake_post)
    return calls


def _workspace(client, slug: str) -> None:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.replace("-", " ")})
    assert response.status_code == 201
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        department = DepartmentService(session).ensure_sales_department(workspace)
        capabilities = {
            capability.key: capability
            for capability in CapabilityService(session).ensure_sales_capabilities(
                workspace, department
            )
        }
        employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.SALES_CONVERSATION,
            name="Meta Sales",
        )
        assignments = AIEmployeeCapabilityAssignmentService(session)
        assignments.assign(
            workspace,
            employee,
            capabilities[BusinessCapabilityKey.ANSWER_CUSTOMER],
        )
        assignments.assign(
            workspace,
            employee,
            capabilities[BusinessCapabilityKey.SEND_MESSAGE],
        )


def _account(
    client,
    workspace_slug: str,
    *,
    provider: str,
    external_account_id: str,
    provider_auth_mode: str | None = None,
    grant_tool_access: bool = True,
    autonomy_level: AIEmployeeAutonomyLevel = (AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION),
) -> dict:
    payload = {
        "provider": provider,
        "external_account_id": external_account_id,
        "secret_reference": META_SECRET_REFERENCE,
    }
    if provider_auth_mode is not None:
        payload["provider_auth_mode"] = provider_auth_mode
    response = client.post(
        "/api/integrations/accounts",
        headers={"X-Workspace-Slug": workspace_slug},
        json=payload,
    )
    assert response.status_code == 201
    account_data = response.json()
    for purpose in ("webhook_app_secret", "api_access_token"):
        configured = client.put(
            (f"/api/integrations/accounts/{account_data['id']}/credential-references/{purpose}"),
            headers={"X-Workspace-Slug": workspace_slug},
            json={"secret_reference": META_SECRET_REFERENCE},
        )
        assert configured.status_code == 200
    if grant_tool_access:
        session_dependency = app.dependency_overrides[get_session]
        with next(session_dependency()) as session:
            workspace = session.exec(
                select(Workspace).where(Workspace.slug == workspace_slug)
            ).one()
            account = session.get(IntegrationAccount, UUID(account_data["id"]))
            send_capability = session.exec(
                select(Capability).where(
                    Capability.workspace_id == workspace.id,
                    Capability.key == BusinessCapabilityKey.SEND_MESSAGE,
                )
            ).one()
            assignment = session.exec(
                select(AIEmployeeCapabilityAssignment).where(
                    AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
                    AIEmployeeCapabilityAssignment.capability_id == send_capability.id,
                )
            ).one()
            assert account is not None
            AIEmployeeCapabilityToolAccessService(session).grant(
                workspace,
                assignment,
                account,
                OutboundIntegrationActionType.SEND_MESSAGE,
                autonomy_level,
            )
    return account_data


def _facebook_message(
    account_id: str,
    *,
    sender_id: str = "fb-user-1",
    event_id: str = "m_fb_1",
    text: str = "Can you tell me the price?",
) -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": account_id,
                "time": 1_720_000_000,
                "messaging": [
                    {
                        "sender": {"id": sender_id, "name": "Sarra"},
                        "recipient": {"id": account_id},
                        "timestamp": 1_720_000_001,
                        "message": {"mid": event_id, "text": text},
                    }
                ],
            }
        ],
    }


def _instagram_message(
    account_id: str,
    *,
    sender_id: str = "ig-user-1",
    event_id: str = "m_ig_1",
) -> dict:
    payload = _facebook_message(
        account_id, sender_id=sender_id, event_id=event_id, text="Is this available?"
    )
    payload["object"] = "instagram"
    return payload


def _facebook_comment(account_id: str, event_id: str = "fb_comment_1") -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": account_id,
                "time": 1_720_000_010,
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": "comment",
                            "verb": "add",
                            "comment_id": event_id,
                            "post_id": "post-1",
                            "parent_id": "parent-1",
                            "message": "Interested",
                            "created_time": 1_720_000_009,
                            "from": {"id": "fb-commenter", "name": "Amina"},
                        },
                    }
                ],
            }
        ],
    }


def _instagram_comment(account_id: str, event_id: str = "ig_comment_1") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": account_id,
                "time": 1_720_000_020,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": event_id,
                            "text": "Please share details",
                            "timestamp": 1_720_000_019,
                            "from": {"id": "ig-commenter", "username": "amina"},
                            "media": {"id": "media-1"},
                        },
                    }
                ],
            }
        ],
    }


def _signed(account: dict, payload: dict, *, valid: bool = True) -> tuple[dict, bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(META_SECRET.encode(), body, sha256).hexdigest()
    return (
        {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={digest if valid else '0' * 64}",
        },
        body,
    )


def _post(client, account: dict, payload: dict, *, valid: bool = True):
    headers, body = _signed(account, payload, valid=valid)
    return client.post(f"{ENDPOINT}/{account['id']}", headers=headers, content=body)


@pytest.mark.parametrize(
    (
        "provider",
        "provider_auth_mode",
        "account_id",
        "payload_factory",
        "channel",
        "expected_host",
        "workspace_key",
    ),
    [
        (
            "facebook_messenger",
            None,
            "page-1",
            _facebook_message,
            "facebook_messenger",
            "graph.facebook.com",
            "messenger",
        ),
        (
            "instagram_dm",
            None,
            "ig-account-1",
            _instagram_message,
            "instagram_dm",
            "graph.facebook.com",
            "instagram-facebook-login",
        ),
        (
            "instagram_dm",
            "instagram_login",
            "ig-native-account-1",
            _instagram_message,
            "instagram_dm",
            "graph.instagram.com",
            "instagram-login",
        ),
    ],
)
def test_direct_message_captures_and_reuses_external_identity(
    client,
    monkeypatch,
    provider,
    provider_auth_mode,
    account_id,
    payload_factory,
    channel,
    expected_host,
    workspace_key,
    fake_meta_graph_transport,
    caplog,
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    workspace_slug = f"{workspace_key}-workspace"
    _workspace(client, workspace_slug)
    account = _account(
        client,
        workspace_slug,
        provider=provider,
        external_account_id=account_id,
        provider_auth_mode=provider_auth_mode,
    )

    first = _post(client, account, payload_factory(account_id))
    second = _post(
        client,
        account,
        payload_factory(account_id, event_id=f"m_{provider}_2"),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["lead_id"] == second.json()["lead_id"]
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identities = session.exec(select(InboundExternalIdentity)).all()
        contacts = session.exec(select(Contact)).all()
        leads = session.exec(select(Lead)).all()
        capture_items = session.exec(
            select(WorkItem).where(WorkItem.work_type == "lead_capture")
        ).all()
        messages = session.exec(select(ConversationMessage)).all()
        assert len(identities) == len(contacts) == len(leads) == 1
        assert identities[0].channel == channel
        assert str(identities[0].lead_id) == first.json()["lead_id"]
        assert len(capture_items) == 2
        assert len(messages) == 4
        assert capture_items[0].input["metadata"] == {
            "account_id": account_id,
            "timestamp": 1_720_000_001,
            "message_type": "text",
        }
        actions = session.exec(select(OutboundIntegrationAction)).all()
        attempts = session.exec(select(OutboundIntegrationDeliveryAttempt)).all()
        audits = session.exec(select(OutboundIntegrationAuditEvent)).all()
        assert len(actions) == len(attempts) == 2
        assert all(
            action.status == OutboundIntegrationActionStatus.DELIVERED
            and action.integration_account_id == UUID(account["id"])
            and action.external_target_id
            == payload_factory(account_id)["entry"][0]["messaging"][0]["sender"]["id"]
            and action.provider_delivery_id
            for action in actions
        )
        assert all(attempt.provider_delivery_id for attempt in attempts)
        assert len(audits) == 6

        def column_state(model) -> dict:
            return {
                attribute.key: getattr(model, attribute.key)
                for attribute in sa_inspect(model).mapper.column_attrs
            }

        persisted_state = json.dumps(
            {
                "actions": [column_state(action) for action in actions],
                "attempts": [column_state(attempt) for attempt in attempts],
                "audits": [column_state(audit) for audit in audits],
                "work_items": [column_state(item) for item in session.exec(select(WorkItem)).all()],
            },
            default=str,
            sort_keys=True,
        )
    assert len(fake_meta_graph_transport) == 2
    for call in fake_meta_graph_transport:
        assert call["url"].startswith(f"https://{expected_host}/")
        assert call["url"].endswith(f"/{account_id}/messages")
        assert call["payload"]["recipient"]["id"] in {"fb-user-1", "ig-user-1"}
        assert call["payload"]["message"]["text"]
        if provider == "facebook_messenger":
            assert call["payload"]["messaging_type"] == "RESPONSE"
        else:
            assert "messaging_type" not in call["payload"]
    externally_visible = first.text + second.text + caplog.text
    assert META_SECRET not in persisted_state
    assert META_SECRET not in externally_visible


@pytest.mark.parametrize(
    ("provider", "account_id", "payload_factory"),
    [
        ("facebook_messenger", "page-duplicate", _facebook_message),
        ("instagram_dm", "ig-duplicate", _instagram_message),
    ],
)
def test_duplicate_direct_event_is_suppressed(
    client,
    monkeypatch,
    provider,
    account_id,
    payload_factory,
    fake_meta_graph_transport,
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, f"{provider}-duplicate")
    account = _account(
        client,
        f"{provider}-duplicate",
        provider=provider,
        external_account_id=account_id,
    )
    payload = payload_factory(account_id)

    first = _post(client, account, payload)
    duplicate = _post(client, account, payload)

    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json() == {
        "duplicate": True,
        "correlation_id": first.json()["correlation_id"],
    }
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(Contact)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 3
        assert len(session.exec(select(ConversationMessage)).all()) == 2
    assert len(fake_meta_graph_transport) == 1


def test_external_identity_is_scoped_by_workspace_and_integration_account(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    for slug in ("meta-scope-a", "meta-scope-b"):
        _workspace(client, slug)
    account_a1 = _account(
        client,
        "meta-scope-a",
        provider="facebook_messenger",
        external_account_id="page-a1",
    )
    account_a2 = _account(
        client,
        "meta-scope-a",
        provider="facebook_messenger",
        external_account_id="page-a2",
    )
    account_b = _account(
        client,
        "meta-scope-b",
        provider="facebook_messenger",
        external_account_id="page-b",
    )

    responses = [
        _post(client, account_a1, _facebook_message("page-a1", sender_id="same-user")),
        _post(client, account_a2, _facebook_message("page-a2", sender_id="same-user")),
        _post(client, account_b, _facebook_message("page-b", sender_id="same-user")),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["lead_id"] for response in responses}) == 3
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identities = session.exec(select(InboundExternalIdentity)).all()
        actions = session.exec(select(OutboundIntegrationAction)).all()
        assert len(identities) == 3
        assert len(actions) == 3
        assert len({identity.integration_account_id for identity in identities}) == 3
        assert len({identity.workspace_id for identity in identities}) == 2
        assert all(action.status == OutboundIntegrationActionStatus.DELIVERED for action in actions)
        workspace_a = session.exec(select(Workspace).where(Workspace.slug == "meta-scope-a")).one()
        first_action_id = next(
            action.id for action in actions if action.workspace_id == workspace_a.id
        )
    assert (
        client.get(
            f"/api/integrations/outbound-actions/{first_action_id}",
            headers={"X-Workspace-Slug": "meta-scope-b"},
        ).status_code
        == 404
    )


def test_account_reference_mismatch_is_rejected_before_capture(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-mismatch")
    account = _account(
        client,
        "meta-mismatch",
        provider="facebook_messenger",
        external_account_id="page-trusted",
    )

    response = _post(client, account, _facebook_message("page-forged"))

    assert response.status_code == 404
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []


def test_invalid_meta_signature_does_not_provision_or_capture(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    session_dependency = app.dependency_overrides[get_session]
    credential = "legacy-meta-credential"
    with next(session_dependency()) as session:
        workspace = Workspace(slug="legacy-meta", name="Legacy Meta")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider="facebook_messenger",
            external_account_id="legacy-page",
            secret_reference=META_SECRET_REFERENCE,
            credential_hash=sha256(credential.encode()).hexdigest(),
        )
        session.add(account)
        session.commit()
        account_id = account.id
    account_read = {"id": str(account_id), "inbound_credential": credential}

    response = _post(client, account_read, _facebook_message("legacy-page"), valid=False)

    assert response.status_code == 401
    with next(session_dependency()) as session:
        assert session.exec(select(Department)).all() == []
        assert session.exec(select(Capability)).all() == []
        assert session.exec(select(Contact)).all() == []
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(WorkItem)).all() == []
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []


@pytest.mark.parametrize(
    ("provider", "account_id", "payload", "channel"),
    [
        (
            "facebook_messenger",
            "page-comment",
            _facebook_comment("page-comment"),
            "facebook_comment",
        ),
        (
            "instagram_dm",
            "ig-comment-account",
            _instagram_comment("ig-comment-account"),
            "instagram_comment",
        ),
    ],
)
def test_comments_are_normalized_and_deduplicated_without_business_capture(
    client, monkeypatch, provider, account_id, payload, channel
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, f"{provider}-comments")
    account = _account(
        client,
        f"{provider}-comments",
        provider=provider,
        external_account_id=account_id,
    )

    first = _post(client, account, payload)
    duplicate = _post(client, account, payload)

    assert first.status_code == duplicate.status_code == 200
    assert first.json()["channel"] == channel
    assert first.json()["event_type"] == "comment"
    assert first.json()["content"]
    assert duplicate.json()["duplicate"] is True
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert session.exec(select(InboundExternalIdentity)).all() == []
        assert session.exec(select(Contact)).all() == []
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(WorkItem)).all() == []
        assert session.exec(select(ConversationMessage)).all() == []
        assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_capture_failure_releases_meta_receipt_for_successful_retry(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-retry")
    account = _account(
        client,
        "meta-retry",
        provider="facebook_messenger",
        external_account_id="page-retry",
    )
    payload = _facebook_message("page-retry", event_id="m_retry")
    original_capture = LeadCaptureService.capture
    attempts = 0

    def fail_once(self, workspace_id, signal):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("forced capture failure")
        return original_capture(self, workspace_id, signal)

    monkeypatch.setattr(LeadCaptureService, "capture", fail_once)

    with pytest.raises(RuntimeError, match="forced capture failure"):
        _post(client, account, payload)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []
        assert session.exec(select(Lead)).all() == []
        identities = session.exec(select(InboundExternalIdentity)).all()
        contacts = session.exec(select(Contact)).all()
        assert len(identities) == len(contacts) == 1
        anchored_identity_id = identities[0].id
        anchored_contact_id = contacts[0].id
        assert identities[0].contact_id == anchored_contact_id
        assert identities[0].lead_id is None

    retry = _post(client, account, payload)
    duplicate = _post(client, account, payload)

    assert retry.status_code == duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    with next(session_dependency()) as session:
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert len(session.exec(select(InboundExternalIdentity)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 3
        assert len(session.exec(select(ConversationMessage)).all()) == 2
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert identity.id == anchored_identity_id
        assert identity.contact_id == anchored_contact_id


def test_identity_anchor_exists_before_capture_and_explicit_contact_is_used(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-identity-first")
    account = _account(
        client,
        "meta-identity-first",
        provider="facebook_messenger",
        external_account_id="page-identity-first",
    )
    original_capture = LeadCaptureService.capture
    observed = {}

    def observe_anchor(self, workspace_id, signal):
        identity = self.session.exec(select(InboundExternalIdentity)).one()
        contact = self.session.get(Contact, identity.contact_id)
        observed.update(
            identity_id=identity.id,
            contact_id=identity.contact_id,
            lead_id=identity.lead_id,
            signal_contact_id=signal.contact_id,
        )
        assert contact is not None and contact.workspace_id == workspace_id
        return original_capture(self, workspace_id, signal)

    monkeypatch.setattr(LeadCaptureService, "capture", observe_anchor)

    response = _post(
        client,
        account,
        _facebook_message("page-identity-first", event_id="m_identity_first"),
    )

    assert response.status_code == 200
    assert observed["lead_id"] is None
    assert observed["signal_contact_id"] == observed["contact_id"]
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.get(InboundExternalIdentity, observed["identity_id"])
        assert identity is not None
        assert str(identity.lead_id) == response.json()["lead_id"]


def test_external_identity_anchor_supports_sender_without_display_name(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-no-display-name")
    account = _account(
        client,
        "meta-no-display-name",
        provider="facebook_messenger",
        external_account_id="page-no-name",
    )
    payload = _facebook_message("page-no-name", event_id="m_no_name")
    payload["entry"][0]["messaging"][0]["sender"].pop("name")

    response = _post(client, account, payload)

    assert response.status_code == 200
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        contact = session.exec(select(Contact)).one()
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert contact.name is None
        assert identity.contact_id == contact.id


def test_post_capture_binding_failure_keeps_receipt_and_repairs_on_later_event(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-binding-recovery")
    account = _account(
        client,
        "meta-binding-recovery",
        provider="facebook_messenger",
        external_account_id="page-binding-recovery",
    )
    first_payload = _facebook_message("page-binding-recovery", event_id="m_binding_failure")
    original_bind = InboundExternalIdentityService.bind_lead

    def fail_binding(self, workspace, integration_account, identity, lead_id):
        raise InboundExternalIdentityBindingError("forced binding failure")

    monkeypatch.setattr(InboundExternalIdentityService, "bind_lead", fail_binding)

    first = _post(client, account, first_payload)
    duplicate = _post(client, account, first_payload)

    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert identity.lead_id is None
        anchored_contact_id = identity.contact_id
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 3

    monkeypatch.setattr(InboundExternalIdentityService, "bind_lead", original_bind)
    later = _post(
        client,
        account,
        _facebook_message("page-binding-recovery", event_id="m_binding_recovery_later"),
    )

    assert later.status_code == 200
    assert later.json()["lead_id"] == first.json()["lead_id"]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert identity.contact_id == anchored_contact_id
        assert str(identity.lead_id) == first.json()["lead_id"]
        assert len(session.exec(select(Contact)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 6


def test_identity_with_multiple_workspace_leads_fails_without_guessing(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-ambiguous-recovery")
    account = _account(
        client,
        "meta-ambiguous-recovery",
        provider="facebook_messenger",
        external_account_id="page-ambiguous",
    )

    def fail_binding(self, workspace, integration_account, identity, lead_id):
        raise InboundExternalIdentityBindingError("forced binding failure")

    monkeypatch.setattr(InboundExternalIdentityService, "bind_lead", fail_binding)
    first = _post(
        client,
        account,
        _facebook_message("page-ambiguous", event_id="m_ambiguous_first"),
    )
    assert first.status_code == 200

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == "meta-ambiguous-recovery")
        ).one()
        session.add(
            Lead(
                tenant_id=workspace.slug,
                contact_id=identity.contact_id,
                full_name="Another lead",
                company_name="Unknown company",
                source="facebook_messenger",
            )
        )
        session.commit()

    monkeypatch.undo()
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    ambiguous = _post(
        client,
        account,
        _facebook_message("page-ambiguous", event_id="m_ambiguous_second"),
    )

    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"] == "External identity has multiple linked Leads"
    with next(session_dependency()) as session:
        assert len(session.exec(select(Lead)).all()) == 2
        assert len(session.exec(select(WorkItem)).all()) == 3
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1


def test_lead_recovery_ignores_leads_from_another_workspace(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-recovery-local")
    _workspace(client, "meta-recovery-foreign")
    account = _account(
        client,
        "meta-recovery-local",
        provider="facebook_messenger",
        external_account_id="page-recovery-local",
    )
    payload = _facebook_message("page-recovery-local", event_id="m_recovery_scope")
    original_capture = LeadCaptureService.capture

    def fail_capture(self, workspace_id, signal):
        raise RuntimeError("forced capture failure")

    monkeypatch.setattr(LeadCaptureService, "capture", fail_capture)
    with pytest.raises(RuntimeError, match="forced capture failure"):
        _post(client, account, payload)

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        foreign_workspace = session.exec(
            select(Workspace).where(Workspace.slug == "meta-recovery-foreign")
        ).one()
        foreign_lead = Lead(
            tenant_id=foreign_workspace.slug,
            contact_id=identity.contact_id,
            full_name="Foreign lead",
            company_name="Unknown company",
            source="facebook_messenger",
        )
        session.add(foreign_lead)
        session.commit()
        session.refresh(foreign_lead)
        foreign_lead_id = foreign_lead.id

    monkeypatch.setattr(LeadCaptureService, "capture", original_capture)
    retry = _post(client, account, payload)

    assert retry.status_code == 200
    assert UUID(retry.json()["lead_id"]) != foreign_lead_id
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        local_workspace = session.exec(
            select(Workspace).where(Workspace.slug == "meta-recovery-local")
        ).one()
        local_lead = session.get(Lead, identity.lead_id)
        assert local_lead is not None
        assert local_lead.tenant_id == local_workspace.slug
        assert len(session.exec(select(Contact)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 3


def test_external_identity_unique_scope_is_enforced(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-identity-unique")
    account = _account(
        client,
        "meta-identity-unique",
        provider="facebook_messenger",
        external_account_id="page-identity-unique",
    )
    response = _post(
        client,
        account,
        _facebook_message("page-identity-unique", event_id="m_identity_unique"),
    )
    assert response.status_code == 200

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        session.add(
            InboundExternalIdentity(
                workspace_id=identity.workspace_id,
                integration_account_id=identity.integration_account_id,
                channel=identity.channel,
                external_subject_id=identity.external_subject_id,
                contact_id=identity.contact_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert len(session.exec(select(InboundExternalIdentity)).all()) == 1


def test_meta_webhook_verification_uses_account_credential_reference(client, monkeypatch):
    slug = "meta-webhook-verify"
    verify_reference = "INTEGRATION_SECRET_META_VERIFY_TOKEN"
    verify_token = "synthetic-meta-verify-token"
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    monkeypatch.setenv(verify_reference, verify_token)
    _workspace(client, slug)
    account = _account(
        client,
        slug,
        provider="facebook_messenger",
        external_account_id="verify-page",
    )
    configured = client.put(
        (f"/api/integrations/accounts/{account['id']}/credential-references/webhook_verify_token"),
        headers={"X-Workspace-Slug": slug},
        json={"secret_reference": verify_reference},
    )
    assert configured.status_code == 200
    endpoint = f"{ENDPOINT}/{account['id']}"

    verified = client.get(
        endpoint,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": verify_token,
            "hub.challenge": "meta-challenge",
        },
    )
    rejected = client.get(
        endpoint,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "meta-challenge",
        },
    )

    assert verified.status_code == 200
    assert verified.text == "meta-challenge"
    assert rejected.status_code == 401


def test_direct_meta_invalid_credential_reference_fails_signature_authentication(
    client, monkeypatch
):
    slug = "meta-invalid-secret-reference"
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, slug)
    account = _account(
        client,
        slug,
        provider="instagram_dm",
        external_account_id="invalid-secret-ig",
    )
    configured = client.put(
        (f"/api/integrations/accounts/{account['id']}/credential-references/webhook_app_secret"),
        headers={"X-Workspace-Slug": slug},
        json={"secret_reference": "INTEGRATION_SECRET_META_NOT_CONFIGURED"},
    )
    assert configured.status_code == 200

    response = _post(client, account, _instagram_message("invalid-secret-ig"))

    assert response.status_code == 401
    with next(app.dependency_overrides[get_session]()) as session:
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []


def test_direct_meta_missing_access_token_fails_delivery_without_http(
    client, monkeypatch, fake_meta_graph_transport
):
    slug = "meta-missing-access-token"
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, slug)
    account = _account(
        client,
        slug,
        provider="facebook_messenger",
        external_account_id="missing-token-page",
    )
    with next(app.dependency_overrides[get_session]()) as session:
        reference = session.exec(
            select(IntegrationCredentialReference).where(
                IntegrationCredentialReference.integration_account_id == UUID(account["id"]),
                IntegrationCredentialReference.purpose == "api_access_token",
            )
        ).one()
        session.delete(reference)
        session.commit()

    response = _post(
        client,
        account,
        _facebook_message("missing-token-page", event_id="missing-token-event"),
    )

    assert response.status_code == 200
    assert fake_meta_graph_transport == []
    with next(app.dependency_overrides[get_session]()) as session:
        action = session.exec(select(OutboundIntegrationAction)).one()
        assert action.status == OutboundIntegrationActionStatus.FAILED
        assert action.failure_code == "meta_access_token_reference_missing"


def test_direct_meta_approval_required_returns_approval_without_delivery(
    client, monkeypatch, fake_meta_graph_transport
):
    slug = "meta-direct-approval"
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, slug)
    account = _account(
        client,
        slug,
        provider="instagram_dm",
        external_account_id="approval-ig",
        provider_auth_mode="instagram_login",
        autonomy_level=AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL,
    )

    response = _post(
        client,
        account,
        _instagram_message("approval-ig", event_id="approval-event"),
    )

    assert response.status_code == 200
    assert response.json()["approval_id"] is not None
    assert fake_meta_graph_transport == []
    with next(app.dependency_overrides[get_session]()) as session:
        approval = session.get(ApprovalRequest, UUID(response.json()["approval_id"]))
        assert approval is not None and approval.status == ApprovalStatus.PENDING
        assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_direct_meta_missing_tool_access_is_unroutable_without_outbound_action(
    client, monkeypatch, fake_meta_graph_transport
):
    slug = "meta-direct-tool-denied"
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, slug)
    account = _account(
        client,
        slug,
        provider="instagram_dm",
        external_account_id="denied-ig",
        provider_auth_mode="instagram_login",
        grant_tool_access=False,
    )

    response = _post(
        client,
        account,
        _instagram_message("denied-ig", event_id="denied-event"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "No eligible send_message AIEmployee assignment is configured"
    )
    assert fake_meta_graph_transport == []
    with next(app.dependency_overrides[get_session]()) as session:
        send_item = session.exec(
            select(WorkItem).where(WorkItem.work_type == "sales_reply_message")
        ).one()
        assert send_item.status == "created"
        assert send_item.assignment_id is None
        assert send_item.error_code is None
        assert session.exec(select(OutboundIntegrationAction)).all() == []
