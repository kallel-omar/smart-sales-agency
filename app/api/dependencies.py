from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.db import get_session
from app.integrations.providers import META_MESSAGING_PROVIDERS
from app.models import IntegrationAccount, Workspace, WorkspaceMember
from app.services.authentication import (
    AuthenticationService,
    InvalidAccessTokenError,
)
from app.services.operator_assignments import OperatorAssignmentActor
from app.services.approval_decisions import ApprovalDecisionActor
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
from app.services.rate_limiting import (
    InMemoryFixedWindowRateLimitBackend,
    RateLimitExceeded,
    RateLimitPolicy,
    RateLimitPolicyId,
    RateLimitService,
    rate_limit_headers,
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
MetaWebhookSignatureHeader = Annotated[str | None, Header(alias="X-Hub-Signature-256")]
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
OperatorAssignmentManagePermissionDep = Annotated[
    None,
    Depends(
        _workspace_permission_dependency(
            WorkspacePermission.OPERATOR_ASSIGNMENT_MANAGE,
        )
    ),
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


def get_approval_decision_actor(
    context: AuthenticatedWorkspaceContextDep,
) -> ApprovalDecisionActor:
    return ApprovalDecisionActor(
        user_id=context.principal.user_id,
        membership_id=context.membership.id,
        workspace_id=context.workspace.id,
        role=context.membership.role,
    )


ApprovalDecisionActorDep = Annotated[
    ApprovalDecisionActor,
    Depends(get_approval_decision_actor),
]


def get_operator_assignment_actor(
    context: AuthenticatedWorkspaceContextDep,
) -> OperatorAssignmentActor:
    return OperatorAssignmentActor(
        user_id=context.principal.user_id,
        membership_id=context.membership.id,
        workspace_id=context.workspace.id,
        role=context.membership.role,
    )


OperatorAssignmentActorDep = Annotated[
    OperatorAssignmentActor,
    Depends(get_operator_assignment_actor),
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


async def get_verified_meta_integration_context(
    account_id: UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    signature: MetaWebhookSignatureHeader = None,
) -> IntegrationContext:
    """Authenticate a raw Meta body against the account configured in the URL."""
    try:
        account = session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.id == account_id,
                IntegrationAccount.active.is_(True),
                IntegrationAccount.provider.in_(META_MESSAGING_PROVIDERS),
            )
        ).first()
        if account is None:
            raise InvalidIntegrationContextError("Integration context is not recognized")
        ProviderWebhookAuthenticationService(settings).authenticate(
            account,
            payload=await request.body(),
            signature=signature,
            timestamp=None,
            event_id=None,
        )
        workspace = resolve_integration_workspace_for_account(session, account)
        return IntegrationContext(account=account, workspace=workspace)
    except (InvalidIntegrationContextError, WebhookAuthenticationError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook authentication",
        ) from exc


VerifiedMetaIntegrationContextDep = Annotated[
    IntegrationContext,
    Depends(get_verified_meta_integration_context),
]


def get_rate_limit_service(
    request: Request,
    settings: SettingsDep,
) -> RateLimitService:
    backend = getattr(request.app.state, "rate_limit_backend", None)
    if backend is None:
        backend = InMemoryFixedWindowRateLimitBackend(
            max_buckets=settings.rate_limit_in_memory_max_buckets,
            cleanup_batch_size=settings.rate_limit_in_memory_cleanup_batch_size,
        )
        request.app.state.rate_limit_backend = backend
    return RateLimitService(
        backend,
        enabled=settings.rate_limit_enabled,
    )


RateLimitServiceDep = Annotated[RateLimitService, Depends(get_rate_limit_service)]


def enforce_auth_login_rate_limit(
    request: Request,
    service: RateLimitServiceDep,
    settings: SettingsDep,
) -> None:
    _enforce_rate_limit(
        service,
        _rate_limit_policy(settings, RateLimitPolicyId.AUTH_LOGIN),
        _scope_key(
            "client_source",
            request.client.host if request.client is not None else "unknown",
        ),
    )


AuthLoginRateLimitDep = Annotated[
    None,
    Depends(enforce_auth_login_rate_limit),
]


def enforce_integration_ingest_rate_limit(
    integration_context: VerifiedIntegrationContextDep,
    service: RateLimitServiceDep,
    settings: SettingsDep,
) -> None:
    _enforce_rate_limit(
        service,
        _rate_limit_policy(settings, RateLimitPolicyId.INTEGRATION_INGEST),
        _scope_key("integration_account", str(integration_context.account.id)),
    )


IntegrationIngestRateLimitDep = Annotated[
    None,
    Depends(enforce_integration_ingest_rate_limit),
]


def enforce_meta_integration_ingest_rate_limit(
    integration_context: VerifiedMetaIntegrationContextDep,
    service: RateLimitServiceDep,
    settings: SettingsDep,
) -> None:
    _enforce_rate_limit(
        service,
        _rate_limit_policy(settings, RateLimitPolicyId.INTEGRATION_INGEST),
        _scope_key("integration_account", str(integration_context.account.id)),
    )


MetaIntegrationIngestRateLimitDep = Annotated[
    None,
    Depends(enforce_meta_integration_ingest_rate_limit),
]


def enforce_outbound_delivery_rate_limit(
    account_id: UUID,
    context: AuthenticatedWorkspaceContextDep,
    _: OutboundActionOperatePermissionDep,
    session: SessionDep,
    service: RateLimitServiceDep,
    settings: SettingsDep,
) -> None:
    account = session.exec(
        select(IntegrationAccount).where(
            IntegrationAccount.id == account_id,
            IntegrationAccount.workspace_id == context.workspace.id,
        )
    ).first()
    if account is None:
        raise HTTPException(status_code=404, detail="Integration account not found")

    _enforce_rate_limit(
        service,
        _rate_limit_policy(settings, RateLimitPolicyId.OUTBOUND_DELIVERY),
        _scope_key(
            "outbound_delivery",
            f"{context.workspace.id}:{context.principal.user_id}:{account.id}",
        ),
    )


OutboundDeliveryRateLimitDep = Annotated[
    None,
    Depends(enforce_outbound_delivery_rate_limit),
]


def enforce_ai_conversation_rate_limit(
    context: AuthenticatedWorkspaceContextDep,
    _: ConversationOperatePermissionDep,
    service: RateLimitServiceDep,
    settings: SettingsDep,
) -> None:
    _enforce_rate_limit(
        service,
        _rate_limit_policy(settings, RateLimitPolicyId.AI_CONVERSATION),
        _scope_key(
            "ai_conversation",
            f"{context.workspace.id}:{context.principal.user_id}",
        ),
    )


AIConversationRateLimitDep = Annotated[
    None,
    Depends(enforce_ai_conversation_rate_limit),
]


def _enforce_rate_limit(
    service: RateLimitService,
    policy: RateLimitPolicy,
    scope_key: str,
) -> None:
    try:
        service.enforce(policy, scope_key)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers=rate_limit_headers(exc.decision),
        ) from exc


def _rate_limit_policy(settings: Settings, policy_id: RateLimitPolicyId) -> RateLimitPolicy:
    if policy_id is RateLimitPolicyId.AUTH_LOGIN:
        return RateLimitPolicy(
            policy_id,
            settings.rate_limit_auth_login_limit,
            settings.rate_limit_auth_login_window_seconds,
        )
    if policy_id is RateLimitPolicyId.INTEGRATION_INGEST:
        return RateLimitPolicy(
            policy_id,
            settings.rate_limit_integration_ingest_limit,
            settings.rate_limit_integration_ingest_window_seconds,
        )
    if policy_id is RateLimitPolicyId.OUTBOUND_DELIVERY:
        return RateLimitPolicy(
            policy_id,
            settings.rate_limit_outbound_delivery_limit,
            settings.rate_limit_outbound_delivery_window_seconds,
        )
    return RateLimitPolicy(
        policy_id,
        settings.rate_limit_ai_conversation_limit,
        settings.rate_limit_ai_conversation_window_seconds,
    )


def _scope_key(scope_type: str, trusted_value: str) -> str:
    digest = sha256(trusted_value.encode()).hexdigest()
    return f"{scope_type}:{digest}"
