from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlmodel import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Workspace
from app.services.webhook_authentication import (
    ProviderWebhookAuthenticationService,
    WebhookAuthenticationError,
)
from app.services.workspaces import (
    IntegrationContext,
    InvalidIntegrationContextError,
    WorkspaceInactiveError,
    WorkspaceNotFoundError,
    require_active_workspace,
    resolve_integration_account,
    resolve_integration_workspace_for_account,
)

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

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


def get_current_workspace(
    session: SessionDep,
    workspace_slug: WorkspaceSlugHeader,
) -> Workspace:
    try:
        return require_active_workspace(
            session,
            workspace_slug,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except WorkspaceInactiveError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


CurrentWorkspaceDep = Annotated[
    Workspace,
    Depends(get_current_workspace),
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
