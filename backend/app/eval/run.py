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
from app.services.regex_extract import extract_signals

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
    # Yield-style counts to track the contact-recovery workstream.
    # pre_pass_* are deterministic regex output and don't require an LLM call,
    # so --prepass-only runs populate them with zero LLM cost. extracted_*
    # are post-LLM-and-evidence-verification counts.
    pre_pass_emails: int = 0
    pre_pass_phones: int = 0
    extracted_emails: int = 0
    extracted_phones: int = 0


@dataclass
class PrePassResult:
    """Deterministic pre-pass output for one fixture. Lets us baseline regex
    yield (and measure de-obfuscation lift) without paying for an LLM call.
    """

    fixture: str
    pre_pass_emails: int
    pre_pass_phones: int
    emails: tuple[str, ...]
    phones: tuple[str, ...]


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
    pre_pass = extract_signals(fixture["page_text"])
    extracted_emails = sum(1 for c in extracted if c.email)
    extracted_phones = sum(1 for c in extracted if c.phone)

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
        pre_pass_emails=len(pre_pass.emails),
        pre_pass_phones=len(pre_pass.phones),
        extracted_emails=extracted_emails,
        extracted_phones=extracted_phones,
    )


def _run_prepass_only(fixture: dict) -> PrePassResult:
    """Deterministic pre-pass over the fixture's page_text. No DB, no LLM."""
    pre_pass = extract_signals(fixture["page_text"])
    return PrePassResult(
        fixture=fixture["name"],
        pre_pass_emails=len(pre_pass.emails),
        pre_pass_phones=len(pre_pass.phones),
        emails=pre_pass.emails,
        phones=pre_pass.phones,
    )


def _print_prepass_report(results: list[PrePassResult]) -> None:
    """Yield-only baseline. Emails/phones the regex pre-pass finds, per fixture
    and overall. Use as a before/after gauge on de-obfuscation work — a change
    is working if these counts go up on the same fixtures.
    """
    total_emails = sum(r.pre_pass_emails for r in results)
    total_phones = sum(r.pre_pass_phones for r in results)
    print()
    print("=" * 70)
    print(f"PRE-PASS YIELD BASELINE (deterministic; no LLM, no DB)")
    print("-" * 70)
    print(f"{'Fixture':<45} {'Emails':>8} {'Phones':>8}")
    print("-" * 70)
    for r in results:
        print(f"{r.fixture:<45} {r.pre_pass_emails:>8} {r.pre_pass_phones:>8}")
    print("-" * 70)
    print(f"{'TOTAL':<45} {total_emails:>8} {total_phones:>8}")
    print("=" * 70)
    print()


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
    p.add_argument(
        "--prepass-only",
        action="store_true",
        help=(
            "Skip the LLM extraction and only report the deterministic regex "
            "pre-pass counts per fixture. No DB, no Anthropic cost. Use this "
            "to baseline yield before/after de-obfuscation work."
        ),
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

    if args.prepass_only:
        prepass_results = [_run_prepass_only(f) for f in fixtures]
        if args.json:
            print(json.dumps([r.__dict__ for r in prepass_results], indent=2, default=list))
            return 0
        _print_prepass_report(prepass_results)
        return 0

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
    total_pre_emails = sum(r.pre_pass_emails for r in results)
    total_pre_phones = sum(r.pre_pass_phones for r in results)
    total_ex_emails = sum(r.extracted_emails for r in results)
    total_ex_phones = sum(r.extracted_phones for r in results)
    overall_p = total_matched / total_extracted if total_extracted else 0.0
    overall_r = total_matched / total_expected if total_expected else 0.0
    overall_f1 = (
        2 * overall_p * overall_r / (overall_p + overall_r) if (overall_p + overall_r) else 0.0
    )
    total_cost = sum(r.cost_cents for r in results)

    print()
    print("=" * 80)
    print(f"{'Fixture':<35} {'P':>5} {'R':>5} {'F1':>5} {'PreE':>5} {'PreP':>5} {'ExE':>4} {'ExP':>4} {'Cost':>6}")
    print("-" * 80)
    for r in results:
        print(
            f"{r.fixture:<35} {r.precision:>5.2f} {r.recall:>5.2f} {r.f1:>5.2f} "
            f"{r.pre_pass_emails:>5} {r.pre_pass_phones:>5} "
            f"{r.extracted_emails:>4} {r.extracted_phones:>4} {r.cost_cents:>5}¢"
        )
        if r.misses:
            print(f"    MISSED:    {', '.join(r.misses)}")
        if r.extras:
            print(f"    EXTRA:     {', '.join(r.extras)}")
    print("-" * 80)
    print(
        f"{'OVERALL':<35} {overall_p:>5.2f} {overall_r:>5.2f} {overall_f1:>5.2f} "
        f"{total_pre_emails:>5} {total_pre_phones:>5} "
        f"{total_ex_emails:>4} {total_ex_phones:>4} {total_cost:>5}¢"
    )
    print("=" * 80)
    print("  Columns: P/R/F1 vs labels; PreE/PreP = regex pre-pass emails/phones;")
    print("           ExE/ExP = post-LLM extracted emails/phones (yield)")
    print()
    # Spec §1.2 target: precision ≥ 0.85 by week 8.
    if overall_p < 0.85:
        print(f"WARNING: overall precision {overall_p:.2f} below the 0.85 spec target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
