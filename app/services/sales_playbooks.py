"""Workspace-scoped persistence boundary for structured Sales Playbook policy."""

from pydantic import ValidationError
from sqlmodel import Session

from app.departments.sales.playbook import SalesPlaybookV1
from app.models import Workspace, utc_now


class WorkspaceSalesPlaybookPersistenceError(RuntimeError):
    """Raised when persisted Playbook JSON violates the supported contract."""


class WorkspaceSalesPlaybookService:
    """Validate and persist one resolved Workspace's complete Sales Playbook."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def read(self, workspace: Workspace) -> SalesPlaybookV1 | None:
        if workspace.sales_playbook is None:
            return None
        try:
            return SalesPlaybookV1.model_validate(workspace.sales_playbook)
        except ValidationError as exc:
            raise WorkspaceSalesPlaybookPersistenceError(
                "Stored Sales Playbook is invalid"
            ) from exc

    def replace(
        self,
        workspace: Workspace,
        playbook: SalesPlaybookV1,
    ) -> SalesPlaybookV1:
        # Round-trip through the contract so service callers cannot persist an
        # unvalidated mutable mapping or a future unsupported schema version.
        validated = SalesPlaybookV1.model_validate(playbook.model_dump(mode="json"))
        workspace.sales_playbook = validated.model_dump(mode="json")
        workspace.updated_at = utc_now()
        self.session.add(workspace)
        self.session.commit()
        self.session.refresh(workspace)
        persisted = self.read(workspace)
        if persisted is None:
            raise WorkspaceSalesPlaybookPersistenceError(
                "Stored Sales Playbook is unavailable"
            )
        return persisted
