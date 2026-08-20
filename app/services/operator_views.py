from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, or_
from sqlmodel import Session, select

from app.core.work_items import WorkItemStatus
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    AIEmployeeCapabilityToolAccess,
    ApprovalRequest,
    ApprovalStatus,
    Capability,
    Department,
    IntegrationAccount,
    Lead,
    WorkItem,
    Workspace,
)
from app.schemas import (
    OperatorAIEmployeeRead,
    OperatorApprovalRead,
    OperatorCapabilityRead,
    OperatorToolAccessRead,
    OperatorWorkItemRead,
)

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "webhook_signature",
)


class OperatorViewNotFoundError(LookupError):
    """Raised when an operator resource is outside the selected workspace."""


class OperatorViewService:
    """Build bounded, workspace-safe projections for the human operator UI."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_workforce(
        self, workspace: Workspace, *, limit: int = 100
    ) -> list[OperatorAIEmployeeRead]:
        employees = list(
            self.session.exec(
                select(AIEmployee)
                .where(AIEmployee.workspace_id == workspace.id)
                .order_by(AIEmployee.created_at.desc(), AIEmployee.id.desc())
                .limit(limit)
            ).all()
        )
        return self._workforce_views(workspace, employees)

    def get_employee(
        self, workspace: Workspace, employee_id: UUID
    ) -> OperatorAIEmployeeRead:
        employee = self.session.exec(
            select(AIEmployee).where(
                AIEmployee.id == employee_id,
                AIEmployee.workspace_id == workspace.id,
            )
        ).first()
        if employee is None:
            raise OperatorViewNotFoundError("AI employee not found")
        return self._workforce_views(workspace, [employee])[0]

    def list_work_items(
        self,
        workspace: Workspace,
        *,
        status: WorkItemStatus | None = None,
        work_type: str | None = None,
        department_id: UUID | None = None,
        ai_employee_id: UUID | None = None,
        capability_id: UUID | None = None,
        limit: int = 100,
    ) -> list[OperatorWorkItemRead]:
        statement = select(WorkItem).where(WorkItem.workspace_id == workspace.id)
        if status is not None:
            statement = statement.where(WorkItem.status == status)
        if work_type:
            statement = statement.where(WorkItem.work_type == work_type.strip())
        if department_id is not None:
            statement = statement.where(WorkItem.department_id == department_id)
        if ai_employee_id is not None:
            statement = statement.where(WorkItem.ai_employee_id == ai_employee_id)
        if capability_id is not None:
            statement = statement.where(WorkItem.capability_id == capability_id)
        items = list(
            self.session.exec(
                statement.order_by(WorkItem.created_at.desc(), WorkItem.id.desc()).limit(
                    limit
                )
            ).all()
        )
        return self._work_item_views(workspace, items)

    def get_work_item(
        self, workspace: Workspace, work_item_id: UUID
    ) -> OperatorWorkItemRead:
        item = self.session.exec(
            select(WorkItem).where(
                WorkItem.id == work_item_id,
                WorkItem.workspace_id == workspace.id,
            )
        ).first()
        if item is None:
            raise OperatorViewNotFoundError("Work item not found")
        return self._work_item_views(workspace, [item])[0]

    def list_approvals(
        self,
        workspace: Workspace,
        *,
        status: ApprovalStatus | None = None,
        limit: int = 100,
    ) -> list[OperatorApprovalRead]:
        statement = (
            select(ApprovalRequest)
            .outerjoin(WorkItem, ApprovalRequest.work_item_id == WorkItem.id)
            .outerjoin(Lead, ApprovalRequest.lead_id == Lead.id)
            .where(self._approval_scope(workspace))
        )
        if status is not None:
            statement = statement.where(ApprovalRequest.status == status)
        approvals = list(
            self.session.exec(
                statement.order_by(
                    case((ApprovalRequest.status == ApprovalStatus.PENDING, 0), else_=1),
                    ApprovalRequest.created_at.desc(),
                    ApprovalRequest.id.desc(),
                ).limit(limit)
            ).all()
        )
        return [self._approval_view(workspace, approval) for approval in approvals]

    def get_approval(
        self, workspace: Workspace, approval_id: UUID
    ) -> OperatorApprovalRead:
        approval = self.session.exec(
            select(ApprovalRequest)
            .outerjoin(WorkItem, ApprovalRequest.work_item_id == WorkItem.id)
            .outerjoin(Lead, ApprovalRequest.lead_id == Lead.id)
            .where(
                ApprovalRequest.id == approval_id,
                self._approval_scope(workspace),
            )
        ).first()
        if approval is None:
            raise OperatorViewNotFoundError("Approval not found")
        return self._approval_view(workspace, approval)

    def _workforce_views(
        self, workspace: Workspace, employees: list[AIEmployee]
    ) -> list[OperatorAIEmployeeRead]:
        if not employees:
            return []
        employee_ids = [employee.id for employee in employees]
        departments = {
            department.id: department
            for department in self.session.exec(
                select(Department).where(Department.workspace_id == workspace.id)
            ).all()
        }
        assignments = list(
            self.session.exec(
                select(AIEmployeeCapabilityAssignment).where(
                    AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
                    AIEmployeeCapabilityAssignment.ai_employee_id.in_(employee_ids),
                )
            ).all()
        )
        capabilities = {
            capability.id: capability
            for capability in self.session.exec(
                select(Capability).where(Capability.workspace_id == workspace.id)
            ).all()
        }
        assignment_ids = [assignment.id for assignment in assignments]
        accesses = (
            list(
                self.session.exec(
                    select(AIEmployeeCapabilityToolAccess).where(
                        AIEmployeeCapabilityToolAccess.workspace_id == workspace.id,
                        AIEmployeeCapabilityToolAccess.assignment_id.in_(assignment_ids),
                    )
                ).all()
            )
            if assignment_ids
            else []
        )
        accounts = {
            account.id: account
            for account in self.session.exec(
                select(IntegrationAccount).where(
                    IntegrationAccount.workspace_id == workspace.id
                )
            ).all()
        }
        by_employee: dict[UUID, list[OperatorCapabilityRead]] = {
            employee.id: [] for employee in employees
        }
        access_by_assignment: dict[UUID, list[OperatorToolAccessRead]] = {}
        for access in accesses:
            account = accounts.get(access.integration_account_id)
            if account is None:
                continue
            access_by_assignment.setdefault(access.assignment_id, []).append(
                OperatorToolAccessRead(
                    integration_account_id=account.id,
                    provider=account.provider,
                    external_account_id=account.external_account_id,
                    action_type=access.action_type,
                    autonomy_level=access.autonomy_level,
                    active=access.active,
                )
            )
        for assignment in assignments:
            capability = capabilities.get(assignment.capability_id)
            if capability is None:
                continue
            by_employee[assignment.ai_employee_id].append(
                OperatorCapabilityRead(
                    id=capability.id,
                    assignment_id=assignment.id,
                    key=capability.key,
                    active=capability.active,
                    tool_access=access_by_assignment.get(assignment.id, []),
                )
            )
        views: list[OperatorAIEmployeeRead] = []
        for employee in employees:
            department = departments.get(employee.department_id)
            if department is None:
                continue
            views.append(
                OperatorAIEmployeeRead(
                    id=employee.id,
                    name=employee.name,
                    role_key=employee.role_key,
                    active=employee.active,
                    department_id=department.id,
                    department=department.kind,
                    capabilities=by_employee[employee.id],
                    created_at=employee.created_at,
                    updated_at=employee.updated_at,
                )
            )
        return views

    def _work_item_views(
        self, workspace: Workspace, items: list[WorkItem]
    ) -> list[OperatorWorkItemRead]:
        if not items:
            return []
        departments = {
            row.id: row
            for row in self.session.exec(
                select(Department).where(Department.workspace_id == workspace.id)
            ).all()
        }
        employees = {
            row.id: row
            for row in self.session.exec(
                select(AIEmployee).where(AIEmployee.workspace_id == workspace.id)
            ).all()
        }
        capabilities = {
            row.id: row
            for row in self.session.exec(
                select(Capability).where(Capability.workspace_id == workspace.id)
            ).all()
        }
        item_ids = [item.id for item in items]
        approvals = {
            row.work_item_id: row
            for row in self.session.exec(
                select(ApprovalRequest).where(ApprovalRequest.work_item_id.in_(item_ids))
            ).all()
            if row.work_item_id is not None
        }
        result: list[OperatorWorkItemRead] = []
        for item in items:
            department = departments[item.department_id]
            employee = employees.get(item.ai_employee_id)
            capability = capabilities.get(item.capability_id)
            approval = approvals.get(item.id)
            result.append(
                OperatorWorkItemRead(
                    id=item.id,
                    title=item.title,
                    work_type=item.work_type,
                    status=item.status,
                    department_id=department.id,
                    department=department.kind,
                    ai_employee_id=item.ai_employee_id,
                    ai_employee_name=employee.name if employee else None,
                    capability_id=item.capability_id,
                    capability_key=capability.key if capability else None,
                    correlation_id=item.correlation_id,
                    input=_safe_json(item.input),
                    result=_safe_json(item.result) if item.result is not None else None,
                    error_code=item.error_code,
                    error_message=item.error_message,
                    source_follow_up_task_id=item.source_follow_up_task_id,
                    parent_work_item_id=item.parent_work_item_id,
                    approval_id=approval.id if approval else None,
                    approval_status=approval.status if approval else None,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    started_at=item.started_at,
                    completed_at=item.completed_at,
                    expires_at=item.expires_at,
                )
            )
        return result

    def _approval_view(
        self, workspace: Workspace, approval: ApprovalRequest
    ) -> OperatorApprovalRead:
        work_item = (
            self.session.get(WorkItem, approval.work_item_id)
            if approval.work_item_id is not None
            else None
        )
        lead = self.session.get(Lead, approval.lead_id) if approval.lead_id else None
        employee = (
            self.session.get(AIEmployee, work_item.ai_employee_id)
            if work_item is not None and work_item.ai_employee_id is not None
            else None
        )
        capability = (
            self.session.get(Capability, work_item.capability_id)
            if work_item is not None and work_item.capability_id is not None
            else None
        )
        account = self._payload_account(workspace, approval.payload)
        return OperatorApprovalRead(
            id=approval.id,
            status=approval.status,
            action_type=approval.action_type,
            channel=approval.channel,
            payload=_safe_json(approval.payload),
            reviewer_note=approval.reviewer_note,
            created_at=approval.created_at,
            decided_at=approval.decided_at,
            lead_id=lead.id if lead else None,
            lead_name=lead.full_name if lead else None,
            company_name=lead.company_name if lead else None,
            work_item_id=work_item.id if work_item else None,
            work_item_title=work_item.title if work_item else None,
            work_type=work_item.work_type if work_item else None,
            work_item_status=work_item.status if work_item else None,
            ai_employee_name=employee.name if employee else None,
            capability_key=capability.key if capability else None,
            integration_provider=account.provider if account else None,
            integration_external_account_id=(
                account.external_account_id if account else None
            ),
        )

    @staticmethod
    def _approval_scope(workspace: Workspace):
        return or_(
            WorkItem.workspace_id == workspace.id,
            and_(ApprovalRequest.work_item_id.is_(None), Lead.tenant_id == workspace.slug),
        )

    def _payload_account(
        self, workspace: Workspace, payload: dict[str, Any]
    ) -> IntegrationAccount | None:
        try:
            account_id = UUID(str(payload.get("integration_account_id")))
        except (TypeError, ValueError, AttributeError):
            return None
        return self.session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.id == account_id,
                IntegrationAccount.workspace_id == workspace.id,
            )
        ).first()


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_json(item)
            for key, item in value.items()
            if not any(part in str(key).casefold() for part in _SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    return value
