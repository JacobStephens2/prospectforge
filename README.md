# ProspectForge

Acme's internal contact-sourcing tool. Replaces CivicIQ. Spec of record: `/srv/orchestration/tasks/prospectforge_spec_v2.md` on the orchestration host.

## What this repo holds

- `backend/` — FastAPI app, SQLAlchemy 2.0 models, Alembic migrations, RQ workers.
- `frontend/` — Next.js 15 + TypeScript review UI (added in Phase 2).
- `docker-compose.yml` — single-host Postgres 16 + Redis 7 + backend (+ frontend later).
- `infra/` — systemd units, Caddyfile, backup cron (added in Phase 0.5).

## Running locally (Phase 0 scaffold)

```bash
cp .env.example .env
docker compose up --build
curl http://localhost:8000/health      # → {"status":"ok"}
curl http://localhost:8000/openapi.json | jq '.info'
```

Once the frontend lands, regenerate the TS client whenever the backend's OpenAPI schema changes:

```bash
docker compose exec backend python scripts/dump_openapi.py > openapi.json
npx openapi-typescript openapi.json -o frontend/src/api/schema.ts
```

## Week-0 blockers (per spec §22)

Status as of 2026-05-12:

| # | Blocker | Status |
|---|---------|--------|
| 1 | CRM lead-import CSV schema | **Drafted** — see `/srv/orchestration/tasks/prospectforge_csv_schema_proposal.md`; pending Riley/Morgan sign-off. |
| 2 | First three pilot sources | Open — spec recommends Northwind, Texas Theater Association, one district board agenda archive. |
| 3 | Target-state list | Open. |
| 4 | CAN-SPAM footer ownership | Open. |
| 5 | BDR hiring timeline | Open. |
| 6 | Why Clay didn't work | **Open — Jacob to document below.** |
| 7 | NeverBounce required for first campaign? | Open. |
| 8 | Single-egress-IP risk accepted? | Open. |

### Why Clay didn't work

Jacob evaluated Clay and rejected it because **you couldn't paste a URL and get leads from it that fit Acme's market**. The tool is general-purpose and built for sales teams who want to compose their own enrichment workflows from a large kit of blocks — powerful but bulky, with too many knobs to wrangle and direct for Acme's specific use case (educational travel: schools, group leaders, segment-tagged by band/theater/choir/etc.).

ProspectForge takes the opposite stance: **pre-configured for Acme's industry and need**, with the URL-paste UX as the primary surface. The tradeoff is less flexibility — but if Morgan wants something Clay can do and ProspectForge can't, we add it deliberately, not by exposing every dial.

Update this note before any future Clay revisit so a new evaluator inherits the actual rejection reasoning, not a generic "we tried it and it didn't fit."

## Sister docs

- Spec: `/srv/orchestration/tasks/prospectforge_spec_v2.md`
- CSV schema proposal: `/srv/orchestration/tasks/prospectforge_csv_schema_proposal.md`
- Orchestration host CLAUDE.md: `/srv/orchestration/CLAUDE.md`
