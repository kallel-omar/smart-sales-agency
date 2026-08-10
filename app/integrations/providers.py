"""Stable provider identifiers used at the integration boundary."""

GENERIC_HMAC_PROVIDER = "generic_hmac"
GENERIC_WEBHOOK_PROVIDER = "generic_webhook"
WHATSAPP_CLOUD_PROVIDER = "whatsapp_cloud"

MACHINE_HMAC_PROVIDERS = frozenset(
    {
        GENERIC_HMAC_PROVIDER,
        WHATSAPP_CLOUD_PROVIDER,
    }
)

GENERIC_WEBHOOK_DELIVERY_PROVIDERS = frozenset(
    {
        GENERIC_WEBHOOK_PROVIDER,
        WHATSAPP_CLOUD_PROVIDER,
    }
)
