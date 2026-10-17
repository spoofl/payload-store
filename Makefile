.PHONY: help test run lint typecheck format install clean

PY ?= python3
ARGS ?=

help:
	@echo "Targets:"
	@echo "  test      run the test suite"
	@echo "  run       start ./run.sh (pass options with ARGS='...')"
	@echo "  lint      ruff check (needs the dev extras)"
	@echo "  typecheck mypy (needs the dev extras)"
	@echo "  format    ruff format (needs the dev extras)"
	@echo "  install   pip install -e .[dev]"
	@echo "  clean     remove caches and local *.db files"

test:
	$(PY) -m unittest discover -s tests -v

run:
	@PY="$(PY)" ./run.sh $(ARGS)

lint:
	ruff check .

typecheck:
	mypy payload_store

format:
	ruff format .

install:
	$(PY) -m pip install -e ".[dev]"

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -f *.db *.db-wal *.db-shm
