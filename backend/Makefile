.PHONY: dev test test-unit contract lint fmt check install

install:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --port 8000

test:
	uv run pytest -q

test-unit:
	uv run pytest tests/unit -q

contract:
	uv run python scripts/export_openapi.py

lint:
	uv run ruff check app tests
	uv run ruff format --check app tests

fmt:
	uv run ruff format app tests
	uv run ruff check --fix app tests

# Run this before every push.
check: lint test contract
	@git diff --quiet ../contracts/openapi.json || \
	  (echo "\n>>> contracts/openapi.json changed. Commit it as 'chore(contract): regenerate openapi' and tell the frontend dev.\n"; exit 1)
	@echo "All checks passed."
