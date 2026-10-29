.PHONY: lint test check

RUFF ?= .venv/bin/ruff
PYTEST ?= .venv/bin/pytest

lint:
	$(RUFF) check src/ tests/

test:
	$(PYTEST)

check: lint
	$(RUFF) format --check src/ tests/
	$(PYTEST)
