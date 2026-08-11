"""Derived, read-only configuration readiness for integration accounts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias
from uuid import UUID
from urllib.parse import urlparse

from sqlmodel import Session

from app.integrations.providers import (
    GENERIC_HMAC_PROVIDER,
    GENERIC_WEBHOOK_DELIVERY_PROVIDERS,
    GENERIC_WEBHOOK_PROVIDER,
    WHATSAPP_CLOUD_PROVIDER,
)
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


class IntegrationRuntimeCapability(StrEnum):
    """Provider-neutral channel capability names exposed to operators."""

    INBOUND_MESSAGES = "inbound_messages"
    OUTBOUND_MESSAGES = "outbound_messages"
    OUTBOUND_APPROVAL_GATE = "outbound_approval_gate"
    PROVIDER_DELIVERY_STATUS = "provider_delivery_status"


STANDARD_CHANNEL_CAPABILITIES: tuple[IntegrationRuntimeCapability, ...] = (
    IntegrationRuntimeCapability.INBOUND_MESSAGES,
    IntegrationRuntimeCapability.OUTBOUND_MESSAGES,
    IntegrationRuntimeCapability.OUTBOUND_APPROVAL_GATE,
    IntegrationRuntimeCapability.PROVIDER_DELIVERY_STATUS,
)

CHANNEL_CAPABILITIES_BY_PROVIDER: dict[
    str, frozenset[IntegrationRuntimeCapability]
] = {
    GENERIC_HMAC_PROVIDER: frozenset(
        {
            IntegrationRuntimeCapability.INBOUND_MESSAGES,
            IntegrationRuntimeCapability.OUTBOUND_MESSAGES,
        }
    ),
    GENERIC_WEBHOOK_PROVIDER: frozenset(
        {
            IntegrationRuntimeCapability.OUTBOUND_MESSAGES,
        }
    ),
    WHATSAPP_CLOUD_PROVIDER: frozenset(STANDARD_CHANNEL_CAPABILITIES),
}

EXTERNAL_ACCOUNT_REQUIRED_PROVIDERS = frozenset({WHATSAPP_CLOUD_PROVIDER})
SIGNED_OUTBOUND_WEBHOOK_REQUIRED_PROVIDERS = frozenset({WHATSAPP_CLOUD_PROVIDER})


@dataclass(frozen=True)
class IntegrationRuntimeCapabilityReadiness:
    capability: IntegrationRuntimeCapability
    supported: bool
    ready: bool
    blockers: tuple[IntegrationRuntimeReadinessBlocker, ...]


@dataclass(frozen=True)
class IntegrationRuntimeReadiness:
    account: IntegrationAccount
    configuration_ready: bool
    blockers: tuple[IntegrationRuntimeReadinessBlocker, ...]
    capabilities: tuple[IntegrationRuntimeCapabilityReadiness, ...]


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
        secret_codes = self._secret_blockers(account)
        external_account_codes = self._external_account_blockers(account)
        adapter_codes = self._outbound_adapter_blockers(account)
        outbound_transport_codes = self._outbound_transport_blockers(account)
        outbound_signing_codes = self._outbound_signing_blockers(account)
        approval_gate_codes = self._approval_gate_blockers()

        if not verifier_configured:
            codes.append(IntegrationRuntimeReadinessReasonCode.INBOUND_VERIFIER_NOT_CONFIGURED)
        else:
            codes.extend(secret_codes)

        codes.extend(adapter_codes)

        if account.provider in GENERIC_WEBHOOK_DELIVERY_PROVIDERS:
            codes.extend(outbound_transport_codes)
            if self.settings.outbound_webhook_signing_enabled:
                codes.extend(secret_codes)

        capability_results = self._evaluate_capabilities(
            workspace=workspace,
            account=account,
            verifier_configured=verifier_configured,
            secret_codes=secret_codes,
            external_account_codes=external_account_codes,
            adapter_codes=adapter_codes,
            outbound_transport_codes=outbound_transport_codes,
            outbound_signing_codes=outbound_signing_codes,
            approval_gate_codes=approval_gate_codes,
        )
        for capability in capability_results:
            if capability.supported:
                codes.extend(blocker.code for blocker in capability.blockers)

        blockers = tuple(self._blocker(code) for code in dict.fromkeys(codes))
        return IntegrationRuntimeReadiness(
            account=account,
            configuration_ready=not blockers,
            blockers=blockers,
            capabilities=capability_results,
        )

    def _has_inbound_verifier(self, account: IntegrationAccount) -> bool:
        return account.provider in ProviderWebhookAuthenticationService(
            self.settings,
            secret_resolver=self.secret_resolver,
        ).verifiers

    def _secret_blockers(
        self,
        account: IntegrationAccount,
    ) -> tuple[ReadinessReasonCode, ...]:
        reference = account.secret_reference
        if not reference:
            return (IntegrationRuntimeReadinessReasonCode.SECRET_REFERENCE_MISSING,)
        if not self.secret_reference_policy.is_allowed(reference):
            return (IntegrationRuntimeReadinessReasonCode.SECRET_REFERENCE_INVALID,)
        if not self.secret_resolver.resolve(reference):
            return (IntegrationRuntimeReadinessReasonCode.SECRET_UNRESOLVABLE,)
        return ()

    def _external_account_blockers(
        self,
        account: IntegrationAccount,
    ) -> tuple[ReadinessReasonCode, ...]:
        if account.provider not in EXTERNAL_ACCOUNT_REQUIRED_PROVIDERS:
            return ()
        if account.external_account_id and account.external_account_id.strip():
            return ()
        return (IntegrationRuntimeReadinessReasonCode.EXTERNAL_ACCOUNT_ID_MISSING,)

    def _outbound_adapter_blockers(
        self,
        account: IntegrationAccount,
    ) -> tuple[ReadinessReasonCode, ...]:
        adapter = self.adapter_registry.get(account.provider)
        capabilities = self.adapter_registry.capabilities_for(account.provider)
        if adapter is None or capabilities is None:
            return (IntegrationRuntimeReadinessReasonCode.OUTBOUND_ADAPTER_NOT_REGISTERED,)
        if OutboundIntegrationActionType.SEND_MESSAGE not in capabilities.supported_action_types:
            return (
                IntegrationRuntimeReadinessReasonCode.OUTBOUND_ADAPTER_CAPABILITY_MISMATCH,
            )
        return ()

    def _outbound_transport_blockers(
        self,
        account: IntegrationAccount,
    ) -> tuple[ReadinessReasonCode, ...]:
        if account.provider not in GENERIC_WEBHOOK_DELIVERY_PROVIDERS:
            return ()
        endpoint = self.settings.outbound_webhook_url.strip()
        if not endpoint:
            return (IntegrationRuntimeReadinessReasonCode.OUTBOUND_CONFIGURATION_MISSING,)
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return (IntegrationRuntimeReadinessReasonCode.OUTBOUND_CONFIGURATION_INVALID,)
        return ()

    def _outbound_signing_blockers(
        self,
        account: IntegrationAccount,
    ) -> tuple[ReadinessReasonCode, ...]:
        if account.provider not in SIGNED_OUTBOUND_WEBHOOK_REQUIRED_PROVIDERS:
            return ()
        if self.settings.outbound_webhook_signing_enabled:
            return ()
        return (IntegrationRuntimeReadinessReasonCode.OUTBOUND_WEBHOOK_SIGNING_DISABLED,)

    def _approval_gate_blockers(self) -> tuple[ReadinessReasonCode, ...]:
        if self.settings.require_human_approval:
            return ()
        return (IntegrationRuntimeReadinessReasonCode.OUTBOUND_APPROVAL_GATE_DISABLED,)

    def _evaluate_capabilities(
        self,
        *,
        workspace: Workspace,
        account: IntegrationAccount,
        verifier_configured: bool,
        secret_codes: tuple[ReadinessReasonCode, ...],
        external_account_codes: tuple[ReadinessReasonCode, ...],
        adapter_codes: tuple[ReadinessReasonCode, ...],
        outbound_transport_codes: tuple[ReadinessReasonCode, ...],
        outbound_signing_codes: tuple[ReadinessReasonCode, ...],
        approval_gate_codes: tuple[ReadinessReasonCode, ...],
    ) -> tuple[IntegrationRuntimeCapabilityReadiness, ...]:
        supported_capabilities = CHANNEL_CAPABILITIES_BY_PROVIDER.get(
            account.provider,
            frozenset(),
        )
        common_codes: list[ReadinessReasonCode] = []
        if not workspace.active:
            common_codes.append(IntegrationRuntimeReadinessReasonCode.WORKSPACE_INACTIVE)
        if not account.active:
            common_codes.append(
                OutboundDeliveryReadinessReasonCode.INTEGRATION_ACCOUNT_INACTIVE
            )

        capability_results: list[IntegrationRuntimeCapabilityReadiness] = []
        for capability in STANDARD_CHANNEL_CAPABILITIES:
            supported = capability in supported_capabilities
            capability_codes: list[ReadinessReasonCode]
            if supported:
                capability_codes = [*common_codes]
                if capability == IntegrationRuntimeCapability.INBOUND_MESSAGES:
                    capability_codes.extend(
                        self._inbound_message_capability_blockers(
                            verifier_configured=verifier_configured,
                            secret_codes=secret_codes,
                            external_account_codes=external_account_codes,
                        )
                    )
                elif capability == IntegrationRuntimeCapability.OUTBOUND_MESSAGES:
                    capability_codes.extend(
                        self._outbound_message_capability_blockers(
                            account=account,
                            adapter_codes=adapter_codes,
                            outbound_transport_codes=outbound_transport_codes,
                            outbound_signing_codes=outbound_signing_codes,
                            secret_codes=secret_codes,
                            external_account_codes=external_account_codes,
                            outbound_webhook_signing_enabled=(
                                self.settings.outbound_webhook_signing_enabled
                            ),
                        )
                    )
                elif capability == IntegrationRuntimeCapability.OUTBOUND_APPROVAL_GATE:
                    capability_codes.extend(
                        self._outbound_message_capability_blockers(
                            account=account,
                            adapter_codes=adapter_codes,
                            outbound_transport_codes=outbound_transport_codes,
                            outbound_signing_codes=outbound_signing_codes,
                            secret_codes=secret_codes,
                            external_account_codes=external_account_codes,
                            outbound_webhook_signing_enabled=(
                                self.settings.outbound_webhook_signing_enabled
                            ),
                        )
                    )
                    capability_codes.extend(approval_gate_codes)
                elif capability == IntegrationRuntimeCapability.PROVIDER_DELIVERY_STATUS:
                    capability_codes.extend(
                        self._provider_delivery_status_capability_blockers(
                            verifier_configured=verifier_configured,
                            secret_codes=secret_codes,
                        )
                    )
            else:
                capability_codes = [
                    IntegrationRuntimeReadinessReasonCode.PROVIDER_CAPABILITY_NOT_SUPPORTED
                ]

            blockers = tuple(
                self._blocker(code) for code in dict.fromkeys(capability_codes)
            )
            capability_results.append(
                IntegrationRuntimeCapabilityReadiness(
                    capability=capability,
                    supported=supported,
                    ready=supported and not blockers,
                    blockers=blockers,
                )
            )
        return tuple(capability_results)

    @staticmethod
    def _inbound_message_capability_blockers(
        *,
        verifier_configured: bool,
        secret_codes: tuple[ReadinessReasonCode, ...],
        external_account_codes: tuple[ReadinessReasonCode, ...],
    ) -> tuple[ReadinessReasonCode, ...]:
        codes: list[ReadinessReasonCode] = []
        if not verifier_configured:
            codes.append(IntegrationRuntimeReadinessReasonCode.INBOUND_VERIFIER_NOT_CONFIGURED)
        codes.extend(secret_codes)
        codes.extend(external_account_codes)
        return tuple(codes)

    @staticmethod
    def _outbound_message_capability_blockers(
        *,
        account: IntegrationAccount,
        adapter_codes: tuple[ReadinessReasonCode, ...],
        outbound_transport_codes: tuple[ReadinessReasonCode, ...],
        outbound_signing_codes: tuple[ReadinessReasonCode, ...],
        secret_codes: tuple[ReadinessReasonCode, ...],
        external_account_codes: tuple[ReadinessReasonCode, ...],
        outbound_webhook_signing_enabled: bool,
    ) -> tuple[ReadinessReasonCode, ...]:
        codes: list[ReadinessReasonCode] = [*adapter_codes]
        if account.provider in GENERIC_WEBHOOK_DELIVERY_PROVIDERS:
            codes.extend(outbound_transport_codes)
        codes.extend(outbound_signing_codes)
        if (
            account.provider in SIGNED_OUTBOUND_WEBHOOK_REQUIRED_PROVIDERS
            or (
                account.provider in GENERIC_WEBHOOK_DELIVERY_PROVIDERS
                and outbound_webhook_signing_enabled
            )
        ):
            codes.extend(secret_codes)
        codes.extend(external_account_codes)
        return tuple(codes)

    @staticmethod
    def _provider_delivery_status_capability_blockers(
        *,
        verifier_configured: bool,
        secret_codes: tuple[ReadinessReasonCode, ...],
    ) -> tuple[ReadinessReasonCode, ...]:
        codes: list[ReadinessReasonCode] = []
        if not verifier_configured:
            codes.append(IntegrationRuntimeReadinessReasonCode.INBOUND_VERIFIER_NOT_CONFIGURED)
        codes.extend(secret_codes)
        return tuple(codes)

    @staticmethod
    def _blocker(code: ReadinessReasonCode) -> IntegrationRuntimeReadinessBlocker:
        if isinstance(code, OutboundDeliveryReadinessReasonCode):
            message = readiness_reason_message(code)
        else:
            message = runtime_readiness_reason_message(code)
        return IntegrationRuntimeReadinessBlocker(code=code, message=message)
