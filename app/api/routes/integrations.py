from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import (
    CurrentWorkspaceDep,
    SessionDep,
    SettingsDep,
    VerifiedIntegrationContextDep,
)
from app.models import IntegrationAccount
from app.schemas import (
    InboundIntegrationEvent,
    IntegrationAccountAuditEventRead,
    IntegrationAccountCredentialRead,
    IntegrationAccountProvision,
    IntegrationAccountRead,
    IntegrationAccountSecretReferenceUpdate,
    SalesReply,
)
from app.services.inbound_integrations import InboundIntegrationService
from app.services.integration_account_audit import IntegrationAccountAuditService
from app.services.integration_accounts import (
    IntegrationAccountNotFoundError,
    IntegrationAccountService,
)
from app.services.repository import NotFoundError
from app.services.secret_reference_policy import SecretReferenceValidationError

router = APIRouter(prefix="/integrations", tags=["integrations"])


def account_read(account: IntegrationAccount) -> IntegrationAccountRead:
    return IntegrationAccountRead.model_validate(account)


def account_credential_read(
    account: IntegrationAccount,
    credential: str,
) -> IntegrationAccountCredentialRead:
    return IntegrationAccountCredentialRead(
        **account_read(account).model_dump(),
        inbound_credential=credential,
    )


@router.post(
    "/accounts",
    response_model=IntegrationAccountCredentialRead,
    status_code=status.HTTP_201_CREATED,
)
def provision_integration_account(
    payload: IntegrationAccountProvision,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountCredentialRead:
    """Provision a workspace-owned account and return its credential once."""
    try:
        account, credential = IntegrationAccountService(session).provision(
            workspace,
            payload.provider,
            payload.external_account_id,
            payload.secret_reference,
        )
    except SecretReferenceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Secret reference is not allowed",
        ) from exc
    return account_credential_read(account, credential)


@router.get("/accounts", response_model=list[IntegrationAccountRead])
def list_integration_accounts(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> list[IntegrationAccountRead]:
    accounts = IntegrationAccountService(session).list_for_workspace(workspace)
    return [account_read(account) for account in accounts]


@router.get(
    "/accounts/{account_id}/audit-events",
    response_model=list[IntegrationAccountAuditEventRead],
)
def list_integration_account_audit_events(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> list[IntegrationAccountAuditEventRead]:
    """Return safe lifecycle history for an account in the current workspace."""
    account_service = IntegrationAccountService(session)
    try:
        account_service.get_for_workspace(workspace, account_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc

    events = IntegrationAccountAuditService(session).list_for_account(workspace, account_id)
    return [IntegrationAccountAuditEventRead.model_validate(event) for event in events]


@router.post("/accounts/{account_id}/deactivate", response_model=IntegrationAccountRead)
def deactivate_integration_account(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountRead:
    try:
        account = IntegrationAccountService(session).deactivate(workspace, account_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_read(account)


@router.post("/accounts/{account_id}/reactivate", response_model=IntegrationAccountRead)
def reactivate_integration_account(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountRead:
    try:
        account = IntegrationAccountService(session).reactivate(workspace, account_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_read(account)


@router.post(
    "/accounts/{account_id}/credential/rotate",
    response_model=IntegrationAccountCredentialRead,
)
def rotate_integration_account_credential(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountCredentialRead:
    try:
        account, credential = IntegrationAccountService(session).rotate_credential(
            workspace,
            account_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_credential_read(account, credential)


@router.post(
    "/accounts/{account_id}/secret-reference",
    response_model=IntegrationAccountRead,
)
def update_integration_account_secret_reference(
    account_id: UUID,
    payload: IntegrationAccountSecretReferenceUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountRead:
    """Update an account's internal secret reference without resolving it."""
    try:
        account = IntegrationAccountService(session).update_secret_reference(
            workspace,
            account_id,
            payload.secret_reference,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except SecretReferenceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Secret reference is not allowed",
        ) from exc
    return account_read(account)


@router.post("/inbound-events", response_model=SalesReply)
async def receive_inbound_event(
    payload: InboundIntegrationEvent,
    session: SessionDep,
    integration_context: VerifiedIntegrationContextDep,
    settings: SettingsDep,
) -> SalesReply:
    """Accept a normalized provider-neutral inbound integration event."""

    integration_service = InboundIntegrationService(session, settings)

    try:
        result = await integration_service.handle_event(
            payload,
            integration_context.workspace,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc

    return SalesReply(
        lead_id=payload.lead_id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
    )
