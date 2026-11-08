from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from api.v1.docs import errors
from api.v1.utils import (
    client_address,
    get_auth_service,
    throttled,
    unauthorized,
)
from components.auth.exceptions import (
    AuthThrottledError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from components.auth.schemas import (
    LoginRequestSchema,
    RefreshRequestSchema,
    TokenPairSchema,
    ValidateRequestSchema,
    ValidationResultSchema,
)
from components.auth.service import AuthService, TokenPair

router = APIRouter(prefix="/auth", tags=["authentication"])

ServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _pair(pair: TokenPair) -> TokenPairSchema:
    return TokenPairSchema(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/login",
    response_model=TokenPairSchema,
    summary="Sign in",
    description="Exchange email and password for an access and refresh token.",
    responses=errors(401, 429),
)
async def login(
    body: LoginRequestSchema, service: ServiceDep, request: Request
) -> TokenPairSchema:
    try:
        return _pair(
            await service.login(
                body.email, body.password, client_address(request)
            )
        )
    except InvalidCredentialsError:
        raise unauthorized("Invalid credentials") from None
    except AuthThrottledError as exc:
        raise throttled(exc) from None


@router.post(
    "/refresh",
    response_model=TokenPairSchema,
    summary="Refresh tokens",
    description="Rotate a refresh token; the old one stops working.",
    responses=errors(401, 429),
)
async def refresh(
    body: RefreshRequestSchema, service: ServiceDep, request: Request
) -> TokenPairSchema:
    try:
        return _pair(
            await service.refresh(body.refresh_token, client_address(request))
        )
    except InvalidRefreshTokenError:
        raise unauthorized("Invalid refresh token") from None
    except AuthThrottledError as exc:
        raise throttled(exc) from None


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out",
    description="Revoke a refresh token.",
    responses=errors(401),
)
async def logout(body: RefreshRequestSchema, service: ServiceDep) -> Response:
    try:
        await service.logout(body.refresh_token)
    except InvalidRefreshTokenError:
        raise unauthorized("Invalid refresh token") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/validate",
    response_model=ValidationResultSchema,
    summary="Validate an access token",
    description=(
        "Answers 200; an unusable token is reported in the body. A client "
        "address with too many unusable tokens is answered 429."
    ),
    responses=errors(429),
)
async def validate(
    body: ValidateRequestSchema, service: ServiceDep, request: Request
) -> ValidationResultSchema:
    """200 with the verdict in the body, 429 for a locked client address."""
    try:
        result = await service.validate(body.token, client_address(request))
    except AuthThrottledError as exc:
        raise throttled(exc) from None
    return ValidationResultSchema(
        valid=result.valid,
        user_id=result.user_id,
        organization_id=result.organization_id,
        role=result.role,
        expires_at=result.expires_at,
        reason=result.reason,
    )
