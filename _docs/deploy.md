# Deploy runbook (Railway + Grafana Cloud)

Every merge to `main` deploys to Railway (EU West, Amsterdam). Agents have no Railway or
Grafana access; the setup steps at the end are done by the repo owner in the UIs.
Never paste a token, password or OTLP header into an issue, PR, commit or chat.

## Layout

Railway project `belegbot`, environment `production` (+ PR environments):

| Service | Root dir | Config file | Start | Health check | Public |
|---|---|---|---|---|---|
| `api` | `/backend` | `/backend/railway.toml` | `honcho start --no-prefix -f Procfile` (uvicorn api + `python -m app.worker`) | `/health` (DB `SELECT 1`) | **no** domain |
| `web` | `/frontend` | `/frontend/railway.toml` | `node .output/server/index.mjs` (Nitro node server) | `/healthz` (no upstream) | yes, generated domain |
| `Postgres` | Railway template | – | – | – | no |

- Config file paths are absolute (they do not follow the root directory).
- Both services build from their `Dockerfile` (`builder = "DOCKERFILE"`).
- Watch paths: `api` redeploys only for `/backend/**`, `web` only for `/frontend/**`.
- **Wait for CI** is on for both, so a deploy starts only after `ci-ok` is green on `main`.
- The browser only talks to `web`; `/api/*` is proxied to `api` over the private network
  (`API_INTERNAL_URL`). `api` binds IPv4 and IPv6 (`--host ''`), because the Railway private
  network is IPv6 and local/CI networks are IPv4.

Variables (set in the Railway UI, never in the repo):

| Where | Variable | Value |
|---|---|---|
| Shared (production) | `OTEL_EXPORTER_OTLP_ENDPOINT` | Grafana OTLP endpoint (`https://otlp-gateway-<region>.grafana.net/otlp`) |
| Shared | `OTEL_EXPORTER_OTLP_HEADERS` | `Authorization=Basic%20<base64 instanceID:token>` |
| Shared | `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` |
| `api` | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (private URL, not `DATABASE_PUBLIC_URL`) |
| `api` | `PORT`, `APP_ENV`, `LOG_LEVEL` | `8000`, `production`, `INFO` |
| `api` | `OTEL_*` | references to the three shared variables. **No** `OTEL_SERVICE_NAME` |
| `web` | `API_INTERNAL_URL` | `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` |
| `web` | `NODE_ENV`, `APP_ENV`, `OTEL_SERVICE_NAME` | `production`, `production`, `belegbot-web` |
| `web` | `OTEL_*` | references to the three shared variables |

Railway itself provides `RAILWAY_ENVIRONMENT_NAME` (→ `deployment.environment.name` on all
telemetry) and `RAILWAY_GIT_COMMIT_SHA` (→ `/version` `commit` and `vcs.ref.head.revision`).

## How a deploy works

1. Merge to `main` → CI runs (`backend`, `frontend`, `deploy-smoke`, `ci-ok`).
2. Railway waits for CI, then builds the image of each service whose watch paths changed.
3. `api` only: the **pre-deploy step** runs `alembic upgrade head` once, in a fresh
   container of the new image, before the new version starts. Migrations never run at app or
   worker startup. `env.py` takes a Postgres advisory lock, so overlapping runs (two quick
   merges, a manual run during a deploy) wait for each other instead of racing.
4. The new container starts; Railway polls the health check (`/health` for `api`, `/healthz`
   for `web`) until it returns 200 (timeout 120 s / 60 s).
5. Traffic switches to the new deployment and the old one is stopped (SIGTERM; honcho stops
   uvicorn and the worker, telemetry is flushed within 5 s).

Restart policy is `ON_FAILURE` (max 5 retries). If either process inside `api` dies, honcho
stops the other and exits non-zero, so Railway restarts the whole container.

## When a migration fails

The pre-deploy command exits non-zero → the new deployment is marked failed and is **never
activated**; the previous deployment keeps serving. Fix the migration in a new PR. Because the
failed migration ran in a transaction, the database is unchanged.

## Rollback

Railway → service → Deployments → pick a previous successful deployment → **Redeploy**.

Downgrades are **not** run automatically. A rollback starts old code against the current
schema, so every migration must stay compatible with the previous app version (expand →
migrate → contract over separate deploys). Run `alembic downgrade` by hand only when you know
the old code needs it.

## PR environments

With PR environments enabled, every PR against `main` gets its own environment (copied
services and variables, its own empty Postgres). The pre-deploy step migrates that database.
Telemetry is tagged with the environment name (e.g. `tax-return-app-pr-42`). The environment
is deleted when the PR is closed or merged. To keep preview telemetry out of Grafana, override
`OTEL_EXPORTER_OTLP_ENDPOINT` to empty in the PR environment settings.

## Finding a request in Grafana

Every api response (also through the web proxy) carries `X-Trace-Id: <32 hex>`:

```bash
curl -si https://<web>/api/health | grep -i x-trace-id
```

- **Tempo**: Grafana → Explore → Tempo → TraceQL / search by trace ID → paste the value. The
  trace shows `belegbot-web` (`web /api proxy`) → `belegbot-api` (`GET /health`) → the DB span.
- **Loki**: `{service_name="belegbot-api"} | json | trace_id="<id>"` shows the request log
  line; `{service_name="belegbot-worker"}` shows the worker lines.
- **Metrics**: `http_server_duration_milliseconds_*{service_name="belegbot-api"}`
  (FastAPI instrumentation).

What telemetry never contains: request/response bodies, query strings, cookies, auth headers,
DB URLs or passwords, the Steuer-ID or document contents. Spans are scrubbed before export
(`backend/app/observability/scrub.py`), log fields are redacted
(`backend/app/observability/logs.py`).

## Local equivalents

```bash
cd backend && PORT=8000 uv run honcho start -f Procfile      # api + worker, JSON logs
OTEL_TRACES_EXPORTER=console uv run honcho start -f Procfile  # print spans as JSON lines
cd frontend && npm run build && PORT=3100 npm run start       # web node server
```

Without `OTEL_EXPORTER_OTLP_ENDPOINT` nothing is exported (logged once as `otel export
disabled`). CI's `deploy-smoke` job builds both images and checks migrations (twice,
concurrently), `/health`, `/version`, `/healthz`, `/`, `/api/health`, `/api/version`, the 502
after stopping the api and the non-root users.

## Deferred to final deployment

The real Railway / Grafana Cloud deployment happens at the end of the backlog (local dev
and CI use no exporter or in-memory exporters). These #3 checks wait until then:

- Setup steps 1–6 below (Grafana stack + token, Railway project, `api`, `web`, Postgres,
  PR environments, regions EU West, Postgres backups)
- `README.md`: the production web URL
- Public URL checks: `/` 200, `/api/health` 200 with `X-Trace-Id`, `/api/version` shows
  `env: production` and the deployed `main` SHA, `/healthz` 200; `api` has no public domain;
  GitHub deployment statuses for the merge commit are `success`
- Railway UI: pre-deploy `alembic upgrade head` runs once and succeeds before the health check;
  watch paths (backend-only merge redeploys only `api`, frontend-only only `web`); **Wait for CI**
- Grafana: Tempo trace `belegbot-web` → `belegbot-api` → DB span with
  `deployment.environment.name=production`; Loki request line with the same `trace_id` and the
  worker's `no queue yet` line; an HTTP server duration metric for `belegbot-api`
- PR environment: own empty Postgres, migrated, `/api/health` 200, traces tagged with the PR
  environment name, removed on close/merge
- Broken-migration PR (agent creates it on request): fails in the pre-deploy step, never active
- No secret, OTLP header, DB password or cookie value in Railway logs or Loki

These #8 (LLM layer, `_docs/llm.md`) checks wait as well (tracked in #42):

- `OPENAI_API_KEY` set as a Railway secret on `api` (api + worker). `uv run python -m
  app.llm.smoke` on Railway prints a request id and a cost; `belegbot.llm.cost` and
  `belegbot.llm.tokens` for that call are visible in Grafana, and the trace shows
  `llm classify` → `chat <model>` with no content attributes
- OpenAI project settings confirmed by the user: data retention (Zero Data Retention if
  eligible), EU data residency (if yes: an EU project and `OPENAI_BASE_URL`), a project spend
  limit; the project has credits (the live run on 2026-10-06 got `credit_balance_exhausted`)

## Setup (repo owner, at the final deployment)

Do these once #3 is merged to `main` (before that, `main` has no Dockerfiles). Keep secrets
in your password manager and Railway only.

**1. Grafana Cloud**

1. Create a Grafana Cloud account and stack in an **EU region**.
2. Stack → *Connections* → *OpenTelemetry (OTLP)*: note the **OTLP endpoint**
   (`https://otlp-gateway-<region>.grafana.net/otlp`) and the **instance ID**.
3. Create an access-policy token with scopes `traces:write`, `logs:write`, `metrics:write`
   (nothing else).
4. Build the header value `Authorization=Basic%20<base64 of "<instanceID>:<token>">` (the
   Grafana OTLP page can generate it).

**2. Railway project**

1. Create a Railway project (e.g. `belegbot`); install/authorise the Railway GitHub app for
   `Tselmeg-C/tax-return-app`.
2. Add **PostgreSQL** (Railway template), region **EU West (Amsterdam)**. Turn on scheduled
   daily backups in its *Backups* tab if your plan offers them (offsite backups: #24).
3. *Project Settings → Shared Variables* (production): `OTEL_EXPORTER_OTLP_ENDPOINT`,
   `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`.

**3. Service `api`** (*New → GitHub Repo → tax-return-app*, rename to `api`)

- Settings: Root Directory `/backend`, branch `main`; config file path
  `/backend/railway.toml`; region EU West; enable **Wait for CI**; **do not generate a public
  domain**.
- Variables: `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `PORT=8000`, `APP_ENV=production`,
  `LOG_LEVEL=INFO`, references to the three shared `OTEL_*` variables. Do not set
  `OTEL_SERVICE_NAME`.

**4. Service `web`** (same repo, rename to `web`)

- Settings: Root Directory `/frontend`, branch `main`; config file path
  `/frontend/railway.toml`; region EU West; enable **Wait for CI**; *Networking → Generate
  Domain*.
- Variables: `API_INTERNAL_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}`,
  `NODE_ENV=production`, `APP_ENV=production`, `OTEL_SERVICE_NAME=belegbot-web`, references to
  the three shared `OTEL_*` variables.

**5. PR environments**

*Project Settings → Environments*: enable **PR Environments** (based on `production`).
Optionally override `OTEL_EXPORTER_OTLP_ENDPOINT` to empty there.

**6. Report back on issue #3** (no secrets): the public `web` URL, the PR environment URL once
it exists, confirmation of regions and backups. Then run the checks below.

**Checks after setup**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<web>/           # 200
curl -si https://<web>/api/health                                  # 200, X-Trace-Id
curl -s https://<web>/api/version                                  # env production, commit = main HEAD
curl -s -o /dev/null -w '%{http_code}\n' https://<web>/healthz    # 200
```

In the UIs: pre-deploy `alembic upgrade head` in the `api` deploy log; watch paths (backend-only
merge redeploys only `api`, frontend-only only `web`); the trace in Tempo
(`belegbot-web` → `belegbot-api` → DB, `deployment.environment.name=production`); Loki lines
for `belegbot-api` (same `trace_id`) and `belegbot-worker` (`no queue yet`); an HTTP server
duration metric for `belegbot-api`; no secret, OTLP header, password or cookie value in
Railway logs or Loki.
