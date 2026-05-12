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

*Jacob to fill in. Capture: cost, integration friction, UX mismatch, or workflow gap that made Clay wrong for Acme. Document here before Phase 4 (pilot) so a future evaluation doesn't re-litigate it.*

## Sister docs

- Spec: `/srv/orchestration/tasks/prospectforge_spec_v2.md`
- CSV schema proposal: `/srv/orchestration/tasks/prospectforge_csv_schema_proposal.md`
- Orchestration host CLAUDE.md: `/srv/orchestration/CLAUDE.md`
