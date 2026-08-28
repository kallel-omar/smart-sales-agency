"""Workspace-scoped operator operations for active Sales conversation handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlmodel import Session, select

from app.config import Settings
from app.models import (
    ConversationMessage,
    InboundExternalIdentity,
    IntegrationAccount,
    IntegrationAccountAuditAction,
    IntegrationAccountAuditEvent,
    Lead,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    SalesConversationHandoff,
    SalesConversationHandoffStatus,
    Workspace,
    utc_now,
)
from app.services.integration_account_audit import IntegrationAccountAuditService
from app.services.operator_assignments import (
    OperatorAssignmentActor,
    OperatorAssignmentService,
    OperatorAssignmentSnapshot,
)
from app.services.outbound_action_ownership import OutboundActionOwnershipService
from app.services.outbound_delivery import (
    IntegrationAccountReconnectRequiredError,
    OutboundIntegrationDeliveryService,
)
from app.services.outbound_integrations import (
    InactiveIntegrationAccountError,
    OutboundIntegrationActionIdempotencyConflictError,
    OutboundIntegrationService,
)

HUMAN_OUTBOUND_DIRECTION = "human_outbound"
DEFAULT_HANDOFF_LIMIT = 50
MAX_HANDOFF_LIMIT = 100
DEFAULT_HANDOFF_CONTEXT_LIMIT = 50
MAX_HANDOFF_CONTEXT_LIMIT = 200


class HumanHandoffNotFoundError(LookupError):
    """Raised when a handoff is absent from the selected workspace."""


class HumanHandoffRoutingError(ValueError):
    """Raised when a unique provider-safe customer route cannot be resolved."""


class HumanReplyIdempotencyValidationError(ValueError):
    """Raised when an operator reply lacks a safe idempotency key."""


class HumanReplyIdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused for a different operator reply."""


class HumanReplyDeliveryUnavailableError(ValueError):
    """Raised when account lifecycle state blocks an attempted delivery."""


@dataclass(frozen=True, slots=True)
class HumanHandoffView:
    handoff: SalesConversationHandoff
    lead: Lead
    assignment: OperatorAssignmentSnapshot | None
    messages: tuple[ConversationMessage, ...] = ()


@dataclass(frozen=True, slots=True)
class HumanReplyResult:
    handoff: SalesConversationHandoff
    action: OutboundIntegrationAction
    message: ConversationMessage | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class HumanHandoffResolutionResult:
    handoff: SalesConversationHandoff
    operator_user_id: UUID
    duplicate: bool


@dataclass(frozen=True, slots=True)
class _OutboundRoute:
    account: IntegrationAccount
    identity: InboundExternalIdentity
    channel: str


class HumanHandoffOperationsService:
    """Operate handoffs without invoking Sales AI, AgentSkills, or WorkItems."""

    def __init__(
        self,
        session: Session,
        *,
        delivery_service: OutboundIntegrationDeliveryService | None = None,
    ) -> None:
        self.session = session
        self.delivery_service = delivery_service or OutboundIntegrationDeliveryService(session)
        self.outbound_service = OutboundIntegrationService(session)
        self.assignment_service = OperatorAssignmentService(session)
        self.account_audit_service = IntegrationAccountAuditService(session)

    @classmethod
    def from_settings(
        cls,
        session: Session,
        settings: Settings,
    ) -> HumanHandoffOperationsService:
        return cls(
            session,
            delivery_service=OutboundIntegrationDeliveryService.from_settings(session, settings),
        )

    def list_handoffs(
        self,
        workspace: Workspace,
        *,
        active_only: bool = True,
        offset: int = 0,
        limit: int = DEFAULT_HANDOFF_LIMIT,
    ) -> list[HumanHandoffView]:
        if not 0 <= offset <= 10_000:
            raise ValueError("Handoff offset must be between 0 and 10000")
        if not 1 <= limit <= MAX_HANDOFF_LIMIT:
            raise ValueError(f"Handoff limit must be between 1 and {MAX_HANDOFF_LIMIT}")

        statement = (
            select(SalesConversationHandoff, Lead)
            .join(Lead, SalesConversationHandoff.lead_id == Lead.id)
            .where(
                SalesConversationHandoff.workspace_id == workspace.id,
                Lead.tenant_id == workspace.slug,
            )
        )
        if active_only:
            statement = statement.where(
                SalesConversationHandoff.status == SalesConversationHandoffStatus.ACTIVE
            )
        rows = self.session.exec(
            statement.order_by(
                SalesConversationHandoff.created_at.desc(),
                SalesConversationHandoff.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [self._view(handoff, lead) for handoff, lead in rows]

    def get_handoff(
        self,
        workspace: Workspace,
        handoff_id: UUID,
        *,
        context_limit: int = DEFAULT_HANDOFF_CONTEXT_LIMIT,
    ) -> HumanHandoffView:
        if not 1 <= context_limit <= MAX_HANDOFF_CONTEXT_LIMIT:
            raise ValueError(
                f"Handoff context limit must be between 1 and {MAX_HANDOFF_CONTEXT_LIMIT}"
            )
        row = self.session.exec(
            select(SalesConversationHandoff, Lead)
            .join(Lead, SalesConversationHandoff.lead_id == Lead.id)
            .where(
                SalesConversationHandoff.id == handoff_id,
                SalesConversationHandoff.workspace_id == workspace.id,
                Lead.tenant_id == workspace.slug,
            )
        ).first()
        if row is None:
            raise HumanHandoffNotFoundError("Sales handoff not found")
        handoff, lead = row
        messages = self._recent_messages(lead.id, context_limit)
        return self._view(handoff, lead, messages=messages)

    def send_human_reply(
        self,
        *,
        workspace: Workspace,
        handoff_id: UUID,
        content: str,
        idempotency_key: str,
        actor: OperatorAssignmentActor,
    ) -> HumanReplyResult:
        self._ensure_actor_workspace(workspace, actor)
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Human reply content must not be empty")
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        view = self.get_handoff(workspace, handoff_id, context_limit=1)
        if view.handoff.status != SalesConversationHandoffStatus.ACTIVE:
            raise HumanHandoffNotFoundError("Active Sales handoff not found")

        route = self._resolve_outbound_route(workspace, view.lead)
        action_key = self._action_idempotency_key(workspace.id, handoff_id, normalized_key)
        message_id = uuid5(NAMESPACE_URL, f"hiri:{action_key}:conversation-message")
        payload = {
            "message_origin": "human_operator",
            "handoff_id": str(view.handoff.id),
            "lead_id": str(view.lead.id),
            "conversation_message_id": str(message_id),
            "operator_user_id": str(actor.user_id),
            "channel": route.channel,
        }
        existing_action = self.session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == route.account.id,
                OutboundIntegrationAction.idempotency_key == action_key,
            )
        ).first()
        try:
            action, account = self.outbound_service.create_action(
                workspace,
                route.account.id,
                external_target_id=route.identity.external_subject_id,
                action_type=OutboundIntegrationActionType.SEND_MESSAGE,
                content=normalized_content,
                payload=payload,
                correlation_id=f"human-handoff:{view.handoff.id}",
                idempotency_key=action_key,
                requires_approval=False,
            )
        except OutboundIntegrationActionIdempotencyConflictError as exc:
            raise HumanReplyIdempotencyConflictError(str(exc)) from exc

        owner_reference = f"user:{actor.user_id}"
        if action.owner_reference not in {None, owner_reference}:
            raise HumanReplyIdempotencyConflictError(
                "Idempotency-Key has already been used by a different operator"
            )
        if action.owner_reference is None:
            OutboundActionOwnershipService(self.session).set_owner_reference(
                workspace,
                action.id,
                owner_reference,
            )
            self.session.refresh(action)

        duplicate = existing_action is not None
        if not duplicate:
            self.account_audit_service.record(
                account,
                IntegrationAccountAuditAction.CONFIGURED,
                actor_user_id=actor.user_id,
                reason_code=self._reply_audit_reason(view.handoff.id),
            )
            self.session.commit()
        if action.status == OutboundIntegrationActionStatus.PENDING:
            try:
                action, _ = self.delivery_service.deliver_pending_action(
                    workspace,
                    account.id,
                    action.id,
                )
            except (InactiveIntegrationAccountError, IntegrationAccountReconnectRequiredError) as exc:
                raise HumanReplyDeliveryUnavailableError(str(exc)) from exc

        message = None
        if action.status == OutboundIntegrationActionStatus.DELIVERED:
            message = self._ensure_human_message(
                message_id=message_id,
                lead=view.lead,
                channel=route.channel,
                content=normalized_content,
            )
        return HumanReplyResult(
            handoff=view.handoff,
            action=action,
            message=message,
            duplicate=duplicate,
        )

    def resolve_handoff(
        self,
        *,
        workspace: Workspace,
        handoff_id: UUID,
        actor: OperatorAssignmentActor,
    ) -> HumanHandoffResolutionResult:
        self._ensure_actor_workspace(workspace, actor)
        view = self.get_handoff(workspace, handoff_id, context_limit=1)
        if view.handoff.status == SalesConversationHandoffStatus.RESOLVED:
            audit = self.session.exec(
                select(IntegrationAccountAuditEvent)
                .where(
                    IntegrationAccountAuditEvent.workspace_id == workspace.id,
                    IntegrationAccountAuditEvent.reason_code
                    == self._resolution_audit_reason(view.handoff.id),
                )
                .order_by(IntegrationAccountAuditEvent.created_at.asc())
            ).first()
            return HumanHandoffResolutionResult(
                handoff=view.handoff,
                operator_user_id=(
                    audit.actor_user_id
                    if audit is not None and audit.actor_user_id is not None
                    else actor.user_id
                ),
                duplicate=True,
            )

        route = self._resolve_outbound_route(workspace, view.lead)
        view.handoff.status = SalesConversationHandoffStatus.RESOLVED
        view.handoff.resolved_at = utc_now()
        self.session.add(view.handoff)
        # Integration-account audit is the existing actor-attributed, workspace-scoped
        # audit envelope. The reason code distinguishes this operator operation from
        # account configuration without adding a new enum value or migration.
        self.account_audit_service.record(
            route.account,
            IntegrationAccountAuditAction.CONFIGURED,
            actor_user_id=actor.user_id,
            reason_code=self._resolution_audit_reason(view.handoff.id),
        )
        self.session.commit()
        self.session.refresh(view.handoff)
        return HumanHandoffResolutionResult(
            handoff=view.handoff,
            operator_user_id=actor.user_id,
            duplicate=False,
        )

    def _view(
        self,
        handoff: SalesConversationHandoff,
        lead: Lead,
        *,
        messages: tuple[ConversationMessage, ...] = (),
    ) -> HumanHandoffView:
        return HumanHandoffView(
            handoff=handoff,
            lead=lead,
            assignment=self.assignment_service.resolve_lead_assignment(lead),
            messages=messages,
        )

    def _recent_messages(self, lead_id: UUID, limit: int) -> tuple[ConversationMessage, ...]:
        rows = list(
            self.session.exec(
                select(ConversationMessage)
                .where(ConversationMessage.lead_id == lead_id)
                .order_by(
                    ConversationMessage.created_at.desc(),
                    ConversationMessage.id.desc(),
                )
                .limit(limit)
            ).all()
        )
        rows.reverse()
        return tuple(rows)

    def _resolve_outbound_route(self, workspace: Workspace, lead: Lead) -> _OutboundRoute:
        latest_message = self.session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.lead_id == lead.id)
            .order_by(
                ConversationMessage.created_at.desc(),
                ConversationMessage.id.desc(),
            )
        ).first()
        if latest_message is None:
            raise HumanHandoffRoutingError("Conversation has no provider-routable message history")

        rows = list(
            self.session.exec(
                select(InboundExternalIdentity, IntegrationAccount)
                .join(
                    IntegrationAccount,
                    InboundExternalIdentity.integration_account_id == IntegrationAccount.id,
                )
                .where(
                    InboundExternalIdentity.workspace_id == workspace.id,
                    InboundExternalIdentity.lead_id == lead.id,
                    InboundExternalIdentity.channel == latest_message.channel,
                    IntegrationAccount.workspace_id == workspace.id,
                    IntegrationAccount.active.is_(True),
                )
                .order_by(
                    InboundExternalIdentity.updated_at.desc(),
                    InboundExternalIdentity.id.desc(),
                )
                .limit(2)
            ).all()
        )
        if len(rows) != 1:
            raise HumanHandoffRoutingError(
                "Conversation does not have exactly one active provider route"
            )
        identity, account = rows[0]
        return _OutboundRoute(
            account=account,
            identity=identity,
            channel=latest_message.channel,
        )

    def _ensure_human_message(
        self,
        *,
        message_id: UUID,
        lead: Lead,
        channel: str,
        content: str,
    ) -> ConversationMessage:
        existing = self.session.get(ConversationMessage, message_id)
        if existing is not None:
            if (
                existing.lead_id != lead.id
                or existing.direction != HUMAN_OUTBOUND_DIRECTION
                or existing.channel != channel
                or existing.content != content
            ):
                raise HumanReplyIdempotencyConflictError(
                    "Human reply history attribution conflicts with the existing record"
                )
            return existing
        message = ConversationMessage(
            id=message_id,
            lead_id=lead.id,
            direction=HUMAN_OUTBOUND_DIRECTION,
            channel=channel,
            stage=lead.sales_stage,
            content=content,
        )
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        return message

    @staticmethod
    def _normalize_idempotency_key(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise HumanReplyIdempotencyValidationError(
                "Idempotency-Key must contain between 1 and 200 characters"
            )
        return normalized

    @staticmethod
    def _action_idempotency_key(
        workspace_id: UUID,
        handoff_id: UUID,
        idempotency_key: str,
    ) -> str:
        digest = sha256(f"{workspace_id}:{handoff_id}:{idempotency_key}".encode()).hexdigest()
        return f"human-handoff:{digest}"

    @staticmethod
    def _reply_audit_reason(handoff_id: UUID) -> str:
        return f"human_handoff_reply_{handoff_id.hex}"

    @staticmethod
    def _resolution_audit_reason(handoff_id: UUID) -> str:
        return f"human_handoff_resolved_{handoff_id.hex}"

    @staticmethod
    def _ensure_actor_workspace(workspace: Workspace, actor: OperatorAssignmentActor) -> None:
        if actor.workspace_id != workspace.id:
            raise HumanHandoffNotFoundError("Sales handoff not found")
