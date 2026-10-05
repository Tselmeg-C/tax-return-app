#!/usr/bin/env bash
# Runs once after the devcontainer is created: installs frontend + backend deps.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "post-create: installing frontend deps (npm ci)"
(cd "$repo_root/frontend" && npm ci)

if [ -f "$repo_root/backend/pyproject.toml" ]; then
  echo "post-create: installing backend deps (uv sync)"
  (cd "$repo_root/backend" && uv sync)
else
  echo "post-create: backend/pyproject.toml not found, skipping uv sync (added in #2)"
fi
