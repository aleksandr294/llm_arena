from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from api.v1.utils import get_auth_service, unauthorized
from components.auth.schemas import (
    LoginRequestSchema,
    RefreshRequestSchema,
    TokenPairSchema,
    ValidateRequestSchema,
    ValidationResultSchema,
)
from components.auth.service import (
    AuthService,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])

ServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _pair(pair: TokenPair) -> TokenPairSchema:
    return TokenPairSchema(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/login", response_model=TokenPairSchema)
async def login(
    body: LoginRequestSchema, service: ServiceDep
) -> TokenPairSchema:
    try:
        return _pair(await service.login(body.email, body.password))
    except InvalidCredentialsError:
        raise unauthorized("Invalid credentials") from None


@router.post("/refresh", response_model=TokenPairSchema)
async def refresh(
    body: RefreshRequestSchema, service: ServiceDep
) -> TokenPairSchema:
    try:
        return _pair(await service.refresh(body.refresh_token))
    except InvalidRefreshTokenError:
        raise unauthorized("Invalid refresh token") from None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequestSchema, service: ServiceDep) -> Response:
    try:
        await service.logout(body.refresh_token)
    except InvalidRefreshTokenError:
        raise unauthorized("Invalid refresh token") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/validate", response_model=ValidationResultSchema)
async def validate(
    body: ValidateRequestSchema, service: ServiceDep
) -> ValidationResultSchema:
    """Always 200: an unusable token is reported in the body."""
    result = await service.validate(body.token)
    return ValidationResultSchema(
        valid=result.valid,
        user_id=result.user_id,
        organization_id=result.organization_id,
        role=result.role,
        expires_at=result.expires_at,
        reason=result.reason,
    )
