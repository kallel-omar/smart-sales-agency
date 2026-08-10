import re
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    SalesLanguage,
    SalesTone,
    SalesWritingScript,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
    utc_now,
)

if TYPE_CHECKING:
    from app.services.identity_memberships import AuthenticatedPrincipal

MAX_WORKSPACE_SALES_INSTRUCTIONS_LENGTH = 4_000


class WorkspaceSalesInstructionsValidationError(ValueError):
    """Raised when trusted workspace Sales configuration is unsafe to store."""


_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:api[_ -]?key|password|secret|access[_ -]?token|authorization)\s*[:=]"
)


class WorkspaceNotFoundError(LookupError):
    pass


class WorkspaceInactiveError(ValueError):
    pass


class WorkspaceCreationPrincipalError(PermissionError):
    """Raised when a stale authenticated identity cannot own a new workspace."""


class DuplicateWorkspaceSlugError(ValueError):
    """Raised when an attempted workspace slug already exists."""


class InvalidIntegrationContextError(PermissionError):
    """Raised when an external integration cannot be mapped safely."""


@dataclass(frozen=True)
class IntegrationContext:
    """The active account and workspace resolved from an inbound credential."""

    account: IntegrationAccount
    workspace: Workspace


class WorkspaceCreationService:
    """Create a new workspace and its server-assigned owner as one unit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_for_principal(
        self,
        *,
        slug: str,
        name: str,
        principal: "AuthenticatedPrincipal",
    ) -> Workspace:
        user = self.session.get(User, principal.user_id)
        if user is None or not user.active or not principal.active:
            raise WorkspaceCreationPrincipalError("Authenticated user is unavailable")

        if self.session.exec(select(Workspace.id).where(Workspace.slug == slug)).first():
            raise DuplicateWorkspaceSlugError("A workspace with this slug already exists")

        workspace = Workspace(slug=slug, name=name)
        self.session.add(workspace)
        try:
            # Flush allocates the server-owned workspace ID before its owner
            # relationship is constructed. A later failure rolls both back.
            self.session.flush()
            self.session.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=principal.user_id,
                    role=WorkspaceMemberRole.OWNER,
                )
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateWorkspaceSlugError("A workspace with this slug already exists") from exc

        self.session.refresh(workspace)
        return workspace


def normalize_workspace_sales_instructions(value: str) -> str | None:
    """Normalize plain-text administrator instructions deterministically.

    The configuration is intentionally not a secret store or an HTML surface.
    It accepts Unicode plain text only; a blank replacement clears the optional
    field. This deterministic validation performs no model or network work.
    """

    if not isinstance(value, str):
        raise WorkspaceSalesInstructionsValidationError(
            "Sales instructions must be text"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceSalesInstructionsValidationError(
            "Sales instructions must be valid Unicode text"
        ) from exc

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(
        ord(character) < 32 and character not in "\n\t"
        for character in normalized
    ):
        raise WorkspaceSalesInstructionsValidationError(
            "Sales instructions contain unsupported control characters"
        )

    if not normalized:
        return None
    if len(normalized) > MAX_WORKSPACE_SALES_INSTRUCTIONS_LENGTH:
        raise WorkspaceSalesInstructionsValidationError(
            "Sales instructions exceed the maximum length"
        )
    if _CREDENTIAL_ASSIGNMENT.search(normalized):
        raise WorkspaceSalesInstructionsValidationError(
            "Sales instructions cannot contain credential assignments"
        )
    return normalized


class WorkspaceSalesInstructionsService:
    """Read and mutate Sales instructions for one already-resolved workspace."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def read(self, workspace: Workspace) -> str | None:
        return workspace.sales_instructions

    def replace(self, workspace: Workspace, instructions: str) -> Workspace:
        workspace.sales_instructions = normalize_workspace_sales_instructions(instructions)
        return self._save(workspace)

    def clear(self, workspace: Workspace) -> Workspace:
        workspace.sales_instructions = None
        return self._save(workspace)

    def _save(self, workspace: Workspace) -> Workspace:
        workspace.updated_at = utc_now()
        self.session.add(workspace)
        self.session.commit()
        self.session.refresh(workspace)
        return workspace


class WorkspaceSalesCommunicationService:
    """Mutate typed Sales language/script/tone defaults for one resolved workspace."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def update(
        self,
        workspace: Workspace,
        *,
        preferred_language: SalesLanguage | None,
        preferred_script: SalesWritingScript | None,
        preferred_tone: SalesTone | None,
    ) -> Workspace:
        workspace.sales_preferred_language = preferred_language
        workspace.sales_preferred_script = preferred_script
        workspace.sales_preferred_tone = preferred_tone
        workspace.updated_at = utc_now()
        self.session.add(workspace)
        self.session.commit()
        self.session.refresh(workspace)
        return workspace


def require_active_workspace(
    session: Session,
    slug: str,
) -> Workspace:
    workspace = get_workspace_by_slug(session, slug)
    if not workspace.active:
        raise WorkspaceInactiveError(
            f"Workspace '{workspace.slug}' is inactive"
        )

    return workspace


def get_workspace_by_slug(
    session: Session,
    slug: str,
) -> Workspace:
    """Resolve a workspace without applying an operation-specific active gate."""
    normalized_slug = slug.strip().lower()

    workspace = session.exec(
        select(Workspace).where(
            Workspace.slug == normalized_slug
        )
    ).first()

    if not workspace:
        raise WorkspaceNotFoundError(
            f"Workspace '{normalized_slug}' was not found"
        )
    return workspace


def resolve_integration_account(
    session: Session,
    integration_key: str,
) -> IntegrationAccount:
    """Resolve an inbound credential to its active persisted account."""
    credential_hash = sha256(integration_key.encode()).hexdigest()
    account = session.exec(
        select(IntegrationAccount).where(
            IntegrationAccount.credential_hash == credential_hash,
            IntegrationAccount.active.is_(True),
        )
    ).first()
    if not account:
        raise InvalidIntegrationContextError("Integration context is not recognized")
    return account


def resolve_integration_workspace_for_account(
    session: Session,
    account: IntegrationAccount,
) -> Workspace:
    """Resolve the active workspace only after account authentication succeeds."""
    workspace = session.get(Workspace, account.workspace_id)
    if not workspace or not workspace.active:
        raise InvalidIntegrationContextError(
            "Integration context is not recognized"
        )
    return workspace


def resolve_integration_context(
    session: Session,
    integration_key: str,
) -> IntegrationContext:
    """Resolve an inbound credential to its active account and workspace."""
    account = resolve_integration_account(session, integration_key)
    workspace = resolve_integration_workspace_for_account(session, account)
    return IntegrationContext(account=account, workspace=workspace)


def resolve_integration_workspace(
    session: Session,
    integration_key: str,
) -> Workspace:
    """Compatibility helper for callers that only require the workspace."""
    return resolve_integration_context(session, integration_key).workspace
