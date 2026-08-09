"""Workspace-scoped operator annotations for outbound actions."""

from uuid import UUID

from sqlmodel import Session, select

from app.models import OutboundActionAnnotation, Workspace
from app.services.outbound_action_query import OutboundIntegrationActionQueryService


class OutboundActionAnnotationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, workspace: Workspace, action_id: UUID, text: str) -> OutboundActionAnnotation:
        OutboundIntegrationActionQueryService(self.session).get_for_workspace(workspace, action_id)
        annotation = OutboundActionAnnotation(
            workspace_id=workspace.id, outbound_integration_action_id=action_id, text=text.strip()
        )
        self.session.add(annotation)
        self.session.commit()
        self.session.refresh(annotation)
        return annotation

    def list_for_action(self, workspace: Workspace, action_id: UUID, *, limit: int = 50) -> list[OutboundActionAnnotation]:
        OutboundIntegrationActionQueryService(self.session).get_for_workspace(workspace, action_id)
        return list(self.session.exec(select(OutboundActionAnnotation).where(
            OutboundActionAnnotation.workspace_id == workspace.id,
            OutboundActionAnnotation.outbound_integration_action_id == action_id,
        ).order_by(OutboundActionAnnotation.created_at.asc(), OutboundActionAnnotation.id.asc()).limit(limit)).all())
