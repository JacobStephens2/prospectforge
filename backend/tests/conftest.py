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

import pytest


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Bring the test DB up to head before any test executes."""
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd="/app")
