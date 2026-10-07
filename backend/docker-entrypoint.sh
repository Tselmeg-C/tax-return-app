#!/bin/sh
# Container entrypoint (#6 Decision 13). Railway mounts volumes owned by root, but every app
# process runs as the non-root `belegbot` user. So the container starts as root only long
# enough to make STORAGE_PATH owned by that user, then drops privileges before exec'ing the
# command (honcho, or Railway's pre-deploy `alembic upgrade head`). No secrets are read here.
set -eu

APP_UID=10001
APP_GID=10001

if [ "$(id -u)" = "0" ]; then
  if [ -n "${STORAGE_PATH:-}" ]; then
    mkdir -p "$STORAGE_PATH"
    if [ "$(stat -c %u "$STORAGE_PATH")" != "$APP_UID" ]; then
      # First boot on a fresh (root-owned) volume; later boots skip this.
      chown -R "$APP_UID:$APP_GID" "$STORAGE_PATH"
    fi
    chmod 700 "$STORAGE_PATH"
  fi
  exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups -- "$@"
fi
exec "$@"
