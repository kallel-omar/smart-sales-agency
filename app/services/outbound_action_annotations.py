"""Workspace-scoped operator annotations for outbound actions."""

from uuid import UUID

from sqlmodel import Session

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
