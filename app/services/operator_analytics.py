from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.core.capabilities import BusinessCapabilityKey
from app.core.work_items import WorkItemStatus
from app.models import (
    AIEmployee,
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    Capability,
    Department,
    Lead,
    LeadStatus,
    WorkItem,
    Workspace,
    utc_now,
)
from app.schemas import (
    OperatorAnalyticsAIUsageRead,
    OperatorAnalyticsApprovalsRead,
    OperatorAnalyticsCapabilityRead,
    OperatorAnalyticsPeriodRead,
    OperatorAnalyticsRead,
    OperatorAnalyticsSalesOutcomesRead,
    OperatorAnalyticsSalesRead,
    OperatorAnalyticsUsageBreakdownRead,
    OperatorAnalyticsWorkBreakdownRead,
    OperatorAnalyticsWorkforceRead,
    OperatorAnalyticsWorkItemCountsRead,
    OperatorAnalyticsWorkItemsRead,
)

AnalyticsDays = Literal[7, 30, 90]
_BREAKDOWN_LIMIT = 50
_DIMENSION_LIMIT = 100


class OperatorAnalyticsService:
    """Build one workspace-scoped operator analytics read model from persisted facts."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def summarize(
        self,
        workspace: Workspace,
        *,
        days: AnalyticsDays = 30,
        now: datetime | None = None,
    ) -> OperatorAnalyticsRead:
        ends_at = now or utc_now()
        starts_at = ends_at - timedelta(days=days)

        current_status_rows = list(
            self.session.exec(
                select(WorkItem.status, func.count(WorkItem.id))
                .where(WorkItem.workspace_id == workspace.id)
                .group_by(WorkItem.status)
            ).all()
        )
        period_items = list(
            self.session.exec(
                select(WorkItem).where(
                    WorkItem.workspace_id == workspace.id,
                    WorkItem.created_at >= starts_at,
                    WorkItem.created_at <= ends_at,
                )
            ).all()
        )
        employees = list(
            self.session.exec(
                select(AIEmployee)
                .where(AIEmployee.workspace_id == workspace.id)
                .order_by(AIEmployee.created_at, AIEmployee.id)
                .limit(_DIMENSION_LIMIT)
            ).all()
        )
        capabilities = list(
            self.session.exec(
                select(Capability)
                .where(Capability.workspace_id == workspace.id)
                .order_by(Capability.created_at, Capability.id)
                .limit(_DIMENSION_LIMIT)
            ).all()
        )
        departments = {
            department.id: department
            for department in self.session.exec(
                select(Department).where(Department.workspace_id == workspace.id)
            ).all()
        }
        usages = list(
            self.session.exec(
                select(AIInvocationUsage).where(
                    AIInvocationUsage.workspace_id == workspace.id,
                    AIInvocationUsage.created_at >= starts_at,
                    AIInvocationUsage.created_at <= ends_at,
                )
            ).all()
        )
        approvals = list(
            self.session.exec(
                select(ApprovalRequest)
                .outerjoin(WorkItem, ApprovalRequest.work_item_id == WorkItem.id)
                .outerjoin(Lead, ApprovalRequest.lead_id == Lead.id)
                .where(
                    self._approval_scope(workspace),
                    ApprovalRequest.created_at >= starts_at,
                    ApprovalRequest.created_at <= ends_at,
                )
            ).all()
        )
        lead_status_rows = list(
            self.session.exec(
                select(Lead.status, func.count(Lead.id))
                .where(Lead.tenant_id == workspace.slug)
                .group_by(Lead.status)
            ).all()
        )
        period_lead_count = self.session.exec(
            select(func.count(Lead.id)).where(
                Lead.tenant_id == workspace.slug,
                Lead.created_at >= starts_at,
                Lead.created_at <= ends_at,
            )
        ).one()

        capability_by_id = {capability.id: capability for capability in capabilities}
        return OperatorAnalyticsRead(
            period=OperatorAnalyticsPeriodRead(days=days, starts_at=starts_at, ends_at=ends_at),
            workitems=self._workitem_metrics(current_status_rows, period_items),
            workforce=self._workforce_metrics(employees, departments, period_items, usages),
            capabilities=self._capability_metrics(capabilities, period_items, usages),
            approvals=self._approval_metrics(period_items, approvals),
            ai_usage=self._usage_metrics(usages),
            sales=self._sales_metrics(
                lead_status_rows, period_lead_count, period_items, capability_by_id
            ),
        )

    def _workitem_metrics(
        self,
        current_status_rows: list[tuple[str, int]],
        period_items: list[WorkItem],
    ) -> OperatorAnalyticsWorkItemsRead:
        current = {status.value: 0 for status in WorkItemStatus}
        for status, count in current_status_rows:
            current[str(status)] = count

        completed = sum(item.status == WorkItemStatus.COMPLETED for item in period_items)
        failed = sum(item.status == WorkItemStatus.FAILED for item in period_items)
        durations = [
            (item.completed_at - item.started_at).total_seconds()
            for item in period_items
            if item.status == WorkItemStatus.COMPLETED
            and item.started_at is not None
            and item.completed_at is not None
            and item.completed_at >= item.started_at
        ]
        breakdowns: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for item in period_items:
            bucket = breakdowns[item.work_type]
            bucket[0] += 1
            bucket[1] += item.status == WorkItemStatus.COMPLETED
            bucket[2] += item.status == WorkItemStatus.FAILED

        return OperatorAnalyticsWorkItemsRead(
            current=OperatorAnalyticsWorkItemCountsRead(**current),
            created=len(period_items),
            completed=completed,
            failed=failed,
            success_rate=_rate(completed, failed),
            average_completion_seconds=(
                round(sum(durations) / len(durations), 2) if durations else None
            ),
            by_work_type=self._work_breakdowns(breakdowns),
        )

    def _workforce_metrics(
        self,
        employees: list[AIEmployee],
        departments: dict[UUID, Department],
        period_items: list[WorkItem],
        usages: list[AIInvocationUsage],
    ) -> list[OperatorAnalyticsWorkforceRead]:
        work = _work_totals(period_items, "ai_employee_id")
        usage = _usage_totals(usages, "ai_employee_id")
        result: list[OperatorAnalyticsWorkforceRead] = []
        for employee in employees:
            department = departments.get(employee.department_id)
            if department is None:
                continue
            work_total = work[employee.id]
            usage_total = usage[employee.id]
            result.append(
                OperatorAnalyticsWorkforceRead(
                    employee_id=employee.id,
                    name=employee.name,
                    role=employee.role_key,
                    department=department.kind,
                    workitems=work_total[0],
                    completed=work_total[1],
                    failed=work_total[2],
                    success_rate=_rate(work_total[1], work_total[2]),
                    **_usage_fields(usage_total),
                )
            )
        return result

    def _capability_metrics(
        self,
        capabilities: list[Capability],
        period_items: list[WorkItem],
        usages: list[AIInvocationUsage],
    ) -> list[OperatorAnalyticsCapabilityRead]:
        work = _work_totals(period_items, "capability_id")
        usage = _usage_totals(usages, "capability_id")
        result: list[OperatorAnalyticsCapabilityRead] = []
        for capability in capabilities:
            work_total = work[capability.id]
            usage_total = usage[capability.id]
            result.append(
                OperatorAnalyticsCapabilityRead(
                    capability_id=capability.id,
                    key=capability.key,
                    workitems=work_total[0],
                    completed=work_total[1],
                    failed=work_total[2],
                    success_rate=_rate(work_total[1], work_total[2]),
                    invocation_count=usage_total[0],
                    total_tokens=usage_total[3],
                    known_estimated_cost=usage_total[4],
                    unknown_pricing_invocation_count=usage_total[5],
                )
            )
        return result

    @staticmethod
    def _approval_metrics(
        period_items: list[WorkItem], approvals: list[ApprovalRequest]
    ) -> OperatorAnalyticsApprovalsRead:
        period_item_ids = {item.id for item in period_items}
        requested_item_ids = {
            approval.work_item_id
            for approval in approvals
            if approval.work_item_id in period_item_ids
        }
        return OperatorAnalyticsApprovalsRead(
            requests_created=len(approvals),
            pending=sum(row.status == ApprovalStatus.PENDING for row in approvals),
            approved=sum(row.status == ApprovalStatus.APPROVED for row in approvals),
            rejected=sum(row.status == ApprovalStatus.REJECTED for row in approvals),
            workitems_with_approval_request=len(requested_item_ids),
            approval_request_rate=(
                len(requested_item_ids) / len(period_items) if period_items else None
            ),
        )

    def _usage_metrics(self, usages: list[AIInvocationUsage]) -> OperatorAnalyticsAIUsageRead:
        total = _usage_bucket(usages)
        providers = _group_usage(usages, "provider")
        models = _group_usage(usages, "model")
        return OperatorAnalyticsAIUsageRead(
            **_usage_fields(total),
            by_provider=_usage_breakdowns(providers),
            by_model=_usage_breakdowns(models),
        )

    @staticmethod
    def _sales_metrics(
        lead_status_rows: list[tuple[str, int]],
        period_lead_count: int,
        period_items: list[WorkItem],
        capability_by_id: dict[UUID, Capability],
    ) -> OperatorAnalyticsSalesRead:
        by_status = {status.value: 0 for status in LeadStatus}
        for status, count in lead_status_rows:
            by_status[str(status)] = count
        outcomes = {
            BusinessCapabilityKey.CAPTURE_LEAD: 0,
            BusinessCapabilityKey.QUALIFY_LEAD: 0,
            BusinessCapabilityKey.FOLLOW_UP_LEAD: 0,
        }
        for item in period_items:
            if item.status != WorkItemStatus.COMPLETED:
                continue
            capability = capability_by_id.get(item.capability_id)
            if capability is not None and capability.key in outcomes:
                outcomes[capability.key] += 1
        return OperatorAnalyticsSalesRead(
            total_leads=sum(by_status.values()),
            leads_created=period_lead_count,
            won_leads=by_status[LeadStatus.WON.value],
            by_status=by_status,
            outcomes=OperatorAnalyticsSalesOutcomesRead(
                capture_lead_completed=outcomes[BusinessCapabilityKey.CAPTURE_LEAD],
                qualification_completed=outcomes[BusinessCapabilityKey.QUALIFY_LEAD],
                follow_up_completed=outcomes[BusinessCapabilityKey.FOLLOW_UP_LEAD],
            ),
        )

    @staticmethod
    def _work_breakdowns(
        values: dict[str, list[int]],
    ) -> list[OperatorAnalyticsWorkBreakdownRead]:
        ordered = sorted(values.items(), key=lambda row: (-row[1][0], row[0]))
        return [
            OperatorAnalyticsWorkBreakdownRead(
                key=key,
                total=counts[0],
                completed=counts[1],
                failed=counts[2],
                success_rate=_rate(counts[1], counts[2]),
            )
            for key, counts in ordered[:_BREAKDOWN_LIMIT]
        ]

    @staticmethod
    def _approval_scope(workspace: Workspace):
        return or_(
            WorkItem.workspace_id == workspace.id,
            and_(ApprovalRequest.work_item_id.is_(None), Lead.tenant_id == workspace.slug),
        )


def _rate(completed: int, failed: int) -> float | None:
    denominator = completed + failed
    return completed / denominator if denominator else None


def _work_totals(items: list[WorkItem], attribute: str) -> defaultdict[UUID | None, list[int]]:
    result: defaultdict[UUID | None, list[int]] = defaultdict(lambda: [0, 0, 0])
    for item in items:
        bucket = result[getattr(item, attribute)]
        bucket[0] += 1
        bucket[1] += item.status == WorkItemStatus.COMPLETED
        bucket[2] += item.status == WorkItemStatus.FAILED
    return result


def _usage_bucket(usages: list[AIInvocationUsage]) -> list[Any]:
    known_costs = [row.estimated_cost for row in usages if row.estimated_cost is not None]
    return [
        len(usages),
        sum(row.input_tokens or 0 for row in usages),
        sum(row.output_tokens or 0 for row in usages),
        sum(row.total_tokens or 0 for row in usages),
        sum(known_costs, Decimal(0)),
        sum(row.estimated_cost is None for row in usages),
    ]


def _usage_totals(
    usages: list[AIInvocationUsage], attribute: str
) -> defaultdict[UUID | None, list[Any]]:
    grouped: defaultdict[UUID | None, list[AIInvocationUsage]] = defaultdict(list)
    for usage in usages:
        grouped[getattr(usage, attribute)].append(usage)
    return defaultdict(
        lambda: [0, 0, 0, 0, Decimal(0), 0],
        {key: _usage_bucket(rows) for key, rows in grouped.items()},
    )


def _group_usage(usages: list[AIInvocationUsage], attribute: str) -> dict[str, list[Any]]:
    grouped: defaultdict[str, list[AIInvocationUsage]] = defaultdict(list)
    for usage in usages:
        grouped[str(getattr(usage, attribute))].append(usage)
    return {key: _usage_bucket(rows) for key, rows in grouped.items()}


def _usage_fields(values: list[Any]) -> dict[str, Any]:
    return {
        "invocation_count": values[0],
        "input_tokens": values[1],
        "output_tokens": values[2],
        "total_tokens": values[3],
        "known_estimated_cost": values[4],
        "unknown_pricing_invocation_count": values[5],
    }


def _usage_breakdowns(
    values: dict[str, list[Any]],
) -> list[OperatorAnalyticsUsageBreakdownRead]:
    ordered = sorted(values.items(), key=lambda row: (-row[1][0], row[0]))
    return [
        OperatorAnalyticsUsageBreakdownRead(key=key, **_usage_fields(counts))
        for key, counts in ordered[:_BREAKDOWN_LIMIT]
    ]
