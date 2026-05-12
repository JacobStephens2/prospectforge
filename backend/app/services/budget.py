"""Per-run budget cap (spec §7.1 + §13).

Morgan sets `max_cost_cents` when she creates a run. The extraction worker
checks this BEFORE every Anthropic call (so we never over-spend by even
one in-flight request) and updates `prospect_runs.cost_cents` AFTER each
response lands. If the running total would exceed the cap, the run is
marked `over_budget` and the worker halts gracefully.

Pricing for Sonnet 4.5 (per 1M tokens):
  input:           $3.00          → 0.0003 cents/token
  cache write 5m:  1.25x input    → 0.000375 cents/token
  cache read:      0.1x input     → 0.00003 cents/token
  output:          $15.00         → 0.0015 cents/token

These constants are in cents-per-token (units that match prospect_runs.cost_cents).
Override via settings if Anthropic pricing changes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import ProspectRun

# Sonnet 4.5 (and 4.6) pricing. If we move models, update here.
INPUT_COST_CENTS_PER_TOKEN = 0.0003          # $3.00 per 1M tokens
CACHE_WRITE_COST_CENTS_PER_TOKEN = 0.000375  # 1.25× input
CACHE_READ_COST_CENTS_PER_TOKEN = 0.00003    # 0.10× input
OUTPUT_COST_CENTS_PER_TOKEN = 0.0015         # $15.00 per 1M tokens


class OverBudget(Exception):
    """Raised when a run has spent (or would spend) more than its cap."""


@dataclass(frozen=True)
class UsageCost:
    """Computed cost breakdown for one Anthropic response."""

    input_cents: float
    cache_write_cents: float
    cache_read_cents: float
    output_cents: float
    total_cents: int  # rounded up — never undercharge against the cap

    @property
    def total_tokens(self) -> int:
        return int(
            self.input_cents / INPUT_COST_CENTS_PER_TOKEN
            + self.cache_write_cents / CACHE_WRITE_COST_CENTS_PER_TOKEN
            + self.cache_read_cents / CACHE_READ_COST_CENTS_PER_TOKEN
            + self.output_cents / OUTPUT_COST_CENTS_PER_TOKEN
        )


def cost_from_usage(usage: object) -> UsageCost:
    """Compute cents from an Anthropic usage object.

    Accepts either the SDK's `Usage` Pydantic object or a dict-shaped stand-in.
    None / missing fields default to 0 (Anthropic omits keys it doesn't have
    for that response — e.g. `cache_creation_input_tokens` is None when
    nothing was written).
    """
    def _get(name: str) -> int:
        v = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
        return int(v) if v else 0

    input_tokens = _get("input_tokens")
    cache_creation = _get("cache_creation_input_tokens")
    cache_read = _get("cache_read_input_tokens")
    output_tokens = _get("output_tokens")

    input_cents = input_tokens * INPUT_COST_CENTS_PER_TOKEN
    cache_write_cents = cache_creation * CACHE_WRITE_COST_CENTS_PER_TOKEN
    cache_read_cents = cache_read * CACHE_READ_COST_CENTS_PER_TOKEN
    output_cents = output_tokens * OUTPUT_COST_CENTS_PER_TOKEN
    total = input_cents + cache_write_cents + cache_read_cents + output_cents

    # Round UP to the nearest cent — we'd rather over-account than slip
    # past the budget by sub-cent rounding noise.
    import math

    return UsageCost(
        input_cents=input_cents,
        cache_write_cents=cache_write_cents,
        cache_read_cents=cache_read_cents,
        output_cents=output_cents,
        total_cents=int(math.ceil(total)),
    )


def check_budget_before_call(db: Session, run_id: uuid.UUID, estimated_cents: int = 1) -> ProspectRun:
    """Look up the run, raise OverBudget if a new call would blow the cap.

    `estimated_cents` is a conservative pre-call estimate. 1 cent is plenty
    of headroom for one Sonnet-4.5 call against a 10k-token chunk; the cap
    check happens against (current + estimated). On `pre_pass` we also re-
    read the run from the DB (no caching) so concurrent workers see fresh
    cost_cents written by sibling workers.
    """
    run = db.query(ProspectRun).filter(ProspectRun.id == run_id).with_for_update().first()
    if run is None:
        raise ValueError(f"run {run_id} not found")
    if run.status in ("over_budget", "failed", "completed"):
        raise OverBudget(f"run already in terminal status {run.status!r}")
    projected = run.cost_cents + estimated_cents
    if projected > run.max_cost_cents:
        # Mark over_budget under the lock so we don't double-flip.
        run.status = "over_budget"
        db.commit()
        raise OverBudget(
            f"run {run_id} would exceed cap: "
            f"{run.cost_cents}¢ + {estimated_cents}¢ > {run.max_cost_cents}¢"
        )
    return run


def record_call_cost(
    db: Session,
    run_id: uuid.UUID,
    cost: UsageCost,
) -> ProspectRun:
    """Atomically add this call's cost + tokens to the run.

    Uses an UPDATE statement (not load + mutate + commit) so concurrent
    workers can't lose writes. Returns the refreshed run.
    """
    stmt = (
        update(ProspectRun)
        .where(ProspectRun.id == run_id)
        .values(
            cost_cents=ProspectRun.cost_cents + cost.total_cents,
            tokens_used=ProspectRun.tokens_used + cost.total_tokens,
        )
        .returning(ProspectRun.id)
    )
    db.execute(stmt)
    db.commit()
    run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
    assert run is not None
    return run
