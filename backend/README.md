# backend

Python 3.12 backend (FastAPI, Postgres via SQLAlchemy 2.0 + psycopg 3, Alembic), managed with `uv`.

- `app/api/` — FastAPI app (`app.api.main:app`)
- `app/config.py` — `Settings` from env / optional `.env` (`DATABASE_URL`, `APP_ENV`, `LOG_LEVEL`, `GIT_SHA`, `FIELD_ENCRYPTION_KEY`)
- `app/domain/enums.py` — domain enums (`Category`, `DocType`, …), `CATEGORY_GROUP`, `LABELS_DE`
- `app/db/` — `base.py` (declarative base, naming convention, PII-free `repr`), `session.py` (async engine/sessions), `models/` (core tables), `types.py` / `crypto.py` (encrypted columns, enum type), `scope.py` (`HouseholdScope`), `audit.py` (audit helper), `seed.py` (dev seed), `migrations/` (Alembic)
- `app/observability/` — structlog JSON logging + OpenTelemetry (`setup_observability`)
- `app/worker/` — worker process (`python -m app.worker`; placeholder until #6)
- `tests/` — pytest suite, incl. tax golden tests (later)
- `evals/` — LLM eval harness: synthetic dataset `bills_v0`, runner, gate (see `evals/README.md`)

See `plan.md` §4 for the full layout.

## Setup

Inside the Codespace / devcontainer `DATABASE_URL` is already set (Postgres at host `db`).
Elsewhere, copy `../.env.example` to `.env` or export `DATABASE_URL`.
`postgres://` and `postgresql://` URLs are accepted and mapped to `postgresql+psycopg://`.

```bash
cd backend
uv sync
uv run alembic upgrade head
```

## Run the API

```bash
uv run uvicorn app.api.main:app --reload --port 8000
curl localhost:8000/health    # {"status":"ok","db":"ok"} or 503 {"status":"error","db":"error"}
curl localhost:8000/version   # {"version":"0.1.0","commit":"<GIT_SHA|unknown>","env":"development"}
```

As deployed (api + worker, see `Procfile`; JSON log lines, every response has `X-Trace-Id`):

```bash
PORT=8000 uv run honcho start -f Procfile
OTEL_TRACES_EXPORTER=console uv run honcho start -f Procfile   # spans printed as JSON lines
```

Telemetry is exported only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Docker image:
`Dockerfile`; Railway config: `railway.toml` (see `../_docs/deploy.md`).

The app starts without a database; `/health` reports the DB state (`SELECT 1`, 2 s timeout).
Startup fails if `DATABASE_URL` is missing.

## Tests and checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app evals
```

Tests use a separate database: `TEST_DATABASE_URL`, or `DATABASE_URL` with the name
replaced by `belegbot_test`. The DB is created if missing and migrated once per run; each
test is rolled back. The run aborts if the test DB name does not end in `_test`, and DB
tests fail (not skip) if the database is unreachable.

## Migrations

```bash
uv run alembic revision --autogenerate -m "add foo"   # after adding models under app/db
uv run alembic upgrade head
uv run alembic check          # fails if models and migrations disagree
uv run alembic downgrade base
```

The URL comes from `Settings` (`DATABASE_URL`); `alembic.ini` holds no URL. In production
migrations run only in Railway's pre-deploy step, never at startup; `env.py` takes a Postgres
advisory lock so concurrent runs serialise. Rules for new
tables (household_id, enums, encryption, indexes) are in `../CLAUDE.md`.

## Dev seed

```bash
uv run alembic upgrade head
uv run python -m app.db.seed                                 # fictional "Musterhaushalt"
SEED_OWNER_EMAIL=you@example.org uv run python -m app.db.seed  # owner e-mail for local login
```

Creates 1 household, 3 persons, 2 users (`owner@example.com`, `member@example.com`), 9
documents (no real files) and 9 tax items shaped like `frontend/src/lib/mock.ts`. Idempotent
(a second run prints `seed: already present, nothing to do`), refuses `APP_ENV=production`,
writes no audit rows and prints counts only. It needs no `FIELD_ENCRYPTION_KEY`.
