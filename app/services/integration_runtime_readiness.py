"""Derived, read-only configuration readiness for integration accounts."""

from dataclasses import dataclass
from typing import TypeAlias
from uuid import UUID
from urllib.parse import urlparse

from sqlmodel import Session

from app.config import Settings
from app.models import (
    IntegrationAccount,
    OutboundIntegrationActionType,
    Workspace,
)
from app.services.delivery_adapters import DeliveryAdapterRegistry
from app.services.integration_accounts import IntegrationAccountService
from app.services.integration_runtime_readiness_reasons import (
    IntegrationRuntimeReadinessReasonCode,
    runtime_readiness_reason_message,
)
from app.services.outbound_delivery_readiness_reasons import (
    OutboundDeliveryReadinessReasonCode,
    readiness_reason_message,
)
from app.services.secret_reference_policy import IntegrationSecretReferencePolicy
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolver
from app.services.webhook_authentication import ProviderWebhookAuthenticationService


ReadinessReasonCode: TypeAlias = (
    IntegrationRuntimeReadinessReasonCode | OutboundDeliveryReadinessReasonCode
)


@dataclass(frozen=True)
class IntegrationRuntimeReadinessBlocker:
    code: ReadinessReasonCode
    message: str


@dataclass(frozen=True)
class IntegrationRuntimeReadiness:
    account: IntegrationAccount
    configuration_ready: bool
    blockers: tuple[IntegrationRuntimeReadinessBlocker, ...]


class IntegrationRuntimeReadinessService:
    """Compose existing integration configuration contracts without adapter I/O."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        adapter_registry: DeliveryAdapterRegistry,
        *,
        secret_resolver: SecretResolver | None = None,
        secret_reference_policy: IntegrationSecretReferencePolicy | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.adapter_registry = adapter_registry
        self.account_service = IntegrationAccountService(session)
        self.secret_reference_policy = (
            secret_reference_policy or IntegrationSecretReferencePolicy()
        )
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver(
            secret_reference_policy=self.secret_reference_policy
        )

    def evaluate(
        self,
        workspace: Workspace,
        account_id: UUID,
    ) -> IntegrationRuntimeReadiness:
        account = self.account_service.get_for_workspace(workspace, account_id)
        codes: list[ReadinessReasonCode] = []

        if not workspace.active:
            codes.append(IntegrationRuntimeReadinessReasonCode.WORKSPACE_INACTIVE)
        if not account.active:
            codes.append(OutboundDeliveryReadinessReasonCode.INTEGRATION_ACCOUNT_INACTIVE)

        verifier_configured = self._has_inbound_verifier(account)
        if not verifier_configured:
            codes.append(IntegrationRuntimeReadinessReasonCode.INBOUND_VERIFIER_NOT_CONFIGURED)
        else:
            self._append_secret_blockers(account, codes)

        adapter = self.adapter_registry.get(account.provider)
        capabilities = self.adapter_registry.capabilities_for(account.provider)
        if adapter is None or capabilities is None:
            codes.append(IntegrationRuntimeReadinessReasonCode.OUTBOUND_ADAPTER_NOT_REGISTERED)
        elif OutboundIntegrationActionType.SEND_MESSAGE not in capabilities.supported_action_types:
            codes.append(
                IntegrationRuntimeReadinessReasonCode.OUTBOUND_ADAPTER_CAPABILITY_MISMATCH
            )

        if account.provider == "generic_webhook":
            self._append_generic_webhook_configuration_blockers(codes)
            if self.settings.outbound_webhook_signing_enabled:
                self._append_secret_blockers(account, codes)

        blockers = tuple(self._blocker(code) for code in dict.fromkeys(codes))
        return IntegrationRuntimeReadiness(
            account=account,
            configuration_ready=not blockers,
            blockers=blockers,
        )

    def _has_inbound_verifier(self, account: IntegrationAccount) -> bool:
        return account.provider in ProviderWebhookAuthenticationService(
            self.settings,
            secret_resolver=self.secret_resolver,
        ).verifiers

    def _append_secret_blockers(
        self,
        account: IntegrationAccount,
        codes: list[ReadinessReasonCode],
    ) -> None:
        reference = account.secret_reference
        if not reference:
            codes.append(IntegrationRuntimeReadinessReasonCode.SECRET_REFERENCE_MISSING)
            return
        if not self.secret_reference_policy.is_allowed(reference):
            codes.append(IntegrationRuntimeReadinessReasonCode.SECRET_REFERENCE_INVALID)
            return
        if not self.secret_resolver.resolve(reference):
            codes.append(IntegrationRuntimeReadinessReasonCode.SECRET_UNRESOLVABLE)

    def _append_generic_webhook_configuration_blockers(
        self,
        codes: list[ReadinessReasonCode],
    ) -> None:
        endpoint = self.settings.outbound_webhook_url.strip()
        if not endpoint:
            codes.append(IntegrationRuntimeReadinessReasonCode.OUTBOUND_CONFIGURATION_MISSING)
            return
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            codes.append(IntegrationRuntimeReadinessReasonCode.OUTBOUND_CONFIGURATION_INVALID)

    @staticmethod
    def _blocker(code: ReadinessReasonCode) -> IntegrationRuntimeReadinessBlocker:
        if isinstance(code, OutboundDeliveryReadinessReasonCode):
            message = readiness_reason_message(code)
        else:
            message = runtime_readiness_reason_message(code)
        return IntegrationRuntimeReadinessBlocker(code=code, message=message)
