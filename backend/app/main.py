from fastapi import FastAPI
from pydantic import BaseModel

from app.config import settings

app = FastAPI(
    title="ProspectForge API",
    version="0.0.0",
    description="Acme's contact-sourcing tool. Phase 0 scaffold; endpoints land per spec §18 DAG.",
)


class HealthResponse(BaseModel):
    status: str
    env: str


class RootResponse(BaseModel):
    name: str
    version: str
    docs: str


@app.get("/", response_model=RootResponse)
def root() -> RootResponse:
    return RootResponse(name="ProspectForge API", version=app.version, docs="/docs")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", env=settings.app_env)
