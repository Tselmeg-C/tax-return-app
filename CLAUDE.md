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

Frontend (available now):

```bash
cd frontend
npm ci            # install deps
npm run dev       # dev server on http://localhost:3000
npm test          # vitest
npm run lint      # eslint
npm run build
```

Database (devcontainer):

```bash
psql -h db -U belegbot -d belegbot -c 'select 1'      # from the workspace container
docker compose -f .devcontainer/docker-compose.yml up -d db   # DB only, outside the devcontainer
```

Backend (available after #2):

```bash
cd backend
uv sync                       # install deps
uv run pytest                 # tests incl. tax golden tests
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run alembic upgrade head   # migrations
uv run python -m evals.run    # eval runner (see _docs/adlc.md)
```

## Env

Copy `.env.example` to `.env` (git-ignored) and fill in values locally; real
secrets live in Codespaces / Railway secrets, never in the repo.
