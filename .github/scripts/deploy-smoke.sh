#!/usr/bin/env bash
# Deploy smoke test (CI only, needs docker): builds the api and web images, runs the
# migrations twice concurrently against a throwaway Postgres 16, starts both containers
# and checks the endpoints a Railway deploy depends on. Uses no repository secrets.
set -euo pipefail

NET=belegbot-smoke
PG=smoke-db
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
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*"
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
docker run -d --name "$API" --network "$NET" -p 8000:8000 \
  -e DATABASE_URL="$DB_URL" -e APP_ENV=ci -e GIT_SHA="${GITHUB_SHA:-unknown}" \
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
expect_status http://localhost:3000/ 200
grep -q '<title>' /tmp/smoke-body || fail "/ has no HTML title"
expect_status http://localhost:3000/api/health 200 X-Trace-Id
expect_status http://localhost:3000/api/version 200

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
docker logs "$API" 2>&1 | grep -q 'no queue yet' || fail "worker did not start"

echo "== non-root users"
for c in "$API" "$WEB"; do
  uid=$(docker exec "$c" id -u)
  echo "$c runs as uid $uid ($(docker exec "$c" id -un))"
  [ "$uid" != 0 ] || fail "$c runs as root"
done

echo "== api stopped: /api/health must be 502, web stays up"
docker stop "$API" >/dev/null
expect_status http://localhost:3000/api/health 502
expect_status http://localhost:3000/healthz 200
expect_status http://localhost:3000/ 200

if [ "$FAILURES" -gt 0 ]; then
  echo "deploy smoke: $FAILURES failure(s)"
  exit 1
fi
echo "deploy smoke: all checks passed"
