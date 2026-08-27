"""Workspace-scoped read-only producer for deterministic ICP facts."""

from uuid import UUID

from sqlmodel import Session, select

from app.departments.sales.icp_scoring import ICPScoringInput
from app.departments.sales.qualification_facts import (
    PersistedQualificationEvidence,
    adapt_qualification_facts,
)
from app.models import Lead, LeadResearch, Workspace
from app.services.sales_playbooks import WorkspaceSalesPlaybookService

MAX_QUALIFICATION_RESEARCH_RECORDS = 10


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
        facts = adapt_qualification_facts(
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
