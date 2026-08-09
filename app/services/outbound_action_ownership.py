"""Safe opaque ownership references for future authenticated operators."""

import re
from uuid import UUID

from sqlmodel import Session

from app.models import OutboundIntegrationAction, Workspace
from app.services.outbound_action_query import OutboundIntegrationActionQueryService

OWNER_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


class OutboundActionOwnerReferenceValidationError(ValueError):
    """Raised when an opaque operator reference has an unsafe format."""


class OutboundActionOwnershipService:
    """Persist opaque references only; authenticated identity validation comes later."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.action_query = OutboundIntegrationActionQueryService(session)

    def set_owner_reference(
        self,
        workspace: Workspace,
        action_id: UUID,
        owner_reference: str | None,
    ) -> OutboundIntegrationAction:
        action, _ = self.action_query.get_for_workspace(workspace, action_id)
        action.owner_reference = self.normalize(owner_reference)
        self.session.add(action)
        self.session.commit()
        self.session.refresh(action)
        return action

    @staticmethod
    def normalize(owner_reference: str | None) -> str | None:
        if owner_reference is None:
            return None
        normalized = owner_reference.strip()
        if not OWNER_REFERENCE_PATTERN.fullmatch(normalized):
            raise OutboundActionOwnerReferenceValidationError(
                "Owner reference must use letters, numbers, periods, colons, hyphens, or underscores"
            )
        return normalized
