import hmac

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.api.dependencies import (
    SessionDep,
    SettingsDep,
    VerifiedIntegrationContextDep,
)
from app.integrations.providers import WHATSAPP_CLOUD_PROVIDER
from app.models import Lead
from app.schemas import (
    InboundIntegrationDuplicateRead,
    InboundIntegrationEvent,
    InboundIntegrationReplyRead,
    WhatsAppCloudInboundTextEvent,
)
from app.services.inbound_integrations import (
    InboundIntegrationEventIdValidationError,
    InboundIntegrationService,
)
from app.services.repository import NotFoundError

router = APIRouter(
    prefix="/integrations/inbound-events/whatsapp-cloud",
    tags=["integrations"],
)


@router.post(
    "",
    response_model=InboundIntegrationReplyRead | InboundIntegrationDuplicateRead,
)
async def receive_whatsapp_cloud_text_event(
    payload: WhatsAppCloudInboundTextEvent,
    session: SessionDep,
    integration_context: VerifiedIntegrationContextDep,
    settings: SettingsDep,
) -> InboundIntegrationReplyRead | InboundIntegrationDuplicateRead:
    """Accept n8n-normalized WhatsApp Cloud text after machine authentication."""

    account = integration_context.account
    workspace = integration_context.workspace
    if account.provider != WHATSAPP_CLOUD_PROVIDER:
        raise HTTPException(status_code=401, detail="Invalid webhook authentication")
    if not account.external_account_id or not hmac.compare_digest(
        account.external_account_id,
        payload.recipient_account_id.strip(),
    ):
        raise HTTPException(status_code=404, detail="Integration account not found")

    lead = session.exec(
        select(Lead).where(
            Lead.tenant_id == workspace.slug,
            Lead.phone == payload.sender_external_id,
        )
    ).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

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
        result = await integration_service.handle_event(
            InboundIntegrationEvent(
                lead_id=lead.id,
                channel=WHATSAPP_CLOUD_PROVIDER,
                content=payload.content,
                external_event_id=payload.provider_event_id,
            ),
            workspace,
        )
    except InboundIntegrationEventIdValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc

    return InboundIntegrationReplyRead(
        lead_id=lead.id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
        handoff_required=result.handoff_required,
        handoff_reason_code=result.handoff_reason_code,
        correlation_id=reservation.receipt.correlation_id,
    )
