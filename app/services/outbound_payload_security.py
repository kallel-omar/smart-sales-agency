"""Provider-neutral safeguards for persisted outbound integration payloads."""

from typing import Any

_FORBIDDEN_OUTBOUND_SECRET_KEYS = {
    "access_token",
    "app_secret",
    "authorization",
    "bearer_token",
    "client_secret",
    "permanent_token",
    "token",
    "verify_token",
    "whatsapp_access_token",
}


class OutboundPayloadSecretError(ValueError):
    """Raised when persisted outbound payload data contains provider credentials."""


def assert_no_outbound_payload_secrets(payload: Any) -> None:
    """Reject provider credentials inside FastAPI-persisted outbound payloads."""

    forbidden = _find_forbidden_secret_key(payload)
    if forbidden is not None:
        raise OutboundPayloadSecretError(
            f"Outbound payload must not contain provider credential key: {forbidden}"
        )


def _find_forbidden_secret_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = _normalize_key(str(key))
            if normalized_key in _FORBIDDEN_OUTBOUND_SECRET_KEYS or normalized_key.endswith(
                ("_secret", "_token")
            ):
                return str(key)
            nested = _find_forbidden_secret_key(nested_value)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for nested_value in value:
            nested = _find_forbidden_secret_key(nested_value)
            if nested is not None:
                return nested
    return None


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")
