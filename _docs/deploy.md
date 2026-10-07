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
| `api` | `MAIL_BACKEND`, `APP_BASE_URL` | `resend`, `https://<web domain>` (login links, CSRF Origin check, `__Host-` cookie) |
| `api` | `RESEND_API_KEY`, `RESEND_FROM_EMAIL` | sending-only key for the verified domain; e.g. `belegbot <login@your-domain>` |
| `web` | `TRUSTED_PROXY_HOPS` | `1` (take the client address the Railway edge appended to `X-Forwarded-For`) |
| `web` | `API_INTERNAL_URL` | `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` |
| `web` | `NODE_ENV`, `APP_ENV`, `OTEL_SERVICE_NAME` | `production`, `production`, `belegbot-web` |
| `web` | `OTEL_*` | references to the three shared variables |

The api refuses to start with `APP_ENV=production` unless `MAIL_BACKEND=resend`,
`RESEND_API_KEY`, `RESEND_FROM_EMAIL` are set and `APP_BASE_URL` is `https://` (the error
names the variable). Set them **before** merging #5, so the old deployment keeps serving
until they exist.

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

## Storage volume (#6)

Uploaded originals live on a Railway **volume on the `api` service** (api and worker are two
processes in that one container, so both see it). A volume attaches to one service only, so
`api` runs **one replica**.

- Mount path `/data`, variable `STORAGE_PATH=/data/storage` on `api`. With
  `APP_ENV=production` the api and the worker refuse to start unless `STORAGE_PATH` is set and
  absolute, and both write and delete a probe file there at startup (a failure names
  `STORAGE_PATH`).
- Railway mounts volumes owned by root, while every app process runs as `belegbot` (uid
  10001). The image therefore starts as root only in `docker-entrypoint.sh`: it creates
  `STORAGE_PATH`, chowns it to the app user (first boot only) and `setpriv`s to uid 10001
  before `exec`ing honcho (or the pre-deploy `alembic upgrade head`). Do not set
  `RAILWAY_RUN_UID=0`.
- `drainingSeconds = 15` in `backend/railway.toml`: Railway waits that long after SIGTERM, so
  honcho can stop the worker (it releases a running job within
  `WORKER_SHUTDOWN_GRACE_SECONDS=3`, below honcho's 5 s kill wait) and uvicorn.
- **Uploads in flight during a deploy:** with a volume Railway stops the old deployment before
  the new one starts, so an upload in that window gets a 5xx; the web UI shows "Upload
  fehlgeschlagen – bitte erneut hochladen" and the button re-sends the same file. A job that
  was running is released (or, if the container is killed, re-claimed after its lease) and
  finishes on the new deployment. Nothing is lost or duplicated (per-household sha256 dedupe).
- Layout on the volume: `households/<h>/documents/<d>/original` plus `tmp/`. The worker's sweep
  (startup and every 6 h) removes stale temp files and orphan directories. Backups of
  `households/`: #24.

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
is deleted when the PR is closed or merged. PR environments get **no volume**: either attach
their own empty volume with the same `STORAGE_PATH`, or accept that uploads there land on the
ephemeral container disk and vanish on redeploy (fine for previews; decided at the final
deployment, #42). To keep preview telemetry out of Grafana, override
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
- Public URL checks: `/login` 200 (`/` redirects to it without a session), `/api/health` 200 with `X-Trace-Id`, `/api/version` shows
  `env: production` and the deployed `main` SHA, `/healthz` 200; `api` has no public domain;
  GitHub deployment statuses for the merge commit are `success`
- Railway UI: pre-deploy `alembic upgrade head` runs once and succeeds before the health check;
  watch paths (backend-only merge redeploys only `api`, frontend-only only `web`); **Wait for CI**
- Grafana: Tempo trace `belegbot-web` → `belegbot-api` → DB span with
  `deployment.environment.name=production`; Loki request line with the same `trace_id` and the
  worker's `worker started` line; an HTTP server duration metric for `belegbot-api`
- PR environment: own empty Postgres, migrated, `/api/health` 200, traces tagged with the PR
  environment name, removed on close/merge
- Broken-migration PR (agent creates it on request): fails in the pre-deploy step, never active
- No secret, OTLP header, DB password or cookie value in Railway logs or Loki

These #5 (login) checks wait until then as well:

- Resend: a domain verified in Resend (SPF/DKIM), a sending-only API key restricted to it,
  click and open tracking **off**; the `api` / `web` variables above
- Bootstrap the first owner and invite the family (see "Users" below)
- `curl -si https://<web>/api/auth/me` → 401; `curl -si https://<web>/belege` → 3xx to
  `/login?next=%2Fbelege`; `POST https://<web>/api/auth/magic-link` with
  `X-Requested-With: belegbot` and a non-invited address → `202 {"status":"sent"}`, without the
  header → 403; `https://<web>/api/docs` → 404
- User-verified: the mail arrives within 1 min, in German, not in spam; the link works on
  desktop and phone (requested on one, opened on the other); the cookie shows `HttpOnly`,
  `Secure`, `SameSite=Lax`; reused or >15 min old links show the error; a link that sat in
  the inbox still works on the first click; "Abmelden" / "Auf allen Geräten abmelden"; every
  invited member can sign in, a non-invited address gets no mail; Railway logs and Loki contain
  neither the address nor any part of a token

These #8 (LLM layer, `_docs/llm.md`) checks wait as well (tracked in #42):

- `OPENAI_API_KEY` set as a Railway secret on `api` (api + worker). `uv run python -m
  app.llm.smoke` on Railway prints a request id and a cost; `belegbot.llm.cost` and
  `belegbot.llm.tokens` for that call are visible in Grafana, and the trace shows
  `llm classify` → `chat <model>` with no content attributes
- OpenAI project settings confirmed by the user: data retention (Zero Data Retention if
  eligible), EU data residency (if yes: an EU project and `OPENAI_BASE_URL`), a project spend
  limit; the project has credits (the live run on 2026-10-06 got `credit_balance_exhausted`)

These #6 (uploads, queue, volume) checks wait as well (tracked in #42):

- `curl -si https://<web>/api/documents` without a cookie → 401; `POST` without the CSRF
  header → 403
- The `api` service has a volume at `/data`, `STORAGE_PATH=/data/storage`, the deploy log shows
  no permission error (root-owned mount + privilege drop), and `drainingSeconds` is honoured
- Phone uploads (iPhone camera HEIC/JPEG, Android camera, a ~20 MB desktop PDF) against the
  public web URL reach "verarbeitet"; "Original öffnen" shows the right file
- Railway "Redeploy" of `api` right after uploading 3 files: all 3 reach "verarbeitet", none is
  lost, no file duplicated
- A second family member sees the documents; signed out, "Original öffnen" in a new tab gets
  401
- PR environments: own empty volume or no uploads (see "PR environments")
- Grafana: Loki shows `job.succeeded` lines; Tempo shows the `job process_document` span linked
  to the upload request; `belegbot.queue.depth` and `belegbot.storage.free_bytes` exist. No
  file name or content appears in Loki or Tempo

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
  `OTEL_SERVICE_NAME`. `STORAGE_PATH=/data/storage`.
- Volume (#6): *Settings → Volumes → New volume*, mount path `/data`, region EU West, size as
  the plan allows (5 GB is plenty; about 2–5 MB per photo). Keep one replica.

**4. Service `web`** (same repo, rename to `web`)

- Settings: Root Directory `/frontend`, branch `main`; config file path
  `/frontend/railway.toml`; region EU West; enable **Wait for CI**; *Networking → Generate
  Domain*.
- Variables: `API_INTERNAL_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}`,
  `NODE_ENV=production`, `APP_ENV=production`, `OTEL_SERVICE_NAME=belegbot-web`, references to
  the three shared `OTEL_*` variables.

**Users** (after the first successful `api` deploy; output shows ids and masked e-mails):

```bash
railway ssh --service api
python -m app.auth.cli bootstrap --household-name "Familie X" --email you@your-domain
python -m app.auth.cli invite --email partner@your-domain            # --role owner for a 2nd owner
python -m app.auth.cli disable --email someone@your-domain           # also: enable, revoke-sessions
```

**5. PR environments**

*Project Settings → Environments*: enable **PR Environments** (based on `production`).
Optionally override `OTEL_EXPORTER_OTLP_ENDPOINT` to empty there.

**6. Report back on issue #3** (no secrets): the public `web` URL, the PR environment URL once
it exists, confirmation of regions and backups. Then run the checks below.

**Checks after setup**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<web>/login      # 200 (/ redirects there)
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
