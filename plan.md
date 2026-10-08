# Steuerbeleg-App — Design Plan

> Working name: **belegbot**. Personal/family tool that turns photos & PDFs of bills into a German income-tax (ESt) summary with a near-exact refund estimate.
> Status: design v1.1 · 2026-10-05 · Owner: Tselmeg — v1.1 confirms the decisions from the M0 grooming (React/TanStack web client replaced NiceGUI, Anlage V in v1, Resend, family-only, ADLC, golden values via browser automation)

---

## 1. Decisions (from Q&A)

| Topic | Decision |
|---|---|
| Audience | Me + family (MVP, single household, few users) |
| Tax scope | Anlage N (Werbungskosten), Sonderausgaben, außergewöhnliche Belastungen, §35a, Anlage Kind, Anlage KAP, Anlage V |
| Anlage V | **In v1** (rental income §21 incl. AfA basis per property, Zinsen, Erhaltung, Nebenkosten) |
| Not in scope | Self-employed / EÜR / USt, ELSTER submission |
| Output | Per-Anlage/line summary + **near-exact** refund estimate + PDF/CSV export (manual entry in ELSTER/WISO) |
| Clients | **Web client: React + TanStack (Start/Router/Query)** in `frontend/` (own Railway `web` service, same-origin `/api` proxy to `api`) + Telegram bot (v1), WhatsApp later behind same interface. *(Replaced the earlier NiceGUI idea.)* |
| Backend | Python 3.12, FastAPI, Postgres (Railway) |
| File storage | Railway volume (files) + metadata in Postgres; behind a `Storage` interface so S3/R2 is a swap |
| LLM | Provider-agnostic abstraction; **OpenAI (GPT) first**, Claude/Gemini pluggable |
| Review flow | Fully automatic (no review queue) — but every decision stays editable & logged |
| Tax years | 2025 + 2026, parameters versioned per year in config |
| Household | Household → members (spouses, kids); bills assigned to a member; Zusammenveranlagung supported |
| Auth | Magic link + passkeys (WebAuthn); Telegram linked via one-time code |
| E-mail | **Resend** sends the magic-link e-mails |
| Tenancy | **Family-only** — a single household of family members; no non-family users planned (no multi-tenant hardening) |
| Observability | Grafana: app metrics/logs/traces, LLM cost & quality, user-facing tax dashboard |
| Dev flow | GitHub Codespaces + Claude Code, CLAUDE.md, GitHub Actions CI, Railway auto-deploy from `main` |
| LLM lifecycle (ADLC) | **Evals are first-class from M1**: spec → eval set → build → eval gate → observe → iterate (see `_docs/adlc.md`) |
| Tax golden values | Collected from the official **BMF calculator via browser automation** and stored as golden test fixtures |

> Note: "GÜT API" in the Q&A was read as **GPT (OpenAI) API**. Correct me if wrong — the abstraction makes this a one-line config change anyway.

---

## 2. Core user flows

1. **Onboarding / profile** → create household, add members (name, DOB, Steuer-ID optional, Konfession, Bundesland, employment), marital status, kids (DOB, Kindergeld recipient, Betreuung), tax class (Steuerklasse), employer(s).
2. **Pflichtveranlagung check** → app tells you if filing is mandatory (§46 EStG / §56 EStDV) or voluntary (Antragsveranlagung, 4-year window).
3. **Upload** (web drag-drop, phone camera, or Telegram photo/PDF/forwarded e-mail PDF) → stored → queued.
4. **Pipeline** (async): pre-process → classify *tax-relevant?* → if yes extract fields → map to Anlage/line → dedupe → persist → notify ("✅ Handwerkerrechnung 312,40 €, §35a Lohnanteil 180 € → Anlage Haushaltsnahe Aufwendungen").
5. **Official docs import**: Lohnsteuerbescheinigung, Jahressteuerbescheinigung (bank), Kindergeld/Elterngeld letters, Nebenkostenabrechnung → structured extraction with dedicated schemas.
6. **Summary & estimate** per tax year → per Anlage/line totals, refund estimate, "what's missing" hints (e.g. no Lohnsteuerbescheinigung yet).
7. **Export** → PDF report (Anlage/Zeile ordered) + CSV + ZIP of original Belege.

---

## 3. Architecture

```
            ┌──────────────────┐     ┌──────────────┐
 Browser ──►│ web (React/      │     │ Telegram Bot │◄── Telegram (webhook)
            │ TanStack, /api ─►│     └──────┬───────┘
            │ same-origin proxy│            │
            └─────┬────────────┘            │
                  │  /api → REST (private)  │
              ┌───▼───────────────────▼───┐
              │       FastAPI  (api)       │── auth, households, docs, summaries
              └───┬───────────┬───────────┘
                  │ enqueue   │ SQL
          ┌───────▼──┐   ┌────▼─────┐   ┌──────────────┐
          │  worker  │──►│ Postgres │   │ Railway vol. │ (originals + previews)
          │ (pipeline│   └──────────┘   └──────────────┘
          │  + LLM)  │──► LLM gateway ──► OpenAI | Anthropic | Vertex (Gemini)
          └──────────┘
   all services ──OTel──► Grafana Cloud (Prometheus/Loki/Tempo) ; Grafana ──► Postgres (read-only) for tax dashboard
```

**Railway services** (one monorepo, multiple services):

| Service | Start cmd | Health check | Notes |
|---|---|---|---|
| `api` | `honcho start --no-prefix -f Procfile` (`backend/Procfile`: uvicorn `app.api.main:app` + `python -m app.worker`) | `/health` (DB) | no public domain; pre-deploy `alembic upgrade head`; Telegram webhook route decided in #11 |
| `web` | `node .output/server/index.mjs` (TanStack Start built with the Nitro node-server preset) | `/healthz` (no upstream) | React/TanStack app from `frontend/`; serves the UI and a **same-origin `/api` proxy to `api`** over the Railway private network (no CORS, cookies stay first-party) |
| `worker` | process inside `api` in v1 (`python -m app.worker` via honcho) | – | queue consumer (#6); a process in the `api` container, owns no volume of its own: the volume mounts on `api` and both processes see it |
| `postgres` | Railway plugin | – | daily backups enabled |

Config as code: `backend/railway.toml`, `frontend/railway.toml`; runbook in `_docs/deploy.md`.

**Queue** (#6): our own `job` table claimed with `SELECT … FOR UPDATE SKIP LOCKED` (leases, heartbeat, fencing, retries with backoff), not `procrastinate`. The document and its job are inserted in the same SQLAlchemy transaction, and the table follows #4's schema rules (no native enums, no tables outside Alembic) — no Redis needed at family scale.

**Volume caveat**: a Railway volume mounts to **one** service. → `worker` owns the volume and serves file bytes to `api` via an internal endpoint, *or* merge `api`+`worker` into one service in v1. Recommendation: **v1 = api+worker in one service** (FastAPI + background worker process via `honcho`), split later. Because of `Storage` interface, moving to R2/S3 later is trivial.

---

## 4. Repo layout

```
tax-return-app/                         # monorepo
├── .devcontainer/
│   ├── devcontainer.json               # py3.12, uv, Node 22, Claude Code
│   └── docker-compose.yml              # workspace + Postgres 16 (service `db`)
├── .github/                            # issue + PR templates, workflows/ci.yml (CI, #2)
├── .env.example                        # every env var through M6, placeholders only
├── CLAUDE.md                           # conventions for Claude Code
├── plan.md
├── _docs/                              # process, roles, task template, adlc.md
├── backend/
│   ├── pyproject.toml                  # uv, ruff, mypy, pytest (from #2)
│   ├── app/
│   │   ├── api/            # FastAPI routers (auth, household, documents, summary, export, bot)
│   │   ├── bot/            # channel-agnostic bot core + telegram/ + whatsapp/ adapters
│   │   ├── pipeline/       # preprocess, classify, extract, map, dedupe
│   │   ├── llm/            # provider abstraction (see §6)
│   │   ├── tax/            # rules engine + calculator (pure functions, no I/O)
│   │   │   └── params/     # 2025.yaml, 2026.yaml
│   │   ├── domain/         # pydantic models & enums
│   │   ├── db/             # SQLAlchemy 2.0 models, alembic migrations
│   │   ├── storage/        # Storage interface: LocalVolume, S3 (later)
│   │   └── observability/  # OTel setup, metrics
│   ├── tests/
│   │   ├── tax/            # golden tests vs. BMF Einkommensteuerrechner (values via browser automation)
│   │   └── pipeline/fixtures/  # anonymised sample receipts + expected JSON
│   └── evals/              # LLM eval sets & runner (first-class from M1, see `_docs/adlc.md`)
└── frontend/                           # React + TanStack web client (`web` service)
    └── src/                            # routes/, components/, lib/, test/
```

---

## 5. Data model (Postgres)

Models live in `backend/app/db/models/`, enums in `backend/app/domain/enums.py`. Rules for
every table (enforced by meta-tests in `backend/tests/domain/`):

- **Tenancy:** every table except `household` has `household_id uuid NOT NULL REFERENCES household(id) ON DELETE RESTRICT`, child tables included, so one filter works everywhere. No composite FKs (family-only, §1). Query through `HouseholdScope` (`app/db/scope.py`).
- **Keys and types:** `id uuid` generated in Python (no server default); money `NUMERIC(12,2)`, `cost_eur NUMERIC(12,6)`; `created_at` / `updated_at timestamptz NOT NULL DEFAULT now()`. Every FK column leads an index.
- **Enums:** `VARCHAR(64)` + `CHECK` (no native Postgres enums), storing the lowercase value codes.
- **PII:** `person.steuer_id` and `extraction.raw_json` are Fernet-encrypted `bytea` (`FIELD_ENCRYPTION_KEY`, §10); they cannot be filtered or made unique. Names, dob, e-mail, vendors and amounts are plain text but never printed (`repr` shows `<Class id=…>` only, engine `hide_parameters=True`).

Core tables (#4, migration `727a2e042810`):

- `household(id, name, created_at)`
- `app_user(id, household_id, email UNIQUE lowercase, role[owner|member], person_id? UNIQUE → person SET NULL, disabled_at? (#5), created_at, updated_at)` (`user` is reserved in Postgres)
- `person(id, household_id, kind[adult|child], first_name, last_name?, dob? (required for children), steuer_id? 🔒, religion[none|ev|rk|other], disability_grade? (20–100, step 10), created_at, updated_at)`
- `document(id, household_id, uploaded_by_user_id → app_user RESTRICT, channel[web|telegram], sha256 (UNIQUE per household), mime_type, size_bytes, page_count?, storage_key (opaque, UNIQUE), status[queued|processing|done|needs_attention|failed], doc_type?, error_kind?, created_at, updated_at)`
- `extraction(id, household_id, document_id → document CASCADE, step[classify|extract], doc_type?, provider, model, prompt_version, raw_json? 🔒, confidence?, input_tokens, output_tokens, cost_eur, latency_ms, error_kind?, created_at)` — one row per LLM call (§6)
- `tax_item(id, household_id, document_id? → document CASCADE, extraction_id? → extraction SET NULL, person_id? → person RESTRICT (NULL = household-level), year, category, anlage?, zeile?, gross_amount, deductible_amount, labour_share_35a?, vendor?, invoice_date?, payment_date?, payment_method, is_relevant (irrelevant ⇒ deductible 0), reason?, confidence?, overridden_by_user, version (#10, optimistic locking, default 1), created_at, updated_at)`
- `audit_log(id, household_id, entity, entity_id (no FK), action[create|update|delete], before? jsonb, after? jsonb, actor_type[user|system], actor_user_id? (no FK), created_at)` — written via `app/db/audit.py`, encrypted values stored as `"[redacted]"`

Queue and uploads (#6, migration `b7e4d2a91c3f`):

- `job(id, household_id, kind[process_document], document_id? → document CASCADE, status[queued|running|succeeded|failed], attempts, max_attempts, run_after, locked_by?, locked_until?, last_error_kind? (class name), trace_context? (W3C traceparent), started_at?, finished_at?, created_at, updated_at)`; one active (`queued`/`running`) job per document (partial unique index). Ids only — never file contents, names or paths.
- `document.original_filename varchar(255)?` (sanitised display name, plain text, never used as a path). `document.uploaded_by_user_id` stays `RESTRICT`: members are only disabled, and removing a member never deletes documents.
- Storage key layout: `households/<household_id>/documents/<document_id>/original` under `STORAGE_PATH` (no file name, no extension); temp files in `<STORAGE_PATH>/tmp/`.

🔒 = encrypted. `Category` (30 codes in 9 groups, §7) and `DocType` / `PaymentMethod` are the vocabulary of the LLM schemas; `Category → Anlage/Zeile` lives per year in `params/{year}.yaml` (#9).

Auth tables (#5, migration `5a1c9e3b7d42`; both store only `sha256(token)` as hex, no IP or user agent):

- `magic_link_token(id, household_id, user_id → app_user CASCADE, token_hash char(64) UNIQUE, redirect_path? varchar(512), created_at, expires_at, used_at?)`, index `(user_id, created_at)`
- `user_session(id, household_id, user_id → app_user CASCADE, token_hash char(64) UNIQUE, created_at, last_seen_at, expires_at (absolute), revoked_at?)`

Pipeline (#9, migration `c3d9a7e1f2b4`): `document.attention_reason varchar(64)?` (`AttentionReason`, priority order; CHECK `status <> 'needs_attention' OR attention_reason IS NOT NULL`). `extraction` is the append-only LLM call log (one row per attempt, `raw_json` only on the successful one); one `tax_item` per document in v1 (#43 splits).

User edits (#10, migration `e5f1b8c2d6a9`): `tax_item.version integer NOT NULL DEFAULT 1` (SQLAlchemy `version_id_col`). Every user PATCH sets `overridden_by_user` (the pipeline never touches such an item) and writes one `audit_log` row with the changed columns; the pipeline's original value of a field is the `before` of the first user row that changed it.

Household and profile (#13, migration `f2a6c4d8e1b3`; one return per household and year, persons household-wide, the rest per year):

- `tax_profile(id, household_id, year (2000–2100), filing_status[single|joint], bundesland[16 lowercase ISO 3166-2:DE codes], taxpayer_person_id → person RESTRICT, spouse_person_id? → person RESTRICT, created_at, updated_at)`; `UNIQUE (household_id, year)`, CHECK `(filing_status = 'joint') = (spouse_person_id IS NOT NULL)`, spouse ≠ taxpayer. No church tax rate: derived from `person.religion` + `params/{year}.yaml` → `church_tax.rate_by_state[bundesland.upper()]`
- `employment(id, household_id, person_id → person CASCADE, year, employer_name varchar(200), steuerklasse[1–6], has_factor (only with IV), commute_km? (0–999), office_days, homeoffice_days, created_at, updated_at)`, 0..n per person and year, index `(person_id, year)`; office + homeoffice days over all of a person's employments ≤ days in the year (API). Employments of a spouse no longer in the return stay (`in_return: false`)
- `child_year(id, household_id, person_id → person CASCADE, year, months (0–12, ≤ `max_months` from birth month / 25th birthday, no limit with a disability grade), allowance_share[full|half], in_household, created_at, updated_at)`, `UNIQUE (person_id, year)`. Kinderbetreuung costs stay tax items
- `person.steuer_id` is validated (§ 139b AO check digit) and write-only (`steuer_id_masked` on read)

Tables added later, each by the issue that first uses it, in its own migration and under the same rules:

| Table(s) | Issue |
|---|---|
| ~~queue tables, extra `document` columns~~ → `job`, `document.original_filename` (above) | #6 |
| `channel_link(user_id, channel, external_id)`, one-time link codes | #11 |
| `passkey` | #12 |
| ~~`tax_profile`, `employment`, `child_year`~~ → above; `property` (+ `tax_item.property_id`, owners) | #20 |
| `estimate(household_id, year, params_version, inputs_hash, result jsonb, created_at)` | #17 |
| `official_record(person_id, year, kind, fields)`, kinds `lstb`, `elterngeld`, `alg`, `kindergeld` (`jstb` #19, Nebenkosten #20; encrypted where it holds a Steuer-ID) | #18 |
| read-only Grafana role + PII-free views | #22 |

Dedupe: `sha256` for identical files + fuzzy key `(vendor, date, amount)` for re-photographed bills.

---

## 6. LLM abstraction

Implemented in `backend/app/llm/` (#8); details, error table, telemetry and data handling in
[`_docs/llm.md`](_docs/llm.md).

```python
class LLMProvider(Protocol):
    name: str                                   # "openai", "fake"; "anthropic", "gemini" with #23
    async def structured(self, request: LLMRequest[T]) -> LLMResult[T]: ...   # exactly one HTTP call
    async def aclose(self) -> None: ...

@dataclass(frozen=True)
class LLMResult(Generic[T]):
    data: T; raw_text: str                      # raw_text = exact model output (stored encrypted)
    provider: str; model: str; input_tokens: int; output_tokens: int
    cost_eur: Decimal; latency_ms: int          # of the successful call; Decimal, 6 places
    request_id: str | None; pricing_version: str; fallback_used: bool
    calls: tuple[LLMCallRecord, ...]            # one per HTTP attempt incl. failed ones

await get_router().structured(task=..., system=..., parts=[...], schema=..., prompt_version=...)
```

- Implementations: `OpenAIProvider` (Responses API, strict JSON-schema structured output,
  vision, native PDF, `store: false`, SDK retries off), `FakeProvider` (scripted, tests and
  local dev); `AnthropicProvider` / `VertexGeminiProvider` follow in #23. No LiteLLM.
- `Part` = `TextPart` | `ImagePart(bytes, mime)` | `PdfPart(bytes)`; preflight downscales
  images and strips metadata, rejects oversized PDFs (never drops pages); providers without
  native PDF get rasterised pages (`pypdfium2`).
- **Router** (`config/routing.yaml` + `LLM_*_MODEL` env overrides): per-task model,
  temperature, limits and fallback list → retries transient errors with backoff, re-asks on
  schema-invalid / truncated output, then falls back; overall deadline below the job timeout.
- **Errors:** transient (`LLMTimeout`, `LLMRateLimited`, `LLMUnavailable` → job retry), output
  (`LLMSchemaValidationError`, `LLMTruncated`, `LLMRefusal`, `LLMContentFiltered` →
  `needs_attention`), permanent (auth, quota, bad request, input too large / invalid, not
  configured, schema unsupported → `PermanentJobError`); `is_permanent(exc)`.
- Prompts versioned in `app/pipeline/prompts/*.md` with `prompt_version` stored per extraction.
- Pricing table `config/pricing.yaml` (decimal strings, dated ECB USD→EUR rate) → `cost_eur`
  per call → `belegbot.llm.*` metrics → Grafana.

---

## 7. Pipeline

Implemented in `backend/app/pipeline/` (#9); details in [`_docs/pipeline.md`](_docs/pipeline.md).

1. **Preprocess**: decode every stored type (HEIC/HEIF/AVIF/TIFF/BMP/JP2 → JPEG, GIF frame 1, EXIF rotation, JPEG XL unsupported), PDF text layer for classify else page 1 rendered; page limit `PIPELINE_MAX_PAGES` (never truncated); undecodable → `failed`.
2. **Classify** (cheap model) → `DocType`, `tax_relevant`, readable / several documents, printed total, date, vendor, `reason_de`. (The heuristic pre-filter moved to #46.)
3. **Extract** (strong model, `GenericBillExtraction`) for relevant single bills: vendor, recipient, dates, payment method, total, VAT, line items with a category and §35a cost kind each. Official documents are only classified in v1 (`doc_type_not_supported`; #18–#20).
4. **Rules** (`app/tax/bill_rules.py`, pure, golden-tested): primary category = largest line sum (one item per document, #43 splits), deductible, §35a labour share and cash rule, AfA above the GWG limit, plausibility checks, tax year (§11), `category → anlage/zeile` from `params/{year}.yaml`. The LLM only reads.
5. **Assign** person (recipient name, else uploader; §35a household-level) and check fuzzy duplicates.
6. **Persist**: replace the document's item unless the user overrode one; doubtful results → `needs_attention` with one stored `AttentionReason`.
7. **Notify** via `DocumentProcessed` (#11 subscribes).

**Category taxonomy (v1)**: Arbeitsmittel, Fortbildung, Fahrtkosten/Entfernungspauschale, Homeoffice, Arbeitszimmer, Bewerbung, Kontoführung, Gewerkschaft/Berufsverband, Doppelte Haushaltsführung · Vorsorge (KV/PV/RV/Riester/Rürup) · Spenden · Kirchensteuer · Kinderbetreuung · Schulgeld · Krankheitskosten · Pflege · Behinderung · §35a haushaltsnahe Dienstleistungen / Handwerker · Steuerberatung · Anlage V Werbungskosten (AfA, Zinsen, Erhaltung, Nebenkosten) · Kapital (Bescheinigung) · `irrelevant`.

---

## 8. Tax engine (`app/tax/`, pure Python, fully unit-tested)

Near-exact scope:
- **Einkünfte**: §19 (from LStB), §20 KAP incl. Günstigerprüfung, §21 V+V
- Werbungskosten vs. Arbeitnehmer-Pauschbetrag; Entfernungspauschale; Homeoffice-Pauschale vs. Arbeitszimmer
- Sonderausgaben: Vorsorgeaufwendungen (Basis-KV/PV, Altersvorsorge with Höchstbetrag), Spenden (20% cap), Kinderbetreuung (80%, cap), Kirchensteuer, Schulgeld
- agB with **zumutbare Belastung** (stufenweise, BFH 2017)
- §35a Steuerermäßigung (20%, caps)
- §32a tariff, **Splittingverfahren**, Progressionsvorbehalt (Elterngeld, ALG I, Kurzarbeitergeld)
- Kinder: **Günstigerprüfung Kindergeld vs. Kinderfreibetrag** per child
- Soli (Freigrenze + Milderungszone), Kirchensteuer (8% BY/BW, 9% else)
- Result = festzusetzende ESt − (Lohnsteuer + Soli + KiSt from LStB + anrechenbare KapESt) ⇒ refund / back-payment
- **Pflichtveranlagung check**: Steuerklassenkombi III/V or IV mit Faktor, Lohnersatzleistungen > 410 €, Nebeneinkünfte > 410 €, mehrere Arbeitgeber (VI), etc.

**Parameters** — `params/2025.yaml`, `params/2026.yaml` (seed values, **verify against BMF before use**):

| Param | 2025 | 2026 |
|---|---|---|
| Grundfreibetrag (single) | 12,096 € | 12,348 € |
| Kinderfreibetrag + BEA (per child, both parents) | 6,672 + 2,928 = 9,600 € | 6,828 + 2,928 = 9,756 € |
| Kindergeld / month | 255 € | 259 € |
| Arbeitnehmer-Pauschbetrag | 1,230 € | 1,230 € |
| Entfernungspauschale | 0.30 €/km (km 1–20), 0.38 € from km 21 | 0.38 €/km from km 1 |
| Homeoffice-Pauschale | 6 €/day, max 1,260 € | 6 €/day, max 1,260 € |
| Sparer-Pauschbetrag | 1,000 / 2,000 € | 1,000 / 2,000 € |
| Kinderbetreuung | 80%, max 4,800 €/child | same |
| Übungsleiter / Ehrenamt | 3,000 / 840 € | 3,300 / 960 € |

Also tariff zone formulas (§32a Abs. 1), Soli Freigrenze, Sonderausgaben-Pauschbetrag, Vorsorge-Höchstbeträge, §35a caps.

**Validation**: golden tests against the official **BMF Einkommensteuerrechner** for ~20 synthetic household scenarios per year; golden values are collected from the BMF calculator via **browser automation** (scripted, reproducible) and committed as fixtures; CI fails on deviation > 1 €.

---

## 9. Clients

**Web (React + TanStack, `frontend/`)**: own Railway `web` service; the browser only talks to its own origin and `/api/*` is proxied to the `api` service (same-origin, so session cookies stay httpOnly/first-party and no CORS is needed). Dashboard per year (refund estimate, per-Anlage totals, missing-docs hints), document list (#10, `/belege?jahr=`): upload area, "Hochgeladene Belege" (documents without a tax item: in flight, failed, needs attention without item, with "Manuell erfassen" for unreadable / failed / multi-receipt files), then "Belege {jahr}" with filter chips (Alle, Relevant, Unsicher, Prüfen, Manuell) and inline edit of category, relevance, gross / deductible / §35a labour amounts, tax year and person (Anlage / Zeile follow the mapping, vendor / dates / payment method are read-only until #61); every save is an override (`version`-checked, audited, counted); thumbnails come with #35; Haushalt (#13, `/haushalt?jahr=`): a wizard while the year has no profile ("Aus {Jahr} übernehmen" when another year has one, then Du → Veranlagung → Arbeit → Kinder, each step saved, resumed from the data), else a page view with the Veranlagung card, a card per adult (greyed when not in the return), children with months / Freibetrag and "Person hinzufügen"; every nav link keeps `?jahr=`; export button. Mobile-friendly upload via `<input capture>`.

**Bot core** (`app/bot/core.py`) is channel-agnostic: `IncomingMessage(user, files, text)` → commands. Adapters:
- **Telegram** (v1, `python-telegram-bot` or `aiogram`, webhook mode): send photo/PDF/album, `/summary 2025`, `/missing`, `/undo`, `/year 2025`, `/link <code>`
- **WhatsApp** (v2, Meta Cloud API) — same core, new adapter only.

---

## 10. Auth & security / GDPR

- Magic link (random, hashed at rest, 15 min, single use, sent via **Resend**) + **passkeys** (`webauthn` lib) after first login; sessions via httpOnly cookie. Details (#5):
  - **Invite-only:** only existing, non-disabled `app_user` rows get a link; users come from the CLI (`python -m app.auth.cli bootstrap|invite|disable|enable|revoke-sessions`) or the dev seed. No signup.
  - **Tokens:** `secrets.token_urlsafe(32)`, stored only as `sha256` (no signing secret). Sessions are opaque `user_session` rows: 7 days idle, 30 days absolute, a new one on every login (any old cookie sent along is revoked).
  - **Link:** `{APP_BASE_URL}/login/verify#token=…`. The token sits in the fragment (never reaches a server or log); the page strips it and only an "Anmelden" click posts it, so mail scanners cannot burn it. Works on any device. One atomic `UPDATE … RETURNING` = single use; a login also burns the user's other open links.
  - **Cookie:** `__Host-belegbot_session; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age` (plain `belegbot_session` without Secure on `http://localhost`), set through the same-origin `/api` proxy.
  - **CSRF:** `SameSite=Lax` + `X-Requested-With: belegbot` on every POST/PUT/PATCH/DELETE + `Origin` (if sent) must match `APP_BASE_URL` → else `403 csrf`.
  - **No enumeration:** identical `202` for known/unknown/disabled/limited addresses, mail sent after the response; identical `400 invalid_or_expired` for every verify failure; a `422` never echoes input.
  - **Rate limits:** per e-mail 3 / 15 min and 10 / 24 h (DB, silent); per IP 10 link requests and 20 failed verifies / 15 min (in memory, `429` + `Retry-After`). The web proxy replaces `X-Forwarded-For` (`TRUSTED_PROXY_HOPS`).
  - **Protected by default:** every api route needs a session except `/health`, `/version`, `/auth/magic-link`, `/auth/verify`, `/auth/logout` (meta-test); docs are off in production. Every app page sits under the `_authed` layout (SSR-safe redirect to `/login?next=…`).
- Telegram linking: web shows one-time code → `/link 123456` → `channel_link` row; unknown chat IDs are ignored.
- Webhook secret path + `X-Telegram-Bot-Api-Secret-Token` check.
- Steuer-ID & documents are sensitive: encrypt sensitive columns (app-level Fernet key in Railway env), volume on Railway (note: Railway region choice — pick **EU West (Amsterdam)**).
- Uploaded files and their original file names (#6): the name is stored in plain text as display metadata and never logged, traced, put in metrics, job rows or error bodies, or used as a path. Files (mode 0600) and file names are **not encrypted** at this stage (user decision 2026-10-05); encryption at rest (#33) is deferred and revisited with #24's hardening.
- LLM data processing: use providers' zero/limited-retention options; document which provider sees what. Prefer EU endpoints where available (Vertex europe-west3, OpenAI EU data residency if on eligible plan). What OpenAI receives, `store: false`, retention / ZDR and EU residency: [`_docs/llm.md`](_docs/llm.md#data-handling-openai).
- Backups: Railway Postgres backups + nightly `pg_dump` + volume tarball to an offsite bucket (v1.1).
- Disclaimer in UI: estimate, not Steuerberatung.

---

## 11. Observability (Grafana)

- **OTel SDK** in all processes → Grafana Cloud free tier (OTLP endpoint): traces (Tempo), logs (Loki, structured JSON via `structlog`), metrics (Prometheus/Mimir).
- **App dashboard**: request rate/latency/errors, queue depth & job age, pipeline step durations, failures by step.
- **LLM dashboard**: tokens & € per provider/model/step, € per document, latency p50/p95, schema-validation failure rate, fallback rate, **user override rate** (= proxy for error rate, since flow is fully automatic), eval scores per prompt version.
- **Tax dashboard** (user-facing, family only): Grafana Postgres datasource with a **read-only DB role** on views `v_deductions_by_month`, `v_deductions_by_category`, `v_estimate_history`. Embed via Grafana public dashboard link or keep in Grafana with family accounts.
- Alerts: worker down, queue age > 10 min, daily LLM cost > X €.

---

## 12. Quality: evals for "fully automatic"

Since there's no review queue, accuracy must be measured offline:
- The repo is public, so the committed eval set is synthetic: `evals/datasets/bills_v0` (60 documents rendered from `evals/synth/specs/bills_v0.yaml`, fictional "Muster" people and vendors, PDF / scanned PDF / photo / PNG variants) with hand-labelled ground truth (relevant?, category, amounts, §35a share, dates, tax year).
- Optional anonymised real samples stay local only (`evals/datasets_private/`, never committed, #39); user overrides are exported as new labelled examples, private as well (#23).
- `python -m evals.run --dataset bills_v0 --predictor … [--provider openai --model … --prompt-version v3]` → relevance precision/recall, category accuracy, amount exact-match, € error, cost, latency; `report.json` keys are pushed to Grafana (#22); providers are compared via baselines.
- Gate: `--gate` checks `evals/thresholds.yaml` (bills_v0: `status: confirmed` by the user, #9) and the `compare_to` baseline: don't switch default model/prompt unless eval ≥ current. Real runs are recorded once (`--record pipeline-<provider>-<prompt_version>`) and replayed offline in CI (`tests/evals/test_pipeline_recording.py`, which also checks the recording's `prompt_sha256` against `prompts/LOCK.yaml`); the accepted run becomes the baseline `compare_to`. The perfect-reader run (`--predictor pipeline --provider fake --gate`) proves the rules offline in CI.

---

## 13. Dev workflow (Codespaces + Claude Code + Railway)

- `.devcontainer/devcontainer.json` + `.devcontainer/docker-compose.yml`: Python 3.12, `uv`, Node 22 (frontend + Claude Code), Claude Code, Postgres 16 service `db` (healthcheck, named volume). `postCreateCommand` runs `npm ci` in `frontend/` and `uv sync` in `backend/` (once `backend/pyproject.toml` exists). Secrets via Codespaces secrets (`OPENAI_API_KEY`, `RESEND_API_KEY`, `TELEGRAM_BOT_TOKEN`, …); `.env.example` lists every variable.
- Frontend dev: `cd frontend && npm run dev` (port 3000); `/api` is proxied to the FastAPI `api` service (`API_INTERNAL_URL`, default `http://localhost:8000`).
- ADLC for LLM features: evals are first-class from M1 — spec → eval set → build → eval gate → observe → iterate (`_docs/adlc.md`, labels `adlc:spec` / `adlc:eval-gate`).
- Claude Code: note Claude **Pro** includes Claude Code usage; the app's own LLM calls use the separate OpenAI/Anthropic API key.
- `CLAUDE.md`: stack, commands (`uv run pytest`, `ruff`, `alembic`), rule "tax/ is pure, every rule change needs a golden test", "never log document contents or Steuer-ID".
- Telegram in dev: Codespaces forwarded port (public) as webhook URL, or polling mode with `BOT_MODE=polling`.
- **CI (GitHub Actions)**: ruff, mypy, pytest (incl. tax golden tests), alembic migration check, frontend lint + tests. Evals run on demand (manual workflow) to control cost; LLM changes need an eval run before merge.
- **CD**: Railway GitHub integration — auto-deploy `main` (EU West) with **Wait for CI** (`ci-ok`); watch paths `/backend/**` → `api`, `/frontend/**` → `web`; PR environments with their own empty Postgres; migrations run only in the Railway pre-deploy command `alembic upgrade head` (never at startup), serialised by a Postgres advisory lock in `env.py`. CI's `deploy-smoke` job builds both images and smoke-tests them before any deploy. Runbook: `_docs/deploy.md`.

---

## 14. Roadmap

| Milestone | Scope | Exit criterion |
|---|---|---|
| **M0 Skeleton** (1 wk) | repo, devcontainer, CLAUDE.md, FastAPI+Postgres+alembic, Railway deploy, OTel→Grafana hello-world | `/health` live on Railway, trace visible in Grafana |
| **M1 Ingest + LLM layer** (1–2 wk) | upload (web), storage, queue, `LLMProvider` + OpenAI, classify+extract generic bills, **first eval set + runner (ADLC)** | 20 sample bills processed, results in DB, eval gate passes |
| **M2 Telegram** (1 wk) | bot core + Telegram adapter, linking, notifications, `/summary` | photo → reply with category & amount |
| **M3 Profile + tax engine** (2–3 wk) | household wizard, params 2025/2026, full calculator, Pflichtveranlagung check, golden tests | ≤1 € deviation vs BMF calculator on test scenarios |
| **M4 Official docs** (1–2 wk) | Lohnsteuerbescheinigung, Jahressteuerbescheinigung, Nebenkostenabrechnung schemas; KAP + V | own 2025 return reproduced |
| **M5 Summary/export + dashboards** (1 wk) | per-Anlage report PDF/CSV/ZIP, Grafana LLM & tax dashboards | file own 2025 return using the export |
| **M6 Evals + 2nd provider** (1 wk) | expanded eval set, Anthropic/Gemini provider, comparison dashboard | data-driven default model choice |
| **Later** | WhatsApp, e-mail inbox ingest, multi-receipt split, S3/R2 storage, ERiC/ELSTER, public product (GDPR, billing) | — |

---

## 15. Risks & open questions

- **Fully automatic misclassification** → silent missed deductions or wrong claims. Mitigation: evals, override-rate metric, `needs_attention` on validation failures, "low-confidence" badge in UI (no blocking queue). Pipeline rules, attention reasons and the eval gate: [`_docs/pipeline.md`](_docs/pipeline.md).
- **Tax law drift** (yearly changes, e.g. 2026 Entfernungspauschale) → params per year + golden tests; review params each January.
- **Near-exact calc complexity** — Vorsorgeaufwand and Progressionsvorbehalt are the hardest parts; consider validating against ERiC's calculation later.
- **Railway volume single-mount** → v1 merges api+worker; plan migration to object storage.
- **LLM data protection** for family documents → choose retention settings, keep EU option ready (Vertex).
- ~~Open: Anlage V — own rental property now or future-proofing?~~ **Answered:** Anlage V is **in v1** (AfA basis per property needed).
- ~~Open: should non-family users ever be possible?~~ **Answered:** **No — family-only** tenancy; no multi-tenant hardening planned.

---

### Sources for seed parameters (verify before use)
- https://wundertax.de/en/tax-tips/tax-changes-2026/
- https://www.steuertipps.de/steuererklaerung-finanzamt/themen/pauschbetraege-freibetraege-und-hoechstbetraege-fuer-die-steuererklaerung
- BMF Einkommensteuerrechner (for golden tests): https://www.bmf-steuerrechner.de
