from __future__ import annotations

from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    Workspace,
    utc_now,
)
from app.services.integration_account_audit import IntegrationAccountAuditService
from app.services.secret_reference_policy import IntegrationSecretReferencePolicy


class IntegrationAccountNotFoundError(LookupError):
    """Raised when an account is absent from the requesting workspace."""


class IntegrationAccountService:
    """Workspace-scoped lifecycle operations for inbound integration accounts."""

    def __init__(
        self,
        session: Session,
        secret_reference_policy: IntegrationSecretReferencePolicy | None = None,
    ) -> None:
        self.session = session
        self.secret_reference_policy = (
            secret_reference_policy or IntegrationSecretReferencePolicy()
        )
        self.audit_service = IntegrationAccountAuditService(session)

    def provision(
        self,
        workspace: Workspace,
        provider: str,
        external_account_id: str | None,
        secret_reference: str,
    ) -> tuple[IntegrationAccount, str]:
        validated_secret_reference = self.secret_reference_policy.validate(
            secret_reference
        )
        credential = self._new_credential()
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider=provider.strip(),
            external_account_id=external_account_id.strip()
            if external_account_id is not None
            else None,
            secret_reference=validated_secret_reference,
            credential_hash=self._hash_credential(credential),
        )
        self.session.add(account)
        self.audit_service.record(account, IntegrationAccountAuditAction.PROVISIONED)
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
        account = self.get_for_workspace(workspace, account_id)
        account.active = False
        return self._save(account, IntegrationAccountAuditAction.DEACTIVATED)

    def reactivate(self, workspace: Workspace, account_id: UUID) -> IntegrationAccount:
        account = self.get_for_workspace(workspace, account_id)
        account.active = True
        return self._save(account, IntegrationAccountAuditAction.REACTIVATED)

    def rotate_credential(
        self,
        workspace: Workspace,
        account_id: UUID,
    ) -> tuple[IntegrationAccount, str]:
        account = self.get_for_workspace(workspace, account_id)
        credential = self._new_credential()
        account.credential_hash = self._hash_credential(credential)
        self._save(account, IntegrationAccountAuditAction.CREDENTIAL_ROTATED)
        return account, credential

    def update_secret_reference(
        self,
        workspace: Workspace,
        account_id: UUID,
        secret_reference: str,
    ) -> IntegrationAccount:
        """Update a validated reference without resolving its value.

        Inactive accounts are intentionally eligible: this changes future
        verifier configuration only and does not reactivate the account.
        """
        account = self.get_for_workspace(workspace, account_id)
        account.secret_reference = self.secret_reference_policy.validate(secret_reference)
        return self._save(
            account,
            IntegrationAccountAuditAction.SECRET_REFERENCE_CHANGED,
        )

    def get_for_workspace(
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

    def _save(
        self,
        account: IntegrationAccount,
        audit_action: IntegrationAccountAuditAction,
    ) -> IntegrationAccount:
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(account, audit_action)
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
