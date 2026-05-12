"""Raw HTML archive — per spec §4.5/§15: 90-day retention of full source.

Evidence excerpts stay in the DB indefinitely (they're tiny and load-bearing
for audit). Full raw HTML is fat and PII-adjacent, so we write it to disk and
expect a cron to purge files >90 days old.

Path layout: /var/lib/prospectforge/archive/<run_id>/<page_id>.html
That puts entire runs in one directory, so `find -mtime +90 -delete` is a
trivial nightly cron. Caller passes the path back as SourcePage.raw_html_path.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


def write_raw_html(
    *,
    archive_root: str,
    run_id: uuid.UUID,
    page_id: uuid.UUID,
    body: bytes,
) -> str | None:
    """Persist raw bytes. Returns the absolute path written, or None on error.

    Failures are non-fatal — a crawl that succeeds at the network layer
    but fails to archive is still valuable; we log and continue.
    """
    if not body:
        return None
    try:
        run_dir = Path(archive_root) / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{page_id}.html"
        path.write_bytes(body)
        return str(path)
    except OSError as exc:
        log.warning(
            "archive write failed for run=%s page=%s: %s",
            run_id, page_id, exc,
        )
        return None
