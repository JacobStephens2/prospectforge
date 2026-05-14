# Eval harness

Measures **extraction precision and recall** against hand-labeled fixtures
(spec §14). Per spec §1.2 we want ≥0.85 extraction precision by week 8;
per spec §17 Phase 5 we want 15–20 fixtures before fully trusting the
harness; per spec §14 the week-1 deliverable is **5 fixtures co-labeled
by Morgan + Jacob**.

## Fixture format

Each `fixtures/*.yaml` pairs a page's text with the contacts a reviewer
would approve from it. See [`phoenix_isd_starter.yaml`](./fixtures/phoenix_isd_starter.yaml)
for the canonical shape:

```yaml
name: <slug used in result tables>
description: |
  Why this fixture exists / what it's testing.
source_url: https://example.test/some/page
page_text: |
  The page text the extractor sees. Strip cruft for auditability.
expected_organizations:
  - name: <Org name>
    state: <2-letter US state>
expected_contacts:
  - name: <Full name>
    title_contains: <substring>     # optional
    email: <address>                # optional
```

Matching rules (see `_matches` in [`run.py`](./run.py)):

- **Name** match is case-insensitive and whitespace-normalized.
- **Email** is checked only if the label has one.
- **`title_contains`** is checked only if the label has one.

## Adding a fixture

1. Pick a page from a recent run — query `source_pages` for a
   `content_markdown` worth labeling. Aim for pages where Morgan already
   approved or rejected the contacts (real ground truth).
2. Copy the markdown into `fixtures/<slug>.yaml`.
3. Hand-write `expected_contacts` — only people a reviewer would approve.
   Skip deny-listed roles (students, parents, PTA/PTO).
4. Run `python -m app.eval.run --fixture <slug>` to sanity-check.

## Running

```bash
# Run all fixtures live (hits Anthropic, costs ~1¢/fixture).
docker compose exec backend python -m app.eval.run

# Run one fixture by name (without .yaml).
docker compose exec backend python -m app.eval.run --fixture phoenix_isd_starter

# Machine-readable output for CI gating.
docker compose exec backend python -m app.eval.run --json
```

`tests/test_eval_runner.py` exercises the matching logic offline — it
runs every CI pass without an API key.

## What the score means

- **recall** — fraction of labeled contacts the extractor found.
  Misses here are real false negatives.
- **precision** — fraction of extractor output that matched a label.
  Anything extracted that *wasn't* labeled is counted as a false
  positive. Sometimes that's correct (the labeler missed a contact);
  promote those to the label set in a follow-up commit, don't tune the
  prompt against unlabeled ground truth.

`run.py` warns on the console when corpus precision drops below 0.85
(the spec §1.2 target). That's a soft warning today — turn it into a
hard exit-1 gate once we have ≥5 fixtures.
