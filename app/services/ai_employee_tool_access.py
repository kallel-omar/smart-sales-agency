from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.ai_tool_access import (
    CAPABILITY_ACTION_COMPATIBILITY,
    CONTROLLED_AUTOMATION_SAFE_ACTION_TYPES,
    AIEmployeeAutonomyLevel,
)
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    AIEmployeeCapabilityToolAccess,
    Capability,
    IntegrationAccount,
    OutboundIntegrationActionType,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentDepartmentMismatchError,
    AIEmployeeCapabilityAssignmentNotFoundError,
    AIEmployeeCapabilityAssignmentScopeError,
)
from app.services.integration_accounts import IntegrationAccountNotFoundError
from app.services.workspaces import WorkspaceNotFoundError


class DuplicateAIEmployeeCapabilityToolAccessError(ValueError):
    """Raised when the assignment already has this integration action grant."""


class IncompatibleAIEmployeeCapabilityActionError(ValueError):
    """Raised when a business Capability is not allowed to use an action type."""


class AIEmployeeCapabilityToolAccessScopeError(PermissionError):
    """Raised when grant inputs do not share the requested workspace."""


@dataclass(frozen=True)
class AIEmployeeToolAccessDecision:
    """Deterministic policy decision for future AI-originated execution."""

    allowed: bool
    autonomy_level: AIEmployeeAutonomyLevel | None
    requires_human_approval: bool
    may_execute_automatically: bool
    denial_reason: str | None = None


class AIEmployeeCapabilityToolAccessRepository:
    """Workspace-scoped grant queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
        integration_account: IntegrationAccount,
        action_type: OutboundIntegrationActionType,
    ) -> AIEmployeeCapabilityToolAccess | None:
        return self.session.exec(
            select(AIEmployeeCapabilityToolAccess).where(
                AIEmployeeCapabilityToolAccess.workspace_id == workspace.id,
                AIEmployeeCapabilityToolAccess.assignment_id == assignment.id,
                AIEmployeeCapabilityToolAccess.integration_account_id
                == integration_account.id,
                AIEmployeeCapabilityToolAccess.action_type == action_type,
            )
        ).first()

    def add(
        self,
        grant: AIEmployeeCapabilityToolAccess,
    ) -> AIEmployeeCapabilityToolAccess:
        self.session.add(grant)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateAIEmployeeCapabilityToolAccessError(
                "AIEmployee capability already has this tool access grant"
            ) from exc
        self.session.refresh(grant)
        return grant

    def list_for_assignment(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> list[AIEmployeeCapabilityToolAccess]:
        statement = (
            select(AIEmployeeCapabilityToolAccess)
            .where(
                AIEmployeeCapabilityToolAccess.workspace_id == workspace.id,
                AIEmployeeCapabilityToolAccess.assignment_id == assignment.id,
            )
            .order_by(
                AIEmployeeCapabilityToolAccess.created_at.asc(),
                AIEmployeeCapabilityToolAccess.id.asc(),
            )
        )
        return list(self.session.exec(statement).all())


class AIEmployeeCapabilityToolAccessService:
    """Govern AIEmployee use of integration actions through assigned Capabilities."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = AIEmployeeCapabilityToolAccessRepository(session)

    def grant(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
        integration_account: IntegrationAccount,
        action_type: OutboundIntegrationActionType,
        autonomy_level: AIEmployeeAutonomyLevel,
    ) -> AIEmployeeCapabilityToolAccess:
        stored_assignment, stored_account, stored_capability = self._validate_scope(
            workspace,
            assignment,
            integration_account,
        )
        canonical_action = self._action_type(action_type)
        self._require_capability_action_compatibility(
            stored_capability,
            canonical_action,
        )
        canonical_autonomy = self._autonomy_level(autonomy_level)
        existing = self.repository.get(
            workspace,
            stored_assignment,
            stored_account,
            canonical_action,
        )
        if existing is not None:
            raise DuplicateAIEmployeeCapabilityToolAccessError(
                "AIEmployee capability already has this tool access grant"
            )
        return self.repository.add(
            AIEmployeeCapabilityToolAccess(
                workspace_id=workspace.id,
                assignment_id=stored_assignment.id,
                integration_account_id=stored_account.id,
                action_type=canonical_action,
                autonomy_level=canonical_autonomy,
            )
        )

    def evaluate(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
        integration_account: IntegrationAccount,
        action_type: OutboundIntegrationActionType,
    ) -> AIEmployeeToolAccessDecision:
        stored_assignment, stored_account, stored_capability = self._validate_scope(
            workspace,
            assignment,
            integration_account,
        )
        canonical_action = self._action_type(action_type)
        self._require_capability_action_compatibility(
            stored_capability,
            canonical_action,
        )
        grant = self.repository.get(
            workspace,
            stored_assignment,
            stored_account,
            canonical_action,
        )
        if grant is None:
            return AIEmployeeToolAccessDecision(
                allowed=False,
                autonomy_level=None,
                requires_human_approval=False,
                may_execute_automatically=False,
                denial_reason="no_active_grant",
            )
        if not grant.active:
            return AIEmployeeToolAccessDecision(
                allowed=False,
                autonomy_level=grant.autonomy_level,
                requires_human_approval=False,
                may_execute_automatically=False,
                denial_reason="grant_inactive",
            )
        return self._decision_for(grant)

    def list_for_assignment(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> list[AIEmployeeCapabilityToolAccess]:
        self._validate_assignment_scope(workspace, assignment)
        return self.repository.list_for_assignment(workspace, assignment)

    def _validate_scope(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
        integration_account: IntegrationAccount,
    ) -> tuple[AIEmployeeCapabilityAssignment, IntegrationAccount, Capability]:
        stored_assignment, stored_capability = self._validate_assignment_scope(
            workspace,
            assignment,
        )
        stored_account = self._validate_integration_account_scope(
            workspace,
            integration_account,
        )
        return stored_assignment, stored_account, stored_capability

    def _validate_assignment_scope(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> tuple[AIEmployeeCapabilityAssignment, Capability]:
        self._require_workspace(workspace)
        stored_assignment = self.session.get(
            AIEmployeeCapabilityAssignment,
            assignment.id,
        )
        if stored_assignment is None:
            raise AIEmployeeCapabilityAssignmentNotFoundError(
                "AIEmployee capability assignment not found"
            )
        if stored_assignment.workspace_id != workspace.id:
            raise AIEmployeeCapabilityAssignmentScopeError(
                "AIEmployee capability assignment does not belong to this workspace"
            )

        employee = self.session.get(AIEmployee, stored_assignment.ai_employee_id)
        capability = self.session.get(Capability, stored_assignment.capability_id)
        if employee is None or capability is None:
            raise AIEmployeeCapabilityAssignmentNotFoundError(
                "AIEmployee capability assignment is incomplete"
            )
        if employee.workspace_id != workspace.id or capability.workspace_id != workspace.id:
            raise AIEmployeeCapabilityAssignmentScopeError(
                "AIEmployee capability assignment does not belong to this workspace"
            )
        if employee.department_id != capability.department_id:
            raise AIEmployeeCapabilityAssignmentDepartmentMismatchError(
                "AIEmployee and Capability must belong to the same Department"
            )
        return stored_assignment, capability

    def _validate_integration_account_scope(
        self,
        workspace: Workspace,
        integration_account: IntegrationAccount,
    ) -> IntegrationAccount:
        stored_account = self.session.get(IntegrationAccount, integration_account.id)
        if stored_account is None:
            raise IntegrationAccountNotFoundError("Integration account not found")
        if stored_account.workspace_id != workspace.id:
            raise AIEmployeeCapabilityToolAccessScopeError(
                "Integration account does not belong to this workspace"
            )
        return stored_account

    @staticmethod
    def _require_capability_action_compatibility(
        capability: Capability,
        action_type: OutboundIntegrationActionType,
    ) -> None:
        compatible_actions = CAPABILITY_ACTION_COMPATIBILITY.get(capability.key, frozenset())
        if action_type.value not in compatible_actions:
            raise IncompatibleAIEmployeeCapabilityActionError(
                "Capability is not compatible with this action type"
            )

    @staticmethod
    def _decision_for(
        grant: AIEmployeeCapabilityToolAccess,
    ) -> AIEmployeeToolAccessDecision:
        autonomy = AIEmployeeAutonomyLevel(grant.autonomy_level)
        if autonomy == AIEmployeeAutonomyLevel.SUGGEST:
            return AIEmployeeToolAccessDecision(
                allowed=True,
                autonomy_level=autonomy,
                requires_human_approval=False,
                may_execute_automatically=False,
            )
        if autonomy == AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL:
            return AIEmployeeToolAccessDecision(
                allowed=True,
                autonomy_level=autonomy,
                requires_human_approval=True,
                may_execute_automatically=False,
            )
        if autonomy == AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION:
            action_type = OutboundIntegrationActionType(grant.action_type)
            may_execute = action_type.value in CONTROLLED_AUTOMATION_SAFE_ACTION_TYPES
            return AIEmployeeToolAccessDecision(
                allowed=True,
                autonomy_level=autonomy,
                requires_human_approval=not may_execute,
                may_execute_automatically=may_execute,
            )
        return AIEmployeeToolAccessDecision(
            allowed=True,
            autonomy_level=autonomy,
            requires_human_approval=False,
            may_execute_automatically=True,
        )

    def _require_workspace(self, workspace: Workspace) -> None:
        if self.session.get(Workspace, workspace.id) is None:
            raise WorkspaceNotFoundError("Workspace not found")

    @staticmethod
    def _action_type(
        action_type: OutboundIntegrationActionType,
    ) -> OutboundIntegrationActionType:
        return OutboundIntegrationActionType(action_type)

    @staticmethod
    def _autonomy_level(
        autonomy_level: AIEmployeeAutonomyLevel,
    ) -> AIEmployeeAutonomyLevel:
        return AIEmployeeAutonomyLevel(autonomy_level)
