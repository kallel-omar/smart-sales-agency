from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    AIInvocationUsage,
    Contact,
    ConversationMessage,
    InboundExternalIdentity,
    IntegrationAccount,
    IntegrationAccountAuditEvent,
    Lead,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationDeliveryAttempt,
    SalesConversationHandoff,
    SalesConversationHandoffStatus,
    SalesHandoffReasonCode,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.delivery_adapters import (
    DEFAULT_DELIVERY_ADAPTER_CAPABILITIES,
    DeliveryAdapterRegistry,
    DeliveryAdapterResult,
)
from app.services.human_handoff_operations import (
    HUMAN_OUTBOUND_DIRECTION,
    HumanHandoffOperationsService,
    HumanReplyIdempotencyConflictError,
)
from app.services.operator_assignments import OperatorAssignmentActor
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.repository import SalesRepository


class RecordingAdapter:
    capabilities = DEFAULT_DELIVERY_ADAPTER_CAPABILITIES

    def __init__(self, *, delivered: bool = True) -> None:
        self.delivered = delivered
        self.actions = []

    def deliver(self, action, account):
        self.actions.append((action.id, account.id, action.content, action.external_target_id))
        if self.delivered:
            return DeliveryAdapterResult.success("provider-human-1")
        return DeliveryAdapterResult.failure("temporary_failure", "Provider temporarily unavailable")


def _session():
    return next(app.dependency_overrides[get_session]())


def _workspace_fixture(
    slug: str,
    *,
    member_role: WorkspaceMemberRole = WorkspaceMemberRole.ADMIN,
) -> SimpleNamespace:
    with _session() as session:
        workspace = Workspace(slug=slug, name=f"{slug} workspace")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)

        lead = Lead(
            tenant_id=workspace.slug,
            full_name="Pilot Prospect",
            company_name="Pilot Company",
            email="prospect@example.test",
            source="generic_hmac",
        )
        contact = Contact(workspace_id=workspace.id, name="Pilot Prospect")
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider="generic_hmac",
            external_account_id=f"account-{slug}",
            credential_hash=sha256(f"credential-{slug}".encode()).hexdigest(),
            active=True,
        )
        session.add_all([lead, contact, account])
        session.commit()
        session.refresh(lead)
        session.refresh(contact)
        session.refresh(account)

        identity = InboundExternalIdentity(
            workspace_id=workspace.id,
            integration_account_id=account.id,
            channel="generic_hmac",
            external_subject_id=f"customer-{slug}",
            contact_id=contact.id,
            lead_id=lead.id,
        )
        message = ConversationMessage(
            lead_id=lead.id,
            direction="inbound",
            channel="generic_hmac",
            content="I need a person to help with this request.",
        )
        session.add_all([identity, message])
        session.commit()
        handoff = SalesRepository(session).ensure_sales_handoff(
            workspace=workspace,
            lead=lead,
            reason_code=SalesHandoffReasonCode.HUMAN_REQUESTED,
            explanation="A human operator needs to continue this conversation.",
        )

        fixture_user = session.exec(
            select(User).where(User.email == "fixture-operator@example.com")
        ).one()
        membership = WorkspaceMember(
            workspace_id=workspace.id,
            user_id=fixture_user.id,
            role=member_role,
        )
        session.add(membership)
        session.commit()
        session.refresh(membership)
        return SimpleNamespace(
            workspace_id=workspace.id,
            workspace_slug=workspace.slug,
            lead_id=lead.id,
            handoff_id=handoff.id,
            account_id=account.id,
            user_id=fixture_user.id,
            membership_id=membership.id,
        )


def _actor(fixture: SimpleNamespace) -> OperatorAssignmentActor:
    return OperatorAssignmentActor(
        user_id=fixture.user_id,
        membership_id=fixture.membership_id,
        workspace_id=fixture.workspace_id,
        role=WorkspaceMemberRole.ADMIN,
    )


def _service(session, adapter: RecordingAdapter) -> HumanHandoffOperationsService:
    delivery = OutboundIntegrationDeliveryService(
        session,
        adapter_registry=DeliveryAdapterRegistry({"generic_hmac": adapter}),
    )
    return HumanHandoffOperationsService(session, delivery_service=delivery)


def test_authorized_operator_lists_active_handoffs_with_safe_context_and_pagination(client):
    active = _workspace_fixture("handoff-list")
    with _session() as session:
        workspace = session.get(Workspace, active.workspace_id)
        lead = Lead(
            tenant_id=workspace.slug,
            full_name="Resolved Prospect",
            company_name="Resolved Company",
        )
        session.add(lead)
        session.commit()
        session.refresh(lead)
        resolved = SalesRepository(session).ensure_sales_handoff(
            workspace=workspace,
            lead=lead,
            reason_code=SalesHandoffReasonCode.CUSTOM_PRICING_REQUIRED,
            explanation="Pricing needs human review.",
        )
        resolved.status = SalesConversationHandoffStatus.RESOLVED
        session.add(resolved)
        session.commit()

    response = client.get(
        "/api/operator/handoffs?limit=1&offset=0",
        headers={"X-Workspace-Slug": active.workspace_slug},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    item = response.json()[0]
    assert item["id"] == str(active.handoff_id)
    assert item["lead"]["full_name"] == "Pilot Prospect"
    assert item["reason_code"] == "human_requested"
    assert item["status"] == "active"
    assert item["updated_at"] == item["created_at"]
    assert "notes" not in item["lead"]
    assert "prompt" not in item


def test_handoff_list_and_detail_are_tenant_isolated(client):
    workspace_a = _workspace_fixture("handoff-scope-a")
    workspace_b = _workspace_fixture("handoff-scope-b")

    list_b = client.get(
        "/api/operator/handoffs",
        headers={"X-Workspace-Slug": workspace_b.workspace_slug},
    )
    foreign_detail = client.get(
        f"/api/operator/handoffs/{workspace_a.handoff_id}",
        headers={"X-Workspace-Slug": workspace_b.workspace_slug},
    )
    detail_a = client.get(
        f"/api/operator/handoffs/{workspace_a.handoff_id}?context_limit=1",
        headers={"X-Workspace-Slug": workspace_a.workspace_slug},
    )

    assert list_b.status_code == 200
    assert {item["id"] for item in list_b.json()} == {str(workspace_b.handoff_id)}
    assert foreign_detail.status_code == 404
    assert detail_a.status_code == 200
    assert detail_a.json()["messages"][0]["content"] == (
        "I need a person to help with this request."
    )


def test_human_reply_uses_outbound_delivery_without_ai_and_is_idempotent(client):
    fixture = _workspace_fixture("handoff-reply")
    adapter = RecordingAdapter()
    with _session() as session:
        workspace = session.get(Workspace, fixture.workspace_id)
        service = _service(session, adapter)
        first = service.send_human_reply(
            workspace=workspace,
            handoff_id=fixture.handoff_id,
            content="I am taking over and will help you directly.",
            idempotency_key="human-reply-1",
            actor=_actor(fixture),
        )
        duplicate = service.send_human_reply(
            workspace=workspace,
            handoff_id=fixture.handoff_id,
            content="I am taking over and will help you directly.",
            idempotency_key="human-reply-1",
            actor=_actor(fixture),
        )
        actions = list(session.exec(select(OutboundIntegrationAction)).all())
        messages = list(
            session.exec(
                select(ConversationMessage).where(
                    ConversationMessage.direction == HUMAN_OUTBOUND_DIRECTION
                )
            ).all()
        )
        attempts = list(session.exec(select(OutboundIntegrationDeliveryAttempt)).all())
        audits = list(session.exec(select(IntegrationAccountAuditEvent)).all())
        handoff = session.get(SalesConversationHandoff, fixture.handoff_id)

    assert first.action.status == OutboundIntegrationActionStatus.DELIVERED
    assert first.message is not None
    assert first.message.direction == HUMAN_OUTBOUND_DIRECTION
    assert first.action.requires_approval is False
    assert first.action.owner_reference == f"user:{fixture.user_id}"
    assert duplicate.duplicate is True
    assert len(adapter.actions) == 1
    assert len(actions) == 1
    assert len(messages) == 1
    assert len(attempts) == 1
    assert handoff.status == SalesConversationHandoffStatus.ACTIVE
    assert any(
        event.reason_code == f"human_handoff_reply_{fixture.handoff_id.hex}"
        and event.actor_user_id == fixture.user_id
        for event in audits
    )
    assert list(session.exec(select(AIInvocationUsage)).all()) == []


def test_human_reply_idempotency_key_reuse_with_different_content_conflicts(client):
    fixture = _workspace_fixture("handoff-reply-conflict")
    adapter = RecordingAdapter()
    with _session() as session:
        workspace = session.get(Workspace, fixture.workspace_id)
        service = _service(session, adapter)
        service.send_human_reply(
            workspace=workspace,
            handoff_id=fixture.handoff_id,
            content="First human reply.",
            idempotency_key="same-key",
            actor=_actor(fixture),
        )
        with pytest.raises(HumanReplyIdempotencyConflictError):
            service.send_human_reply(
                workspace=workspace,
                handoff_id=fixture.handoff_id,
                content="Different human reply.",
                idempotency_key="same-key",
                actor=_actor(fixture),
            )
    assert len(adapter.actions) == 1


def test_provider_failure_is_recorded_without_history_or_handoff_resolution(client):
    fixture = _workspace_fixture("handoff-reply-failure")
    adapter = RecordingAdapter(delivered=False)
    with _session() as session:
        workspace = session.get(Workspace, fixture.workspace_id)
        service = _service(session, adapter)
        result = service.send_human_reply(
            workspace=workspace,
            handoff_id=fixture.handoff_id,
            content="A provider-safe human reply.",
            idempotency_key="failed-reply",
            actor=_actor(fixture),
        )
        initial_status = result.action.status
        initial_failure_message = result.action.failure_message
        initial_message = result.message
        handoff = session.get(SalesConversationHandoff, fixture.handoff_id)
        messages = list(
            session.exec(
                select(ConversationMessage).where(
                    ConversationMessage.direction == HUMAN_OUTBOUND_DIRECTION
                )
            ).all()
        )
        assert messages == []
        adapter.delivered = True
        retried, _ = service.delivery_service.retry_failed_action(
            workspace,
            fixture.account_id,
            result.action.id,
        )
        retried_messages = list(
            session.exec(
                select(ConversationMessage).where(
                    ConversationMessage.direction == HUMAN_OUTBOUND_DIRECTION
                )
            ).all()
        )
        handoff_status = handoff.status

    assert initial_status == OutboundIntegrationActionStatus.FAILED
    assert initial_failure_message == "Provider temporarily unavailable"
    assert initial_message is None
    assert retried.status == OutboundIntegrationActionStatus.DELIVERED
    assert len(retried_messages) == 1
    assert retried_messages[0].content == "A provider-safe human reply."
    assert handoff_status == SalesConversationHandoffStatus.ACTIVE


def test_operator_resolution_is_explicit_idempotent_attributed_and_audited(client):
    fixture = _workspace_fixture("handoff-resolve-operator")
    with _session() as session:
        workspace = session.get(Workspace, fixture.workspace_id)
        service = HumanHandoffOperationsService(session)
        first = service.resolve_handoff(
            workspace=workspace,
            handoff_id=fixture.handoff_id,
            actor=_actor(fixture),
        )
        duplicate = service.resolve_handoff(
            workspace=workspace,
            handoff_id=fixture.handoff_id,
            actor=_actor(fixture),
        )
        audits = list(session.exec(select(IntegrationAccountAuditEvent)).all())

    assert first.duplicate is False
    assert first.handoff.status == SalesConversationHandoffStatus.RESOLVED
    assert first.handoff.resolved_at is not None
    assert duplicate.duplicate is True
    assert len(
        [
            event
            for event in audits
            if event.reason_code == f"human_handoff_resolved_{fixture.handoff_id.hex}"
        ]
    ) == 1
    assert any(event.actor_user_id == fixture.user_id for event in audits)


def test_member_can_view_but_cannot_send_human_reply(client):
    fixture = _workspace_fixture(
        "handoff-member-permissions",
        member_role=WorkspaceMemberRole.MEMBER,
    )
    listed = client.get(
        "/api/operator/handoffs",
        headers={"X-Workspace-Slug": fixture.workspace_slug},
    )
    denied = client.post(
        f"/api/operator/handoffs/{fixture.handoff_id}/reply",
        headers={
            "X-Workspace-Slug": fixture.workspace_slug,
            "Idempotency-Key": "member-denied",
        },
        json={"content": "This must not be delivered."},
    )
    assert listed.status_code == 200
    assert denied.status_code == 403


def test_foreign_workspace_cannot_reply_or_resolve_handoff(client):
    workspace_a = _workspace_fixture("handoff-act-a")
    workspace_b = _workspace_fixture("handoff-act-b")
    reply = client.post(
        f"/api/operator/handoffs/{workspace_a.handoff_id}/reply",
        headers={
            "X-Workspace-Slug": workspace_b.workspace_slug,
            "Idempotency-Key": "foreign-denied",
        },
        json={"content": "Foreign reply"},
    )
    resolve = client.post(
        f"/api/operator/handoffs/{workspace_a.handoff_id}/resolve",
        headers={"X-Workspace-Slug": workspace_b.workspace_slug},
    )
    assert reply.status_code == 404
    assert resolve.status_code == 404


def test_human_reply_api_reports_delivery_and_does_not_resolve(monkeypatch, client):
    fixture = _workspace_fixture("handoff-reply-api")
    adapter = RecordingAdapter()

    def from_settings(cls, session, settings):
        del settings
        return _service(session, adapter)

    monkeypatch.setattr(
        HumanHandoffOperationsService,
        "from_settings",
        classmethod(from_settings),
    )
    response = client.post(
        f"/api/operator/handoffs/{fixture.handoff_id}/reply",
        headers={
            "X-Workspace-Slug": fixture.workspace_slug,
            "Idempotency-Key": "api-human-reply",
        },
        json={"content": "Human-authored API reply"},
    )
    assert response.status_code == 200
    assert response.json()["delivered"] is True
    assert response.json()["conversation_message"]["direction"] == HUMAN_OUTBOUND_DIRECTION
    with _session() as session:
        handoff = session.get(SalesConversationHandoff, fixture.handoff_id)
        assert handoff.status == SalesConversationHandoffStatus.ACTIVE


def test_operator_resolution_route_is_repeatable_without_duplicate_audit(client):
    fixture = _workspace_fixture("handoff-resolve-api")
    first = client.post(
        f"/api/operator/handoffs/{fixture.handoff_id}/resolve",
        headers={"X-Workspace-Slug": fixture.workspace_slug},
    )
    second = client.post(
        f"/api/operator/handoffs/{fixture.handoff_id}/resolve",
        headers={"X-Workspace-Slug": fixture.workspace_slug},
    )
    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
