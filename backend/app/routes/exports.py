"""Exports — generate + download the per-run CSVs.

POST /api/runs/{run_id}/exports triggers the export service, which produces
both `<timestamp>_main.csv` and `<timestamp>_bdr_verify.csv` (spec §11.3).

GET /api/exports/{export_id}/download streams the file. We use FileResponse
rather than reading-then-returning so large CSVs don't sit in memory.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Export, User
from app.routes._common import acting_user
from app.services.export_service import export_run

router = APIRouter(tags=["exports"])


class ExportOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    csv_format: str
    file_path: str
    row_count: int
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class ExportSummary(BaseModel):
    main: ExportOut
    bdr_verify: ExportOut
    skipped: dict[str, int]


@router.post("/api/runs/{run_id}/exports", response_model=ExportSummary)
def create_export(
    run_id: uuid.UUID,
    user: User = Depends(acting_user),
    db: Session = Depends(get_db),
) -> ExportSummary:
    try:
        result = export_run(db=db, run_id=run_id, created_by=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    main = db.query(Export).filter(Export.id == result.main_export_id).first()
    bdr = db.query(Export).filter(Export.id == result.bdr_export_id).first()
    assert main is not None and bdr is not None
    return ExportSummary(
        main=ExportOut.model_validate(main),
        bdr_verify=ExportOut.model_validate(bdr),
        skipped=result.skipped,
    )


@router.get("/api/runs/{run_id}/exports", response_model=list[ExportOut])
def list_exports(run_id: uuid.UUID, db: Session = Depends(get_db)) -> list[ExportOut]:
    rows = (
        db.query(Export)
        .filter(Export.run_id == run_id)
        .order_by(Export.created_at.desc())
        .all()
    )
    return [ExportOut.model_validate(e) for e in rows]


@router.get("/api/exports/{export_id}/download")
def download_export(export_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    export = db.query(Export).filter(Export.id == export_id).first()
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")
    path = Path(export.file_path)
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail=f"export file is gone (was at {export.file_path!r})",
        )
    download_name = f"{export.csv_format}_{export.id}.csv"
    return FileResponse(
        path=path,
        media_type="text/csv",
        filename=download_name,
    )
