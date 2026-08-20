from enum import StrEnum
from typing import ClassVar


class WorkItemStatus(StrEnum):
    """Generic lifecycle states for HIRI's universal unit of work."""

    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING = "waiting"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_WORK_ITEM_STATUSES = frozenset(
    {
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
        WorkItemStatus.CANCELLED,
        WorkItemStatus.EXPIRED,
    }
)


class WorkItemInvalidStateTransitionError(ValueError):
    """Raised when a WorkItem lifecycle transition is not allowed."""


class WorkItemStateTransitionGuard:
    """Centralize legal WorkItem lifecycle transitions."""

    _ALLOWED_TARGETS: ClassVar[dict[WorkItemStatus, frozenset[WorkItemStatus]]] = {
        WorkItemStatus.CREATED: frozenset(
            {
                WorkItemStatus.ASSIGNED,
                WorkItemStatus.CANCELLED,
                WorkItemStatus.EXPIRED,
            }
        ),
        WorkItemStatus.ASSIGNED: frozenset(
            {
                WorkItemStatus.RUNNING,
                WorkItemStatus.CANCELLED,
                WorkItemStatus.EXPIRED,
            }
        ),
        WorkItemStatus.RUNNING: frozenset(
            {
                WorkItemStatus.WAITING,
                WorkItemStatus.APPROVAL_REQUIRED,
                WorkItemStatus.COMPLETED,
                WorkItemStatus.FAILED,
                WorkItemStatus.CANCELLED,
                WorkItemStatus.EXPIRED,
            }
        ),
        WorkItemStatus.WAITING: frozenset(
            {
                WorkItemStatus.RUNNING,
                WorkItemStatus.CANCELLED,
                WorkItemStatus.EXPIRED,
            }
        ),
        WorkItemStatus.APPROVAL_REQUIRED: frozenset(
            {
                WorkItemStatus.RUNNING,
                WorkItemStatus.COMPLETED,
                WorkItemStatus.FAILED,
                WorkItemStatus.CANCELLED,
                WorkItemStatus.EXPIRED,
            }
        ),
        WorkItemStatus.COMPLETED: frozenset(),
        WorkItemStatus.FAILED: frozenset(),
        WorkItemStatus.CANCELLED: frozenset(),
        WorkItemStatus.EXPIRED: frozenset(),
    }

    def can_transition(self, source: WorkItemStatus, target: WorkItemStatus) -> bool:
        return target in self._ALLOWED_TARGETS[source]

    def is_terminal(self, status: WorkItemStatus) -> bool:
        return status in TERMINAL_WORK_ITEM_STATUSES

    def require_transition(
        self,
        source: WorkItemStatus,
        target: WorkItemStatus,
    ) -> None:
        if self.can_transition(source, target):
            return
        raise WorkItemInvalidStateTransitionError(
            f"WorkItem cannot transition from {source} to {target}"
        )
