"""Workspace-scoped read-only producer for deterministic ICP facts."""

from uuid import UUID

from sqlmodel import Session, select

from app.departments.sales.icp_scoring import MAX_ICP_FACTS, ICPScoringInput
from app.departments.sales.qualification_collection import (
    conversation_facts_for_playbook,
)
from app.departments.sales.qualification_facts import (
    PersistedQualificationEvidence,
    adapt_qualification_facts,
)
from app.models import ConversationMessage, Lead, LeadResearch, Workspace
from app.services.sales_playbooks import WorkspaceSalesPlaybookService

MAX_QUALIFICATION_RESEARCH_RECORDS = 10
MAX_QUALIFICATION_CONVERSATION_MESSAGES = 20


class QualificationFactScopeError(PermissionError):
    """Raised when persisted evidence is outside the resolved workspace Lead."""


class QualificationFactPlaybookUnavailableError(ValueError):
    """Raised when the current workspace has no structured Playbook."""

    reason_code = "playbook_not_configured"


class QualificationFactAdapter:
    """Read scoped persisted evidence and produce facts without business mutation."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def build_icp_input(
        self,
        workspace: Workspace,
        lead_id: UUID,
        *,
        research_id: UUID | None = None,
        conversation_message_ids: tuple[UUID, ...] = (),
    ) -> ICPScoringInput:
        lead = self.session.get(Lead, lead_id)
        if lead is None or lead.tenant_id != workspace.slug:
            raise QualificationFactScopeError(
                "Qualification facts require a Lead in the resolved workspace"
            )
        playbook = WorkspaceSalesPlaybookService(self.session).read(workspace)
        if playbook is None:
            raise QualificationFactPlaybookUnavailableError(
                "Sales Playbook is not configured"
            )
        research_records = self._research_records(lead, research_id)
        research_facts = list(
            adapt_qualification_facts(
                playbook,
                tuple(
                    PersistedQualificationEvidence(
                        research.id,
                        (
                            tuple(research.evidence)
                            if isinstance(research.evidence, list)
                            else ()
                        ),
                    )
                    for research in research_records
                ),
            )
        )
        conversation_facts = [
            fact
            for message in self._conversation_messages(
                lead,
                conversation_message_ids,
            )
            for fact in conversation_facts_for_playbook(
                playbook,
                message.content,
                source_reference=f"conversation_message:{message.id}",
            )
        ]
        facts = tuple((conversation_facts + research_facts)[:MAX_ICP_FACTS])
        return ICPScoringInput(workspace.id, lead.id, facts)

    def _research_records(
        self,
        lead: Lead,
        research_id: UUID | None,
    ) -> tuple[LeadResearch, ...]:
        if research_id is not None:
            research = self.session.get(LeadResearch, research_id)
            if research is None or research.lead_id != lead.id:
                raise QualificationFactScopeError(
                    "LeadResearch does not belong to the resolved workspace Lead"
                )
            return (research,)
        return tuple(
            self.session.exec(
                select(LeadResearch)
                .where(LeadResearch.lead_id == lead.id)
                .order_by(LeadResearch.created_at.desc(), LeadResearch.id.desc())
                .limit(MAX_QUALIFICATION_RESEARCH_RECORDS)
            ).all()
        )

    def _conversation_messages(
        self,
        lead: Lead,
        message_ids: tuple[UUID, ...],
    ) -> tuple[ConversationMessage, ...]:
        if (
            not isinstance(message_ids, tuple)
            or len(message_ids) > MAX_QUALIFICATION_CONVERSATION_MESSAGES
            or len(set(message_ids)) != len(message_ids)
        ):
            raise QualificationFactScopeError("Qualification conversation evidence is invalid")
        if not message_ids:
            return ()
        messages = tuple(
            self.session.exec(
                select(ConversationMessage).where(
                    ConversationMessage.id.in_(message_ids),
                    ConversationMessage.lead_id == lead.id,
                    ConversationMessage.direction == "inbound",
                )
            ).all()
        )
        if len(messages) != len(set(message_ids)):
            raise QualificationFactScopeError(
                "Qualification conversation evidence does not belong to the resolved Lead"
            )
        by_id = {message.id: message for message in messages}
        return tuple(by_id[message_id] for message_id in message_ids)
