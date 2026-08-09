"""Policy for safe integration secret references."""

from __future__ import annotations

import re


class SecretReferenceValidationError(ValueError):
    """Raised when a reference is outside the integration secret namespace."""


class IntegrationSecretReferencePolicy:
    """Allow only explicit environment names reserved for integrations.

    Integration accounts store references, not secret values. Restricting those
    references prevents an account record from being used to read arbitrary
    process environment variables through the environment secret resolver.
    """

    prefix = "INTEGRATION_SECRET_"
    _pattern = re.compile(r"^INTEGRATION_SECRET_[A-Z][A-Z0-9_]{0,200}$")

    def validate(self, reference: str) -> str:
        if not self._pattern.fullmatch(reference):
            raise SecretReferenceValidationError(
                "Secret reference is not allowed"
            )
        return reference

    def is_allowed(self, reference: str | None) -> bool:
        if reference is None:
            return False
        try:
            self.validate(reference)
        except SecretReferenceValidationError:
            return False
        return True
