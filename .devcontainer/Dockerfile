# Workspace image: Python 3.12 (Debian bookworm) + psql client.
# Node 22, uv and Claude Code come from devcontainer features (devcontainer.json).
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
