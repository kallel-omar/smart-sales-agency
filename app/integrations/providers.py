"""Stable provider identifiers used at the integration boundary."""

GENERIC_HMAC_PROVIDER = "generic_hmac"
GENERIC_WEBHOOK_PROVIDER = "generic_webhook"
WHATSAPP_CLOUD_PROVIDER = "whatsapp_cloud"
FACEBOOK_MESSENGER_PROVIDER = "facebook_messenger"
INSTAGRAM_DM_PROVIDER = "instagram_dm"
TIKTOK_DM_PROVIDER = "tiktok_dm"

TIKTOK_COMMENT_CHANNEL = "tiktok_comment"

INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE = "facebook_login"
INSTAGRAM_LOGIN_AUTH_MODE = "instagram_login"
INSTAGRAM_DM_AUTH_MODES = frozenset(
    {
        INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE,
        INSTAGRAM_LOGIN_AUTH_MODE,
    }
)

META_MESSAGING_PROVIDERS = frozenset(
    {
        FACEBOOK_MESSENGER_PROVIDER,
        INSTAGRAM_DM_PROVIDER,
    }
)

MACHINE_HMAC_PROVIDERS = frozenset(
    {
        GENERIC_HMAC_PROVIDER,
        WHATSAPP_CLOUD_PROVIDER,
        *META_MESSAGING_PROVIDERS,
    }
)

GENERIC_WEBHOOK_DELIVERY_PROVIDERS = frozenset(
    {
        GENERIC_WEBHOOK_PROVIDER,
        WHATSAPP_CLOUD_PROVIDER,
        *META_MESSAGING_PROVIDERS,
    }
)
