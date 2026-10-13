from fastapi import APIRouter

from api.v1.endpoints import health

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router)
