from __future__ import annotations

from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlmodel import Session, select

from app.models import IntegrationAccount, Workspace, utc_now


class IntegrationAccountNotFoundError(LookupError):
    """Raised when an account is absent from the requesting workspace."""


class IntegrationAccountService:
    """Workspace-scoped lifecycle operations for inbound integration accounts."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def provision(
        self,
        workspace: Workspace,
        provider: str,
        external_account_id: str | None,
        secret_reference: str,
    ) -> tuple[IntegrationAccount, str]:
        credential = self._new_credential()
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider=provider.strip(),
            external_account_id=external_account_id.strip()
            if external_account_id is not None
            else None,
            secret_reference=secret_reference.strip(),
            credential_hash=self._hash_credential(credential),
        )
        self.session.add(account)
        self.session.commit()
        self.session.refresh(account)
        return account, credential

    def list_for_workspace(self, workspace: Workspace) -> list[IntegrationAccount]:
        statement = (
            select(IntegrationAccount)
            .where(IntegrationAccount.workspace_id == workspace.id)
            .order_by(IntegrationAccount.created_at.desc())
        )
        return list(self.session.exec(statement).all())

    def deactivate(self, workspace: Workspace, account_id: UUID) -> IntegrationAccount:
        account = self._get_for_workspace(workspace, account_id)
        account.active = False
        return self._save(account)

    def reactivate(self, workspace: Workspace, account_id: UUID) -> IntegrationAccount:
        account = self._get_for_workspace(workspace, account_id)
        account.active = True
        return self._save(account)

    def rotate_credential(
        self,
        workspace: Workspace,
        account_id: UUID,
    ) -> tuple[IntegrationAccount, str]:
        account = self._get_for_workspace(workspace, account_id)
        credential = self._new_credential()
        account.credential_hash = self._hash_credential(credential)
        self._save(account)
        return account, credential

    def _get_for_workspace(
        self,
        workspace: Workspace,
        account_id: UUID,
    ) -> IntegrationAccount:
        account = self.session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.id == account_id,
                IntegrationAccount.workspace_id == workspace.id,
            )
        ).first()
        if not account:
            raise IntegrationAccountNotFoundError("Integration account not found")
        return account

    def _save(self, account: IntegrationAccount) -> IntegrationAccount:
        account.updated_at = utc_now()
        self.session.add(account)
        self.session.commit()
        self.session.refresh(account)
        return account

    def _new_credential(self) -> str:
        """Generate a high-entropy credential whose hash is not already in use."""
        while True:
            credential = token_urlsafe(32)
            credential_hash = self._hash_credential(credential)
            existing = self.session.exec(
                select(IntegrationAccount.id).where(
                    IntegrationAccount.credential_hash == credential_hash
                )
            ).first()
            if not existing:
                return credential

    @staticmethod
    def _hash_credential(credential: str) -> str:
        return sha256(credential.encode()).hexdigest()
