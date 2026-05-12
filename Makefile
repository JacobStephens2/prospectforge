# ProspectForge dev-loop helpers. Use docker compose directly for anything not here.

.PHONY: up down logs build test openapi shell

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

build:
	docker compose build

test:
	docker compose run --rm backend pytest -v

openapi:
	docker compose exec backend python scripts/dump_openapi.py > openapi.json
	@echo "wrote openapi.json"

shell:
	docker compose exec backend bash
