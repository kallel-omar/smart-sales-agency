import asyncio
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlmodel import select

from app.api.dependencies import get_settings
from app.config import Settings
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.core.comment_triggers import InboundCommentChannel
from app.core.work_items import WorkItemStatus
from app.db import get_session
from app.departments.sales.agents.follow_up import FollowUpAgent
from app.departments.sales.services.work_item_execution import (
    SalesWorkItemExecutionService,
)
from app.main import app
from app.models import (
    AIEmployeeCapabilityAssignment,
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    Contact,
    InboundExternalIdentity,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    Lead,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    OutboundIntegrationAuditAction,
    OutboundIntegrationAuditEvent,
    WorkItem,
    Workspace,
    utc_now,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentScopeError,
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import (
    AIEmployeeCapabilityToolAccessService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.comment_trigger_rules import CommentTriggerRuleService
from app.services.customer_contacts import ContactNotFoundError, CustomerContactService
from app.services.delivery_adapters import (
    DEFAULT_DELIVERY_ADAPTER_CAPABILITIES,
    DeliveryAdapterRegistry,
    DeliveryAdapterResult,
)
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.departments import DepartmentService
from app.services.follow_up_work_items import FollowUpWorkItemMaterializationService
from app.services.llm import LLMCompletion, OpenAICompatibleLLM
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.send_message_work_items import SendMessageWorkItemService
from app.services.work_item_approvals import WorkItemApprovalNotPermittedError
from app.services.work_items import WorkItemNotFoundError

WHATSAPP_ENDPOINT = "/api/integrations/inbound-events/whatsapp-cloud"
META_ENDPOINT = "/api/integrations/inbound-events/meta"
WHATSAPP_ACCOUNT_ID = "m17-whatsapp-number"
WHATSAPP_SENDER = "21655501717"
WHATSAPP_EVENT_ID = "m17-whatsapp-message-1"
META_SECRET_REFERENCE = "INTEGRATION_SECRET_M17_META"
META_SECRET = "m17-meta-secret"


class RecordingDeliveryAdapter:
    capabilities = DEFAULT_DELIVERY_ADAPTER_CAPABILITIES

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    def deliver(self, action, account) -> DeliveryAdapterResult:
        self.calls.append((action.id, account.id))
        return DeliveryAdapterResult.success(f"provider-delivery-{len(self.calls)}")


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _settings() -> Settings:
    fixture_settings = app.dependency_overrides[get_settings]()
    return Settings(
        environment="test",
        database_url="sqlite://",
        auth_token_secret=fixture_settings.auth_token_secret,
        llm_mode="openai_compatible",
        llm_api_key="m17-provider-key",
        require_human_approval=True,
        ai_model_tier_mappings={
            "economy": {"provider": "m17-ai", "model": "economy-model"},
            "standard": {"provider": "m17-ai", "model": "standard-model"},
        },
        ai_model_pricing=[
            {
                "provider": "m17-ai",
                "model": "economy-model",
                "input_cost_per_million_tokens": "2.00",
                "output_cost_per_million_tokens": "4.00",
            },
            {
                "provider": "m17-ai",
                "model": "standard-model",
                "input_cost_per_million_tokens": "2.00",
                "output_cost_per_million_tokens": "4.00",
            },
        ],
    )


def _install_external_boundaries(monkeypatch, settings: Settings):
    llm_calls: list[tuple[str, str]] = []

    async def complete_with_metadata(self, system_prompt, user_prompt):
        del self
        llm_calls.append((system_prompt, user_prompt))
        return LLMCompletion(
            content="The monthly plan is 49 USD, and I can help you get started.",
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
        )

    monkeypatch.setattr(
        OpenAICompatibleLLM,
        "complete_with_metadata",
        complete_with_metadata,
    )
    adapter = RecordingDeliveryAdapter()

    def from_settings(cls, session, configured_settings, **kwargs):
        del cls, configured_settings, kwargs
        return OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry(
                {
                    "whatsapp_cloud": adapter,
                    "facebook_messenger": adapter,
                }
            ),
        )

    monkeypatch.setattr(
        OutboundIntegrationDeliveryService,
        "from_settings",
        classmethod(from_settings),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return llm_calls, adapter


def _create_workspace(client, slug: str) -> UUID:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def _provision_sales_workforce(workspace_id: UUID):
    with next(app.dependency_overrides[get_session]()) as session:
        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        department = DepartmentService(session).ensure_sales_department(workspace)
        capabilities = {
            capability.key: capability
            for capability in CapabilityService(session).ensure_sales_capabilities(
                workspace, department
            )
        }
        conversation_employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.SALES_CONVERSATION,
            name="M17 Sales Conversation",
        )
        follow_up_employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.FOLLOW_UP,
            name="M17 Follow-up",
        )
        assignments = AIEmployeeCapabilityAssignmentService(session)
        answer_assignment = assignments.assign(
            workspace,
            conversation_employee,
            capabilities[BusinessCapabilityKey.ANSWER_CUSTOMER],
        )
        send_assignment = assignments.assign(
            workspace,
            conversation_employee,
            capabilities[BusinessCapabilityKey.SEND_MESSAGE],
        )
        follow_up_assignment = assignments.assign(
            workspace,
            follow_up_employee,
            capabilities[BusinessCapabilityKey.FOLLOW_UP_LEAD],
        )
        return SimpleNamespace(
            workspace_id=workspace.id,
            department_id=department.id,
            conversation_employee_id=conversation_employee.id,
            follow_up_employee_id=follow_up_employee.id,
            answer_assignment_id=answer_assignment.id,
            send_assignment_id=send_assignment.id,
            follow_up_assignment_id=follow_up_assignment.id,
            capability_ids={key: value.id for key, value in capabilities.items()},
        )


def _create_account(
    client,
    slug: str,
    *,
    provider: str,
    external_account_id: str,
    secret_reference: str,
) -> tuple[UUID, str]:
    response = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": provider,
            "external_account_id": external_account_id,
            "secret_reference": secret_reference,
        },
    )
    assert response.status_code == 201
    return UUID(response.json()["id"]), response.json()["inbound_credential"]


def _grant(
    workspace_id: UUID,
    assignment_id: UUID,
    account_id: UUID,
    autonomy: AIEmployeeAutonomyLevel,
) -> None:
    with next(app.dependency_overrides[get_session]()) as session:
        workspace = session.get(Workspace, workspace_id)
        assignment = session.get(AIEmployeeCapabilityAssignment, assignment_id)
        account = session.get(IntegrationAccount, account_id)
        assert workspace and assignment and account
        AIEmployeeCapabilityToolAccessService(session).grant(
            workspace,
            assignment,
            account,
            OutboundIntegrationActionType.SEND_MESSAGE,
            autonomy,
        )


def _whatsapp_payload() -> dict:
    return {
        "channel": "whatsapp_cloud",
        "provider_event_id": WHATSAPP_EVENT_ID,
        "sender_external_id": WHATSAPP_SENDER,
        "recipient_account_id": WHATSAPP_ACCOUNT_ID,
        "content": "What is the monthly price?",
        "timestamp": 1_720_000_000,
        "provider_metadata": {"waba_id": "m17-waba"},
    }


def _meta_comment(account_external_id: str, event_id: str) -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": account_external_id,
                "time": 1_720_000_010,
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": "comment",
                            "verb": "add",
                            "comment_id": event_id,
                            "post_id": "m17-post",
                            "message": "I am interested in the offer",
                            "created_time": 1_720_000_009,
                            "from": {"id": "m17-meta-prospect", "name": "Amina"},
                        },
                    }
                ],
            }
        ],
    }


def _post_meta(client, account_id: UUID, payload: dict, *, valid: bool = True):
    body = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(META_SECRET.encode(), body, sha256).hexdigest()
    return client.post(
        f"{META_ENDPOINT}/{account_id}",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={digest if valid else '0' * 64}",
        },
        content=body,
    )


def _business_counts() -> tuple[int, ...]:
    with next(app.dependency_overrides[get_session]()) as session:
        return tuple(
            len(session.exec(select(model)).all())
            for model in (
                InboundIntegrationEventReceipt,
                Contact,
                Lead,
                WorkItem,
                ApprovalRequest,
                OutboundIntegrationAction,
                AIInvocationUsage,
            )
        )


def test_sales_mvp_real_boundaries_end_to_end(
    client,
    monkeypatch,
    signed_webhook_request,
) -> None:
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    settings = _settings()
    llm_calls, adapter = _install_external_boundaries(monkeypatch, settings)
    workspace_a_id = _create_workspace(client, "m17-sales-a")
    workspace_b_id = _create_workspace(client, "m17-sales-b")
    workforce = _provision_sales_workforce(workspace_a_id)
    whatsapp_account_id, whatsapp_credential = _create_account(
        client,
        "m17-sales-a",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_ID,
        secret_reference="INTEGRATION_SECRET_GENERIC_HMAC_TEST",
    )
    _grant(
        workspace_a_id,
        workforce.send_assignment_id,
        whatsapp_account_id,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )

    whatsapp_headers, whatsapp_body = signed_webhook_request(
        whatsapp_credential,
        _whatsapp_payload(),
        event_id=WHATSAPP_EVENT_ID,
    )
    invalid_headers = dict(whatsapp_headers)
    invalid_headers["X-Webhook-Signature"] = "0" * 64
    before_invalid_whatsapp = _business_counts()
    invalid_whatsapp = client.post(
        WHATSAPP_ENDPOINT,
        headers=invalid_headers,
        content=whatsapp_body,
    )
    assert invalid_whatsapp.status_code == 401
    assert _business_counts() == before_invalid_whatsapp

    whatsapp = client.post(
        WHATSAPP_ENDPOINT,
        headers=whatsapp_headers,
        content=whatsapp_body,
    )
    replay = client.post(
        WHATSAPP_ENDPOINT,
        headers=whatsapp_headers,
        content=whatsapp_body,
    )
    assert whatsapp.status_code == replay.status_code == 200
    assert whatsapp.json()["draft_reply"].startswith("The monthly plan")
    assert replay.json() == {
        "duplicate": True,
        "correlation_id": whatsapp.json()["correlation_id"],
    }
    assert len(llm_calls) == 1
    assert len(adapter.calls) == 1

    with next(app.dependency_overrides[get_session]()) as session:
        workspace_a = session.get(Workspace, workspace_a_id)
        workspace_b = session.get(Workspace, workspace_b_id)
        assert workspace_a and workspace_b
        receipts = session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.workspace_id == workspace_a.id,
                InboundIntegrationEventReceipt.external_event_id == WHATSAPP_EVENT_ID,
            )
        ).all()
        contacts = session.exec(select(Contact).where(Contact.workspace_id == workspace_a.id)).all()
        leads = session.exec(select(Lead).where(Lead.tenant_id == workspace_a.slug)).all()
        identities = session.exec(
            select(InboundExternalIdentity).where(
                InboundExternalIdentity.workspace_id == workspace_a.id,
                InboundExternalIdentity.integration_account_id == whatsapp_account_id,
            )
        ).all()
        assert len(receipts) == len(contacts) == len(leads) == len(identities) == 1
        lead = leads[0]
        assert lead.contact_id == contacts[0].id
        assert identities[0].lead_id == lead.id
        assert identities[0].external_subject_id == WHATSAPP_SENDER

        capture = session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace_a.id,
                WorkItem.work_type == "lead_capture",
            )
        ).one()
        answer = session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace_a.id,
                WorkItem.work_type == BusinessCapabilityKey.ANSWER_CUSTOMER.value,
            )
        ).one()
        reply = session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace_a.id,
                WorkItem.work_type == "sales_reply_message",
            )
        ).one()
        assert capture.department_id == workforce.department_id
        assert capture.capability_id == workforce.capability_ids[BusinessCapabilityKey.CAPTURE_LEAD]
        assert answer.status == WorkItemStatus.COMPLETED
        assert answer.assignment_id == workforce.answer_assignment_id
        assert answer.ai_employee_id == workforce.conversation_employee_id
        assert (
            answer.capability_id == workforce.capability_ids[BusinessCapabilityKey.ANSWER_CUSTOMER]
        )
        assert answer.started_at and answer.completed_at and answer.result
        assert answer.result["ai_invoked"] is True
        assert reply.status == WorkItemStatus.COMPLETED
        assert reply.parent_work_item_id == answer.id
        assert reply.assignment_id == workforce.send_assignment_id
        assert reply.capability_id == workforce.capability_ids[BusinessCapabilityKey.SEND_MESSAGE]
        assert reply.result and reply.result["outcome"] == "outbound_delivered"

        usage = session.exec(select(AIInvocationUsage)).one()
        assert usage.workspace_id == workspace_a.id
        assert usage.department_id == workforce.department_id
        assert usage.ai_employee_id == workforce.conversation_employee_id
        assert (
            usage.capability_id == workforce.capability_ids[BusinessCapabilityKey.ANSWER_CUSTOMER]
        )
        assert usage.work_item_id == answer.id
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (12, 8, 20)
        assert usage.estimated_cost == Decimal("0.00005600")

        action = session.exec(select(OutboundIntegrationAction)).one()
        assert action.integration_account_id == whatsapp_account_id
        assert action.action_type == OutboundIntegrationActionType.SEND_MESSAGE
        assert action.status == OutboundIntegrationActionStatus.DELIVERED
        assert action.correlation_id == str(receipts[0].correlation_id)
        assert action.payload["work_item_id"] == str(reply.id)
        audits = session.exec(
            select(OutboundIntegrationAuditEvent).where(
                OutboundIntegrationAuditEvent.outbound_integration_action_id == action.id
            )
        ).all()
        assert {event.action for event in audits} == {
            OutboundIntegrationAuditAction.CREATED,
            OutboundIntegrationAuditAction.DELIVERY_ATTEMPTED,
            OutboundIntegrationAuditAction.DELIVERED,
        }

        with pytest.raises(ContactNotFoundError):
            CustomerContactService(session).get_contact(workspace_b, contacts[0].id)
        with pytest.raises(WorkItemNotFoundError):
            DepartmentSupervisorRoutingService(session).route_work_item(workspace_b, answer.id)
        assignment = session.get(
            AIEmployeeCapabilityAssignment,
            workforce.send_assignment_id,
        )
        account = session.get(IntegrationAccount, whatsapp_account_id)
        assert assignment and account
        with pytest.raises(AIEmployeeCapabilityAssignmentScopeError):
            AIEmployeeCapabilityToolAccessService(session).evaluate(
                workspace_b,
                assignment,
                account,
                OutboundIntegrationActionType.SEND_MESSAGE,
            )
        answer_id = answer.id
        action_id = action.id
        lead_id = lead.id
        contact_id = contacts[0].id
        receipt_correlation_id = receipts[0].correlation_id

    assert client.get(f"/api/leads/{lead_id}", headers=_headers("m17-sales-b")).status_code == 404
    assert (
        client.get(
            f"/api/operator/work-items/{answer_id}", headers=_headers("m17-sales-b")
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/operator/workforce/{workforce.conversation_employee_id}",
            headers=_headers("m17-sales-b"),
        ).status_code
        == 404
    )
    cross_account = client.post(
        f"/api/integrations/accounts/{whatsapp_account_id}/outbound-actions",
        headers=_headers("m17-sales-b"),
        json={
            "external_target_id": "must-not-send",
            "action_type": "send_message",
            "content": "must not send",
            "idempotency_key": "m17-cross-workspace-denied",
        },
    )
    assert cross_account.status_code == 404
    assert (
        client.get(
            f"/api/integrations/outbound-actions/{action_id}",
            headers=_headers("m17-sales-b"),
        ).status_code
        == 404
    )
    trace = client.get(
        f"/api/integrations/execution-traces/{receipt_correlation_id}",
        headers=_headers("m17-sales-a"),
    )
    assert trace.status_code == 200
    assert trace.json()["inbound"]["external_event_id"] == WHATSAPP_EVENT_ID
    assert len(trace.json()["outbound_actions"]) == 1
    assert trace.json()["outbound_actions"][0]["status"] == "delivered"
    assert (
        client.get(
            f"/api/integrations/execution-traces/{receipt_correlation_id}",
            headers=_headers("m17-sales-b"),
        ).status_code
        == 404
    )

    with next(app.dependency_overrides[get_session]()) as session:
        workspace_a = session.get(Workspace, workspace_a_id)
        lead = session.get(Lead, lead_id)
        assert workspace_a and lead
        task = FollowUpAgent(session).schedule(
            lead,
            "Check whether the prospect wants a demo",
            delay_days=1,
        )
        task.due_at = utc_now() - timedelta(minutes=1)
        session.add(task)
        session.commit()
        follow_up, routing = FollowUpWorkItemMaterializationService(session).materialize_due(
            workspace_a, task.id
        )
        assert follow_up and routing and routing.routable
        assert follow_up.assignment_id == workforce.follow_up_assignment_id
        assert follow_up.input["integration_account_id"] == str(whatsapp_account_id)
        assert follow_up.input["channel"] == "whatsapp_cloud"
        assert follow_up.input["recipient"] == WHATSAPP_SENDER
        completed_follow_up = asyncio.run(
            SalesWorkItemExecutionService(session, settings).execute(workspace_a, follow_up.id)
        )
        repeated_follow_up, repeated_routing = FollowUpWorkItemMaterializationService(
            session
        ).materialize_due(workspace_a, task.id)
        assert repeated_follow_up and repeated_follow_up.id == follow_up.id
        assert repeated_routing is None
        assert completed_follow_up.status == WorkItemStatus.COMPLETED
        assert completed_follow_up.result["action"] == "send"
        assert completed_follow_up.result["send_outcome"] == "outbound_delivered"
        follow_send = session.get(
            WorkItem,
            UUID(completed_follow_up.result["send_work_item_id"]),
        )
        assert follow_send and follow_send.status == WorkItemStatus.COMPLETED
        assert follow_send.parent_work_item_id == follow_up.id
        assert follow_send.assignment_id == workforce.send_assignment_id
        session.refresh(task)
        assert task.status == "completed"
    assert len(adapter.calls) == 2

    meta_external_account_id = "m17-meta-page"
    meta_account_id, _ = _create_account(
        client,
        "m17-sales-a",
        provider="facebook_messenger",
        external_account_id=meta_external_account_id,
        secret_reference=META_SECRET_REFERENCE,
    )
    _grant(
        workspace_a_id,
        workforce.send_assignment_id,
        meta_account_id,
        AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL,
    )
    with next(app.dependency_overrides[get_session]()) as session:
        workspace_a = session.get(Workspace, workspace_a_id)
        assert workspace_a
        CommentTriggerRuleService(session).create(
            workspace_a,
            integration_account_id=meta_account_id,
            channel=InboundCommentChannel.FACEBOOK_COMMENT,
            name="M17 interested prospect",
            enabled=True,
            keywords=["interested"],
            content_external_id="m17-post",
            dm_message="Thanks for your interest. Here are the details.",
            send_assignment_id=workforce.send_assignment_id,
        )

    invalid_meta_payload = _meta_comment(meta_external_account_id, "m17-meta-invalid")
    before_invalid_meta = _business_counts()
    invalid_meta = _post_meta(
        client,
        meta_account_id,
        invalid_meta_payload,
        valid=False,
    )
    assert invalid_meta.status_code == 401
    assert _business_counts() == before_invalid_meta

    first_meta_payload = _meta_comment(meta_external_account_id, "m17-meta-approve")
    first_meta = _post_meta(client, meta_account_id, first_meta_payload)
    first_meta_replay = _post_meta(client, meta_account_id, first_meta_payload)
    assert first_meta.status_code == first_meta_replay.status_code == 200
    assert first_meta.json()["trigger_result"] == "approval_required"
    assert first_meta_replay.json()["duplicate"] is True
    assert len(adapter.calls) == 2
    with next(app.dependency_overrides[get_session]()) as session:
        first_approval = session.exec(
            select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING)
        ).one()
        assert first_approval.work_item_id is not None
        first_send_id = first_approval.work_item_id
        assert session.get(WorkItem, first_send_id).status == WorkItemStatus.APPROVAL_REQUIRED
        action_count_before_approval = len(session.exec(select(OutboundIntegrationAction)).all())

    cross_approval = client.post(
        f"/api/approvals/{first_approval.id}/approve",
        headers=_headers("m17-sales-b"),
        json={"reviewer_note": "cross-workspace attempt"},
    )
    assert cross_approval.status_code == 404
    approved = client.post(
        f"/api/approvals/{first_approval.id}/approve",
        headers=_headers("m17-sales-a"),
        json={"reviewer_note": "approved in M17"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    with next(app.dependency_overrides[get_session]()) as session:
        workspace_a = session.get(Workspace, workspace_a_id)
        account = session.get(IntegrationAccount, meta_account_id)
        assert workspace_a and account
        approved_send = SendMessageWorkItemService(session, settings).execute_work_item(
            workspace_a,
            first_send_id,
            account,
            approval_id=first_approval.id,
        )
        assert approved_send.outcome.value == "outbound_delivered"
        assert approved_send.work_item.status == WorkItemStatus.COMPLETED
        assert approved_send.outbound_action is not None
        assert approved_send.outbound_action.status == OutboundIntegrationActionStatus.DELIVERED
        assert len(session.exec(select(OutboundIntegrationAction)).all()) == (
            action_count_before_approval + 1
        )
    assert len(adapter.calls) == 3

    second_meta = _post_meta(
        client,
        meta_account_id,
        _meta_comment(meta_external_account_id, "m17-meta-reject"),
    )
    assert second_meta.status_code == 200
    assert second_meta.json()["trigger_result"] == "approval_required"
    with next(app.dependency_overrides[get_session]()) as session:
        second_approval = session.exec(
            select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING)
        ).one()
        assert second_approval.work_item_id is not None
        second_send_id = second_approval.work_item_id
    rejected = client.post(
        f"/api/approvals/{second_approval.id}/reject",
        headers=_headers("m17-sales-a"),
        json={"reviewer_note": "rejected in M17"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    with next(app.dependency_overrides[get_session]()) as session:
        workspace_a = session.get(Workspace, workspace_a_id)
        account = session.get(IntegrationAccount, meta_account_id)
        assert workspace_a and account
        with pytest.raises(WorkItemApprovalNotPermittedError):
            SendMessageWorkItemService(session, settings).execute_work_item(
                workspace_a,
                second_send_id,
                account,
                approval_id=second_approval.id,
            )
        rejected_item = session.get(WorkItem, second_send_id)
        assert rejected_item and rejected_item.status == WorkItemStatus.APPROVAL_REQUIRED
        actions = session.exec(select(OutboundIntegrationAction)).all()
        assert len(actions) == 3
        assert all(action.status == OutboundIntegrationActionStatus.DELIVERED for action in actions)
        assert len(adapter.calls) == 3

        all_audits = session.exec(select(OutboundIntegrationAuditEvent)).all()
        for action in actions:
            assert {
                event.action
                for event in all_audits
                if event.outbound_integration_action_id == action.id
            } == {
                OutboundIntegrationAuditAction.CREATED,
                OutboundIntegrationAuditAction.DELIVERY_ATTEMPTED,
                OutboundIntegrationAuditAction.DELIVERED,
            }
        assert session.get(Contact, contact_id).workspace_id == workspace_a.id
        assert (
            len(
                session.exec(
                    select(InboundIntegrationEventReceipt).where(
                        InboundIntegrationEventReceipt.external_event_id == WHATSAPP_EVENT_ID
                    )
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.exec(
                    select(WorkItem).where(
                        WorkItem.workspace_id == workspace_a.id,
                        WorkItem.work_type == BusinessCapabilityKey.ANSWER_CUSTOMER.value,
                    )
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.exec(
                    select(WorkItem).where(
                        WorkItem.workspace_id == workspace_a.id,
                        WorkItem.work_type == "sales_reply_message",
                    )
                ).all()
            )
            == 1
        )

    assert (
        client.get(
            f"/api/operator/approvals/{first_approval.id}",
            headers=_headers("m17-sales-b"),
        ).status_code
        == 404
    )
    analytics_a_response = client.get(
        "/api/operator/analytics",
        headers=_headers("m17-sales-a"),
    )
    analytics_b_response = client.get(
        "/api/operator/analytics",
        headers=_headers("m17-sales-b"),
    )
    assert analytics_a_response.status_code == analytics_b_response.status_code == 200
    analytics_a = analytics_a_response.json()
    analytics_b = analytics_b_response.json()
    assert analytics_a["sales"]["leads_created"] == 2
    assert analytics_a["workitems"]["created"] == 9
    assert analytics_a["workitems"]["completed"] == 5
    assert analytics_a["workitems"]["current"]["approval_required"] == 1
    assert analytics_a["approvals"]["requests_created"] == 2
    assert analytics_a["approvals"]["approved"] == 1
    assert analytics_a["approvals"]["rejected"] == 1
    assert analytics_a["ai_usage"]["invocation_count"] == 1
    assert analytics_a["ai_usage"]["total_tokens"] == 20
    assert analytics_a["ai_usage"]["known_estimated_cost"] == "0.00005600"
    assert analytics_a["sales"]["outcomes"]["follow_up_completed"] == 1
    workforce_rows = {row["employee_id"]: row for row in analytics_a["workforce"]}
    assert workforce_rows[str(workforce.conversation_employee_id)]["workitems"] == 5
    assert workforce_rows[str(workforce.follow_up_employee_id)]["workitems"] == 1
    capability_rows = {row["capability_id"]: row for row in analytics_a["capabilities"]}
    assert (
        capability_rows[str(workforce.capability_ids[BusinessCapabilityKey.ANSWER_CUSTOMER])][
            "invocation_count"
        ]
        == 1
    )
    assert (
        capability_rows[str(workforce.capability_ids[BusinessCapabilityKey.FOLLOW_UP_LEAD])][
            "workitems"
        ]
        == 1
    )
    assert (
        capability_rows[str(workforce.capability_ids[BusinessCapabilityKey.SEND_MESSAGE])][
            "workitems"
        ]
        == 4
    )
    assert analytics_b["sales"]["total_leads"] == 0
    assert analytics_b["workitems"]["created"] == 0
    assert analytics_b["approvals"]["requests_created"] == 0
    assert analytics_b["ai_usage"]["invocation_count"] == 0
