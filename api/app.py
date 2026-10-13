from fastapi import FastAPI

from api.v1.router import api_v1_router


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Arena")
    app.include_router(api_v1_router)
    return app
