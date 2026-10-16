from fastapi import APIRouter

from api.v1.endpoints import datasets, experiments, health

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router)
api_v1_router.include_router(datasets.router)
api_v1_router.include_router(experiments.router)
