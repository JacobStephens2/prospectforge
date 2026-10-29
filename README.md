# ProspectForge

> **Status: archived.** Built in 2026; no longer maintained. Names, domains,
> and data in this repo are fictional placeholders.

ProspectForge turns a curated URL (a school's faculty page, an association's
member directory, a conference speaker list) into a reviewed, evidence-backed,
segment-tagged contact list ready for outbound. Every extracted contact carries
a verbatim quote from the source page proving the name, title, and email were
really there, and a human approves each one before export.

It was built in response to general-purpose enrichment tools being too
knob-heavy for a narrow niche: the goal was *paste a URL, get leads that fit
the market*, with sensible defaults rather than a workflow builder.

## How it works

```
URL ─▶ discovery ─▶ Tier-1 crawl ─▶ regex pre-extract ─▶ LLM extraction ─▶ evidence check
        (sitemap,     (httpx +        (emails, phones,     (Claude, JSON      (quote must
        pagination,   trafilatura;    de-obfuscation,      schema)            appear in page)
        URL scoring)  Tier-2 SPA      PDF + OCR)                                   │
                      escalation)                                                  ▼
CSV export ◀─ human review queue ◀─ scoring + segment tags ◀─ dedupe vs CRM + suppressions
                   │
                   └─▶ per-contact enrichment (email-pattern inference, SERP, validation)
```

Notable pieces:

- **Polite, safe crawling** — `robots.txt` honored and cached per domain,
  per-domain token-bucket rate limiting, and an SSRF guard that resolves DNS and
  refuses private, loopback, link-local and denylisted ranges before every fetch
  (`backend/app/services/ssrf_guard.py`).
- **Yield work** — sitemap discovery, staff-directory and pagination detection,
  URL scoring, Cloudflare/HTML-entity/`[at]`-style email de-obfuscation, PDF text
  extraction, and OCR for image-rendered contact info.
- **Evidence-gated LLM extraction** — the model must return a supporting excerpt
  for each contact; `evidence.py` checks it against the fetched page (exact or
  ≥0.9 fuzzy match) and anything that fails is flagged for review and never
  auto-exported, which keeps hallucinated people out of outbound lists.
- **Budget caps** — every run carries a cost ceiling in cents; LLM and crawl spend
  is metered and the orchestrator stops cleanly when the cap is hit.
- **Dedupe & suppression** — a read-only bridge into an external CRM marks
  existing customers and do-not-contact records with match tiers, so they never
  reach export.
- **Enrichment** — email-pattern inference from sibling addresses at the same
  domain (handles hyphenated surnames, initials, etc.), SERP lookup, and
  deliverability validation.
- **Eval harness** — YAML fixtures with labeled expected contacts
  (`backend/app/eval/`) to measure extraction precision and recall as prompts
  change.

## Stack

FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 · Redis + RQ ·
Anthropic Claude · trafilatura · pypdf · Tesseract · Next.js 15 / React 19 ·
Tailwind + shadcn/ui · Docker Compose · Caddy

~400 backend tests, written as integration tests against a real dockerized Postgres.

## Running locally

```bash
cp .env.example .env          # add an ANTHROPIC_API_KEY to exercise extraction
docker compose up --build
make test                      # runs pytest inside the backend container
```

The backend serves on `:8000` (OpenAPI at `/docs`), the review UI on `:3000`.
Login is by magic link to users in the `users` table; the seeded users are
placeholders.

## Notes

- Some code comments refer to spec sections (`spec §7`) and planning docs that
  are not included in this repo.
- Commits authored by "Orchestrator Bot" were made by an AI coding-agent
  setup I ran during development.

## License

[MIT](LICENSE) © 2026 Jacob Stephens
