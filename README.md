# belegbot (tax-return-app)

Tax return assistant for a family: photograph receipts, let the app classify and map them to
the German income tax return (Einkommensteuer), and estimate the refund.

- Design: [`plan.md`](plan.md) · Team process: [`_docs/process.md`](_docs/process.md) ·
  LLM lifecycle: [`_docs/adlc.md`](_docs/adlc.md) · Agent conventions: [`CLAUDE.md`](CLAUDE.md)
- Production web URL: not deployed yet. The Railway deployment happens at the end of the
  backlog (#42, see [`_docs/deploy.md`](_docs/deploy.md)).

## Stack

| Part | Tech |
|---|---|
| `backend/` | Python 3.12, `uv`, FastAPI, SQLAlchemy 2 + Alembic, Postgres 16 |
| `frontend/` | React 19, TanStack Start / Router / Query, Vite, Tailwind, Vitest; Node 22 |
| LLM | provider-agnostic layer (`backend/app/llm/`), OpenAI first |
| Evals | `backend/evals/`, synthetic dataset, runs offline in CI |

## Start locally

The Codespace / devcontainer (`.devcontainer/`) already has Python 3.12, `uv`, Node 22 and a
Postgres 16 service at host `db` with `DATABASE_URL` set. Outside it, copy `.env.example` to
`.env` and start Postgres with
`docker compose -f .devcontainer/docker-compose.yml up -d db`.

**1. Backend (API on port 8000)**

```bash
cd backend
uv sync
uv run alembic upgrade head      # create / migrate the dev database
uv run python -m app.db.seed     # fictional "Musterhaushalt" with owner@example.com (idempotent)
uv run uvicorn app.api.main:app --reload --port 8000
curl localhost:8000/health       # {"status":"ok","db":"ok"}
```

**2. Frontend (web app on port 3000)**, in a second terminal:

```bash
cd frontend
npm ci
npm run dev                      # http://localhost:3000, /api is proxied to localhost:8000
```

**3. Log in.** No real e-mail is sent locally (`MAIL_BACKEND=file`):

1. Open <http://localhost:3000/login>, enter `owner@example.com`, click **Link senden**.
2. Open the newest file in `backend/.dev-mail/` and copy the link from it.
3. Open the link and click **Anmelden**.

### In GitHub Codespaces

The Codespaces port forwarder needs three adjustments until #51 is fixed:

```bash
# backend: the forwarder rewrites the browser's Origin to http://localhost:3000
cd backend && APP_BASE_URL=http://localhost:3000 uv run uvicorn app.api.main:app --reload --port 8000

# frontend: allow the *.app.github.dev host and listen on all interfaces (avoids 502)
cd frontend && __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=".$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN" npm run dev -- --host
```

Open `https://$CODESPACE_NAME-3000.$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN/login` (or the
port 3000 link in the **Ports** tab). The login link in `backend/.dev-mail/` starts with
`http://localhost:3000`: replace that part with your Codespaces URL before opening it
(VS Code desktop forwards `localhost:3000`, so there it works as is).

## Test locally

**Backend**, from `backend/`:

```bash
uv run pytest                                  # full suite; uses its own DB belegbot_test
uv run ruff check . && uv run ruff format --check .
uv run mypy app evals
uv run alembic check                           # models and migrations in sync
```

**Frontend**, from `frontend/`:

```bash
npm test             # vitest
npm run lint
npm run typecheck
npm run build
```

**Evals** (offline, no API key needed), from `backend/`:

```bash
uv run python -m evals.validate --dataset bills_v0                         # labels, coverage, privacy scan
uv run python -m evals.run --dataset bills_v0 --predictor oracle --gate    # harness self-test
```

**Live LLM check** (optional, paid): settings read `.env` from the directory you run in, so
put `OPENAI_API_KEY=...` in the git-ignored `backend/.env` (or export it), then from
`backend/` run `uv run python -m app.llm.smoke` (one call, prints tokens and cost)
or `uv run pytest -m live`. Without a key both print `SKIPPED`. CI never uses a key.

CI (`.github/workflows/ci.yml`) runs all of the above on every pull request; `ci-ok` is the
check to require. More detail: [`backend/README.md`](backend/README.md),
[`backend/evals/README.md`](backend/evals/README.md).

## Repository rules

The repo is public. Never commit credentials, `.env`, real documents or anything derived from
them; test and eval data is synthetic. See [`CLAUDE.md`](CLAUDE.md) for the full rules.
