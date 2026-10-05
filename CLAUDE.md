# CLAUDE.md

Conventions for Claude Code sessions in this repo. Read `plan.md` (design),
`_docs/process.md` (team process) and `_docs/adlc.md` (LLM feature lifecycle)
before starting a task.

## Rules

- dont log, print, publish, commit, leak any kind of credentials like password, tokens, internal data, privacy data etc.
- allowed to create pull requests when the criteria met, but leave them for me to merge.
- `backend/app/tax/` is pure (no I/O, no DB, no network); every tax rule change needs a golden test.
- Never log document contents or the Steuer-ID (not in logs, traces, metrics, fixtures or error messages).
- Any LLM change (prompt, schema, model, provider, routing) needs an eval run; see `_docs/adlc.md`.

## Stack

- Backend (`backend/`): Python 3.12, `uv`, FastAPI, SQLAlchemy 2.0 + Alembic, Postgres 16, Postgres-based job queue
- Frontend (`frontend/`): React 19 + TanStack Start/Router/Query, Vite, Tailwind, Vitest, ESLint; Node 22
- LLM: provider-agnostic `LLMProvider` (OpenAI first, Anthropic / Gemini pluggable)
- Bot: channel-agnostic core + Telegram adapter
- E-mail: Resend (magic links)
- Observability: OpenTelemetry → Grafana Cloud
- Deploy: Railway (`api`, `web`, `worker`, `postgres`), auto-deploy from `main`
- Dev env: Codespaces devcontainer (`.devcontainer/`), Postgres at host `db`, port 5432

## Layout

- `backend/app/` — Python package, `backend/tests/` — pytest, `backend/evals/` — LLM evals
- `frontend/` — web client
- `_docs/` — process, role docs, task template, ADLC
- `.github/` — issue and PR templates

## Commands

Frontend:

```bash
cd frontend
npm ci              # install deps
npm run dev         # dev server on http://localhost:3000
npm test            # vitest
npm run lint        # eslint
npm run typecheck   # tsc --noEmit
npm run build
```

Database (devcontainer):

```bash
psql -h db -U belegbot -d belegbot -c 'select 1'      # from the workspace container
docker compose -f .devcontainer/docker-compose.yml up -d db   # DB only, outside the devcontainer
```

Backend:

```bash
cd backend
uv sync                       # install deps (uv sync --locked in CI)
uv run uvicorn app.api.main:app --reload --port 8000   # API: GET /health, GET /version
uv run pytest                 # tests incl. tax golden tests (against belegbot_test)
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run alembic upgrade head   # migrations (URL from DATABASE_URL, never in alembic.ini)
uv run alembic revision --autogenerate -m "..."   # new migration after changing models
uv run python -m evals.run    # eval runner (see _docs/adlc.md; arrives with the first LLM feature)
```

Test database: `uv run pytest` never touches `belegbot`. It uses `TEST_DATABASE_URL`,
or `DATABASE_URL` with the database name replaced by `belegbot_test`; it creates that
DB if missing and runs `alembic upgrade head` once per session. Each test runs in a
transaction that is rolled back. pytest aborts before any test if the test DB name
does not end in `_test`, and DB tests fail (not skip) when the DB is unreachable.

## CI

`.github/workflows/ci.yml` runs on every pull request (any base), on push to `main`
and on `workflow_dispatch`:

- `changes`: path filter. `backend` runs for `backend/**` or `.github/workflows/**`,
  `frontend` for `frontend/**` or `.github/workflows/**`; both run on `main` and manual runs
- `backend`: Postgres 16 service, `uv sync --locked`, ruff check, ruff format --check,
  mypy, `alembic upgrade head` / `check` / `downgrade base` / `upgrade head`, pytest
- `frontend`: `npm ci`, lint, typecheck, test, build
- `ci-ok`: passes when every needed job succeeded or was skipped by the path filter,
  fails on any failure or cancellation. This is the only check branch protection needs

No repository secrets are used; the Postgres password in the workflow is a CI-only value.

Branch protection (recommended, applied by the repo owner in GitHub settings, not by
agents): protect `main`, require a pull request, require status check `ci-ok`, and
require branches to be up to date before merging.

## Env

Copy `.env.example` to `.env` (git-ignored) and fill in values locally; real
secrets live in Codespaces / Railway secrets, never in the repo.
