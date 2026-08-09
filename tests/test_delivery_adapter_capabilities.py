import pytest

from app.models import OutboundIntegrationActionType
from app.services.delivery_adapters import (
    DEFAULT_DELIVERY_ADAPTER_CAPABILITIES,
    DeliveryAdapterCapabilities,
    DeliveryAdapterRegistry,
    NoopDeliveryAdapter,
)


def test_adapters_declare_safe_capabilities_without_secret_data():
    capabilities = DeliveryAdapterCapabilities(
        supported_action_types=frozenset({OutboundIntegrationActionType.SEND_MESSAGE}),
        max_content_length=500,
    )
    assert capabilities.supported_action_types == {OutboundIntegrationActionType.SEND_MESSAGE}
    assert capabilities.max_content_length == 500
    assert not hasattr(capabilities, "secret")


def test_registry_keeps_existing_adapters_compatible_with_generic_capabilities():
    class LegacyAdapter:
        def deliver(self, action, account):
            del action, account

    registry = DeliveryAdapterRegistry({"legacy": LegacyAdapter(), "noop": NoopDeliveryAdapter()})
    assert registry.capabilities_for("legacy") == DEFAULT_DELIVERY_ADAPTER_CAPABILITIES
    assert registry.capabilities_for("noop") == DEFAULT_DELIVERY_ADAPTER_CAPABILITIES
    assert registry.capabilities_for("missing") is None


def test_invalid_or_unknown_capability_declarations_fail_safely():
    with pytest.raises(ValueError, match="at least one action type"):
        DeliveryAdapterCapabilities(supported_action_types=frozenset())
    with pytest.raises(ValueError, match="maximum content length"):
        DeliveryAdapterCapabilities(
            supported_action_types=frozenset({OutboundIntegrationActionType.SEND_MESSAGE}),
            max_content_length=0,
        )

    class InvalidCapabilityAdapter:
        capabilities = object()

        def deliver(self, action, account):
            del action, account

    assert DeliveryAdapterRegistry({"invalid": InvalidCapabilityAdapter()}).capabilities_for("invalid") is None
