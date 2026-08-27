"""Workspace-scoped governed execution boundaries for icp_scoring:v1."""

from uuid import UUID

from sqlmodel import Session

from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.departments.sales.evidence import SalesEvidenceClassification
from app.departments.sales.icp_scoring import (
    ICP_SCORING_KEY,
    ICP_SCORING_VERSION,
    ICPScoringAuthorizationError,
    ICPScoringExecutionResult,
    ICPScoringInput,
    execute_icp_scoring,
)
from app.departments.sales.skills import sales_agent_skill_registry
from app.models import Lead, Workspace
from app.services.qualification_facts import (
    QualificationFactAdapter,
    QualificationFactPlaybookUnavailableError,
)
from app.services.sales_playbooks import (
    WorkspaceSalesPlaybookPersistenceError,
    WorkspaceSalesPlaybookService,
)

ICP_ASSESSMENT_STATUS_ASSESSED = "assessed"
ICP_ASSESSMENT_STATUS_UNAVAILABLE = "unavailable"
ICP_ASSESSMENT_PLAYBOOK_INVALID = "playbook_invalid"


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


class QualificationICPAssessmentService:
    """Compose existing scoped facts and deterministic scoring for qualification."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def assess(
        self,
        workspace: Workspace,
        lead: Lead,
        context: AgentSkillExecutionContext,
        *,
        research_id: UUID | None,
    ) -> dict[str, object]:
        skill = self._skill_attribution(workspace, context)
        try:
            source = QualificationFactAdapter(self.session).build_icp_input(
                workspace,
                lead.id,
                research_id=research_id,
            )
            execution = WorkspaceICPScoringService(self.session).score(
                workspace,
                source,
                context,
            )
        except (QualificationFactPlaybookUnavailableError, ICPScoringUnavailableError):
            return self._unavailable(skill, ICPScoringUnavailableError.reason_code)
        except WorkspaceSalesPlaybookPersistenceError:
            return self._unavailable(skill, ICP_ASSESSMENT_PLAYBOOK_INVALID)

        assessment = execution.result.as_dict()
        return {
            "status": ICP_ASSESSMENT_STATUS_ASSESSED,
            "skill": {
                **skill,
                "attribution_identifier": execution.attribution_identifier,
                "ai_invoked": execution.ai_invoked,
            },
            **assessment,
            "evidence_summary": self._evidence_summary(source),
        }

    @staticmethod
    def _skill_attribution(
        workspace: Workspace,
        context: AgentSkillExecutionContext,
    ) -> dict[str, object]:
        definition = sales_agent_skill_registry().require_eligible(
            ICP_SCORING_KEY,
            ICP_SCORING_VERSION,
            department=context.department,
            role=context.employee_role,
            capability=context.capability,
        )
        if (
            context.workspace_id != workspace.id
            or context.skill_key != ICP_SCORING_KEY
            or context.skill_version != ICP_SCORING_VERSION
            or context.input_contract != definition.input_contract
            or context.output_contract != definition.output_contract
            or context.validator != definition.validator
            or context.instruction_component != definition.instruction_component
            or context.attribution_identifier != definition.attribution_identifier
            or context.effective_tool_ceiling
        ):
            raise ICPScoringAuthorizationError(
                "Qualification assessment requires authorized icp_scoring:v1"
            )
        return {
            "key": context.skill_key,
            "version": context.skill_version,
            "attribution_identifier": context.attribution_identifier,
            "ai_invoked": False,
        }

    @staticmethod
    def _unavailable(
        skill: dict[str, object],
        reason_code: str,
    ) -> dict[str, object]:
        return {
            "status": ICP_ASSESSMENT_STATUS_UNAVAILABLE,
            "reason_code": reason_code,
            "skill": skill,
        }

    @staticmethod
    def _evidence_summary(source: ICPScoringInput) -> dict[str, int]:
        return {
            "total": len(source.facts),
            "confirmed": sum(
                fact.classification is SalesEvidenceClassification.CONFIRMED
                for fact in source.facts
            ),
            "inference": sum(
                fact.classification is SalesEvidenceClassification.INFERENCE
                for fact in source.facts
            ),
            "unknown": sum(
                fact.classification is SalesEvidenceClassification.UNKNOWN
                for fact in source.facts
            ),
        }
