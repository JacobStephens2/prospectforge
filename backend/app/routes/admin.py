"""Admin endpoints — operator-only views beyond the normal review workflow.

Mounted under /api/admin. All routes require an active User (current_user
already gates that); we don't add a separate role gate yet because the
whitelist IS the admin list at this stage (spec §3 v1.1 will add SSO +
roles).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models import User
from app.routes.auth import current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


MEMLOG_PATH = Path("/var/log/prospectforge_memlog.csv")
PROC_MEMINFO = Path("/proc/meminfo")


# ---------- schemas ----------


class MemorySnapshot(BaseModel):
    """One sample. KB units so we don't lose precision; UI divides."""

    iso_ts: str
    mem_total_kb: int
    mem_available_kb: int
    mem_free_kb: int
    swap_total_kb: int
    swap_free_kb: int
    buffers_kb: int
    cached_kb: int


class MemorySummary(BaseModel):
    """Roll-up over the loaded history window."""

    sample_count: int
    window_start: str | None
    window_end: str | None
    peak_used_kb: int
    avg_used_kb: int
    current_used_kb: int
    mem_total_kb: int
    headroom_pct: float       # 100 * (1 - peak_used / mem_total)


class MemoryResponse(BaseModel):
    current: MemorySnapshot | None
    history: list[MemorySnapshot]
    summary: MemorySummary | None


# ---------- helpers ----------


_MEMINFO_RE = re.compile(r"^([A-Za-z_()]+):\s+(\d+)\s+kB$")


def _read_proc_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a dict of kB values."""
    out: dict[str, int] = {}
    if not PROC_MEMINFO.exists():
        return out
    for line in PROC_MEMINFO.read_text().splitlines():
        m = _MEMINFO_RE.match(line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def _current_snapshot() -> MemorySnapshot | None:
    """Live /proc/meminfo read — what the kernel sees right now."""
    info = _read_proc_meminfo()
    if not info:
        return None
    from datetime import datetime, timezone

    return MemorySnapshot(
        iso_ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        mem_total_kb=info.get("MemTotal", 0),
        mem_available_kb=info.get("MemAvailable", 0),
        mem_free_kb=info.get("MemFree", 0),
        swap_total_kb=info.get("SwapTotal", 0),
        swap_free_kb=info.get("SwapFree", 0),
        buffers_kb=info.get("Buffers", 0),
        cached_kb=info.get("Cached", 0),
    )


def _read_history(limit: int) -> list[MemorySnapshot]:
    """Load the most recent `limit` CSV rows. Empty list if no log yet."""
    if not MEMLOG_PATH.exists():
        return []
    rows: list[MemorySnapshot] = []
    with MEMLOG_PATH.open() as f:
        for r in csv.DictReader(f):
            try:
                rows.append(
                    MemorySnapshot(
                        iso_ts=r["iso_ts"],
                        mem_total_kb=int(r["mem_total_kb"]),
                        mem_available_kb=int(r["mem_available_kb"]),
                        mem_free_kb=int(r["mem_free_kb"]),
                        swap_total_kb=int(r["swap_total_kb"]),
                        swap_free_kb=int(r["swap_free_kb"]),
                        buffers_kb=int(r["buffers_kb"]),
                        cached_kb=int(r["cached_kb"]),
                    )
                )
            except (KeyError, ValueError):
                continue
    # Keep most recent `limit`; CSV is appended chronologically.
    return rows[-limit:] if limit > 0 else rows


def _summarize(history: list[MemorySnapshot], current: MemorySnapshot | None) -> MemorySummary | None:
    if not history and current is None:
        return None
    rows = list(history)
    if current is not None:
        rows.append(current)
    if not rows:
        return None
    # "Used" = total - available. Matches what `free -h` calls "used" in the
    # MemAvailable era — counts disposable cache as free.
    used = [r.mem_total_kb - r.mem_available_kb for r in rows]
    total = max((r.mem_total_kb for r in rows), default=0)
    peak = max(used)
    avg = sum(used) // len(used)
    current_used = (current.mem_total_kb - current.mem_available_kb) if current else used[-1]
    headroom = 100.0 * (1 - (peak / total)) if total else 0.0
    return MemorySummary(
        sample_count=len(rows),
        window_start=history[0].iso_ts if history else (current.iso_ts if current else None),
        window_end=history[-1].iso_ts if history else (current.iso_ts if current else None),
        peak_used_kb=peak,
        avg_used_kb=avg,
        current_used_kb=current_used,
        mem_total_kb=total,
        headroom_pct=round(headroom, 1),
    )


# ---------- routes ----------


@router.get("/memory", response_model=MemoryResponse)
def get_memory(
    limit: int = 2016,  # 7 days at 5-min cadence
    _user: User = Depends(current_user),
) -> MemoryResponse:
    """Memory usage right now + recent samples + a rolling summary.

    `limit` caps how many historical rows the response carries; defaults
    to 7 days at 5-minute cadence (12/hr × 24 × 7 = 2016). Bump it to read
    further back when sysstat/the CSV has retained more.
    """
    if limit < 0 or limit > 20000:
        raise HTTPException(status_code=400, detail="limit out of range")
    current = _current_snapshot()
    history = _read_history(limit)
    summary = _summarize(history, current)
    return MemoryResponse(current=current, history=history, summary=summary)
