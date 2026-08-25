from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from app.core.ai_employees import (
    AI_EMPLOYEE_ROLE_DEFAULT_NAMES,
    AIEmployeeRoleKey,
)
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Department,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService, UnsupportedAIEmployeeRoleError
from app.services.capabilities import CapabilityService

CANONICAL_SALES_WORKFORCE_CONTRACT: dict[
    AIEmployeeRoleKey,
    tuple[BusinessCapabilityKey, ...],
] = {
    AIEmployeeRoleKey.LEAD_RESEARCH: (
        BusinessCapabilityKey.CAPTURE_LEAD,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    ),
    AIEmployeeRoleKey.QUALIFICATION: (BusinessCapabilityKey.QUALIFY_LEAD,),
    AIEmployeeRoleKey.SALES_CONVERSATION: (
        BusinessCapabilityKey.ANSWER_CUSTOMER,
        BusinessCapabilityKey.SEND_MESSAGE,
    ),
    AIEmployeeRoleKey.FOLLOW_UP: (BusinessCapabilityKey.FOLLOW_UP_LEAD,),
}


@dataclass(frozen=True, slots=True)
class SalesWorkforceProvisioningResult:
    employees: dict[AIEmployeeRoleKey, AIEmployee]
    capabilities: dict[BusinessCapabilityKey, Capability]
    assignments: dict[
        tuple[AIEmployeeRoleKey, BusinessCapabilityKey],
        AIEmployeeCapabilityAssignment,
    ]


class SalesWorkforceProvisioningService:
    """Converge one Sales Department to HIRI's default MVP workforce contract."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.employees = AIEmployeeService(session)
        self.capabilities = CapabilityService(session)
        self.assignments = AIEmployeeCapabilityAssignmentService(session)

    def ensure_default_workforce(
        self,
        workspace: Workspace,
        department: Department,
    ) -> SalesWorkforceProvisioningResult:
        if department.kind != DepartmentKind.SALES:
            raise UnsupportedAIEmployeeRoleError(
                "Default Sales workforce requires a Sales department"
            )

        capabilities: dict[BusinessCapabilityKey, Capability] = {}
        selected: dict[AIEmployeeRoleKey, AIEmployee] = {}
        assignments: dict[
            tuple[AIEmployeeRoleKey, BusinessCapabilityKey],
            AIEmployeeCapabilityAssignment,
        ] = {}

        for role in CANONICAL_SALES_WORKFORCE_CONTRACT:
            employee, role_capabilities, role_assignments = self.ensure_default_role(
                workspace,
                department,
                role,
            )
            selected[role] = employee
            capabilities.update(role_capabilities)
            assignments.update(
                {
                    (role, capability_key): assignment
                    for capability_key, assignment in role_assignments.items()
                }
            )

        return SalesWorkforceProvisioningResult(
            employees=selected,
            capabilities=capabilities,
            assignments=assignments,
        )

    def ensure_default_role(
        self,
        workspace: Workspace,
        department: Department,
        role: AIEmployeeRoleKey,
    ) -> tuple[
        AIEmployee,
        dict[BusinessCapabilityKey, Capability],
        dict[BusinessCapabilityKey, AIEmployeeCapabilityAssignment],
    ]:
        if department.kind != DepartmentKind.SALES:
            raise UnsupportedAIEmployeeRoleError(
                "Default Sales workforce requires a Sales department"
            )
        capability_keys = CANONICAL_SALES_WORKFORCE_CONTRACT[role]
        capabilities = {
            key: self.capabilities.ensure_for_department(
                workspace,
                department,
                key,
            )
            for key in capability_keys
        }
        employee = self._select_or_create_employee(
            workspace,
            department,
            role,
            self.employees.list_for_department(workspace, department),
        )
        assignments: dict[
            BusinessCapabilityKey,
            AIEmployeeCapabilityAssignment,
        ] = {}
        for key, capability in capabilities.items():
            assignment = self.assignments.repository.get(
                workspace,
                employee,
                capability,
            )
            if assignment is None:
                assignment = self.assignments.assign(
                    workspace,
                    employee,
                    capability,
                )
            assignments[key] = assignment
        return employee, capabilities, assignments

    def _select_or_create_employee(
        self,
        workspace: Workspace,
        department: Department,
        role: AIEmployeeRoleKey,
        available: list[AIEmployee],
    ) -> AIEmployee:
        default_name = AI_EMPLOYEE_ROLE_DEFAULT_NAMES[role]
        candidates = [
            employee
            for employee in available
            if employee.role_key == role
            and employee.active
            and employee.name == default_name
        ]
        candidates.sort(
            key=lambda employee: (
                employee.created_at,
                employee.id,
            )
        )
        if candidates:
            return candidates[0]
        return self.employees.create_for_department(
            workspace,
            department,
            role,
        )
