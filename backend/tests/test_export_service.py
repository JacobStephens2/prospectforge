"""CSV export service tests.

Exercises the pre-export gate (spec §11.2) and the file shape against
both buckets (main + bdr_verify). All tests use a tmp_path-scoped export
directory so we don't pollute /var/lib.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import SessionLocal
from app.models import (
    Contact,
    Export,
    ExportRow,
    Organization,
    ProspectRun,
    Source,
    Suppression,
    User,
)
from app.services.export_service import CSV_COLUMNS, export_run


@pytest.fixture()
def fixture_run_with_contacts(tmp_path):
    """A run with one approved+verified contact, one rejected, one with
    blocking dedupe_status, one pattern_guess. Returns (run_id, user_id,
    export_root, contact_ids).

    Uses a unique email domain per fixture instance so export_rows from
    prior test runs don't trip the "already exported in prior run" check —
    in real use the ExportRow table is global, not per-run.
    """
    domain = f"phoenixisd-{uuid.uuid4().hex[:8]}.example.org"

    db = SessionLocal()
    try:
        user = User(
            email=f"exp-{uuid.uuid4()}@acme-travel.example",
            full_name="Exp User",
            role="admin",
        )
        db.add(user)
        db.flush()

        source = Source(
            name="Northwind Member Directory",
            url="https://example.com/northwind",
            created_by=user.id,
        )
        db.add(source)
        db.flush()

        run = ProspectRun(
            source_id=source.id,
            created_by=user.id,
            max_cost_cents=500,
            budget_approved_by=user.id,
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()

        org = Organization(
            run_id=run.id,
            name="Phoenix ISD",
            domain=domain,
            state="AZ",
            city="Phoenix",
            country="USA",
            school_type="Public",
            school_grade="9-12",
        )
        db.add(org)
        db.flush()

        # 1. Approved + verified → goes to main.csv
        c_approved = Contact(
            run_id=run.id,
            organization_id=org.id,
            name="Jane Smith",
            title="Band Director",
            email=f"jane.smith@{domain}",
            phone="(602) 555-0100",
            email_confidence="verified",
            evidence_excerpt=f"Band Director: Jane Smith. Email: jane.smith@{domain}",
            evidence_verified="verified",
            segment_tag="K-12 band",
            review_status="approved",
            reviewed_by=user.id,
            reviewed_at=datetime.now(timezone.utc),
            dedupe_status="new",
        )

        # 2. Rejected → skipped
        c_rejected = Contact(
            run_id=run.id,
            organization_id=org.id,
            name="Bob Rejected",
            email=f"bob@{domain}",
            evidence_excerpt="Bob Rejected works at Phoenix",
            evidence_verified="verified",
            review_status="rejected",
            dedupe_status="new",
        )

        # 3. Approved BUT existing_customer → skipped (block)
        c_blocked_dedupe = Contact(
            run_id=run.id,
            organization_id=org.id,
            name="Carlos Customer",
            email=f"carlos@{domain}",
            evidence_excerpt="Carlos Customer leads the orchestra",
            evidence_verified="verified",
            review_status="approved",
            reviewed_by=user.id,
            dedupe_status="existing_customer",
        )

        # 4. Approved BUT evidence not verified → skipped
        c_bad_evidence = Contact(
            run_id=run.id,
            organization_id=org.id,
            name="Dana Unverified",
            email=f"dana@{domain}",
            evidence_excerpt="Dana invented backstory",
            evidence_verified="failed",
            review_status="approved",
            reviewed_by=user.id,
            dedupe_status="new",
        )

        # 5. Approved + pattern_guess → goes to bdr_verify.csv
        c_pattern_guess = Contact(
            run_id=run.id,
            organization_id=org.id,
            name="Eve Pattern",
            email=f"eve.pattern@{domain}",
            email_confidence="pattern_guess",
            evidence_excerpt="Eve Pattern teaches at Phoenix ISD according to the directory",
            evidence_verified="verified",
            review_status="approved",
            reviewed_by=user.id,
            dedupe_status="new",
            segment_tag="K-12 choir",
        )

        # 6. Approved + edited (also exportable per spec)
        c_edited = Contact(
            run_id=run.id,
            organization_id=org.id,
            name="Frank Edited",
            email=f"frank.edited@{domain}",
            email_confidence="verified",
            evidence_excerpt="Frank Edited runs the theater program at Phoenix",
            evidence_verified="verified",
            review_status="edited",
            reviewed_by=user.id,
            dedupe_status="new",
        )

        db.add_all(
            [c_approved, c_rejected, c_blocked_dedupe, c_bad_evidence, c_pattern_guess, c_edited]
        )
        db.commit()

        result = {
            "run_id": run.id,
            "user_id": user.id,
            "export_root": str(tmp_path / "exports"),
            "domain": domain,
            "approved": c_approved.id,
            "rejected": c_rejected.id,
            "blocked_dedupe": c_blocked_dedupe.id,
            "bad_evidence": c_bad_evidence.id,
            "pattern_guess": c_pattern_guess.id,
            "edited": c_edited.id,
        }
    finally:
        db.close()
    return result


def _read_csv(path: str) -> list[dict[str, str]]:
    """Read a CSV file that was written with BOM + CRLF."""
    content = Path(path).read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(content)))


def test_export_writes_two_files(fixture_run_with_contacts) -> None:
    db = SessionLocal()
    try:
        result = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()
    assert Path(result.main_file_path).exists()
    assert Path(result.bdr_file_path).exists()


def test_main_csv_includes_only_eligible_rows(fixture_run_with_contacts) -> None:
    db = SessionLocal()
    try:
        result = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()

    rows = _read_csv(result.main_file_path)
    names = sorted(r["Name"] for r in rows)
    # approved (Jane), edited (Frank) → in.
    # pattern_guess (Eve) → in bdr_verify, not main.
    # rejected (Bob), existing_customer (Carlos), failed evidence (Dana) → out.
    assert names == ["Frank Edited", "Jane Smith"]
    assert result.main_row_count == 2


def test_bdr_verify_csv_has_pattern_guess(fixture_run_with_contacts) -> None:
    db = SessionLocal()
    try:
        result = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()

    rows = _read_csv(result.bdr_file_path)
    names = [r["Name"] for r in rows]
    assert names == ["Eve Pattern"]
    assert result.bdr_row_count == 1


def test_columns_match_spec_order(fixture_run_with_contacts) -> None:
    db = SessionLocal()
    try:
        result = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()

    rows = _read_csv(result.main_file_path)
    assert list(rows[0].keys()) == CSV_COLUMNS


def test_jane_row_has_expected_values(fixture_run_with_contacts) -> None:
    db = SessionLocal()
    try:
        result = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()

    rows = _read_csv(result.main_file_path)
    jane = next(r for r in rows if r["Name"] == "Jane Smith")
    assert jane["School"] == "Phoenix ISD"
    assert jane["Title"] == "Band Director"
    assert jane["Email"] == f"jane.smith@{fixture_run_with_contacts['domain']}"
    assert jane["State"] == "AZ"
    assert jane["SchoolType"] == "Public"
    assert jane["SalesLeadType"] == "ProspectForge"
    assert jane["SalesLeadSource"] == "Northwind Member Directory"
    assert jane["SegmentTag"] == "K-12 band"
    assert "Source: ProspectForge" in jane["Comments"]
    assert "Evidence:" in jane["Comments"]


def test_export_records_export_rows(fixture_run_with_contacts) -> None:
    """ExportRow rows are written so re-export is blocked next time."""
    db = SessionLocal()
    try:
        result = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
        main_rows = (
            db.query(ExportRow).filter(ExportRow.export_id == result.main_export_id).all()
        )
        bdr_rows = (
            db.query(ExportRow).filter(ExportRow.export_id == result.bdr_export_id).all()
        )
    finally:
        db.close()
    assert len(main_rows) == 2
    assert len(bdr_rows) == 1
    emails = {r.exported_email for r in main_rows}
    assert f"jane.smith@{fixture_run_with_contacts['domain']}" in emails


def test_second_export_skips_already_exported(fixture_run_with_contacts) -> None:
    """Run export twice — second time, nothing eligible (contact_id already exported)."""
    db = SessionLocal()
    try:
        first = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
        assert first.main_row_count == 2
        second = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()
    assert second.main_row_count == 0
    assert second.bdr_row_count == 0
    assert any("already exported" in k for k in second.skipped.keys())


def test_suppression_blocks_export_even_if_approved(fixture_run_with_contacts) -> None:
    """Live suppression check beats prior dedupe state."""
    db = SessionLocal()
    try:
        run_id = fixture_run_with_contacts["run_id"]
        user_id = fixture_run_with_contacts["user_id"]
        domain = fixture_run_with_contacts["domain"]
        db.add(
            Suppression(
                type="email",
                value=f"jane.smith@{domain}",
                value_lower=f"jane.smith@{domain}",
                reason="hard bounce",
                added_by=user_id,
            )
        )
        db.commit()
        result = export_run(
            db=db,
            run_id=run_id,
            created_by=user_id,
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()
    rows = _read_csv(result.main_file_path)
    names = [r["Name"] for r in rows]
    # Jane is suppressed, Frank remains.
    assert "Jane Smith" not in names
    assert "Frank Edited" in names


def test_csv_has_bom(fixture_run_with_contacts) -> None:
    """UTF-8 BOM so Excel double-click renders correctly."""
    db = SessionLocal()
    try:
        result = export_run(
            db=db,
            run_id=fixture_run_with_contacts["run_id"],
            created_by=fixture_run_with_contacts["user_id"],
            export_root=fixture_run_with_contacts["export_root"],
        )
    finally:
        db.close()
    raw = Path(result.main_file_path).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
