"""Budget cap + cost calculation tests (spec §7.1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.models import ProspectRun, Source, User
from app.services.budget import (
    INPUT_COST_CENTS_PER_TOKEN,
    OUTPUT_COST_CENTS_PER_TOKEN,
    OverBudget,
    check_budget_before_call,
    cost_from_usage,
    record_call_cost,
)


def test_cost_from_usage_basic() -> None:
    """1k input + 500 output Sonnet 4.5: 0.3¢ + 0.75¢ = 1.05¢ → 2¢ (ceil)."""
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=500,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    cost = cost_from_usage(usage)
    assert abs(cost.input_cents - 1000 * INPUT_COST_CENTS_PER_TOKEN) < 1e-9
    assert abs(cost.output_cents - 500 * OUTPUT_COST_CENTS_PER_TOKEN) < 1e-9
    # 0.3 + 0.75 = 1.05 → ceil to 2
    assert cost.total_cents == 2


def test_cost_from_usage_with_cache_hits() -> None:
    """Cache reads are cheap: 0.1× input cost."""
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=200,
        cache_creation_input_tokens=2000,  # written once
        cache_read_input_tokens=8000,      # read many times
    )
    cost = cost_from_usage(usage)
    # 100 × 0.0003 + 2000 × 0.000375 + 8000 × 0.00003 + 200 × 0.0015
    # = 0.03 + 0.75 + 0.24 + 0.30 = 1.32¢ → ceil to 2¢
    assert cost.total_cents == 2
    assert cost.cache_read_cents > 0
    assert cost.cache_write_cents > 0


def test_cost_from_usage_dict_shape() -> None:
    """SDK Usage objects work; bare dicts also work (handy for tests)."""
    cost = cost_from_usage({"input_tokens": 1000, "output_tokens": 1000})
    assert cost.total_cents > 0


def test_cost_from_usage_handles_none_fields() -> None:
    """Anthropic omits cache fields when none — must not crash."""
    usage = SimpleNamespace(input_tokens=10, output_tokens=10)
    cost = cost_from_usage(usage)
    assert cost.cache_read_cents == 0
    assert cost.cache_write_cents == 0


# ---------- DB-integrated tests ----------


@pytest.fixture()
def fixture_run():
    """Create a real user/source/run; yields run_id."""
    db = SessionLocal()
    try:
        user = User(
            email=f"budget-{uuid.uuid4()}@acme-travel.example",
            full_name="Budget Test",
            role="admin",
        )
        db.add(user)
        db.flush()
        source = Source(name="Budget Source", url="https://example.com/", created_by=user.id)
        db.add(source)
        db.flush()
        run = ProspectRun(
            source_id=source.id,
            created_by=user.id,
            max_cost_cents=100,
            budget_approved_by=user.id,
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    return run_id


def test_check_budget_passes_under_cap(fixture_run) -> None:
    db = SessionLocal()
    try:
        run = check_budget_before_call(db, fixture_run, estimated_cents=1)
        assert run.status == "pending"
    finally:
        db.close()


def test_check_budget_blocks_over_cap(fixture_run) -> None:
    """Push cost_cents close to the cap, then a 1¢ call must be blocked."""
    db = SessionLocal()
    try:
        run = db.query(ProspectRun).filter(ProspectRun.id == fixture_run).first()
        run.cost_cents = 100  # at the cap
        db.commit()
        with pytest.raises(OverBudget):
            check_budget_before_call(db, fixture_run, estimated_cents=1)
        # Status must have flipped to over_budget under the lock
        db.expire_all()
        run = db.query(ProspectRun).filter(ProspectRun.id == fixture_run).first()
        assert run.status == "over_budget"
    finally:
        db.close()


def test_check_budget_rejects_terminal_run(fixture_run) -> None:
    db = SessionLocal()
    try:
        run = db.query(ProspectRun).filter(ProspectRun.id == fixture_run).first()
        run.status = "completed"
        db.commit()
        with pytest.raises(OverBudget):
            check_budget_before_call(db, fixture_run, estimated_cents=1)
    finally:
        db.close()


def test_record_call_cost_atomically_adds(fixture_run) -> None:
    db = SessionLocal()
    try:
        cost = cost_from_usage({"input_tokens": 1000, "output_tokens": 1000})
        run = record_call_cost(db, fixture_run, cost)
        assert run.cost_cents == cost.total_cents
        # Second call accumulates.
        run = record_call_cost(db, fixture_run, cost)
        assert run.cost_cents == cost.total_cents * 2
    finally:
        db.close()
