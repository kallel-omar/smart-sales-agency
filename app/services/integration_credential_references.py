from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    IntegrationCredentialReference,
    Workspace,
    utc_now,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.secret_reference_policy import IntegrationSecretReferencePolicy


class IntegrationCredentialReferenceNotFoundError(LookupError):
    """Raised when a credential reference is absent from the scoped account."""


class IntegrationCredentialPurposeValidationError(ValueError):
    """Raised when a credential-reference purpose is invalid."""


class IntegrationCredentialReferenceService:
    """Workspace-scoped external credential references for integration accounts."""

    def __init__(
        self,
        session: Session,
        secret_reference_policy: IntegrationSecretReferencePolicy | None = None,
    ) -> None:
        self.session = session
        self.secret_reference_policy = (
            secret_reference_policy or IntegrationSecretReferencePolicy()
        )
        self.account_service = IntegrationAccountService(
            session,
            self.secret_reference_policy,
        )

    def set_reference(
        self,
        workspace: Workspace,
        account_id: UUID,
        purpose: str,
        secret_reference: str,
    ) -> IntegrationCredentialReference:
        """Create or update one secret reference without resolving its value."""
        self.account_service.get_for_workspace(workspace, account_id)

        normalized_purpose = self._validate_purpose(purpose)
        validated_secret_reference = self.secret_reference_policy.validate(
            secret_reference
        )

        reference = self.session.exec(
            select(IntegrationCredentialReference).where(
                IntegrationCredentialReference.workspace_id == workspace.id,
                IntegrationCredentialReference.integration_account_id == account_id,
                IntegrationCredentialReference.purpose == normalized_purpose,
            )
        ).first()

        if reference is None:
            reference = IntegrationCredentialReference(
                workspace_id=workspace.id,
                integration_account_id=account_id,
                purpose=normalized_purpose,
                secret_reference=validated_secret_reference,
            )
        else:
            reference.secret_reference = validated_secret_reference
            reference.updated_at = utc_now()

        self.session.add(reference)
        self.session.commit()
        self.session.refresh(reference)
        return reference

    def list_for_account(
        self,
        workspace: Workspace,
        account_id: UUID,
    ) -> list[IntegrationCredentialReference]:
        """List safe credential-reference records for one scoped account."""
        self.account_service.get_for_workspace(workspace, account_id)

        statement = (
            select(IntegrationCredentialReference)
            .where(
                IntegrationCredentialReference.workspace_id == workspace.id,
                IntegrationCredentialReference.integration_account_id == account_id,
            )
            .order_by(
                IntegrationCredentialReference.purpose,
                IntegrationCredentialReference.created_at,
            )
        )
        return list(self.session.exec(statement).all())

    def get_for_account(
        self,
        workspace: Workspace,
        account_id: UUID,
        purpose: str,
    ) -> IntegrationCredentialReference:
        """Return one credential reference from the requesting workspace only."""
        self.account_service.get_for_workspace(workspace, account_id)
        normalized_purpose = self._validate_purpose(purpose)

        reference = self.session.exec(
            select(IntegrationCredentialReference).where(
                IntegrationCredentialReference.workspace_id == workspace.id,
                IntegrationCredentialReference.integration_account_id == account_id,
                IntegrationCredentialReference.purpose == normalized_purpose,
            )
        ).first()

        if reference is None:
            raise IntegrationCredentialReferenceNotFoundError(
                "Integration credential reference not found"
            )

        return reference
    def get_for_integration_account(
        self,
        account: IntegrationAccount,
        purpose: str,
    ) -> IntegrationCredentialReference:
        """Return a credential reference for an account already scoped by HIRI."""
        normalized_purpose = self._validate_purpose(purpose)

        reference = self.session.exec(
            select(IntegrationCredentialReference).where(
                IntegrationCredentialReference.workspace_id == account.workspace_id,
                IntegrationCredentialReference.integration_account_id == account.id,
                IntegrationCredentialReference.purpose == normalized_purpose,
            )
        ).first()

        if reference is None:
            raise IntegrationCredentialReferenceNotFoundError(
                "Integration credential reference not found"
            )

        return reference
    @staticmethod
    def _validate_purpose(purpose: str) -> str:
        normalized = purpose.strip().lower()

        if not normalized:
            raise IntegrationCredentialPurposeValidationError(
                "Credential purpose is required"
            )

        if len(normalized) > 100:
            raise IntegrationCredentialPurposeValidationError(
                "Credential purpose is too long"
            )

        if not normalized[0].isalpha() or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in normalized
        ):
            raise IntegrationCredentialPurposeValidationError(
                "Credential purpose must use lowercase letters, numbers, and underscores"
            )

        return normalized