.PHONY: test lint format run

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

run:
	uvicorn saga_trpg.api.app:create_app --factory --reload --port 4850
