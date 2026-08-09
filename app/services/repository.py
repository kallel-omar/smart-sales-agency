from datetime import datetime, timezone
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    Lead,
    LeadResearch,
    LeadStatus,
    Product,
)


class NotFoundError(LookupError):
    pass


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
        lead.updated_at = datetime.now(timezone.utc)
        self.session.add(research)
        self.session.add(lead)
        self.session.commit()
        self.session.refresh(research)
        return research

    def update_lead_score(self, lead: Lead, score: int, qualified: bool) -> Lead:
        lead.score = max(0, min(100, score))
        lead.status = LeadStatus.QUALIFIED if qualified else LeadStatus.UNQUALIFIED
        lead.updated_at = datetime.now(timezone.utc)
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
