from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import PromptVersion

app = FastAPI(
    title="ProspectForge API",
    version="0.0.0",
    description="Acme's contact-sourcing tool. Phase 0 scaffold; endpoints land per spec §18 DAG.",
)


class HealthResponse(BaseModel):
    status: str
    env: str
    db: str
    prompt_versions_seeded: int


class RootResponse(BaseModel):
    name: str
    version: str
    docs: str


@app.get("/", response_model=RootResponse)
def root() -> RootResponse:
    return RootResponse(name="ProspectForge API", version=app.version, docs="/docs")


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    """Liveness + DB reachability + smoke check that the seed data exists."""
    db_status = "ok"
    seeded = 0
    try:
        db.execute(text("SELECT 1"))
        seeded = db.scalar(select(PromptVersion).limit(1)) and db.query(PromptVersion).count() or 0
    except Exception as exc:  # surface failure to the dashboard
        db_status = f"error: {exc.__class__.__name__}"
    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        env=settings.app_env,
        db=db_status,
        prompt_versions_seeded=seeded,
    )
