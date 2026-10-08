.PHONY: check test lint format

PRETTIER = npx --yes prettier@3.9.9
SHELL_SCRIPTS = install.sh kitty/flower-cursor-shell

check: test lint

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run shfmt --diff $(SHELL_SCRIPTS)
	$(PRETTIER) --check .

format:
	uv run ruff format .
	uv run shfmt --write $(SHELL_SCRIPTS)
	$(PRETTIER) --write .
