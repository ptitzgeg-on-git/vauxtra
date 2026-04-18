.PHONY: dev backend frontend test lint lint-fix build release help

PYTHON   ?= python
NPM      ?= npm
VERSION  ?= $(shell git describe --tags --abbrev=0 2>/dev/null || echo "dev")

help:
	@echo "Vauxtra — available targets:"
	@echo ""
	@echo "  make dev          Start backend + frontend dev servers"
	@echo "  make backend      Start backend only (uvicorn --reload)"
	@echo "  make frontend     Start frontend only (vite dev)"
	@echo "  make test         Run Python test suite"
	@echo "  make lint         Run ruff + tsc (read-only)"
	@echo "  make lint-fix     Run ruff --fix in place"
	@echo "  make build        Build Docker image locally"
	@echo "  make release V=x.y.z  Tag and push a release (e.g. make release V=0.2.0)"
	@echo ""

## Development

backend:
	uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload

frontend:
	cd frontend && $(NPM) run dev

dev:
	@echo "Starting backend and frontend in background..."
	uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload &
	cd frontend && $(NPM) run dev

## Quality

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	ruff check app/ vauxtra_mcp/
	cd frontend && ./node_modules/.bin/tsc --noEmit

lint-fix:
	ruff check app/ vauxtra_mcp/ --fix

## Docker

build:
	docker compose up --build -d

## Release

release:
ifndef V
	$(error Usage: make release V=x.y.z  (e.g. make release V=0.2.0))
endif
	@echo "Creating release v$(V)..."
	git tag v$(V)
	git push origin v$(V)
	@echo "Tag v$(V) pushed — CI will build and publish the Docker image automatically."
