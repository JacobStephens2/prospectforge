"""Offline tests for the eval harness matching logic in app/eval/run.py.

No DB / no Anthropic API — exercises the pure scoring helpers so a
broken comparison rule fails CI without paying for an extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.eval.run import FIXTURE_DIR, _matches, _normalize, _run_prepass_only, main


@dataclass
class FakeContact:
    """Stand-in for ExtractedContact — only the fields _matches reads."""

    name: str
    email: str | None = None
    title: str | None = None


def test_normalize_collapses_whitespace_and_case():
    assert _normalize("  Jane   Smith ") == "jane smith"
    assert _normalize(None) == ""


def test_matches_name_and_email_exact():
    c = FakeContact(name="Jane Smith", email="j@a.test")
    assert _matches(c, {"name": "Jane Smith", "email": "j@a.test"})


def test_matches_is_case_insensitive():
    c = FakeContact(name="  JANE  smith ", email="J@a.TEST")
    assert _matches(c, {"name": "Jane Smith", "email": "j@a.test"})


def test_email_mismatch_fails_match():
    c = FakeContact(name="Jane Smith", email="other@a.test")
    assert not _matches(c, {"name": "Jane Smith", "email": "j@a.test"})


def test_label_without_email_falls_back_to_name():
    """If the labeler didn't put an email on a row, name alone is enough."""
    c = FakeContact(name="Jane Smith", email="j@a.test")
    assert _matches(c, {"name": "Jane Smith"})


def test_title_contains_filters():
    c_band = FakeContact(name="Jane Smith", title="Band Director")
    c_choir = FakeContact(name="Jane Smith", title="Choir Director")
    assert _matches(c_band, {"name": "Jane Smith", "title_contains": "band"})
    assert not _matches(c_choir, {"name": "Jane Smith", "title_contains": "band"})


def test_starter_fixture_parses():
    """The checked-in starter fixture must be well-formed and runnable."""
    path = FIXTURE_DIR / "phoenix_isd_starter.yaml"
    assert path.exists(), f"missing starter fixture at {path}"
    doc = yaml.safe_load(path.read_text())
    assert "name" in doc and "page_text" in doc and "expected_contacts" in doc
    assert len(doc["expected_contacts"]) >= 1


def test_prepass_only_counts_signals():
    """Pre-pass mode runs without DB/LLM and reports signal counts."""
    fixture = {
        "name": "synthetic",
        "page_text": (
            "Email Jane at jane@school.edu or call (512) 555-1234. "
            "Bob's address: bob@school.edu."
        ),
    }
    result = _run_prepass_only(fixture)
    assert result.fixture == "synthetic"
    assert result.pre_pass_emails == 2
    assert result.pre_pass_phones == 1
    assert "jane@school.edu" in result.emails
    assert "(512) 555-1234" in result.phones


def test_prepass_only_main_returns_zero(capsys, tmp_path, monkeypatch):
    """CLI --prepass-only path: exits 0, prints a baseline report, no DB calls."""
    fx_dir = tmp_path / "fixtures"
    fx_dir.mkdir()
    (fx_dir / "demo.yaml").write_text(
        "name: demo\n"
        "source_url: https://x.test/\n"
        "page_text: |\n"
        "  Contact Jane at jane@x.test or call (512) 555-1212.\n"
    )
    monkeypatch.setattr("app.eval.run.FIXTURE_DIR", fx_dir)
    rc = main(["--prepass-only"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PRE-PASS YIELD BASELINE" in out
    assert "demo" in out
