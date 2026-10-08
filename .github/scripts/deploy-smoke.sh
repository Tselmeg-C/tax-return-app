#!/usr/bin/env bash
# Deploy smoke test (CI only, needs docker): builds the api and web images, runs the
# migrations twice concurrently against a throwaway Postgres 16, starts both containers
# and checks the endpoints a Railway deploy depends on. Uses no repository secrets.
set -euo pipefail

NET=belegbot-smoke
PG=smoke-db
VOLUME=smoke-storage
API=smoke-api
WEB=smoke-web
API_IMAGE=belegbot-api:smoke
WEB_IMAGE=belegbot-web:smoke
# Throwaway password for the ephemeral CI database; not a secret.
DB_URL="postgresql://belegbot:smoke-only-throwaway@${PG}:5432/belegbot"
FAILURES=0

cleanup() {
  echo "::group::container logs"
  for c in "$API" "$WEB"; do
    echo "--- $c"
    docker logs "$c" 2>&1 | tail -n 50 || true
  done
  echo "::endgroup::"
  docker rm -f "$API" "$WEB" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*"
  echo "::error title=deploy-smoke::$*"
  FAILURES=$((FAILURES + 1))
}

# expect_status <url> <expected status> [header that must be present]
expect_status() {
  local url=$1 want=$2 header=${3:-}
  local out status
  out=$(curl -s -m 10 -D - -o /tmp/smoke-body "$url" || true)
  status=$(printf '%s' "$out" | head -n1 | awk '{print $2}')
  echo "$url -> ${status:-none} $(head -c 200 /tmp/smoke-body 2>/dev/null)"
  [ "$status" = "$want" ] || { fail "$url: expected $want, got ${status:-none}"; return; }
  if [ -n "$header" ]; then
    local value
    value=$(printf '%s' "$out" | grep -i "^${header}:" | head -n1 | cut -d' ' -f2 | tr -d '\r')
    echo "  $header: ${value:-missing}"
    [[ "$value" =~ ^[0-9a-f]{32}$ ]] || fail "$url: $header missing or not 32 hex"
  fi
}

wait_for() {
  local url=$1
  for _ in $(seq 1 60); do
    curl -s -m 2 -o /dev/null "$url" && return 0
    sleep 1
  done
  fail "$url never answered"
  return 1
}

echo "::group::build images"
docker build -t "$API_IMAGE" backend
docker build -t "$WEB_IMAGE" frontend
echo "::endgroup::"

docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER=belegbot -e POSTGRES_PASSWORD=smoke-only-throwaway -e POSTGRES_DB=belegbot \
  postgres:16 >/dev/null
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -U belegbot -d belegbot >/dev/null 2>&1 && break
  sleep 1
done

echo "== concurrent migrations: two 'alembic upgrade head' runs at the same moment"
docker run --rm --network "$NET" -e DATABASE_URL="$DB_URL" "$API_IMAGE" alembic upgrade head \
  >/tmp/mig1.log 2>&1 &
P1=$!
docker run --rm --network "$NET" -e DATABASE_URL="$DB_URL" "$API_IMAGE" alembic upgrade head \
  >/tmp/mig2.log 2>&1 &
P2=$!
RC1=0; wait "$P1" || RC1=$?
RC2=0; wait "$P2" || RC2=$?
echo "migration run 1 exit: $RC1"; cat /tmp/mig1.log
echo "migration run 2 exit: $RC2"; cat /tmp/mig2.log
[ "$RC1" = 0 ] && [ "$RC2" = 0 ] || fail "concurrent migrations"
ROWS=$(docker exec "$PG" psql -U belegbot -d belegbot -tAc 'select count(*) from alembic_version')
echo "alembic_version rows: $ROWS"
[ "$ROWS" = 1 ] || fail "alembic_version has $ROWS rows"

echo "== start api (honcho, no OTel env) and web"
# A fresh named volume is root-owned, like a Railway volume (#6 Decision 13).
docker volume create "$VOLUME" >/dev/null
# Throwaway CI-only key for encrypted columns (raw LLM output); never printed.
FIELD_KEY=$(docker run --rm --entrypoint python "$API_IMAGE" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
docker run -d --name "$API" --network "$NET" -p 8000:8000 \
  -v "$VOLUME":/data -e STORAGE_PATH=/data/storage \
  -e DATABASE_URL="$DB_URL" -e APP_ENV=ci -e GIT_SHA="${GITHUB_SHA:-unknown}" \
  -e LLM_CLASSIFY_MODEL=fake:test -e LLM_EXTRACT_MODEL=fake:test -e FIELD_ENCRYPTION_KEY="$FIELD_KEY" \
  "$API_IMAGE" >/dev/null
docker run -d --name "$WEB" --network "$NET" -p 3000:3000 \
  -e API_INTERNAL_URL="http://${API}:8000" \
  "$WEB_IMAGE" >/dev/null
wait_for http://localhost:8000/version
wait_for http://localhost:3000/healthz

echo "== endpoints"
expect_status http://localhost:8000/health 200 X-Trace-Id
expect_status http://localhost:8000/version 200
expect_status http://localhost:3000/healthz 200
# App pages need a session (#5): / redirects to the login page, which renders.
expect_status http://localhost:3000/ 307
expect_status http://localhost:3000/login 200
grep -q '<title>' /tmp/smoke-body || fail "/login has no HTML title"
expect_status http://localhost:3000/api/health 200 X-Trace-Id
expect_status http://localhost:3000/api/version 200
expect_status http://localhost:3000/api/auth/me 401

echo "== api logs are JSON lines"
docker logs "$API" 2>&1 | grep '^{' | python3 -c '
import json, sys
n = 0
for line in sys.stdin:
    d = json.loads(line)
    assert {"timestamp", "level", "event", "service"} <= set(d), line
    n += 1
print(f"{n} JSON log lines ok")
' || fail "api log lines are not all JSON"
WORKER_UP=no
for _ in $(seq 1 30); do
  docker logs "$API" 2>&1 | grep -q '"worker started"' && { WORKER_UP=yes; break; }
  sleep 1
done
[ "$WORKER_UP" = yes ] || fail "worker did not start"

echo "== upload through the web proxy lands on the volume and the job reaches done"
# Test helper: a household, a user and a session in the throwaway DB. The cookie value is
# kept in a shell variable and never printed.
SESSION_LINE=$(docker exec "$API" python -c '
import asyncio
from datetime import timedelta
from app.auth.clock import SystemClock
from app.auth.service import create_session
from app.config import get_settings
from app.db.models import AppUser, Household
from app.db.session import create_engine, create_sessionmaker
from app.domain.enums import UserRole

async def main() -> None:
    engine = create_engine(get_settings())
    async with create_sessionmaker(engine)() as s:
        hh = Household(name="Smoke")
        s.add(hh)
        await s.flush()
        user = AppUser(household_id=hh.id, email="smoke@example.com", role=UserRole.OWNER)
        s.add(user)
        await s.flush()
        token, _ = await create_session(s, user, now=SystemClock().now(), max_age=timedelta(hours=1))
        await s.commit()
        print(hh.id, token)
    await engine.dispose()

asyncio.run(main())
' | tail -n1)
HH_ID=${SESSION_LINE%% *}
SESSION=${SESSION_LINE#* }
# A real, decodable JPEG (the pipeline decodes it); Pillow ships in the api image.
docker run --rm --entrypoint python "$API_IMAGE" -c 'import io, sys; from PIL import Image; b = io.BytesIO(); Image.new("RGB", (400, 300), (250, 250, 245)).save(b, "JPEG"); sys.stdout.buffer.write(b.getvalue())' >/tmp/smoke.jpg
UP_STATUS=$(curl -s -m 30 -o /tmp/smoke-upload.json -w '%{http_code}' \
  -b "belegbot_session=${SESSION}" -H 'X-Requested-With: belegbot' \
  -H 'Content-Type: application/octet-stream' -H "X-Filename: UTF-8''smoke.jpg" \
  --data-binary @/tmp/smoke.jpg http://localhost:3000/api/documents || true)
echo "POST /api/documents -> $UP_STATUS"
DOC_ID=$(python3 -c 'import json; print(json.load(open("/tmp/smoke-upload.json"))["document"]["id"])' 2>/dev/null || true)
if [ "$UP_STATUS" != 201 ] || [ -z "$DOC_ID" ]; then
  fail "upload: expected 201 with a document id"
else
  STATUS=none
  for _ in $(seq 1 30); do
    STATUS=$(curl -s -m 5 -b "belegbot_session=${SESSION}" "http://localhost:3000/api/documents/${DOC_ID}" \
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])' 2>/dev/null || true)
    [ "$STATUS" = done ] && break
    sleep 1
  done
  echo "document $DOC_ID status: $STATUS"
  [ "$STATUS" = done ] || fail "job did not reach done (status: $STATUS; worker log: $(docker logs "$API" 2>&1 | grep -oE '"event": *"(job|pipeline)[^"]*"|"error_kind": *"[^"]*"|"attention_reason": *"[^"]*"' | tail -6 | tr '\n' ' '))"
  KEY="/data/storage/households/${HH_ID}/documents/${DOC_ID}/original"
  docker exec "$API" cat "$KEY" >/tmp/smoke-back.jpg || fail "file not on the volume"
  cmp -s /tmp/smoke.jpg /tmp/smoke-back.jpg || fail "stored bytes differ"
  OWNER_UID=$(docker exec "$API" stat -c %u "$KEY" || echo none)
  echo "stored file owner uid: $OWNER_UID"
  [ "$OWNER_UID" = 10001 ] || fail "stored file not owned by the app user"
  curl -s -m 10 -b "belegbot_session=${SESSION}" -o /tmp/smoke-dl.jpg \
    "http://localhost:3000/api/documents/${DOC_ID}/file" || true
  cmp -s /tmp/smoke.jpg /tmp/smoke-dl.jpg || fail "download differs"
fi

echo "== non-root users"
# The api container starts as root (entrypoint chowns the volume) and drops privileges:
# every honcho, uvicorn and worker process must run with a non-zero uid.
docker exec "$API" ps -eo uid=,args= | tee /tmp/smoke-ps.txt
grep -E 'honcho|uvicorn|app\.worker' /tmp/smoke-ps.txt >/tmp/smoke-app-ps.txt || true
[ "$(wc -l </tmp/smoke-app-ps.txt)" -ge 3 ] || fail "expected honcho, uvicorn and worker processes"
if awk '$1 == 0' /tmp/smoke-app-ps.txt | grep -q .; then fail "an api process runs as root"; fi
for c in "$WEB"; do
  uid=$(docker exec "$c" id -u)
  echo "$c runs as uid $uid ($(docker exec "$c" id -un))"
  [ "$uid" != 0 ] || fail "$c runs as root"
done

echo "== api stopped: /api/health must be 502, web stays up"
docker stop "$API" >/dev/null
expect_status http://localhost:3000/api/health 502
expect_status http://localhost:3000/healthz 200
expect_status http://localhost:3000/login 200

if [ "$FAILURES" -gt 0 ]; then
  echo "deploy smoke: $FAILURES failure(s)"
  exit 1
fi
echo "deploy smoke: all checks passed"
