# Makefile for the financial-model toolkit
# Usage: make <target>

PYTHON ?= python3
IMAGE  ?= financial-model

.PHONY: install install-dev test test-cov lint format format-check typecheck check build serve cli docs docker-build docker-run clean all

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests/ --tb=short

test-cov:
	$(PYTHON) -m pytest tests/ --cov=financial_model --cov-report=term-missing --cov-report=xml

lint:
	$(PYTHON) -m ruff check financial_model/

format:
	$(PYTHON) -m ruff format financial_model/

format-check:
	$(PYTHON) -m ruff format --check financial_model/

typecheck:
	$(PYTHON) -m mypy financial_model/

# Everything CI runs
check: lint typecheck test

build:
	$(PYTHON) -m pip install build && $(PYTHON) -m build

serve:
	financial-model serve --reload

cli:
	financial-model --help

docs:
	$(PYTHON) -m pip install -e ".[docs]" && mkdocs build --strict

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run --rm -p 8000:8000 $(IMAGE)

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml site/

all: clean install-dev lint typecheck test
