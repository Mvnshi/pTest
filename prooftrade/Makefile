# ProofTrade - investor demo MVP
#
#   make setup   install backend and frontend dependencies
#   make data    regenerate the offline synthetic snapshot (deterministic)
#   make dev     run the API and the Vite dev server together
#   make test    run the backend test suite
#   make build   build the SPA and serve everything from FastAPI on :8000

PY      := backend/.venv/bin/python
PIP     := backend/.venv/bin/pip
PYTEST  := backend/.venv/bin/pytest
UVICORN := backend/.venv/bin/uvicorn

.PHONY: setup setup-backend setup-frontend data data-real dev api web test test-backend \
        test-dsl dsl-schema dsl-typecheck dsl-cli verify build serve clean

setup: setup-backend setup-frontend

setup-backend:
	python3 -m venv backend/.venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	@echo "backend ready"

setup-frontend:
	cd frontend && npm install
	@echo "frontend ready"

# Deterministic offline bars. Same seed in, byte-identical CSVs out.
data:
	python3 scripts/generate_sample_data.py --force

# Optional: replace the synthetic snapshot with real daily bars from a free public
# endpoint. Requires network access and is a one-time vendoring step - the engine
# never fetches anything at runtime.
data-real:
	python3 scripts/fetch_data.py

api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

web:
	cd frontend && npm run dev

# Both halves in one terminal; Ctrl-C stops both.
dev:
	@echo "API on http://127.0.0.1:8000  ·  UI on http://127.0.0.1:5173"
	@trap 'kill 0' EXIT INT TERM; \
	 (cd backend && .venv/bin/uvicorn app.main:app --port 8000) & \
	 (cd frontend && npm run dev) & \
	 wait

# The whole Python suite: the engine's tests and the DSL's.
test: test-backend test-dsl

test-backend:
	cd backend && .venv/bin/python -m pytest tests -q

test-dsl:
	$(PY) -m pytest dsl_v2/tests -q

# The generated schema must match the models. Fails if someone edited models.py and
# forgot to regenerate, which would let the Python and JSON descriptions disagree.
dsl-schema:
	$(PY) -m dsl_v2.generate_schema --check

# Real tsc, not the Python regex drift test. The regex compares field and enum NAMES;
# only tsc proves the types are well-formed and that the unions narrow exhaustively.
dsl-typecheck:
	frontend/node_modules/.bin/tsc -p dsl_v2/tsconfig.json
	@echo "dsl_v2 typecheck: ok"

dsl-cli:
	$(PY) -m dsl_v2.cli dsl_v2/examples/*.json

# Everything a change to the DSL has to survive. Run this before committing.
verify: test dsl-schema dsl-typecheck dsl-cli
	@echo ""
	@echo "verify: python tests, schema freshness, TypeScript strict, CLI examples - all ok"

build:
	cd frontend && npm run build

# One process serving the built SPA and the API together.
serve: build
	cd backend && .venv/bin/uvicorn app.main:app --port 8000

clean:
	rm -rf frontend/dist backend/prooftrade.db backend/**/__pycache__ backend/.pytest_cache
