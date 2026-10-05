# Workspace image: Python 3.12 (Debian bookworm) + psql client.
# Node 22, uv and Claude Code come from devcontainer features (devcontainer.json).
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The base image ships a yarn apt source whose signing key has expired, which
# breaks `apt-get update`; yarn is not needed (frontend uses npm), so drop it.
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
