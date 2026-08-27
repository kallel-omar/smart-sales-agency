from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.departments.sales.qualification_authority import (
    QualificationAuthorityDecisionValue,
)
from app.departments.sales.services.work_item_execution import (
    SalesWorkItemExecutionService,
)
from app.models import Capability, Contact, Department, Lead, WorkItem, Workspace
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.departments import DepartmentService
from app.services.sales_workforce import SalesWorkforceProvisioningService
from app.services.work_items import WorkItemService


class SalesAcquisitionCoordinationError(RuntimeError):
    """Raised when a persisted Sales acquisition trace cannot advance safely."""


class SalesAcquisitionRoutingError(SalesAcquisitionCoordinationError):
    """Raised when a required acquisition WorkItem has no eligible assignment."""


class SalesAcquisitionResultError(SalesAcquisitionCoordinationError):
    """Raised when a terminal WorkItem result cannot drive a safe transition."""


class SalesWorkItemResultCoordinator:
    """Create the one deterministic next WorkItem from a completed Sales result."""

    _TRANSITIONS: ClassVar[dict[BusinessCapabilityKey, BusinessCapabilityKey]] = {
        BusinessCapabilityKey.CAPTURE_LEAD: BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.RESEARCH_COMPANY: BusinessCapabilityKey.QUALIFY_LEAD,
    }

    def __init__(self, session: Session) -> None:
        self.session = session
        self.work_items = WorkItemService(session)

    def process_completed(
        self,
        workspace: Workspace,
        work_item_id: UUID,
    ) -> WorkItem | None:
        source = self.work_items.get_work_item(workspace, work_item_id)
        if WorkItemStatus(source.status) != WorkItemStatus.COMPLETED:
            return None
        department = self.session.get(Department, source.department_id)
        capability = self.session.get(Capability, source.capability_id)
        if (
            department is None
            or department.workspace_id != workspace.id
            or department.kind != DepartmentKind.SALES
            or capability is None
            or capability.workspace_id != workspace.id
            or capability.department_id != department.id
        ):
            raise SalesAcquisitionCoordinationError(
                "Completed acquisition WorkItem has an invalid Sales scope"
            )
        capability_key = BusinessCapabilityKey(capability.key)
        next_key = self._TRANSITIONS.get(capability_key)
        if next_key is None:
            return None
        existing = self._child(workspace, source)
        if existing is not None:
            self._validate_existing_child(workspace, source, existing, next_key)
            return existing
        next_capability = self._active_capability(
            workspace,
            department,
            next_key,
        )
        child_input = self._child_input(source, capability_key)
        try:
            return self.work_items.create_work_item(
                workspace,
                department,
                work_type=next_key.value,
                title=self._title(next_key),
                capability=next_capability,
                input=child_input,
                parent_work_item_id=source.id,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self._child(workspace, source)
            if existing is None:
                raise
            self._validate_existing_child(workspace, source, existing, next_key)
            return existing

    def find_capture_root(
        self,
        workspace: Workspace,
        department: Department,
        lead_id: UUID,
    ) -> WorkItem | None:
        capability = self._active_capability(
            workspace,
            department,
            BusinessCapabilityKey.CAPTURE_LEAD,
        )
        candidates = list(
            self.session.exec(
                select(WorkItem)
                .where(
                    WorkItem.workspace_id == workspace.id,
                    WorkItem.department_id == department.id,
                    WorkItem.capability_id == capability.id,
                    WorkItem.parent_work_item_id.is_(None),
                    WorkItem.work_type == "lead_capture",
                )
                .order_by(WorkItem.created_at.asc(), WorkItem.id.asc())
            ).all()
        )
        matching = [
            item for item in candidates if str(item.input.get("lead_id")) == str(lead_id)
        ]
        for item in matching:
            if WorkItemStatus(item.status) == WorkItemStatus.COMPLETED:
                return item
        return next(
            (
                item
                for item in reversed(matching)
                if WorkItemStatus(item.status)
                not in {
                    WorkItemStatus.FAILED,
                    WorkItemStatus.CANCELLED,
                    WorkItemStatus.EXPIRED,
                }
            ),
            None,
        )

    def _child(self, workspace: Workspace, source: WorkItem) -> WorkItem | None:
        return self.session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.parent_work_item_id == source.id,
            )
        ).first()

    def _validate_existing_child(
        self,
        workspace: Workspace,
        source: WorkItem,
        child: WorkItem,
        expected_key: BusinessCapabilityKey,
    ) -> None:
        capability = self.session.get(Capability, child.capability_id)
        if (
            child.workspace_id != workspace.id
            or child.department_id != source.department_id
            or child.correlation_id != source.correlation_id
            or capability is None
            or capability.workspace_id != workspace.id
            or capability.department_id != source.department_id
            or BusinessCapabilityKey(capability.key) != expected_key
        ):
            raise SalesAcquisitionCoordinationError(
                "Existing acquisition child conflicts with the expected transition"
            )

    def _active_capability(
        self,
        workspace: Workspace,
        department: Department,
        key: BusinessCapabilityKey,
    ) -> Capability:
        capability = self.session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.department_id == department.id,
                Capability.key == key,
                Capability.active.is_(True),
            )
        ).first()
        if capability is None:
            raise SalesAcquisitionCoordinationError(
                f"{key.value} Capability is not configured for Sales"
            )
        return capability

    @staticmethod
    def _child_input(
        source: WorkItem,
        capability_key: BusinessCapabilityKey,
    ) -> dict[str, Any]:
        result = source.result
        if not isinstance(result, dict):
            raise SalesAcquisitionResultError(
                "Completed acquisition WorkItem requires a structured result"
            )
        lead_id = SalesWorkItemResultCoordinator._result_uuid(result, "lead_id")
        child_input: dict[str, Any] = {"lead_id": str(lead_id)}
        if capability_key == BusinessCapabilityKey.RESEARCH_COMPANY:
            child_input["lead_research_id"] = str(
                SalesWorkItemResultCoordinator._result_uuid(
                    result,
                    "lead_research_id",
                )
            )
        return child_input

    @staticmethod
    def _result_uuid(result: dict[str, Any], field: str) -> UUID:
        try:
            return UUID(str(result[field]))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise SalesAcquisitionResultError(
                f"Completed acquisition result requires a valid {field}"
            ) from exc

    @staticmethod
    def _title(key: BusinessCapabilityKey) -> str:
        return {
            BusinessCapabilityKey.RESEARCH_COMPANY: "Research lead company",
            BusinessCapabilityKey.QUALIFY_LEAD: "Qualify researched lead",
        }[key]


class SalesAcquisitionWorkItemService:
    """Run the persisted capture → research → qualification trace to completion."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        ai_invocation_gateway: AIInvocationGateway | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.gateway = ai_invocation_gateway
        self.work_items = WorkItemService(session)
        self.coordinator = SalesWorkItemResultCoordinator(session)

    async def run(self, workspace: Workspace, lead_id: UUID) -> dict[str, Any]:
        lead = self.session.get(Lead, lead_id)
        if lead is None or lead.tenant_id != workspace.slug:
            raise SalesAcquisitionCoordinationError("Lead not found")
        department = DepartmentService(self.session).ensure_sales_department(workspace)
        SalesWorkforceProvisioningService(self.session).ensure_default_workforce(
            workspace,
            department,
        )
        current = self.coordinator.find_capture_root(workspace, department, lead.id)
        if current is None:
            current = self._create_compatibility_capture(workspace, department, lead)

        for _ in range(3):
            status = WorkItemStatus(current.status)
            if status == WorkItemStatus.CREATED:
                decision = DepartmentSupervisorRoutingService(
                    self.session
                ).route_and_assign(workspace, current.id)
                current = self.work_items.get_work_item(workspace, current.id)
                if not decision.routable or WorkItemStatus(current.status) != WorkItemStatus.ASSIGNED:
                    raise SalesAcquisitionRoutingError(
                        "No eligible Sales acquisition AIEmployee assignment is configured"
                    )
                status = WorkItemStatus(current.status)
            if status == WorkItemStatus.ASSIGNED:
                current = await SalesWorkItemExecutionService(
                    self.session,
                    self.settings,
                    ai_invocation_gateway=self.gateway,
                ).execute(workspace, current.id)
                status = WorkItemStatus(current.status)
            if status != WorkItemStatus.COMPLETED:
                raise SalesAcquisitionCoordinationError(
                    f"Sales acquisition stopped at {status.value}"
                )
            capability = self.session.get(Capability, current.capability_id)
            if capability is None:
                raise SalesAcquisitionCoordinationError(
                    "Sales acquisition WorkItem Capability was not found"
                )
            if BusinessCapabilityKey(capability.key) == BusinessCapabilityKey.QUALIFY_LEAD:
                return self._terminal_state(workspace, lead, current)
            child = self.coordinator.process_completed(workspace, current.id)
            if child is None:
                raise SalesAcquisitionCoordinationError(
                    "Sales acquisition did not produce its required next WorkItem"
                )
            current = child
        raise SalesAcquisitionCoordinationError("Sales acquisition exceeded its bounded stages")

    def _create_compatibility_capture(
        self,
        workspace: Workspace,
        department: Department,
        lead: Lead,
    ) -> WorkItem:
        capability = self.session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.department_id == department.id,
                Capability.key == BusinessCapabilityKey.CAPTURE_LEAD,
                Capability.active.is_(True),
            )
        ).one()
        input_data: dict[str, Any] = {
            "lead_id": str(lead.id),
            "source": lead.source or "workflow_compatibility",
            "customer_created": False,
            "contact_created": False,
            "lead_created": False,
        }
        if lead.contact_id is not None:
            contact = self.session.get(Contact, lead.contact_id)
            if contact is None or contact.workspace_id != workspace.id:
                raise SalesAcquisitionCoordinationError(
                    "Lead Contact does not belong to this workspace"
                )
            input_data["contact_id"] = str(contact.id)
            if contact.customer_id is not None:
                input_data["customer_id"] = str(contact.customer_id)
        return self.work_items.create_work_item(
            workspace,
            department,
            work_type="lead_capture",
            title="Capture lead",
            capability=capability,
            input=input_data,
        )

    def _terminal_state(
        self,
        workspace: Workspace,
        lead: Lead,
        qualification: WorkItem,
    ) -> dict[str, Any]:
        result = qualification.result
        if not isinstance(result, dict) or not isinstance(result.get("qualified"), bool):
            raise SalesAcquisitionResultError(
                "Qualification WorkItem requires a terminal qualified outcome"
            )
        research = self.session.get(WorkItem, qualification.parent_work_item_id)
        if (
            research is None
            or research.workspace_id != workspace.id
            or not isinstance(research.result, dict)
        ):
            raise SalesAcquisitionResultError("Persisted research result was not found")
        qualified = result["qualified"]
        policy = result.get("qualification_policy")
        needs_more_information = (
            isinstance(policy, dict)
            and policy.get("decision")
            == QualificationAuthorityDecisionValue.NEEDS_MORE_INFORMATION.value
        )
        if needs_more_information:
            status = QualificationAuthorityDecisionValue.NEEDS_MORE_INFORMATION.value
            next_action = "collect_more_information"
        else:
            status = "qualified" if qualified else "unqualified"
            next_action = (
                "await_business_event"
                if qualified
                else "collect_more_information_or_archive"
            )
        return {
            "lead_id": lead.id,
            "lead": lead,
            "research": dict(research.result),
            "score": int(result["score"]),
            "qualified": qualified,
            "qualification_reasons": list(result.get("reasons", [])),
            "status": status,
            "draft_message": None,
            "approval_id": None,
            "next_action": next_action,
        }
