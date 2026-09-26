.PHONY: help test run lint typecheck format install clean

PY ?= python3

help:
	@echo "Targets:"
	@echo "  test      run the test suite"
	@echo "  run       start the server (127.0.0.1:8787)"
	@echo "  lint      ruff check (needs the dev extras)"
	@echo "  typecheck mypy (needs the dev extras)"
	@echo "  format    ruff format (needs the dev extras)"
	@echo "  install   pip install -e .[dev]"
	@echo "  clean     remove caches and local *.db files"

test:
	$(PY) -m unittest discover -s tests -v

run:
	$(PY) -m payload_store

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
