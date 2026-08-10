"""Credential verification and signed human-principal resolution for Task 280."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.models import User, UserPasswordCredential
from app.services.identity_memberships import (
    AuthenticatedPrincipal,
    DuplicateUserEmailError,
    IdentityMembershipService,
    UserIdentityValidationError,
    normalize_display_name,
    normalize_user_email,
)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1_024


class PasswordPolicyError(ValueError):
    """Raised when a submitted password is outside the deterministic policy."""


class InvalidCredentialsError(PermissionError):
    """Safe generic failure shared by all login credential errors."""


class InvalidAccessTokenError(PermissionError):
    """Safe generic failure shared by all bearer-token errors."""


class AuthenticationConfigurationError(RuntimeError):
    """Raised when a server cannot safely issue or verify access tokens."""


class PasswordHashingService:
    """Argon2id hashing boundary; plaintext never leaves the calling operation."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher(type=Type.ID)

    @staticmethod
    def validate_password(password: str) -> None:
        if not password:
            raise PasswordPolicyError("Password must not be blank")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise PasswordPolicyError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        if len(password) > MAX_PASSWORD_LENGTH:
            raise PasswordPolicyError(
                f"Password must not exceed {MAX_PASSWORD_LENGTH} characters"
            )

    def hash(self, password: str) -> str:
        self.validate_password(password)
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False


class AuthenticationService:
    """Transactional human registration, login, and short-lived token handling."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.passwords = PasswordHashingService()

    def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None = None,
    ) -> User:
        canonical_email = normalize_user_email(email)
        normalized_display_name = normalize_display_name(display_name)
        password_hash = self.passwords.hash(password)
        if self.session.exec(select(User.id).where(User.email == canonical_email)).first():
            raise DuplicateUserEmailError("A user with this email already exists")

        user = User(email=canonical_email, display_name=normalized_display_name)
        self.session.add(user)
        try:
            self.session.flush()
            self.session.add(
                UserPasswordCredential(user_id=user.id, password_hash=password_hash)
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateUserEmailError("A user with this email already exists") from exc
        self.session.refresh(user)
        return user

    def authenticate(self, *, email: str, password: str) -> User:
        try:
            canonical_email = normalize_user_email(email)
        except UserIdentityValidationError as exc:
            raise InvalidCredentialsError("Invalid credentials") from exc
        user = self.session.exec(select(User).where(User.email == canonical_email)).first()
        if user is None or not user.active:
            raise InvalidCredentialsError("Invalid credentials")
        credential = self.session.exec(
            select(UserPasswordCredential).where(UserPasswordCredential.user_id == user.id)
        ).first()
        if credential is None or not self.passwords.verify(credential.password_hash, password):
            raise InvalidCredentialsError("Invalid credentials")
        return user

    def issue_access_token(self, user: User) -> str:
        if not user.active:
            raise InvalidCredentialsError("Invalid credentials")
        now = datetime.now(UTC)
        payload = {
            "sub": str(user.id),
            "iat": now,
            "exp": now + timedelta(seconds=self.settings.auth_token_expiration_seconds),
            "iss": self.settings.auth_token_issuer,
            "jti": str(uuid4()),
        }
        return jwt.encode(payload, self._token_secret(), algorithm=self.settings.auth_token_algorithm)

    def resolve_principal_from_access_token(self, token: str) -> AuthenticatedPrincipal:
        try:
            claims = jwt.decode(
                token,
                self._token_secret(),
                algorithms=[self.settings.auth_token_algorithm],
                issuer=self.settings.auth_token_issuer,
                options={"require": ["sub", "iat", "exp", "iss"]},
            )
            user_id = UUID(claims["sub"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessTokenError("Invalid bearer authentication") from exc
        try:
            return IdentityMembershipService(self.session).resolve_active_principal(user_id)
        except Exception as exc:
            # Token subjects must always be resolved against current persistence;
            # unknown and inactive users are intentionally indistinguishable.
            raise InvalidAccessTokenError("Invalid bearer authentication") from exc

    def _token_secret(self) -> str:
        secret = self.settings.auth_token_secret.get_secret_value()
        if not secret:
            raise AuthenticationConfigurationError("Authentication token secret is unavailable")
        return secret
