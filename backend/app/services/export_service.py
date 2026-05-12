"""CSV export — the gate between approved contacts and Riley's outbound sender.

Spec §11 + the v2 hard rules in §5.3:

  - review_status must be 'approved' (or 'edited' — also a green light)
  - dedupe_status must NOT be in (exact, existing_customer, suppressed)
  - evidence_verified must be 'verified' or 'fuzzy'
  - The contact must not already appear in export_rows by either contact_id
    OR normalized email — re-export requires an explicit override (logged
    to review_actions)

Two output files per export (spec §11.3):

  - <run>_main.csv         — direct emails, ready for Riley's sender
  - <run>_bdr_verify.csv  — email_confidence='pattern_guess', BDR verifies manually

Columns match `/srv/orchestration/tasks/prospectforge_csv_schema_proposal.md`,
which mirrors CRM's `tblGroupInfo` import shape (the CivicIQ precedent).

File layout: /var/lib/prospectforge/exports/<run_id>/<timestamp>_<format>.csv
with UTF-8 BOM (matches CRM's existing export — Excel double-click works).
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Contact,
    Export,
    ExportRow,
    Organization,
    ProspectRun,
    Source,
    Suppression,
)

log = logging.getLogger(__name__)


# ---------- output shape ----------


# Order matches the CSV proposal; changing this breaks downstream import.
CSV_COLUMNS = [
    "Name",
    "School",
    "Title",
    "Email",
    "Phone",
    "Address1",
    "City",
    "State",
    "Zip",
    "Website",
    "Country",
    "SchoolType",
    "SchoolGrade",
    "TeacherSubject",
    "SalesLeadType",
    "SalesLeadSource",
    "SourceURL",
    "EvidenceExcerpt",
    "SegmentTag",
    "Comments",
]


# ---------- result types ----------


@dataclass(frozen=True)
class ExportResult:
    """Outcome of one run-export operation. Both files always written even
    if one is empty — callers shouldn't have to handle a missing file."""

    main_export_id: uuid.UUID
    main_file_path: str
    main_row_count: int
    bdr_export_id: uuid.UUID
    bdr_file_path: str
    bdr_row_count: int
    skipped: dict[str, int]   # reason → count, for the operator runbook


# ---------- pre-export gate ----------


_BLOCKING_DEDUPE_STATUSES = frozenset(
    {"exact", "existing_customer", "suppressed"}
)


def _email_already_exported(db: Session, email: str | None) -> bool:
    if not email:
        return False
    return db.execute(
        select(ExportRow.id)
        .where(ExportRow.exported_email == email.lower())
        .limit(1)
    ).first() is not None


def _contact_already_exported(db: Session, contact_id: uuid.UUID) -> bool:
    return db.execute(
        select(ExportRow.id)
        .where(ExportRow.contact_id == contact_id)
        .limit(1)
    ).first() is not None


def _is_suppressed(db: Session, email: str | None) -> bool:
    """Belt-and-braces: re-check suppression even though dedupe should
    have caught it. Morgan may have added a new suppression after the run."""
    if not email:
        return False
    if db.query(Suppression).filter(
        Suppression.type == "email",
        Suppression.value_lower == email.lower(),
    ).first():
        return True
    domain = email.split("@", 1)[1].lower() if "@" in email else None
    if domain and db.query(Suppression).filter(
        Suppression.type == "domain",
        Suppression.value_lower == domain,
    ).first():
        return True
    return False


def _eligible(db: Session, contact: Contact) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok."""
    if contact.review_status not in ("approved", "edited"):
        return False, f"review_status={contact.review_status}"
    if contact.dedupe_status in _BLOCKING_DEDUPE_STATUSES:
        return False, f"dedupe_status={contact.dedupe_status}"
    if contact.evidence_verified not in ("verified", "fuzzy"):
        return False, f"evidence_verified={contact.evidence_verified}"
    if _is_suppressed(db, contact.email):
        return False, "suppressed (live check)"
    if _contact_already_exported(db, contact.id):
        return False, "contact already exported"
    if contact.email and _email_already_exported(db, contact.email):
        return False, "email already exported in prior run"
    return True, ""


# ---------- row construction ----------


def _row_hash(values: dict[str, str | None]) -> str:
    """Stable hash for ExportRow.row_hash — used for audit/dedupe later."""
    parts = "|".join(str(values.get(c) or "") for c in CSV_COLUMNS)
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _normalize_state(state: str | None) -> str | None:
    if not state:
        return None
    s = state.strip().upper()
    return s if re.fullmatch(r"[A-Z]{2}", s) else None


def _format_comments(
    *,
    source_url: str,
    evidence_excerpt: str | None,
    segment_tag: str | None,
    title: str | None,
    school_type: str | None,
    run_id: uuid.UUID,
) -> str:
    """Multi-line key:value block — matches the CivicIQ→CRM precedent
    (`crm/mcrm/ajax/createGroupRequestFromCivicIQ.php`)."""
    lines = []
    if title:
        lines.append(f"Designation: {title}")
    if segment_tag:
        lines.append(f"Segment: {segment_tag}")
    if school_type:
        lines.append(f"School type: {school_type}")
    lines.append("Source: ProspectForge")
    lines.append(f"Source URL: {source_url}")
    if evidence_excerpt:
        # Keep evidence brief; spec proposes it lands in Comments AND its own column
        truncated = evidence_excerpt.strip().replace("\n", " ")
        if len(truncated) > 240:
            truncated = truncated[:237] + "..."
        lines.append(f"Evidence: {truncated}")
    lines.append(f"ProspectForge Run: {run_id}")
    return "\n".join(lines)


def _row_for_contact(
    *,
    contact: Contact,
    organization: Organization | None,
    run: ProspectRun,
    source: Source,
) -> dict[str, str | None]:
    """Map ORM rows → CSV column values."""
    return {
        "Name": contact.name,
        "School": organization.name if organization else None,
        "Title": contact.title,
        "Email": contact.email,
        "Phone": contact.phone,
        "Address1": organization.address1 if organization else None,
        "City": organization.city if organization else None,
        "State": _normalize_state(
            organization.state if organization else None
        ),
        "Zip": organization.zip if organization else None,
        "Website": organization.website if organization else (
            organization.domain if organization else None
        ),
        "Country": (organization.country if organization else None) or "USA",
        "SchoolType": organization.school_type if organization else None,
        "SchoolGrade": organization.school_grade if organization else None,
        "TeacherSubject": contact.segment_tag,
        "SalesLeadType": "ProspectForge",
        "SalesLeadSource": source.name,
        "SourceURL": source.url,
        "EvidenceExcerpt": contact.evidence_excerpt,
        "SegmentTag": contact.segment_tag,
        "Comments": _format_comments(
            source_url=source.url,
            evidence_excerpt=contact.evidence_excerpt,
            segment_tag=contact.segment_tag,
            title=contact.title,
            school_type=organization.school_type if organization else None,
            run_id=run.id,
        ),
    }


# ---------- writer ----------


def _write_csv(file_path: Path, rows: list[dict[str, str | None]]) -> None:
    """UTF-8 with BOM, RFC 4180 CRLF — matches CRM's existing export."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    buf.write("﻿")  # BOM
    writer = csv.DictWriter(
        buf,
        fieldnames=CSV_COLUMNS,
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({c: (r.get(c) or "") for c in CSV_COLUMNS})
    file_path.write_text(buf.getvalue(), encoding="utf-8")


def export_run(
    *,
    db: Session,
    run_id: uuid.UUID,
    created_by: uuid.UUID,
    export_root: str = "/var/lib/prospectforge/exports",
) -> ExportResult:
    """Generate the main + BDR-verify CSVs for one run. Records ExportRows.

    Both files are always written, even when empty — callers and the
    operator runbook are simpler when the path is unconditional.
    """
    run = db.get(ProspectRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    source = db.get(Source, run.source_id)
    if source is None:
        raise ValueError(f"source {run.source_id} not found (orphaned run?)")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(export_root) / str(run.id)

    # Walk all contacts in the run. Eligibility check is per-row; we collect
    # main vs. bdr_verify buckets and the skip reasons for the runbook.
    contacts = (
        db.query(Contact).filter(Contact.run_id == run.id).order_by(Contact.created_at).all()
    )
    org_cache: dict[uuid.UUID, Organization | None] = {}

    main_rows: list[dict[str, str | None]] = []
    bdr_rows: list[dict[str, str | None]] = []
    main_contact_ids: list[uuid.UUID] = []
    bdr_contact_ids: list[uuid.UUID] = []
    skipped: dict[str, int] = {}

    for contact in contacts:
        ok, reason = _eligible(db, contact)
        if not ok:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        org = None
        if contact.organization_id is not None:
            if contact.organization_id not in org_cache:
                org_cache[contact.organization_id] = db.get(Organization, contact.organization_id)
            org = org_cache[contact.organization_id]
        row = _row_for_contact(contact=contact, organization=org, run=run, source=source)
        if contact.email_confidence == "pattern_guess":
            bdr_rows.append(row)
            bdr_contact_ids.append(contact.id)
        else:
            main_rows.append(row)
            main_contact_ids.append(contact.id)

    main_path = run_dir / f"{timestamp}_main.csv"
    bdr_path = run_dir / f"{timestamp}_bdr_verify.csv"
    _write_csv(main_path, main_rows)
    _write_csv(bdr_path, bdr_rows)

    # Persist Export + ExportRow rows. We commit at the end so a write failure
    # mid-loop leaves the DB consistent (no half-recorded export).
    main_export = Export(
        run_id=run.id,
        csv_format="main",
        file_path=str(main_path),
        row_count=len(main_rows),
        created_by=created_by,
    )
    bdr_export = Export(
        run_id=run.id,
        csv_format="bdr_verify",
        file_path=str(bdr_path),
        row_count=len(bdr_rows),
        created_by=created_by,
    )
    db.add_all([main_export, bdr_export])
    db.flush()

    for cid, row in zip(main_contact_ids, main_rows):
        db.add(
            ExportRow(
                export_id=main_export.id,
                contact_id=cid,
                row_hash=_row_hash(row),
                exported_email=(row.get("Email") or "").lower() or None,
            )
        )
    for cid, row in zip(bdr_contact_ids, bdr_rows):
        db.add(
            ExportRow(
                export_id=bdr_export.id,
                contact_id=cid,
                row_hash=_row_hash(row),
                exported_email=(row.get("Email") or "").lower() or None,
            )
        )

    db.commit()

    return ExportResult(
        main_export_id=main_export.id,
        main_file_path=str(main_path),
        main_row_count=len(main_rows),
        bdr_export_id=bdr_export.id,
        bdr_file_path=str(bdr_path),
        bdr_row_count=len(bdr_rows),
        skipped=skipped,
    )
