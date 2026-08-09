"""Provider-neutral secret resolution for integration authentication."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol


class SecretResolver(Protocol):
    """Resolves an internal secret reference without exposing its value."""

    def resolve(self, reference: str | None) -> str | None: ...


class EnvironmentSecretResolver:
    """Resolves a reference as an environment-variable name.

    The reference is stored with the integration account while the value stays
    in runtime environment configuration. Future secret-manager adapters can
    implement the same protocol without changing webhook verification.
    """

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.environment = environment if environment is not None else os.environ

    def resolve(self, reference: str | None) -> str | None:
        if not reference:
            return None
        return self.environment.get(reference)
