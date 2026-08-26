"""Stable provider identifiers and safe connection requirements.

The requirements in this module describe HIRI's implemented integration
contracts only.  They never contain credential values or provider endpoints.
"""

from dataclasses import dataclass

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

API_ACCESS_TOKEN_PURPOSE = "api_access_token"
API_REFRESH_TOKEN_PURPOSE = "api_refresh_token"
WEBHOOK_APP_SECRET_PURPOSE = "webhook_app_secret"
WEBHOOK_VERIFY_TOKEN_PURPOSE = "webhook_verify_token"


@dataclass(frozen=True)
class IntegrationProviderRequirements:
    """Provider-neutral configuration metadata for one supported channel mode."""

    provider: str
    auth_mode: str | None
    required_credential_purposes: frozenset[str]
    allowed_credential_purposes: frozenset[str]
    external_identity_required: bool
    native_delivery_adapter: bool
    validation_credential_purposes: frozenset[str] = frozenset()


_META_CREDENTIAL_PURPOSES = frozenset(
    {
        API_ACCESS_TOKEN_PURPOSE,
        WEBHOOK_APP_SECRET_PURPOSE,
        WEBHOOK_VERIFY_TOKEN_PURPOSE,
    }
)
_TIKTOK_CREDENTIAL_PURPOSES = frozenset(
    {
        API_ACCESS_TOKEN_PURPOSE,
        API_REFRESH_TOKEN_PURPOSE,
        WEBHOOK_APP_SECRET_PURPOSE,
    }
)
_LEGACY_GENERIC_CREDENTIAL_PURPOSES = frozenset(
    {
        API_ACCESS_TOKEN_PURPOSE,
        API_REFRESH_TOKEN_PURPOSE,
        WEBHOOK_APP_SECRET_PURPOSE,
        WEBHOOK_VERIFY_TOKEN_PURPOSE,
    }
)

_PROVIDER_REQUIREMENTS = {
    (GENERIC_HMAC_PROVIDER, None): IntegrationProviderRequirements(
        provider=GENERIC_HMAC_PROVIDER,
        auth_mode=None,
        required_credential_purposes=frozenset(),
        allowed_credential_purposes=_LEGACY_GENERIC_CREDENTIAL_PURPOSES,
        external_identity_required=False,
        native_delivery_adapter=True,
    ),
    (GENERIC_WEBHOOK_PROVIDER, None): IntegrationProviderRequirements(
        provider=GENERIC_WEBHOOK_PROVIDER,
        auth_mode=None,
        required_credential_purposes=frozenset(),
        allowed_credential_purposes=_LEGACY_GENERIC_CREDENTIAL_PURPOSES,
        external_identity_required=False,
        native_delivery_adapter=True,
    ),
    (WHATSAPP_CLOUD_PROVIDER, None): IntegrationProviderRequirements(
        provider=WHATSAPP_CLOUD_PROVIDER,
        auth_mode=None,
        required_credential_purposes=_META_CREDENTIAL_PURPOSES,
        allowed_credential_purposes=_META_CREDENTIAL_PURPOSES,
        external_identity_required=True,
        native_delivery_adapter=True,
        validation_credential_purposes=frozenset({API_ACCESS_TOKEN_PURPOSE}),
    ),
    (FACEBOOK_MESSENGER_PROVIDER, None): IntegrationProviderRequirements(
        provider=FACEBOOK_MESSENGER_PROVIDER,
        auth_mode=None,
        required_credential_purposes=_META_CREDENTIAL_PURPOSES,
        allowed_credential_purposes=_META_CREDENTIAL_PURPOSES,
        external_identity_required=True,
        native_delivery_adapter=True,
    ),
    (
        INSTAGRAM_DM_PROVIDER,
        INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE,
    ): IntegrationProviderRequirements(
        provider=INSTAGRAM_DM_PROVIDER,
        auth_mode=INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE,
        required_credential_purposes=_META_CREDENTIAL_PURPOSES,
        allowed_credential_purposes=_META_CREDENTIAL_PURPOSES,
        external_identity_required=True,
        native_delivery_adapter=True,
    ),
    (INSTAGRAM_DM_PROVIDER, INSTAGRAM_LOGIN_AUTH_MODE): IntegrationProviderRequirements(
        provider=INSTAGRAM_DM_PROVIDER,
        auth_mode=INSTAGRAM_LOGIN_AUTH_MODE,
        required_credential_purposes=_META_CREDENTIAL_PURPOSES,
        allowed_credential_purposes=_META_CREDENTIAL_PURPOSES,
        external_identity_required=True,
        native_delivery_adapter=True,
        validation_credential_purposes=frozenset({API_ACCESS_TOKEN_PURPOSE}),
    ),
    (TIKTOK_DM_PROVIDER, None): IntegrationProviderRequirements(
        provider=TIKTOK_DM_PROVIDER,
        auth_mode=None,
        required_credential_purposes=frozenset(
            {API_ACCESS_TOKEN_PURPOSE, WEBHOOK_APP_SECRET_PURPOSE}
        ),
        allowed_credential_purposes=_TIKTOK_CREDENTIAL_PURPOSES,
        external_identity_required=True,
        native_delivery_adapter=True,
    ),
}

EXCLUSIVE_ACTIVE_IDENTITY_PROVIDERS = frozenset(
    {
        WHATSAPP_CLOUD_PROVIDER,
        FACEBOOK_MESSENGER_PROVIDER,
        INSTAGRAM_DM_PROVIDER,
        TIKTOK_DM_PROVIDER,
    }
)


def get_provider_requirements(
    provider: str,
    auth_mode: str | None,
) -> IntegrationProviderRequirements | None:
    """Return requirements for an exact allowlisted provider/auth-mode pair."""

    # Task 291 established Facebook Login as the durable compatibility default
    # for historical Instagram rows whose auth mode was not recorded yet.
    if provider == INSTAGRAM_DM_PROVIDER and auth_mode is None:
        auth_mode = INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE
    return _PROVIDER_REQUIREMENTS.get((provider, auth_mode))

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
