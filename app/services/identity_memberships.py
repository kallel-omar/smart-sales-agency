"""Canonical platform identity and workspace membership foundation.

This module deliberately does not authenticate HTTP requests.  Task 280 can
resolve an ``AuthenticatedPrincipal`` from real credentials and pass it to the
membership operations introduced here.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import User, Workspace, WorkspaceMember, WorkspaceMemberRole, utc_now
from app.services.workspaces import WorkspaceNotFoundError


class UserIdentityValidationError(ValueError):
    """Raised when deterministic user identity data is invalid."""


class DuplicateUserEmailError(ValueError):
    """Raised when a canonical email already belongs to a persisted user."""


class UserNotFoundError(LookupError):
    """Raised when a persisted platform user is absent."""


class InactiveUserError(PermissionError):
    """Raised when historical inactive identity is requested as an active principal."""


class DuplicateWorkspaceMembershipError(ValueError):
    """Raised when a user is already canonically linked to the workspace."""


class WorkspaceMembershipNotFoundError(LookupError):
    """Safe not-found result for an absent membership in the requested workspace."""


class InactiveWorkspaceMembershipError(PermissionError):
    """Raised when a historical membership is not eligible for active resolution."""


class WorkspaceMemberRoleValidationError(ValueError):
    """Raised when membership metadata uses a role outside the small enum."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Provider-neutral authenticated identity seam for the future auth layer.

    Constructing this type does not authenticate anybody.  It only represents a
    user already authenticated by a future trusted mechanism.
    """

    user_id: UUID
    active: bool


def normalize_user_email(value: str) -> str:
    """Return the single canonical persisted representation for an email address."""

    if not isinstance(value, str):
        raise UserIdentityValidationError("User email must be text")
    normalized = value.strip().lower()
    local, separator, domain = normalized.partition("@")
    if (
        not normalized
        or len(normalized) > 320
        or not separator
        or not local
        or not domain
        or "@" in domain
        or any(character.isspace() for character in normalized)
    ):
        raise UserIdentityValidationError("User email is invalid")
    return normalized


def normalize_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise UserIdentityValidationError("User display name must be text")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 200:
        raise UserIdentityValidationError("User display name exceeds the maximum length")
    return normalized


class IdentityMembershipService:
    """Persistence-backed identity and membership domain operations.

    Callers provide a resolved ``Workspace`` rather than any request body
    ownership field.  This keeps workspace authority outside identity payloads
    until real authentication and RBAC are introduced.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def register_user(self, *, email: str, display_name: str | None = None) -> User:
        canonical_email = normalize_user_email(email)
        if self.session.exec(select(User.id).where(User.email == canonical_email)).first():
            raise DuplicateUserEmailError("A user with this email already exists")

        user = User(
            email=canonical_email,
            display_name=normalize_display_name(display_name),
        )
        self.session.add(user)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateUserEmailError("A user with this email already exists") from exc
        self.session.refresh(user)
        return user

    def get_user(self, user_id: UUID) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise UserNotFoundError("User not found")
        return user

    def get_user_by_email(self, email: str) -> User:
        canonical_email = normalize_user_email(email)
        user = self.session.exec(select(User).where(User.email == canonical_email)).first()
        if user is None:
            raise UserNotFoundError("User not found")
        return user

    def resolve_active_principal(self, user_id: UUID) -> AuthenticatedPrincipal:
        user = self.get_user(user_id)
        if not user.active:
            raise InactiveUserError("User is inactive")
        return AuthenticatedPrincipal(user_id=user.id, active=True)

    def add_membership(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        role: WorkspaceMemberRole,
    ) -> WorkspaceMember:
        stored_workspace = self._get_workspace(workspace.id)
        user = self.get_user(user_id)
        if not user.active:
            raise InactiveUserError("User is inactive")
        canonical_role = self._validate_role(role)
        if self._find_membership(stored_workspace.id, user.id) is not None:
            raise DuplicateWorkspaceMembershipError("User is already a member of this workspace")

        membership = WorkspaceMember(
            workspace_id=stored_workspace.id,
            user_id=user.id,
            role=canonical_role,
        )
        self.session.add(membership)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateWorkspaceMembershipError(
                "User is already a member of this workspace"
            ) from exc
        self.session.refresh(membership)
        return membership

    def resolve_active_membership(
        self,
        *,
        principal: AuthenticatedPrincipal,
        workspace: Workspace,
    ) -> WorkspaceMember:
        if not principal.active:
            raise InactiveUserError("User is inactive")
        user = self.get_user(principal.user_id)
        if not user.active:
            raise InactiveUserError("User is inactive")
        stored_workspace = self._get_workspace(workspace.id)
        membership = self._find_membership(stored_workspace.id, user.id)
        if membership is None:
            raise WorkspaceMembershipNotFoundError("Workspace membership not found")
        if not membership.active:
            raise InactiveWorkspaceMembershipError("Workspace membership is inactive")
        return membership

    def list_for_user(self, user_id: UUID) -> list[WorkspaceMember]:
        self.get_user(user_id)
        statement = (
            select(WorkspaceMember)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(WorkspaceMember.created_at.asc())
        )
        return list(self.session.exec(statement).all())

    def list_for_workspace(self, workspace: Workspace) -> list[WorkspaceMember]:
        stored_workspace = self._get_workspace(workspace.id)
        statement = (
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == stored_workspace.id)
            .order_by(WorkspaceMember.created_at.asc())
        )
        return list(self.session.exec(statement).all())

    def deactivate_user(self, user_id: UUID) -> User:
        """Retain the historical identity while making it ineligible as a principal."""

        user = self.get_user(user_id)
        user.active = False
        user.updated_at = utc_now()
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user

    def _get_workspace(self, workspace_id: UUID) -> Workspace:
        workspace = self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError("Workspace not found")
        return workspace

    def _find_membership(
        self,
        workspace_id: UUID,
        user_id: UUID,
    ) -> WorkspaceMember | None:
        return self.session.exec(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        ).first()

    @staticmethod
    def _validate_role(role: WorkspaceMemberRole) -> WorkspaceMemberRole:
        if isinstance(role, WorkspaceMemberRole):
            return role
        try:
            return WorkspaceMemberRole(role)
        except (TypeError, ValueError) as exc:
            raise WorkspaceMemberRoleValidationError("Workspace member role is invalid") from exc
