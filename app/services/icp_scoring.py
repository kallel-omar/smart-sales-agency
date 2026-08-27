"""Workspace-scoped governed execution boundary for icp_scoring:v1."""

from sqlmodel import Session

from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.departments.sales.icp_scoring import (
    ICPScoringAuthorizationError,
    ICPScoringExecutionResult,
    ICPScoringInput,
    execute_icp_scoring,
)
from app.models import Lead, Workspace
from app.services.sales_playbooks import WorkspaceSalesPlaybookService


class ICPScoringUnavailableError(ValueError):
    """Safe absence signal when a workspace has no configured Playbook."""

    reason_code = "playbook_not_configured"


class WorkspaceICPScoringService:
    """Read one workspace Playbook and execute the authorized pure evaluator."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def score(
        self,
        workspace: Workspace,
        source: ICPScoringInput,
        context: AgentSkillExecutionContext,
    ) -> ICPScoringExecutionResult:
        if source.workspace_id != workspace.id or context.workspace_id != workspace.id:
            raise ICPScoringAuthorizationError(
                "ICP scoring input does not belong to the authorized workspace"
            )
        lead = self.session.get(Lead, source.lead_id)
        if lead is None or lead.tenant_id != workspace.slug:
            raise ICPScoringAuthorizationError(
                "ICP scoring Lead does not belong to the authorized workspace"
            )
        playbook = WorkspaceSalesPlaybookService(self.session).read(workspace)
        if playbook is None:
            raise ICPScoringUnavailableError("Sales Playbook is not configured")
        return execute_icp_scoring(playbook, source, context)
