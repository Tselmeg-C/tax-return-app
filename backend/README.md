# backend

Python 3.12 backend (FastAPI, Postgres, worker), managed with `uv`.

Placeholder only: `pyproject.toml`, FastAPI, Alembic and tests arrive in #2.

- `app/` — Python package (`api/`, `bot/`, `pipeline/`, `llm/`, `tax/`, `domain/`, `db/`, `storage/`, `observability/`, see `plan.md` §4)
- `tests/` — pytest suite, incl. tax golden tests
- `evals/` — LLM eval sets and runner (see `_docs/adlc.md`)
