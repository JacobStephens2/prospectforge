"""Eval CLI — run every fixture through extract_from_page, score precision/recall.

Usage:
  docker compose exec backend python -m app.eval.run
  docker compose exec backend python -m app.eval.run --fixture phoenix_isd_starter

Reports:
  precision = matched_extracted / total_extracted    (no junk)
  recall    = matched_extracted / total_expected     (no misses)
  f1        = 2 * P * R / (P + R)

A contact is "matched" if a row exists with the same name (case-insensitive,
whitespace-normalized) AND, if the fixture specifies one, the same email
(case-insensitive). `title_contains` is checked when present.

This module deliberately doesn't write to the production DB. It creates a
throwaway user/source/run for each fixture so cost tracking still works.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.db import SessionLocal
from app.models import ProspectRun, Source, User
from app.services.extraction import ExtractionRunResult, extract_from_page

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass
class FixtureResult:
    fixture: str
    precision: float
    recall: float
    f1: float
    expected_count: int
    extracted_count: int
    matched_count: int
    cost_cents: int
    retried: bool
    misses: list[str]
    extras: list[str]


def _normalize(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _matches(extracted_contact, expected: dict) -> bool:
    if _normalize(extracted_contact.name) != _normalize(expected["name"]):
        return False
    expected_email = expected.get("email")
    if expected_email and (extracted_contact.email or "").lower() != expected_email.lower():
        return False
    title_contains = expected.get("title_contains")
    if title_contains:
        if title_contains.lower() not in _normalize(extracted_contact.title):
            return False
    return True


def _run_one(fixture: dict, db) -> FixtureResult:
    """Spin up a throwaway user/source/run and run extraction."""
    user = User(
        email=f"eval-{uuid.uuid4()}@acme-travel.example",
        full_name="Eval",
        role="admin",
    )
    db.add(user)
    db.flush()
    src = Source(
        name=f"Eval: {fixture['name']}",
        url=fixture["source_url"],
        created_by=user.id,
    )
    db.add(src)
    db.flush()
    run = ProspectRun(
        source_id=src.id,
        created_by=user.id,
        max_cost_cents=200,
        budget_approved_by=user.id,
        budget_approved_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()

    result: ExtractionRunResult = extract_from_page(
        db=db,
        run_id=run.id,
        source_url=fixture["source_url"],
        page_text=fixture["page_text"],
    )

    expected = fixture.get("expected_contacts", [])
    extracted = [vc.contact for vc in result.verified_contacts if vc.keep]

    matched: list[dict] = []
    matched_extracted: set[int] = set()
    misses: list[str] = []
    for exp in expected:
        for i, c in enumerate(extracted):
            if i in matched_extracted:
                continue
            if _matches(c, exp):
                matched.append(exp)
                matched_extracted.add(i)
                break
        else:
            misses.append(exp["name"])

    extras = [
        f"{c.name} <{c.email or 'no-email'}>"
        for i, c in enumerate(extracted)
        if i not in matched_extracted
    ]

    p = (len(matched_extracted) / len(extracted)) if extracted else 0.0
    r = (len(matched_extracted) / len(expected)) if expected else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0

    return FixtureResult(
        fixture=fixture["name"],
        precision=p,
        recall=r,
        f1=f1,
        expected_count=len(expected),
        extracted_count=len(extracted),
        matched_count=len(matched_extracted),
        cost_cents=result.cost.total_cents,
        retried=result.retried,
        misses=misses,
        extras=extras,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="ProspectForge extraction eval harness")
    p.add_argument(
        "--fixture",
        help="Run a single fixture by name (without .yaml). Default: all.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the text report.",
    )
    args = p.parse_args(argv)

    fixtures: list[dict] = []
    if not FIXTURE_DIR.exists():
        print(f"no fixtures directory at {FIXTURE_DIR}", file=sys.stderr)
        return 2
    for path in sorted(FIXTURE_DIR.glob("*.yaml")):
        with open(path) as f:
            doc = yaml.safe_load(f)
        if args.fixture and doc["name"] != args.fixture:
            continue
        fixtures.append(doc)

    if not fixtures:
        print("no fixtures matched", file=sys.stderr)
        return 2

    db = SessionLocal()
    results: list[FixtureResult] = []
    try:
        for f in fixtures:
            print(f"running fixture: {f['name']}", file=sys.stderr)
            results.append(_run_one(f, db))
    finally:
        db.close()

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
        return 0

    total_extracted = sum(r.extracted_count for r in results)
    total_matched = sum(r.matched_count for r in results)
    total_expected = sum(r.expected_count for r in results)
    overall_p = total_matched / total_extracted if total_extracted else 0.0
    overall_r = total_matched / total_expected if total_expected else 0.0
    overall_f1 = (
        2 * overall_p * overall_r / (overall_p + overall_r) if (overall_p + overall_r) else 0.0
    )
    total_cost = sum(r.cost_cents for r in results)

    print()
    print("=" * 70)
    print(f"{'Fixture':<35} {'P':>5} {'R':>5} {'F1':>5} {'Cost':>6}")
    print("-" * 70)
    for r in results:
        print(f"{r.fixture:<35} {r.precision:>5.2f} {r.recall:>5.2f} {r.f1:>5.2f} {r.cost_cents:>5}¢")
        if r.misses:
            print(f"    MISSED:    {', '.join(r.misses)}")
        if r.extras:
            print(f"    EXTRA:     {', '.join(r.extras)}")
    print("-" * 70)
    print(f"{'OVERALL':<35} {overall_p:>5.2f} {overall_r:>5.2f} {overall_f1:>5.2f} {total_cost:>5}¢")
    print("=" * 70)
    print()
    # Spec §1.2 target: precision ≥ 0.85 by week 8.
    if overall_p < 0.85:
        print(f"WARNING: overall precision {overall_p:.2f} below the 0.85 spec target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
