.PHONY: check test lint format

check: test lint

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .
