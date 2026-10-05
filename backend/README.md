# backend

Python 3.12 backend (FastAPI, Postgres via SQLAlchemy 2.0 + psycopg 3, Alembic), managed with `uv`.

- `app/api/` — FastAPI app (`app.api.main:app`)
- `app/config.py` — `Settings` from env / optional `.env` (`DATABASE_URL`, `APP_ENV`, `LOG_LEVEL`, `GIT_SHA`)
- `app/db/` — `base.py` (declarative base, naming convention), `session.py` (async engine/sessions), `migrations/` (Alembic)
- `tests/` — pytest suite, incl. tax golden tests (later)
- `evals/` — LLM eval sets and runner (see `_docs/adlc.md`)

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

The app starts without a database; `/health` reports the DB state (`SELECT 1`, 2 s timeout).
Startup fails if `DATABASE_URL` is missing.

## Tests and checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
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

The URL comes from `Settings` (`DATABASE_URL`); `alembic.ini` holds no URL.
