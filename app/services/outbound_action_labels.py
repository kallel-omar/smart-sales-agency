"""Safe, workspace-scoped labels for outbound delivery intents."""

import re
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import OutboundActionLabel, Workspace
from app.services.outbound_action_query import OutboundIntegrationActionQueryService

MAX_OUTBOUND_ACTION_LABELS = 10
LABEL_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


class OutboundActionLabelValidationError(ValueError):
    """Raised when a label cannot be safely persisted."""


class OutboundActionLabelNotFoundError(LookupError):
    """Raised when a label is not assigned to the scoped outbound action."""


class OutboundActionLabelService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.action_query = OutboundIntegrationActionQueryService(session)

    def add(self, workspace: Workspace, action_id: UUID, raw_label: str) -> OutboundActionLabel:
        self.action_query.get_for_workspace(workspace, action_id)
        label = self.normalize(raw_label)
        existing = self._get(workspace, action_id, label)
        if existing:
            return existing
        label_count = self.session.exec(
            select(func.count())
            .select_from(OutboundActionLabel)
            .where(
                OutboundActionLabel.workspace_id == workspace.id,
                OutboundActionLabel.outbound_integration_action_id == action_id,
            )
        ).one()
        if label_count >= MAX_OUTBOUND_ACTION_LABELS:
            raise OutboundActionLabelValidationError(
                f"An outbound action can have at most {MAX_OUTBOUND_ACTION_LABELS} labels"
            )
        action_label = OutboundActionLabel(
            workspace_id=workspace.id,
            outbound_integration_action_id=action_id,
            label=label,
        )
        self.session.add(action_label)
        self.session.commit()
        self.session.refresh(action_label)
        return action_label

    def remove(self, workspace: Workspace, action_id: UUID, raw_label: str) -> None:
        self.action_query.get_for_workspace(workspace, action_id)
        label = self.normalize(raw_label)
        action_label = self._get(workspace, action_id, label)
        if not action_label:
            raise OutboundActionLabelNotFoundError("Outbound action label not found")
        self.session.delete(action_label)
        self.session.commit()

    def list_for_action(
        self, workspace: Workspace, action_id: UUID, *, limit: int = MAX_OUTBOUND_ACTION_LABELS
    ) -> list[OutboundActionLabel]:
        self.action_query.get_for_workspace(workspace, action_id)
        return list(
            self.session.exec(
                select(OutboundActionLabel)
                .where(
                    OutboundActionLabel.workspace_id == workspace.id,
                    OutboundActionLabel.outbound_integration_action_id == action_id,
                )
                .order_by(OutboundActionLabel.label.asc(), OutboundActionLabel.id.asc())
                .limit(limit)
            ).all()
        )

    @staticmethod
    def normalize(raw_label: str) -> str:
        label = raw_label.strip().lower()
        if not LABEL_PATTERN.fullmatch(label):
            raise OutboundActionLabelValidationError(
                "Label must use lowercase letters, numbers, hyphens, or underscores"
            )
        return label

    def _get(
        self, workspace: Workspace, action_id: UUID, label: str
    ) -> OutboundActionLabel | None:
        return self.session.exec(
            select(OutboundActionLabel).where(
                OutboundActionLabel.workspace_id == workspace.id,
                OutboundActionLabel.outbound_integration_action_id == action_id,
                OutboundActionLabel.label == label,
            )
        ).first()
