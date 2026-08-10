"""Canonical persisted-role policy for human workspace authorization."""

from enum import StrEnum

from app.models import WorkspaceMemberRole


class WorkspacePermission(StrEnum):
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_SETTINGS_MANAGE = "workspace_settings_manage"
    SALES_DATA_READ = "sales_data_read"
    SALES_DATA_WRITE = "sales_data_write"
    CONVERSATION_OPERATE = "conversation_operate"
    APPROVAL_DECIDE = "approval_decide"
    OPERATOR_ASSIGNMENT_MANAGE = "operator_assignment_manage"
    INTEGRATION_READ = "integration_read"
    INTEGRATION_MANAGE = "integration_manage"
    OUTBOUND_ACTION_OPERATE = "outbound_action_operate"
    AI_USAGE_READ = "ai_usage_read"


class UnknownWorkspaceRoleError(PermissionError):
    """Raised when persisted membership role data is outside the known enum."""


class WorkspacePermissionDeniedError(PermissionError):
    """Raised when a known role lacks a requested workspace capability."""


OWNER_PERMISSIONS = frozenset(WorkspacePermission)
ADMIN_PERMISSIONS = frozenset(WorkspacePermission)
MEMBER_PERMISSIONS = frozenset(
    {
        WorkspacePermission.WORKSPACE_READ,
        WorkspacePermission.SALES_DATA_READ,
        WorkspacePermission.SALES_DATA_WRITE,
        WorkspacePermission.CONVERSATION_OPERATE,
    }
)

ROLE_PERMISSIONS: dict[WorkspaceMemberRole, frozenset[WorkspacePermission]] = {
    WorkspaceMemberRole.OWNER: OWNER_PERMISSIONS,
    WorkspaceMemberRole.ADMIN: ADMIN_PERMISSIONS,
    WorkspaceMemberRole.MEMBER: MEMBER_PERMISSIONS,
}


class WorkspaceRBACPolicy:
    """Server-owned role-to-capability matrix for one active membership."""

    @classmethod
    def permissions_for_role(
        cls,
        role: WorkspaceMemberRole | str,
    ) -> frozenset[WorkspacePermission]:
        try:
            canonical_role = WorkspaceMemberRole(role)
        except (TypeError, ValueError) as exc:
            raise UnknownWorkspaceRoleError("Workspace member role is not recognized") from exc
        return ROLE_PERMISSIONS[canonical_role]

    @classmethod
    def allows(
        cls,
        role: WorkspaceMemberRole | str,
        permission: WorkspacePermission,
    ) -> bool:
        try:
            return permission in cls.permissions_for_role(role)
        except UnknownWorkspaceRoleError:
            return False

    @classmethod
    def require_permission(
        cls,
        role: WorkspaceMemberRole | str,
        permission: WorkspacePermission,
    ) -> None:
        if not cls.allows(role, permission):
            raise WorkspacePermissionDeniedError("Insufficient workspace permission")
