from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    DirectConversationTurnReceipt,
    DirectConversationTurnReceiptStatus,
    Lead,
    LeadResearch,
    LeadStatus,
    Product,
    SalesConversationHandoff,
    SalesConversationHandoffStatus,
    SalesHandoffReasonCode,
    SalesStage,
    Workspace,
)


class NotFoundError(LookupError):
    pass


class HandoffLifecycleConflictError(RuntimeError):
    """Raised when a requested Sales handoff lifecycle transition is invalid."""


class SalesRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_lead(self, lead_id: UUID) -> Lead:
        lead = self.session.get(Lead, lead_id)
        if not lead:
            raise NotFoundError(f"Lead {lead_id} was not found")
        return lead

    def list_leads(self, tenant_id: str = "demo") -> list[Lead]:
        statement = select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.created_at.desc())
        return list(self.session.exec(statement).all())

    def list_products(self, tenant_id: str = "demo") -> list[Product]:
        statement = select(Product).where(Product.tenant_id == tenant_id, Product.active.is_(True))
        return list(self.session.exec(statement).all())

    def save_research(
        self,
        lead: Lead,
        summary: str,
        pain_points: list[str],
        opportunities: list[str],
        evidence: list[dict],
    ) -> LeadResearch:
        research = LeadResearch(
            lead_id=lead.id,
            summary=summary,
            pain_points=pain_points,
            opportunities=opportunities,
            evidence=evidence,
        )
        lead.status = LeadStatus.RESEARCHED
        lead.updated_at = datetime.now(UTC)
        self.session.add(research)
        self.session.add(lead)
        self.session.commit()
        self.session.refresh(research)
        return research

    def update_lead_score(self, lead: Lead, score: int, qualified: bool) -> Lead:
        return self.update_lead_qualification_state(
            lead,
            score=score,
            status=(LeadStatus.QUALIFIED if qualified else LeadStatus.UNQUALIFIED),
        )

    def update_lead_qualification_state(
        self,
        lead: Lead,
        *,
        score: int,
        status: LeadStatus,
    ) -> Lead:
        """Persist a score and status already resolved by Sales domain policy."""

        current_status = LeadStatus(lead.status)
        resolved_status = LeadStatus(status)
        if resolved_status not in {
            current_status,
            LeadStatus.QUALIFIED,
            LeadStatus.UNQUALIFIED,
        }:
            raise ValueError(
                "Qualification persistence cannot advance unrelated Lead lifecycle state"
            )
        lead.score = max(0, min(100, score))
        lead.status = resolved_status
        lead.updated_at = datetime.now(UTC)
        self.session.add(lead)
        self.session.commit()
        self.session.refresh(lead)
        return lead

    def update_sales_stage(self, lead: Lead, stage: SalesStage) -> Lead:
        """Persist a transition already validated by the Sales domain policy."""

        lead.sales_stage = stage
        lead.updated_at = datetime.now(UTC)
        self.session.add(lead)
        self.session.commit()
        self.session.refresh(lead)
        return lead

    def add_message(self, message: ConversationMessage) -> ConversationMessage:
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        return message

    def conversation_history(self, lead_id: UUID, limit: int = 20) -> list[ConversationMessage]:
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.lead_id == lead_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        messages = list(self.session.exec(statement).all())
        messages.reverse()
        return messages

    def reserve_direct_conversation_turn_receipt(
        self,
        *,
        workspace: Workspace,
        lead: Lead,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[DirectConversationTurnReceipt, bool]:
        """Persist the authoritative reservation before direct turn execution."""

        if lead.tenant_id != workspace.slug:
            raise NotFoundError("Lead not found")
        receipt = DirectConversationTurnReceipt(
            workspace_id=workspace.id,
            lead_id=lead.id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        self.session.add(receipt)
        try:
            self.session.commit()
            self.session.refresh(receipt)
            return receipt, True
        except IntegrityError:
            self.session.rollback()
            existing = self.session.exec(
                select(DirectConversationTurnReceipt).where(
                    DirectConversationTurnReceipt.workspace_id == workspace.id,
                    DirectConversationTurnReceipt.lead_id == lead.id,
                    DirectConversationTurnReceipt.idempotency_key == idempotency_key,
                )
            ).first()
            if existing is None:
                raise
            return existing, False

    def complete_direct_conversation_turn_receipt(
        self,
        receipt: DirectConversationTurnReceipt,
        *,
        detected_stage: SalesStage,
        draft_reply: str,
        approval_id: UUID | None,
        handoff_required: bool,
        handoff_reason_code: SalesHandoffReasonCode | None,
    ) -> DirectConversationTurnReceipt:
        """Save a minimal safe completed result after canonical turn persistence."""

        receipt.status = DirectConversationTurnReceiptStatus.COMPLETED
        receipt.detected_stage = detected_stage
        receipt.draft_reply = draft_reply
        receipt.approval_id = approval_id
        receipt.handoff_required = handoff_required
        receipt.handoff_reason_code = handoff_reason_code
        receipt.completed_at = datetime.now(UTC)
        self.session.add(receipt)
        self.session.commit()
        self.session.refresh(receipt)
        return receipt

    def discard_direct_conversation_turn_receipt(
        self,
        receipt: DirectConversationTurnReceipt,
    ) -> None:
        """Release an unfinished reservation after a failed turn for retry."""

        self.session.delete(receipt)
        self.session.commit()

    def get_sales_handoff(
        self,
        workspace: Workspace,
        lead_id: UUID,
    ) -> SalesConversationHandoff | None:
        """Read the current active handoff through its server-resolved owner."""

        statement = select(SalesConversationHandoff).where(
            SalesConversationHandoff.workspace_id == workspace.id,
            SalesConversationHandoff.lead_id == lead_id,
            SalesConversationHandoff.status == SalesConversationHandoffStatus.ACTIVE,
        )
        return self.session.exec(statement).first()

    def get_latest_sales_handoff(
        self,
        workspace: Workspace,
        lead_id: UUID,
    ) -> SalesConversationHandoff | None:
        """Return the most recent scoped record for deterministic lifecycle errors."""

        statement = (
            select(SalesConversationHandoff)
            .where(
                SalesConversationHandoff.workspace_id == workspace.id,
                SalesConversationHandoff.lead_id == lead_id,
            )
            .order_by(SalesConversationHandoff.created_at.desc())
        )
        return self.session.exec(statement).first()

    def list_sales_handoffs(
        self,
        workspace: Workspace,
        lead_id: UUID,
    ) -> list[SalesConversationHandoff]:
        """Read scoped lifecycle history without making it a dashboard API."""

        statement = (
            select(SalesConversationHandoff)
            .where(
                SalesConversationHandoff.workspace_id == workspace.id,
                SalesConversationHandoff.lead_id == lead_id,
            )
            .order_by(SalesConversationHandoff.created_at.asc())
        )
        return list(self.session.exec(statement).all())

    def ensure_sales_handoff(
        self,
        *,
        workspace: Workspace,
        lead: Lead,
        reason_code: SalesHandoffReasonCode,
        explanation: str,
    ) -> SalesConversationHandoff:
        """Persist one active handoff while preserving resolved history."""

        if lead.tenant_id != workspace.slug:
            raise NotFoundError("Lead not found")
        existing = self.get_sales_handoff(workspace, lead.id)
        if existing is not None:
            return existing
        handoff = SalesConversationHandoff(
            workspace_id=workspace.id,
            lead_id=lead.id,
            reason_code=reason_code,
            explanation=explanation,
        )
        self.session.add(handoff)
        self.session.commit()
        self.session.refresh(handoff)
        return handoff

    def resolve_sales_handoff(
        self,
        *,
        workspace: Workspace,
        lead: Lead,
    ) -> SalesConversationHandoff:
        """Resolve the current handoff without changing approvals or delivery state."""

        if lead.tenant_id != workspace.slug:
            raise NotFoundError("Lead not found")

        handoff = self.get_sales_handoff(workspace, lead.id)
        if handoff is None:
            latest = self.get_latest_sales_handoff(workspace, lead.id)
            if latest is not None and latest.status == SalesConversationHandoffStatus.RESOLVED:
                raise HandoffLifecycleConflictError("Sales handoff is already resolved")
            raise NotFoundError("Sales handoff not found")

        handoff.status = SalesConversationHandoffStatus.RESOLVED
        handoff.resolved_at = datetime.now(UTC)
        self.session.add(handoff)
        self.session.commit()
        self.session.refresh(handoff)
        return handoff

    def create_approval(
        self, lead_id: UUID, channel: str, payload: dict, action_type: str = "send_message"
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            lead_id=lead_id,
            channel=channel,
            payload=payload,
            action_type=action_type,
        )
        self.session.add(approval)
        self.session.commit()
        self.session.refresh(approval)
        return approval

    def list_approvals(
        self,
        tenant_id: str,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalRequest]:
        statement = (
            select(ApprovalRequest)
            .join(Lead, ApprovalRequest.lead_id == Lead.id)
            .where(Lead.tenant_id == tenant_id)
            .order_by(ApprovalRequest.created_at.desc())
        )
        if status is not None:
            statement = statement.where(ApprovalRequest.status == status)
        return list(self.session.exec(statement).all())
