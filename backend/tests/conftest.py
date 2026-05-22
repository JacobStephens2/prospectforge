"""Test fixtures.

These tests are integration tests by design — they run against the real
dockerized Postgres because our models lean on Postgres-specific types
(JSONB, partial unique indexes, trgm). A mock layer would not exercise
the constraints we care about.

Per project memory (feedback_mocking_db_tests): integration tests must hit
a real database, not mocks. This conftest honors that by applying the
canonical Alembic migration before any test runs.

Sandbox safety (added 2026-05-14 after a pytest run leaked ~100 example.com
sources into the shared DB): an autouse session fixture sweeps out any rows
that match the test signature both BEFORE and AFTER the session. Tests
universally use `https://example.com/` URLs and `*-{uuid}@acme-travel.example`
emails, so the signature is unambiguous and the sweep can't touch a real
row Morgan added through the UI. If a future test ever uses a real-world
domain, swap to a transaction-rollback fixture before the next leak.
"""

import subprocess
import uuid

import pytest


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Bring the test DB up to head before any test executes."""
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd="/app")


def _sweep_test_leaks() -> None:
    """Delete every row tests create. Safe to call before AND after.

    Test signature (intentionally broad — matches every synthetic pattern
    the test suite emits today and tolerates new prefixes added later):

      - users.email: any address at @example.com / @example.org, or any
        @acme-travel.example address shaped `<word>-<uuid-or-hex>`. Real
        people use first-initial-lastname; they can't accidentally match.
      - sources: url contains example.com / example.org, OR created_by
        points at one of the synthetic users above.

    Cascade order respects the FK graph (auth_sessions, magic_link_tokens,
    suppressions, then exports → prospect_runs cascade → sources → users).
    Temp tables snapshot the IDs so later DELETEs don't re-evaluate the
    predicate against partially-deleted rows.
    """
    from sqlalchemy import text

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text(
            "CREATE TEMP TABLE _test_users ON COMMIT DROP AS "
            "SELECT id FROM users "
            "WHERE email LIKE '%@example.com' "
            "   OR email LIKE '%@example.org' "
            r"   OR email ~ '^[a-z][a-z0-9]*-[a-z0-9-]{8,}@acme-travel\.com$'"
        ))
        db.execute(text(
            "CREATE TEMP TABLE _test_sources ON COMMIT DROP AS "
            "SELECT id FROM sources "
            "WHERE url LIKE '%example.com%' "
            "   OR url LIKE '%example.org%' "
            "   OR created_by IN (SELECT id FROM _test_users)"
        ))
        db.execute(text(
            "CREATE TEMP TABLE _test_runs ON COMMIT DROP AS "
            "SELECT id FROM prospect_runs "
            "WHERE source_id IN (SELECT id FROM _test_sources) "
            "   OR created_by IN (SELECT id FROM _test_users) "
            "   OR budget_approved_by IN (SELECT id FROM _test_users)"
        ))
        db.execute(text("DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM _test_users)"))
        db.execute(text("DELETE FROM magic_link_tokens WHERE user_id IN (SELECT id FROM _test_users)"))
        db.execute(text("DELETE FROM suppressions WHERE added_by IN (SELECT id FROM _test_users)"))
        db.execute(text(
            "DELETE FROM exports WHERE run_id IN (SELECT id FROM _test_runs) "
            "   OR created_by IN (SELECT id FROM _test_users)"
        ))
        db.execute(text("DELETE FROM prospect_runs WHERE id IN (SELECT id FROM _test_runs)"))
        db.execute(text("DELETE FROM sources WHERE id IN (SELECT id FROM _test_sources)"))
        db.execute(text("DELETE FROM users WHERE id IN (SELECT id FROM _test_users)"))
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def _sandbox_cleanup(_apply_migrations):
    """Wipe test rows before and after the pytest session.

    Depends on `_apply_migrations` so the schema exists before we DELETE.
    Runs before any test (start-of-session) AND after the last test
    (end-of-session), so an aborted prior run can't strand junk that
    confuses the current one.
    """
    _sweep_test_leaks()
    yield
    _sweep_test_leaks()


# ---------- auth: TestClient with current_user overridden ----------


@pytest.fixture
def seeded_user():
    """Create a unique active User row. Returns the ORM object."""
    from app.db import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        u = User(
            email=f"tester-{uuid.uuid4()}@acme-travel.example",
            full_name="Test User",
            role="admin",
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u
    finally:
        db.close()


@pytest.fixture
def authed_client(seeded_user):
    """TestClient with the current_user dependency overridden to seeded_user.

    Cleaner than minting a real session + cookie for every test, and routes
    don't care which path made the user materialize — they only care that
    `current_user` returned one.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routes.auth import current_user

    app.dependency_overrides[current_user] = lambda: seeded_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.fixture
def anon_client():
    """TestClient with NO auth override — `current_user` will 401."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
