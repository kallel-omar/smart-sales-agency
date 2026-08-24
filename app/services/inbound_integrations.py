from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.lead_capture import LeadCaptureResult, LeadCaptureSignal
from app.core.work_items import WorkItemStatus
from app.departments.sales.services import (
    SalesConversationTurnInput,
    SalesConversationTurnResult,
    SalesConversationTurnService,
    SalesWorkItemExecutionService,
)
from app.models import (
    Capability,
    Department,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    SalesHandoffReasonCode,
    SalesStage,
    Workspace,
)
from app.schemas import InboundIntegrationEvent
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.lead_capture import LeadCaptureService
from app.services.repository import NotFoundError, SalesRepository
from app.services.send_message_work_items import SendMessageWorkItemService
from app.services.work_items import WorkItemService


class InboundIntegrationService:
    """Normalizes trusted integration input into a Sales business event."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
    ) -> None:
        self.repository = SalesRepository(session)
        self.settings = settings

    def reserve_event(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        external_event_id: str,
    ) -> InboundEventReservation:
        """Durably reserve an authenticated provider event before dispatching it."""

        normalized_event_id = self._normalize_external_event_id(external_event_id)
        receipt = InboundIntegrationEventReceipt(
            workspace_id=workspace.id,
            integration_account_id=account.id,
            external_event_id=normalized_event_id,
        )
        self.repository.session.add(receipt)
        try:
            self.repository.session.flush()
            self.repository.session.commit()
            self.repository.session.refresh(receipt)
            return InboundEventReservation(receipt=receipt, first_delivery=True)
        except IntegrityError:
            self.repository.session.rollback()
            existing = self.repository.session.exec(
                select(InboundIntegrationEventReceipt).where(
                    InboundIntegrationEventReceipt.workspace_id == workspace.id,
                    InboundIntegrationEventReceipt.integration_account_id == account.id,
                    InboundIntegrationEventReceipt.external_event_id == normalized_event_id,
                )
            ).first()
            if existing is None:
                raise
            return InboundEventReservation(receipt=existing, first_delivery=False)

    def release_event_reservation(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        reservation: InboundEventReservation,
    ) -> None:
        """Release only this account's reserved event before dispatch has begun."""

        receipt = self.repository.session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.id == reservation.receipt.id,
                InboundIntegrationEventReceipt.workspace_id == workspace.id,
                InboundIntegrationEventReceipt.integration_account_id == account.id,
            )
        ).first()
        if receipt is None:
            return
        self.repository.session.delete(receipt)
        self.repository.session.commit()

    @contextmanager
    def release_event_reservation_on_failure(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        reservation: InboundEventReservation,
    ) -> Iterator[None]:
        """Keep successful receipts and release this reservation on processing failure."""

        try:
            yield
        except Exception:
            self.release_event_reservation(workspace, account, reservation)
            raise

    def capture_reserved_event(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        reservation: InboundEventReservation,
        signal: LeadCaptureSignal,
    ) -> LeadCaptureResult:
        """Capture before dispatch, releasing only this reservation on failure."""

        try:
            return LeadCaptureService(self.repository.session).capture(
                workspace.id,
                signal,
            )
        except Exception:
            self.release_event_reservation(workspace, account, reservation)
            raise

    async def handle_event(
        self,
        event: InboundIntegrationEvent,
        workspace: Workspace,
    ) -> SalesConversationTurnResult:
        """Handle a validated inbound event for its resolved workspace."""

        lead = self.repository.get_lead(event.lead_id)

        if lead.tenant_id != workspace.slug:
            raise NotFoundError("Lead not found")

        return await SalesConversationTurnService(
            repository=self.repository,
            settings=self.settings,
            workspace=workspace,
            ai_invocation_gateway=AIInvocationGateway(
                self.repository.session,
                self.settings,
            ),
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel=event.channel,
                customer_message=event.content,
            )
        )

    async def handle_work_item_event(
        self,
        event: InboundIntegrationEvent,
        workspace: Workspace,
        account: IntegrationAccount,
        *,
        correlation_id: UUID,
        external_target_id: str | None = None,
    ) -> SalesConversationTurnResult:
        """Execute one live inbound Sales turn through routed, persisted WorkItems."""

        lead = self.repository.get_lead(event.lead_id)
        if lead.tenant_id != workspace.slug or account.workspace_id != workspace.id:
            raise NotFoundError("Lead not found")
        department = self.repository.session.exec(
            select(Department).where(
                Department.workspace_id == workspace.id,
                Department.kind == DepartmentKind.SALES,
            )
        ).first()
        if department is None:
            raise InboundSalesWorkItemRoutingError("Sales Department is not configured")
        answer_capability = self._capability(
            workspace, department, BusinessCapabilityKey.ANSWER_CUSTOMER
        )
        work_items = WorkItemService(self.repository.session)
        answer = work_items.create_work_item(
            workspace,
            department,
            work_type=BusinessCapabilityKey.ANSWER_CUSTOMER.value,
            title="Answer inbound customer message",
            capability=answer_capability,
            input={
                "lead_id": str(lead.id),
                "channel": event.channel,
                "customer_message": event.content,
                "integration_account_id": str(account.id),
                "external_event_id": event.external_event_id,
            },
        )
        decision = DepartmentSupervisorRoutingService(self.repository.session).route_and_assign(
            workspace, answer.id
        )
        answer = work_items.get_work_item(workspace, answer.id)
        if not decision.routable or WorkItemStatus(answer.status) != WorkItemStatus.ASSIGNED:
            raise InboundSalesWorkItemRoutingError(
                "No eligible answer_customer AIEmployee assignment is configured"
            )

        # The outbound send_message WorkItem owns human/tool governance for live
        # channel delivery, so the inner conversation service only produces its reply.
        execution_settings = self.settings.model_copy(update={"require_human_approval": False})
        answer = await SalesWorkItemExecutionService(
            self.repository.session,
            execution_settings,
            ai_invocation_gateway=AIInvocationGateway(
                self.repository.session,
                self.settings,
            ),
        ).execute(workspace, answer.id)
        result = dict(answer.result or {})
        reply = self._required_result_text(result, "draft_reply")

        send_capability = self._capability(
            workspace, department, BusinessCapabilityKey.SEND_MESSAGE
        )
        recipient = external_target_id or lead.phone or lead.email or lead.full_name
        send_item = work_items.create_work_item(
            workspace,
            department,
            work_type="sales_reply_message",
            title="Send Sales conversation reply",
            capability=send_capability,
            parent_work_item_id=answer.id,
            input={
                "lead_id": str(lead.id),
                "integration_account_id": str(account.id),
                "channel": event.channel,
                "recipient": recipient,
                "external_subject_id": external_target_id or lead.phone,
                "message": reply,
                "source_answer_work_item_id": str(answer.id),
            },
        )
        send_decision = DepartmentSupervisorRoutingService(
            self.repository.session
        ).route_and_assign(workspace, send_item.id)
        send_item = work_items.get_work_item(workspace, send_item.id)
        if (
            not send_decision.routable
            or WorkItemStatus(send_item.status) != WorkItemStatus.ASSIGNED
        ):
            raise InboundSalesWorkItemRoutingError(
                "No eligible send_message AIEmployee assignment is configured"
            )
        send_result = SendMessageWorkItemService(
            self.repository.session, self.settings
        ).execute_work_item(
            workspace,
            send_item.id,
            account,
            idempotency_source=(f"inbound:{workspace.id}:{account.id}:{event.external_event_id}"),
            correlation_id=correlation_id,
        )

        return SalesConversationTurnResult(
            lead_id=lead.id,
            detected_stage=SalesStage(result["detected_stage"]),
            draft_reply=reply,
            approval_id=send_result.approval_id,
            handoff_required=bool(result.get("handoff_required", False)),
            handoff_reason_code=(
                SalesHandoffReasonCode(result["handoff_reason_code"])
                if result.get("handoff_reason_code")
                else None
            ),
            ai_invoked=bool(result.get("ai_invoked", False)),
        )

    def _capability(
        self,
        workspace: Workspace,
        department: Department,
        key: BusinessCapabilityKey,
    ) -> Capability:
        capability = self.repository.session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.department_id == department.id,
                Capability.key == key,
                Capability.active.is_(True),
            )
        ).first()
        if capability is None:
            raise InboundSalesWorkItemRoutingError(f"{key.value} Capability is not configured")
        return capability

    @staticmethod
    def _required_result_text(result: dict, field: str) -> str:
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            raise InboundSalesWorkItemRoutingError(f"Sales WorkItem result requires {field}")
        return value.strip()

    @staticmethod
    def _normalize_external_event_id(value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:=-]{0,199}", normalized):
            raise InboundIntegrationEventIdValidationError(
                "External event identifier is invalid"
            )
        return normalized


class InboundIntegrationEventIdValidationError(ValueError):
    """Raised when a provider event identifier is not safe to persist."""


class InboundSalesWorkItemRoutingError(RuntimeError):
    """Raised when live inbound Sales work cannot use configured routed execution."""


@dataclass(frozen=True)
class InboundEventReservation:
    receipt: InboundIntegrationEventReceipt
    first_delivery: bool
