"""Test fixtures.

These tests are integration tests by design — they run against the real
dockerized Postgres because our models lean on Postgres-specific types
(JSONB, partial unique indexes, trgm). A mock layer would not exercise
the constraints we care about.

Per project memory (feedback_mocking_db_tests): integration tests must hit
a real database, not mocks. This conftest honors that by applying the
canonical Alembic migration before any test runs.
"""

import subprocess
import uuid

import pytest


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Bring the test DB up to head before any test executes."""
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd="/app")


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
