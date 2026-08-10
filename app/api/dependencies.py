from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Workspace, WorkspaceMember
from app.services.authentication import (
    AuthenticationService,
    InvalidAccessTokenError,
)
from app.services.identity_memberships import (
    AuthenticatedPrincipal,
    IdentityMembershipService,
    InactiveUserError,
    InactiveWorkspaceMembershipError,
    UserNotFoundError,
    WorkspaceMembershipNotFoundError,
)
from app.services.webhook_authentication import (
    ProviderWebhookAuthenticationService,
    WebhookAuthenticationError,
)
from app.services.workspace_rbac import (
    WorkspacePermission,
    WorkspacePermissionDeniedError,
    WorkspaceRBACPolicy,
)
from app.services.workspaces import (
    IntegrationContext,
    InvalidIntegrationContextError,
    WorkspaceNotFoundError,
    get_workspace_by_slug,
    resolve_integration_account,
    resolve_integration_workspace_for_account,
)

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedWorkspaceContext:
    """Trusted request-local human access context for one selected workspace."""

    principal: AuthenticatedPrincipal
    workspace: Workspace
    membership: WorkspaceMember


WorkspaceSlugHeader = Annotated[
    str,
    Header(
        alias="X-Workspace-Slug",
        min_length=1,
    ),
]

IntegrationKeyHeader = Annotated[
    str,
    Header(
        alias="X-Integration-Key",
        min_length=1,
    ),
]

WebhookSignatureHeader = Annotated[str | None, Header(alias="X-Webhook-Signature")]
WebhookTimestampHeader = Annotated[str | None, Header(alias="X-Webhook-Timestamp")]
WebhookEventIdHeader = Annotated[str | None, Header(alias="X-Webhook-Event-Id")]


def _bearer_authentication_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid bearer authentication",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _workspace_not_found_error() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail="Workspace not found",
    )


def _workspace_permission_denied_error() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="Insufficient workspace permission",
    )


def get_authenticated_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    session: SessionDep,
    settings: SettingsDep,
) -> AuthenticatedPrincipal:
    """Resolve human identity only from a verified, current bearer credential."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _bearer_authentication_error()
    try:
        return AuthenticationService(session, settings).resolve_principal_from_access_token(
            credentials.credentials
        )
    except InvalidAccessTokenError as exc:
        raise _bearer_authentication_error() from exc


AuthenticatedPrincipalDep = Annotated[
    AuthenticatedPrincipal,
    Depends(get_authenticated_principal),
]


def _resolve_authenticated_workspace_context(
    *,
    session: Session,
    principal: AuthenticatedPrincipal,
    workspace_slug: str,
    require_workspace_active: bool,
) -> AuthenticatedWorkspaceContext:
    try:
        workspace = get_workspace_by_slug(session, workspace_slug)
        membership = IdentityMembershipService(session).resolve_active_membership(
            principal=principal,
            workspace=workspace,
        )
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found_error() from exc
    except (WorkspaceMembershipNotFoundError, InactiveWorkspaceMembershipError) as exc:
        raise _workspace_not_found_error() from exc
    except (UserNotFoundError, InactiveUserError) as exc:
        raise _bearer_authentication_error() from exc

    if require_workspace_active and not workspace.active:
        raise HTTPException(
            status_code=409,
            detail=f"Workspace '{workspace.slug}' is inactive",
        )

    return AuthenticatedWorkspaceContext(
        principal=principal,
        workspace=workspace,
        membership=membership,
    )


def get_authenticated_workspace_context(
    principal: AuthenticatedPrincipalDep,
    session: SessionDep,
    workspace_slug: WorkspaceSlugHeader,
) -> AuthenticatedWorkspaceContext:
    return _resolve_authenticated_workspace_context(
        session=session,
        principal=principal,
        workspace_slug=workspace_slug,
        require_workspace_active=True,
    )


AuthenticatedWorkspaceContextDep = Annotated[
    AuthenticatedWorkspaceContext,
    Depends(get_authenticated_workspace_context),
]


def get_current_workspace(
    context: AuthenticatedWorkspaceContextDep,
) -> Workspace:
    return context.workspace


CurrentWorkspaceDep = Annotated[
    Workspace,
    Depends(get_current_workspace),
]


def get_authenticated_workspace_readiness_context(
    principal: AuthenticatedPrincipalDep,
    session: SessionDep,
    workspace_slug: WorkspaceSlugHeader,
) -> AuthenticatedWorkspaceContext:
    """Resolve membership for a configuration read, including inactive workspaces."""
    return _resolve_authenticated_workspace_context(
        session=session,
        principal=principal,
        workspace_slug=workspace_slug,
        require_workspace_active=False,
    )


AuthenticatedWorkspaceReadinessContextDep = Annotated[
    AuthenticatedWorkspaceContext,
    Depends(get_authenticated_workspace_readiness_context),
]


def get_workspace_for_readiness(
    context: AuthenticatedWorkspaceReadinessContextDep,
) -> Workspace:
    return context.workspace


WorkspaceReadinessDep = Annotated[
    Workspace,
    Depends(get_workspace_for_readiness),
]


def get_authenticated_path_workspace_context(
    slug: str,
    principal: AuthenticatedPrincipalDep,
    session: SessionDep,
) -> AuthenticatedWorkspaceContext:
    return _resolve_authenticated_workspace_context(
        session=session,
        principal=principal,
        workspace_slug=slug,
        require_workspace_active=False,
    )


AuthenticatedPathWorkspaceContextDep = Annotated[
    AuthenticatedWorkspaceContext,
    Depends(get_authenticated_path_workspace_context),
]


def _authorize_workspace_context(
    context: AuthenticatedWorkspaceContext,
    permission: WorkspacePermission,
) -> None:
    try:
        WorkspaceRBACPolicy.require_permission(context.membership.role, permission)
    except WorkspacePermissionDeniedError as exc:
        raise _workspace_permission_denied_error() from exc


def _workspace_permission_dependency(permission: WorkspacePermission):
    def dependency(context: AuthenticatedWorkspaceContextDep) -> None:
        _authorize_workspace_context(context, permission)

    dependency.__name__ = f"require_{permission.value}_permission"
    return dependency


def _workspace_readiness_permission_dependency(permission: WorkspacePermission):
    def dependency(context: AuthenticatedWorkspaceReadinessContextDep) -> None:
        _authorize_workspace_context(context, permission)

    dependency.__name__ = f"require_{permission.value}_readiness_permission"
    return dependency


WorkspaceReadPermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.WORKSPACE_READ)),
]
WorkspaceSettingsManagePermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.WORKSPACE_SETTINGS_MANAGE)),
]
SalesDataReadPermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.SALES_DATA_READ)),
]
SalesDataWritePermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.SALES_DATA_WRITE)),
]
ConversationOperatePermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.CONVERSATION_OPERATE)),
]
ApprovalDecidePermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.APPROVAL_DECIDE)),
]
IntegrationReadPermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.INTEGRATION_READ)),
]
IntegrationReadinessReadPermissionDep = Annotated[
    None,
    Depends(_workspace_readiness_permission_dependency(WorkspacePermission.INTEGRATION_READ)),
]
IntegrationManagePermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.INTEGRATION_MANAGE)),
]
OutboundActionOperatePermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.OUTBOUND_ACTION_OPERATE)),
]
AIUsageReadPermissionDep = Annotated[
    None,
    Depends(_workspace_permission_dependency(WorkspacePermission.AI_USAGE_READ)),
]


def get_workspace_read_path_context(
    context: AuthenticatedPathWorkspaceContextDep,
) -> AuthenticatedWorkspaceContext:
    _authorize_workspace_context(context, WorkspacePermission.WORKSPACE_READ)
    return context


WorkspaceReadPathContextDep = Annotated[
    AuthenticatedWorkspaceContext,
    Depends(get_workspace_read_path_context),
]


async def get_verified_integration_context(
    request: Request,
    session: SessionDep,
    integration_key: IntegrationKeyHeader,
    settings: SettingsDep,
    signature: WebhookSignatureHeader = None,
    timestamp: WebhookTimestampHeader = None,
    event_id: WebhookEventIdHeader = None,
) -> IntegrationContext:
    try:
        account = resolve_integration_account(session, integration_key)
        ProviderWebhookAuthenticationService(settings).authenticate(
            account,
            payload=await request.body(),
            signature=signature,
            timestamp=timestamp,
            event_id=event_id,
        )
        workspace = resolve_integration_workspace_for_account(session, account)
        return IntegrationContext(account=account, workspace=workspace)
    except (InvalidIntegrationContextError, WebhookAuthenticationError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook authentication",
        ) from exc


VerifiedIntegrationContextDep = Annotated[
    IntegrationContext,
    Depends(get_verified_integration_context),
]
