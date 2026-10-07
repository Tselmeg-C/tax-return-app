# LLM layer (`backend/app/llm/`)

Provider-agnostic structured output: send text, images and PDFs plus a pydantic schema, get
back a validated object with tokens, € cost, latency and a request id. OpenAI is the first
provider (#8); Anthropic / Gemini follow in #23. The package has no DB access and imports
neither `app.queue` nor `evals`.

## Interface

```python
from app.llm import get_router, TextPart, ImagePart, PdfPart, is_permanent

result = await get_router().structured(
    task="extract",                # a key of routing.yaml `tasks`
    system=prompt_text,            # never logged
    parts=[ImagePart(data=jpeg_bytes, mime="image/jpeg"), PdfPart(pdf_bytes)],
    schema=MyExtraction,           # pydantic model; money as strings, never float/Decimal
    prompt_version="extract-v3",   # telemetry only
    model=None,                    # "provider:model" override (evals); replaces the fallback list
)
result.data        # validated MyExtraction
result.raw_text    # exact model output: document content, store encrypted, never log
result.cost_eur    # Decimal (6 places) of the successful call
result.calls       # one LLMCallRecord per HTTP attempt incl. failed ones (persist one row each)
```

- `LLMProvider` (`provider.py`): `name`, `async structured(LLMRequest) -> LLMResult`
  (exactly one HTTP call), `async aclose()`. Registered providers: `openai`, `fake`.
- `LLMRouter` (`router.py`) adds preflight, routing, retries, re-ask, fallback, the deadline,
  pricing and telemetry, identically for every provider. `get_router()` is the process-wide
  instance (providers are created on first use). `LLMRouter(settings, providers={...})` or
  `router.with_providers({"fake": FakeProvider(script)})` gives a router with given provider
  instances (per job / per eval case) without touching `get_router()`.
- `FakeProvider(script)` (`fake.py`, pricing key `fake:test`): returns `FakeReply(data=…)`
  (validated against the request schema) or raises scripted `LLMError`s in order, records
  `FakeCall`s without content, raises `FakeScriptExhausted` when empty, and is refused when
  `APP_ENV=production`.
- `repr()` of parts, requests, results, records and errors shows sizes, ids and numbers only.

### Router behaviour

1. **Preflight** the parts (below). Input errors raise at once, no HTTP call.
2. **Primary model.** `LLMTimeout`, `LLMRateLimited`, `LLMUnavailable` are retried on the same
   model up to `LLM_MAX_ATTEMPTS` (3) in total, sleeping
   `min(LLM_BACKOFF_BASE_SECONDS · 2^(n−1), LLM_BACKOFF_MAX_SECONDS)` × [0.8, 1.2]. A 429 with
   `retry-after(-ms)` waits that long instead; if that is over `LLM_RETRY_AFTER_MAX_SECONDS`
   (30) or the remaining deadline, `LLMRateLimited` (with `retry_after_s`) is raised at once
   so the job queue's backoff takes over.
3. **Output errors.** `LLMSchemaValidationError` / `LLMTruncated` re-ask the same model up to
   `schema_retries` times with the identical request (truncated: `max_output_tokens` doubled,
   capped at the model's `max_output_tokens_cap`); invalid output is never sent back.
   `LLMRefusal` / `LLMContentFiltered` are not re-asked.
4. **Fallback.** When the primary is exhausted by transient or output errors, each `fallback`
   model is tried in order with the same rules (`belegbot.llm.fallbacks` +1 per switch).
   Permanent errors never retry or fall back.
5. **Deadline** `LLM_DEADLINE_SECONDS` (300; #9 requires `JOB_TIMEOUT_SECONDS` ≥ 2 × 300 + 120, default 900) covers the
   whole call incl. sleeps; when exceeded → `LLMTimeout` with the calls so far.
6. The final exception is the last error, with all `calls` and `fallback_used`; output errors
   are raised with `retryable=False`.

Worst case per job attempt: `LLM_MAX_ATTEMPTS × (1 + schema_retries) × (1 + len(fallback))`
calls (12 with the defaults and one fallback). This multiplies with job retries; the hard
budget cap is #41.

## Errors

`str(exc)` is a fixed template (`LLMRateLimited: openai returned HTTP 429`); it never contains
a response body, the SDK message, a key, a prompt or document text. The SDK exception is
chained as `__cause__`: never log `str()` of it. Store `type(exc).__name__` as `error_kind`.
`is_permanent(exc)` = `not exc.retryable` for `LLMError`, else False.

| Class | Triggered by (OpenAI) | Router | `retryable` | #9 maps to |
|---|---|---|---|---|
| `LLMTimeout` | httpx timeout, deadline exceeded | retry, then fallback | True | re-raise → job retry |
| `LLMRateLimited` | 429 (not quota), `rate_limit_exceeded` | retry honouring `retry-after`, then fallback | True | re-raise → job retry |
| `LLMUnavailable` | 500–504, 408, 409, connection error, `status: failed` (`server_error`) | retry, then fallback | True | re-raise → job retry |
| `LLMSchemaValidationError` | not JSON, fails the schema, empty output (`error_types`, `error_count` only) | re-ask, then fallback | False | `needs_attention` (recommended) or `PermanentJobError` |
| `LLMTruncated` | `incomplete` / `max_output_tokens` | re-ask with doubled limit, then fallback | False | as above |
| `LLMRefusal` | `refusal` output item | fallback only | False | as above |
| `LLMContentFiltered` | `incomplete` / `content_filter` | fallback only | False | as above |
| `LLMAuthError` | 401, 403 | none | False | `PermanentJobError` |
| `LLMQuotaExceeded` | 429 with `insufficient_quota` (also code `credit_balance_exhausted`) | none | False | `PermanentJobError` |
| `LLMBadRequest` | 400, 404 (unknown model), 422, other 4xx | none | False | `PermanentJobError` |
| `LLMInputTooLarge` | preflight limits, 400 `context_length_exceeded`, 413 | none | False | `PermanentJobError` |
| `LLMInputInvalid` | undecodable / unsupported image, corrupt or encrypted PDF, decompression bomb | none | False | `PermanentJobError` |
| `LLMNotConfigured` | key unset/empty, unknown task / provider, fake in production | none | False | `PermanentJobError` |
| `LLMSchemaUnsupported` | schema not expressible as strict JSON schema (`Decimal`, `float`, `dict`, `Any`) | none | False | `PermanentJobError` |

Groups: `LLMTransientError` (first three), `LLMOutputError` (next four), `LLMPermanentError`
(rest).

## Inputs (`inputs.py`)

- Images (`image/jpeg|png|webp|gif` only; HEIC, TIFF, … → `LLMInputInvalid`): decoded with
  `Image.MAX_IMAGE_PIXELS = LLM_MAX_IMAGE_PIXELS` (bombs → `LLMInputInvalid`), EXIF
  orientation applied, downscaled with LANCZOS to `LLM_MAX_IMAGE_PX` (2048) on the long side
  (never cropped), re-encoded as JPEG q85 (PNG with alpha) **without any metadata** (EXIF
  incl. GPS, XMP, comments, ICC, unknown APPn segments). Every image is re-encoded, so only
  pixels reach the provider. Animated GIFs and multi-picture JPEGs (MPO, e.g. Android Ultra
  HDR) use the first (primary) frame. Still over `LLM_MAX_IMAGE_BYTES` → `LLMInputTooLarge`.
- PDFs (pypdfium2): corrupt / password-protected → `LLMInputInvalid`; more than
  `LLM_MAX_PDF_PAGES` (20) pages → `LLMInputTooLarge` (pages are never dropped). `pdf_input:
  native` sends the PDF as `input_file` named `document.pdf`; `rasterize` (for #23) renders
  each page to an image (long side ≤ `LLM_MAX_IMAGE_PX`) and applies the image rules.
- Whole request: at most `LLM_MAX_IMAGES` (20) images after rasterising and
  `LLM_MAX_REQUEST_MB` (20 MB, base64) in total.

OpenAI's documented limits (checked 2026-10-06): files under 50 MB each and 50 MB combined per
request ([PDF inputs](https://developers.openai.com/api/docs/guides/pdf-files)); images PNG /
JPEG / WEBP / non-animated GIF, up to 512 MB payload and 1 500 images per request
([vision](https://developers.openai.com/api/docs/guides/images-vision)). No page limit is
documented for PDFs. Our defaults stay well below these.

## Routing (`config/routing.yaml`)

Per task: `model` (`provider:model`), `temperature`, `max_output_tokens`, `timeout_s`,
`schema_retries`, `fallback` (ordered list). Per model under `models:`: `supports_temperature`
(false → no temperature is sent, e.g. reasoning models), `pdf_input` (`native` /
`rasterize`), `max_output_tokens_cap`. Bump `version` on every change.

Env overrides (empty = the file): `LLM_CLASSIFY_MODEL`, `LLM_EXTRACT_MODEL` replace a task's
model; `LLM_FALLBACK_MODEL` (comma-separated, or `none`) replaces every fallback list.
Validation runs when the router is built (and in a test on the committed file): every routed
or fallback model needs a `models:` entry and a price, its provider must be registered
(`anthropic:` is rejected until #23), and `fake` is refused under `APP_ENV=production`.
Messages name the setting or file key, never the value.

Proposed defaults (#8; confirmed by #9's eval gate): `classify` → `openai:gpt-4.1-mini`,
`extract` → `openai:gpt-4.1` with fallback `openai:gpt-4.1-mini`, temperature 0.

## Pricing (`config/pricing.yaml`)

`cost_eur` is an **estimate** from list prices at a fixed exchange rate, not the invoice.
Prices are quoted decimal strings per 1 000 tokens (input, optional cached input, output;
reasoning tokens count as output) in `USD` (× `fx.usd_eur`) or `EUR`:

```
cost = ((input − cached) · input_per_1k + cached · cached_input_per_1k + output · output_per_1k) / 1000
```

quantised to 0.000001 € (`ROUND_HALF_UP`), all `Decimal`. Loading rejects YAML floats,
negative prices and unknown keys. Lookup: `provider:model`, then `provider:<response
model>`; an unpriced model (only possible with an eval `model=` override) costs 0 with
`cost_known=False`, increments `belegbot.llm.unpriced_calls` and logs `llm.unpriced_model`
once per process and model. A response without usage also gives `cost_known=False`.

**Update procedure:** check <https://developers.openai.com/api/docs/pricing> (Standard tier)
and the ECB reference rate
(<https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html>;
`usd_eur = 1 / (USD per EUR)`), edit the prices, `fx.usd_eur`, the dates in `source` /
`fx.source`, and bump `version` to today's date. EU data-residency endpoints charge a 10 %
uplift for models released on or after 2026-03-05; add it to the price if such a model is
used through `eu.api.openai.com`.

## Telemetry (`telemetry.py`)

Every attempt goes through one wrapper in the router, so all providers emit the same data.

- **Spans:** parent `llm {task}` per router call; child `chat {gen_ai.request.model}` (kind
  CLIENT) per HTTP attempt. Allow-listed attributes only: `gen_ai.operation.name`,
  `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`,
  `gen_ai.response.id`, `gen_ai.request.temperature`, `gen_ai.request.max_tokens`,
  `gen_ai.response.finish_reasons`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
  `belegbot.llm.{task,prompt_version,schema,attempt,fallback,cost_eur,pricing_version,request_id}`,
  `belegbot.llm.input.{parts,images,bytes}`, `belegbot.llm.validation_error_count`,
  `error.type`. Errors set status ERROR without a description; no exception messages.
  No content-capturing instrumentation (no `opentelemetry-instrumentation-openai*`).
- **Metrics** (attributes `gen_ai.provider.name`, `gen_ai.request.model`, `belegbot.llm.task`
  plus the listed ones):

  | Name | Type, unit | Extra attributes |
  |---|---|---|
  | `belegbot.llm.calls` | counter `{call}` | `outcome` (`ok` or error class) |
  | `belegbot.llm.tokens` | counter `{token}` | `gen_ai.token.type` (`input` / `output`) |
  | `belegbot.llm.cost` | counter `EUR` | – |
  | `belegbot.llm.duration` | histogram `s` | `outcome` |
  | `belegbot.llm.retries` | counter `{retry}` | `reason` |
  | `belegbot.llm.fallbacks` | counter `{fallback}` | `from_model`, `to_model`, `reason` |
  | `belegbot.llm.unpriced_calls` | counter `{call}` | – |

- **Logs:** `llm.call` per attempt (INFO ok, WARNING error) with `provider`, `model`, `task`,
  `prompt_version`, `attempt`, `outcome`, `error_kind`, `latency_ms`, `usage_in`,
  `usage_out`, `cost_eur`, `request_id`, `retry_in_s` (when retrying). Token counts are
  `usage_in` / `usage_out` because the log redactor masks keys containing `token`. The
  request id is the only provider-side identifier logged. `llm.auth_failed` (ERROR, provider
  only) on `LLMAuthError`. The `openai`, `httpx` and `httpcore` loggers are capped at WARNING
  (the SDK logs request options incl. the prompt at DEBUG).

## Tests

- `uv run pytest` runs `backend/tests/llm/` offline: sockets are blocked, `OPENAI_API_KEY` /
  `LLM_*` are removed from the environment, the router gets an injected clock and sleep.
- `FakeProvider` for router behaviour; the real `OpenAIProvider` against `respx`-mocked
  `https://api.openai.com/v1/responses` with recorded fixtures in
  `tests/llm/fixtures/openai/`. **The repo is public: fixtures are synthetic only** (fictional
  vendors, `req_test_…` / `resp_test_…` ids, no org / project headers, no keys); a test
  enforces it.
- Provider contract suite: `tests/llm/contract.py` (cases + harnesses), run by
  `test_contract.py` against the fake and OpenAI; #23 adds its harnesses.
- Sentinel test: runtime sentinels in prompt, parts, response and SDK error body never reach
  logs, spans, metric attributes or exception text.
- Live (paid, never in CI): `uv run pytest -m live` (skipped without `OPENAI_API_KEY`; a plain
  `uv run pytest` deselects it). Smoke: `uv run python -m app.llm.smoke [--task classify]`
  prints `provider, model, tokens, cost_eur, latency_ms, request_id` or
  `SKIPPED: OPENAI_API_KEY is not set`.

## Data handling (OpenAI)

Checked 2026-10-06 against
[Data controls in the OpenAI platform](https://developers.openai.com/api/docs/guides/your-data).

- **What OpenAI receives:** the system prompt, the text parts, the preflighted images
  (downscaled, metadata stripped) and PDFs (named `document.pdf`), the JSON schema, the
  model id and limits. No user, household, file name, `user` / `metadata` /
  `safety_identifier` field. Prompts must not contain names or the Steuer-ID unless the
  feature needs them (#9 decides and tests it).
- **`store: false`** on every request: no application-state retention of the response.
- **Training:** API data is not used for training by default (since 2023-03-01) unless the
  organisation opts in.
- **Abuse monitoring:** inputs / outputs may be retained up to 30 days. **Zero Data
  Retention** excludes them, but needs prior approval by OpenAI and is then selected in the
  organisation / project data-retention settings.
- **EU data residency:** an EU project uses `https://eu.api.openai.com/v1` (set
  `OPENAI_BASE_URL`); it needs eligible plans / approval and adds a 10 % price uplift for
  models released on or after 2026-03-05.
- These settings are decided by the user at the final deployment (#42), together with a
  project spend limit until #41 adds an in-app cap.
