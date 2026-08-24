import hmac
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import select

from app.api.dependencies import (
    IntegrationIngestRateLimitDep,
    SessionDep,
    SettingsDep,
    VerifiedIntegrationContextDep,
    VerifiedWhatsAppCloudIntegrationContextDep,
    WhatsAppCloudIntegrationIngestRateLimitDep,
)
from app.core.lead_capture import LeadCaptureSignal
from app.integrations.providers import WHATSAPP_CLOUD_PROVIDER
from app.models import IntegrationAccount, Lead
from app.schemas import (
    InboundIntegrationDuplicateRead,
    InboundIntegrationEvent,
    InboundIntegrationReplyRead,
    WhatsAppCloudInboundTextEvent,
)
from app.services.inbound_integrations import (
    InboundIntegrationEventIdValidationError,
    InboundIntegrationService,
    InboundSalesWorkItemRoutingError,
)
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceNotFoundError,
    IntegrationCredentialReferenceService,
)
from app.services.meta_inbound import (
    AmbiguousExternalIdentityLeadError,
    InboundExternalIdentityService,
)
from app.services.repository import NotFoundError
from app.services.secret_resolver import EnvironmentSecretResolver
from app.services.whatsapp_cloud import (
    WhatsAppCloudAccountMismatchError,
    WhatsAppCloudIgnoredEvent,
    WhatsAppCloudNormalizationError,
    WhatsAppCloudUnsupportedMessageError,
    WhatsAppCloudWebhookVerificationError,
    normalize_text_message,
    verify_webhook_challenge,
)
from app.services.workspaces import ensure_workspace_lead_capture_foundation

router = APIRouter(
    prefix="/integrations/inbound-events/whatsapp-cloud",
    tags=["integrations"],
)


@router.get("/{account_id}")
def verify_direct_whatsapp_cloud_webhook(
    account_id: UUID,
    request: Request,
    session: SessionDep,
) -> Response:
    """Verify the callback URL when Meta subscribes the WhatsApp webhook."""
    account = session.exec(
        select(IntegrationAccount).where(
            IntegrationAccount.id == account_id,
            IntegrationAccount.active.is_(True),
            IntegrationAccount.provider == WHATSAPP_CLOUD_PROVIDER,
        )
    ).first()

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook verification",
        )

    try:
        credential_reference = IntegrationCredentialReferenceService(
            session
        ).get_for_integration_account(
            account,
            "webhook_verify_token",
        )

        configured_verify_token = EnvironmentSecretResolver().resolve(
            credential_reference.secret_reference
        )

        challenge = verify_webhook_challenge(
            mode=request.query_params.get("hub.mode"),
            verify_token=request.query_params.get("hub.verify_token"),
            challenge=request.query_params.get("hub.challenge"),
            configured_verify_token=configured_verify_token,
        )

    except (
        IntegrationCredentialReferenceNotFoundError,
        WhatsAppCloudWebhookVerificationError,
    ) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook verification",
        ) from exc

    return Response(
        content=challenge,
        media_type="text/plain",
        status_code=200,
    )


@router.post(
    "/{account_id}",
    response_model=InboundIntegrationReplyRead | InboundIntegrationDuplicateRead,
)
async def receive_direct_whatsapp_cloud_event(
    account_id: UUID,
    request: Request,
    session: SessionDep,
    integration_context: VerifiedWhatsAppCloudIntegrationContextDep,
    rate_limit: WhatsAppCloudIntegrationIngestRateLimitDep,
    settings: SettingsDep,
):
    """Accept a raw Meta WhatsApp Cloud webhook directly into HIRI."""
    del account_id
    del rate_limit

    account = integration_context.account

    try:
        raw_payload = await request.json()

        normalized = normalize_text_message(
            raw_payload,
            expected_recipient_account_id=account.external_account_id,
        )

    except (WhatsAppCloudIgnoredEvent, WhatsAppCloudUnsupportedMessageError):
        # Valid provider events that HIRI does not need to process should still
        # be acknowledged so Meta does not repeatedly retry them.
        return Response(status_code=204)

    except WhatsAppCloudAccountMismatchError as exc:
        raise HTTPException(
            status_code=404,
            detail="Integration account not found",
        ) from exc

    except WhatsAppCloudNormalizationError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    payload = WhatsAppCloudInboundTextEvent(
        provider_event_id=normalized.provider_event_id,
        sender_external_id=normalized.sender_external_id,
        recipient_account_id=normalized.recipient_account_id,
        content=normalized.content,
        timestamp=normalized.timestamp,
    )

    return await receive_whatsapp_cloud_text_event(
        payload=payload,
        session=session,
        integration_context=integration_context,
        rate_limit=None,
        settings=settings,
    )

@router.post(
    "",
    response_model=InboundIntegrationReplyRead | InboundIntegrationDuplicateRead,
)
async def receive_whatsapp_cloud_text_event(
    payload: WhatsAppCloudInboundTextEvent,
    session: SessionDep,
    integration_context: VerifiedIntegrationContextDep,
    rate_limit: IntegrationIngestRateLimitDep,
    settings: SettingsDep,
) -> InboundIntegrationReplyRead | InboundIntegrationDuplicateRead:
    """Accept n8n-normalized WhatsApp Cloud text after machine authentication."""
    del rate_limit

    account = integration_context.account
    workspace = integration_context.workspace
    if account.provider != WHATSAPP_CLOUD_PROVIDER:
        raise HTTPException(status_code=401, detail="Invalid webhook authentication")
    if not account.external_account_id or not hmac.compare_digest(
        account.external_account_id,
        payload.recipient_account_id.strip(),
    ):
        raise HTTPException(status_code=404, detail="Integration account not found")
    ensure_workspace_lead_capture_foundation(session, workspace)

    existing_lead = session.exec(
        select(Lead).where(
            Lead.tenant_id == workspace.slug,
            Lead.phone == payload.sender_external_id,
        )
    ).first()
    integration_service = InboundIntegrationService(session, settings)
    try:
        reservation = integration_service.reserve_event(
            workspace,
            account,
            payload.provider_event_id,
        )
        if not reservation.first_delivery:
            return InboundIntegrationDuplicateRead(
                correlation_id=reservation.receipt.correlation_id,
            )
        with integration_service.release_event_reservation_on_failure(
            workspace,
            account,
            reservation,
        ):
            capture = integration_service.capture_reserved_event(
                workspace,
                account,
                reservation,
                LeadCaptureSignal(
                    source=WHATSAPP_CLOUD_PROVIDER,
                    phone=payload.sender_external_id,
                    message=payload.content,
                    external_reference=payload.provider_event_id,
                    metadata={
                        "timestamp": payload.timestamp,
                    },
                    lead_id=existing_lead.id if existing_lead is not None else None,
                ),
            )
            InboundExternalIdentityService(session).bind_captured_identity(
                workspace,
                account,
                channel=WHATSAPP_CLOUD_PROVIDER,
                external_subject_id=payload.sender_external_id,
                contact_id=capture.contact_id,
                lead_id=capture.lead_id,
            )
            result = await integration_service.handle_work_item_event(
                InboundIntegrationEvent(
                    lead_id=capture.lead_id,
                    channel=WHATSAPP_CLOUD_PROVIDER,
                    content=payload.content,
                    external_event_id=payload.provider_event_id,
                ),
                workspace,
                account,
                correlation_id=reservation.receipt.correlation_id,
            )
    except InboundIntegrationEventIdValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc
    except AmbiguousExternalIdentityLeadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InboundSalesWorkItemRoutingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return InboundIntegrationReplyRead(
        lead_id=capture.lead_id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
        handoff_required=result.handoff_required,
        handoff_reason_code=result.handoff_reason_code,
        correlation_id=reservation.receipt.correlation_id,
    )
