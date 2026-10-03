.PHONY: dev backend frontend test lint lint-fix check lock build release help

PYTHON   ?= python
NPM      ?= npm
VERSION  ?= $(shell git describe --tags --abbrev=0 2>/dev/null || echo "dev")

# Every repository gate under scripts/, the ones tests.yml runs one step at a time.
GATES := $(sort $(wildcard scripts/check_*.py))

help:
	@echo "Vauxtra: available targets"
	@echo ""
	@echo "  make dev          Start the backend in the background, then the frontend dev server"
	@echo "  make backend      Start backend only (uvicorn --reload)"
	@echo "  make frontend     Start frontend only (vite dev)"
	@echo "  make test         Run the Python test suite"
	@echo "  make lint         Run ruff + tsc type check (read-only)"
	@echo "  make lint-fix     Run ruff --fix in place"
	@echo "  make check        Everything CI checks: ruff, pytest, the scripts/check_*.py gates,"
	@echo "                    and the frontend lint, type check, locale checks and unit tests"
	@echo "  make lock         Regenerate the image's hashed lock from requirements.in"
	@echo "  make build        Build the image and start it with docker compose (detached)"
	@echo "  make release V=x.y.z  Create an annotated tag on origin/main and push it"
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

## Dependencies

# The image installs from `requirements.txt`, a lock with the hashes of every published file,
# so a release swapped under a known version on PyPI fails the build instead of shipping. Run
# with the image's Python minor version: pip-compile resolves for the interpreter it runs on.
# The MCP bridge and `requirements-dev.txt` stay as ranges, because they are installed on
# Windows and macOS too, and this lock only holds what Linux needs. Needs pip-tools.
PIP_COMPILE ?= pip-compile

lock:
	$(PIP_COMPILE) --quiet --generate-hashes --allow-unsafe --strip-extras -o requirements.txt requirements.in

## Quality

test:
	$(PYTHON) -m pytest tests/ -v

# `ruff check .` like the CI does: `app/ vauxtra_mcp/` left `tests/` and `scripts/` out.
# And `tsc --noEmit` type-checked *nothing* -- the root tsconfig.json is a solver file with
# `"files": []`, so the whole source tree lives behind the two references. Only `-b` walks
# them, which is why `npm run build` caught an error this target reported as clean.
lint:
	ruff check .
	cd frontend && ./node_modules/.bin/tsc -b --noEmit

lint-fix:
	ruff check . --fix

# What a pull request is judged on, runnable before pushing it. The frontend steps are the
# ones tests.yml runs, minus `npm run build`: the build is `tsc -b` plus vite, and the type
# check below already covers the half that can fail on a code change.
check:
	ruff check .
	$(PYTHON) -m pytest tests/ -q
	@for gate in $(GATES); do echo "== $$gate"; $(PYTHON) $$gate || exit 1; done
	cd frontend && $(NPM) run lint
	cd frontend && ./node_modules/.bin/tsc -b --force
	cd frontend && $(NPM) run i18n:check
	cd frontend && $(NPM) run i18n:quality
	cd frontend && $(NPM) run test

## Docker

build:
	docker compose up --build -d

## Release

# A version tag publishes `latest`, a signed image and a release page, and Build & Publish
# refuses a tag that is not on main. This target refuses earlier, before anything is pushed:
# the tag goes on exactly the commit origin/main points at, from a clean tree, and it is
# annotated so `git describe` and the release workflow see a real release object.
release:
ifndef V
	$(error Usage: make release V=x.y.z  (e.g. make release V=0.2.0))
endif
	@echo "$(V)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' \
		|| { echo "V must be x.y.z, got '$(V)'"; exit 1; }
	@test -z "$$(git status --porcelain)" \
		|| { echo "Working tree is not clean; commit or discard first."; exit 1; }
	git fetch origin main --tags
	@test "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" \
		|| { echo "HEAD is not origin/main. Check out main and pull before releasing."; exit 1; }
	@! git rev-parse -q --verify "refs/tags/v$(V)" >/dev/null \
		|| { echo "Tag v$(V) already exists."; exit 1; }
	git tag -a v$(V) -m v$(V)
	git push origin v$(V)
	@echo "Tag v$(V) pushed. CI builds, signs and publishes the image, then writes the release page."
