"""Workspace-scoped coordination for qualification collection and requalification."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.core.capabilities import BusinessCapabilityKey
from app.core.work_items import WorkItemStatus
from app.departments.sales.qualification_collection import (
    SalesQualificationContext,
    conversation_facts_for_playbook,
    conversation_qualification_facts,
    first_collection_context,
)
from app.models import Capability, ConversationMessage, Department, Lead, WorkItem, Workspace
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.sales_playbooks import WorkspaceSalesPlaybookService
from app.services.work_items import WorkItemService

MAX_QUALIFICATION_LINEAGE_MESSAGES = 20


class QualificationCollectionScopeError(PermissionError):
    """Raised when collection data crosses a workspace or Lead boundary."""


class QualificationCollectionRoutingError(RuntimeError):
    """Raised when the existing qualification workforce cannot accept new evidence."""


class QualificationCollectionService:
    """Expose pending context and reuse governed Qualification WorkItem execution."""

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

    def pending_context(
        self,
        workspace: Workspace,
        lead: Lead,
        customer_message: str,
    ) -> SalesQualificationContext | None:
        source = self._latest_qualification(workspace, lead)
        plan = self._pending_plan(source)
        if plan is None:
            return None
        if conversation_qualification_facts(
            plan,
            customer_message,
            source_reference="pending_customer_message",
        ):
            return None
        return first_collection_context(plan)

    async def process_persisted_message(
        self,
        workspace: Workspace,
        lead: Lead,
        message: ConversationMessage,
    ) -> WorkItem | None:
        """Create and execute one child qualification only for supported new evidence."""

        if lead.tenant_id != workspace.slug or message.lead_id != lead.id:
            raise QualificationCollectionScopeError(
                "Qualification collection evidence does not belong to this workspace Lead"
            )
        if message.direction != "inbound":
            return None
        source = self._latest_qualification(workspace, lead)
        plan = self._pending_plan(source)
        if source is None or plan is None:
            return None
        playbook = WorkspaceSalesPlaybookService(self.session).read(workspace)
        if playbook is None:
            return None
        facts = conversation_facts_for_playbook(
            playbook,
            message.content,
            source_reference=f"conversation_message:{message.id}",
        )
        if not facts:
            return None

        existing = self._child(workspace, source)
        if existing is not None:
            return await self._drive_existing(workspace, existing)
        department = self.session.get(Department, source.department_id)
        capability = self.session.get(Capability, source.capability_id)
        if (
            department is None
            or department.workspace_id != workspace.id
            or capability is None
            or capability.workspace_id != workspace.id
            or capability.department_id != department.id
            or capability.key != BusinessCapabilityKey.QUALIFY_LEAD
            or not capability.active
        ):
            raise QualificationCollectionScopeError(
                "Qualification collection source has an invalid Sales scope"
            )

        child_input = self._child_input(source, lead, message)
        try:
            child = self.work_items.create_work_item(
                workspace,
                department,
                work_type=BusinessCapabilityKey.QUALIFY_LEAD.value,
                title="Requalify lead from new customer evidence",
                capability=capability,
                input=child_input,
                parent_work_item_id=source.id,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self._child(workspace, source)
            if existing is None:
                raise
            return await self._drive_existing(workspace, existing)

        return await self._drive_existing(workspace, child)

    def _latest_qualification(
        self,
        workspace: Workspace,
        lead: Lead,
    ) -> WorkItem | None:
        if lead.tenant_id != workspace.slug:
            raise QualificationCollectionScopeError("Lead not found")
        candidates = self.session.exec(
            select(WorkItem)
            .where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.work_type == BusinessCapabilityKey.QUALIFY_LEAD.value,
                WorkItem.status == WorkItemStatus.COMPLETED,
            )
            .order_by(WorkItem.created_at.desc(), WorkItem.id.desc())
        ).all()
        return next(
            (
                item
                for item in candidates
                if str(item.input.get("lead_id")) == str(lead.id)
            ),
            None,
        )

    @staticmethod
    def _pending_plan(source: WorkItem | None) -> dict[str, object] | None:
        if source is None or not isinstance(source.result, dict):
            return None
        plan = source.result.get("qualification_collection")
        if not isinstance(plan, dict) or plan.get("collection_status") != "pending":
            return None
        if plan.get("qualification_work_item_id") != str(source.id):
            return None
        return plan

    def _child(self, workspace: Workspace, source: WorkItem) -> WorkItem | None:
        return self.session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.parent_work_item_id == source.id,
            )
        ).first()

    async def _drive_existing(
        self,
        workspace: Workspace,
        child: WorkItem,
    ) -> WorkItem:
        status = WorkItemStatus(child.status)
        if status is WorkItemStatus.COMPLETED:
            return child
        if status is WorkItemStatus.CREATED:
            decision = DepartmentSupervisorRoutingService(self.session).route_and_assign(
                workspace,
                child.id,
            )
            child = self.work_items.get_work_item(workspace, child.id)
            if not decision.routable:
                raise QualificationCollectionRoutingError(
                    "No eligible Qualification AIEmployee assignment is configured"
                )
            status = WorkItemStatus(child.status)
        if status is not WorkItemStatus.ASSIGNED:
            raise QualificationCollectionRoutingError(
                "Qualification re-evaluation is not executable"
            )

        # Imported lazily because the existing executor also owns conversation WorkItems.
        from app.departments.sales.services.work_item_execution import (
            SalesWorkItemExecutionService,
        )

        return await SalesWorkItemExecutionService(
            self.session,
            self.settings,
            ai_invocation_gateway=self.gateway,
        ).execute(workspace, child.id)

    @staticmethod
    def _child_input(
        source: WorkItem,
        lead: Lead,
        message: ConversationMessage,
    ) -> dict[str, object]:
        input_data: dict[str, object] = {"lead_id": str(lead.id)}
        if source.input.get("lead_research_id") is not None:
            input_data["lead_research_id"] = source.input["lead_research_id"]
        elif isinstance(source.input.get("research"), dict):
            input_data["research"] = dict(source.input["research"])
        else:
            raise QualificationCollectionScopeError(
                "Qualification collection source has no reusable research input"
            )
        prior = source.input.get("qualification_evidence_message_ids", [])
        if not isinstance(prior, list) or len(prior) >= MAX_QUALIFICATION_LINEAGE_MESSAGES:
            raise QualificationCollectionScopeError(
                "Qualification conversation evidence lineage is invalid"
            )
        identifiers = [str(value) for value in prior]
        if str(message.id) not in identifiers:
            identifiers.append(str(message.id))
        input_data["qualification_evidence_message_ids"] = identifiers
        input_data["qualification_collection_source_work_item_id"] = str(source.id)
        return input_data
