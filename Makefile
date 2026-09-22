.PHONY: up down logs bootstrap test-api api-dev web-dev

up:
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api web worker
bootstrap:
	docker compose run --rm -e BOOTSTRAP_ADMIN_EMAIL -e BOOTSTRAP_ADMIN_PASSWORD api python -m scripts.bootstrap --demo
test-api:
	cd apps/api && python -m pytest -q
api-dev:
	cd apps/api && uvicorn app.main:app --reload --port 8000
web-dev:
	cd apps/web && npm run dev
