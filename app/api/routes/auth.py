from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import AuthenticatedPrincipalDep, SessionDep, SettingsDep
from app.models import User
from app.schemas import AccessTokenRead, AuthLoginCreate, AuthRegistrationCreate, UserRead
from app.services.authentication import (
    AuthenticationService,
    InvalidCredentialsError,
    PasswordPolicyError,
)
from app.services.identity_memberships import (
    DuplicateUserEmailError,
    UserIdentityValidationError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(
    payload: AuthRegistrationCreate,
    session: SessionDep,
    settings: SettingsDep,
) -> UserRead:
    try:
        user = AuthenticationService(session, settings).register(
            email=payload.email,
            password=payload.password.get_secret_value(),
            display_name=payload.display_name,
        )
    except (PasswordPolicyError, UserIdentityValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateUserEmailError as exc:
        raise HTTPException(status_code=409, detail="A user with this email already exists") from exc
    return UserRead.model_validate(user)


@router.post("/login", response_model=AccessTokenRead)
def login(
    payload: AuthLoginCreate,
    session: SessionDep,
    settings: SettingsDep,
) -> AccessTokenRead:
    service = AuthenticationService(session, settings)
    try:
        user = service.authenticate(
            email=payload.email,
            password=payload.password.get_secret_value(),
        )
        token = service.issue_access_token(user)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return AccessTokenRead(
        access_token=token,
        expires_in=settings.auth_token_expiration_seconds,
    )


@router.get("/me", response_model=UserRead)
def get_me(
    principal: AuthenticatedPrincipalDep,
    session: SessionDep,
) -> UserRead:
    user = session.get(User, principal.user_id)
    # The authentication dependency resolved this exact active user first.
    assert user is not None
    return UserRead.model_validate(user)
