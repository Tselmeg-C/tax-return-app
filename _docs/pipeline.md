# Bill pipeline (`backend/app/pipeline/`, #9)

An uploaded document becomes at most one tax item, automatically. The LLM reads, code
decides: classify (cheap model) and extract (strong model) return what is printed; the
deductible amount, §35a labour share, cash rule, AfA, tax year, Anlage / Zeile and every
`needs_attention` reason come from pure functions in `backend/app/tax/` (golden-tested).

```
worker job ─▶ handler.py ─▶ core.run_pipeline ─▶ preprocess ─▶ classify ─▶ extract ─▶ rules
                 │            (no DB, shared with evals/predictors/pipeline.py)
                 └─▶ persons + dedupe ─▶ final transaction ─▶ DocumentProcessed event
```

| Module | Role |
|---|---|
| `core.py` | `run_pipeline(inp, *, router, prompt_set, mapping, today, …) -> PipelineResult` |
| `preprocess.py` | decode / convert / count pages (`Prepared`), runs in a thread (PDFs on the loop thread: PDFium is not thread-safe) |
| `schemas.py` | `ClassifyOutput`, `GenericBillExtraction`, `LineItem`, `Confidence`, `CostKind35a` |
| `prompts/` | versioned prompt sets `vN/`, rendering, `LOCK.yaml` |
| `handler.py` | `process_document` (job handler), `check_pipeline_settings` (worker start) |
| `persons.py`, `dedupe.py` | recipient → person, fuzzy duplicate check (DB) |
| `events.py` | `DocumentProcessed` + in-process subscribers (#11 adds Telegram) |
| `dev_fake.py` | canned replies for `fake:test` routing, scripted routers |
| `errors.py` | `CorruptDocument`, `EncryptedPdf`, `TooManyPages`, `ImageTooLarge`, `UnsupportedImageFormat` |
| `app/tax/bill_rules.py`, `app/tax/mapping.py` | the rules (pure) |
| `app/tax/params/{year}.yaml` → `mapping` | category → Anlage / Zeile per year (loader `app/tax_params.py`) |

## Preprocess

| Stored type (#6) | Classify / extract get |
|---|---|
| JPEG, PNG, WebP | the original bytes after a full decode check (#8 rotates, strips metadata, downscales) |
| GIF | PNG of frame 1 |
| BMP, TIFF, JPEG 2000 | JPEG (PNG with alpha); every TIFF page, `page_count` = frames |
| HEIC, HEIF, AVIF | JPEG, EXIF rotation applied (`pillow-heif` / Pillow) |
| JPEG XL | `failed` / `UnsupportedImageFormat` (no maintained, permissively licensed decoder) |
| PDF | extract: the PDF as `PdfPart`; classify: the text layer (≤ `PIPELINE_CLASSIFY_MAX_CHARS`) if every page has ≥ 50 non-space characters, else page 1 as JPEG |

Decode error / truncated → `CorruptDocument`; password → `EncryptedPdf`; more than
`PIPELINE_MAX_PAGES` pages / frames → `TooManyPages` (never truncated); more pixels than
`LLM_MAX_IMAGE_PIXELS` → `ImageTooLarge`. All end `failed` after one attempt, without an LLM
call. `document.page_count` is set for every document.

## Schemas and prompts

`schemas.py` is the spec (#9 issue). Money is a string `^-?\d{1,9}\.\d{2}$` (converted with
`money()` → `Decimal`), dates ISO, enums from `app/domain/enums.py`, confidence an enum
(`high/medium/low` → 0.900 / 0.600 / 0.300). Free text is capped in code (no `maxLength` in the
strict schema). v1 adds `ClassifyOutput.certificate_year` (the year an official document
covers), used as the eval's `tax_year` for documents without a tax item.

Prompt sets live in `prompts/vN/` (`classify.md`, `extract_generic_bill.md`, `guidance.yaml`).
`{{categories}}`, `{{doc_types}}`, `{{payment_methods}}` are rendered from the enums, `LABELS_DE`
and `guidance.yaml`. `prompt_sha256` = sha256 over the rendered prompts and the strict JSON
schemas; `prompts/LOCK.yaml` pins it per version and `tests/pipeline/test_schemas_prompts.py`
fails if an existing version changes. **Never edit an existing version**: copy it to `vN+1`,
change it, add the hash to `LOCK.yaml`, run the eval (`_docs/adlc.md`).
`PIPELINE_PROMPT_VERSION` (empty = highest) selects the set; an unknown one stops the worker.

Only the system prompt and the document parts reach the LLM: never `original_filename`, the
uploader's e-mail, household or person names, ids or the storage key (sentinel test).

## Rules (`app/tax/bill_rules.py`)

1. Not readable → no item, `unreadable`; several documents → `multiple_documents`; official
   type → `doc_type_not_supported` (no extract; #18–#20 add their extraction).
2. Classify says not relevant → one `irrelevant` item (a relevant `other` document is
   extracted like a bill since v2, so a classify slip does not drop a deduction) (gross = printed total or
   0.00, date = `document_date`, payment method `unknown`).
3. With an extraction: lines with a category ≠ `irrelevant` count. None → irrelevant item.
   Otherwise the primary category has the largest absolute line sum; more than one relevant
   category → `multiple_categories` (one item per document in v1, splitting is #43).
   Deductible = signed sum of the primary lines.
4. §35a (`haushaltsnahe` group): labour share = stated amount, else labour + travel + machine
   lines; deductible = labour share. Cash → `is_relevant = false`, 0.00 (labour share kept);
   payment `unknown` → `payment_method_unknown_35a`; no labour share → `labour_share_missing`,
   0.00; negative → `credit_note_35a`, labour share NULL, 0.00.
5. `behinderung`: travel lines (Fahrdienst, §33 Abs. 2a: Pauschale only) do not count; only
   travel → `is_relevant = false`.
6. `wk_arbeitsmittel` / `wk_arbeitszimmer` above 800 € net (GWG): deductible = linear AfA share
   (13 years, pro rata from the month of acquisition) + `asset_depreciation`.
7. Checks: Σ lines vs total > 0.02 → `sum_mismatch`; credit-note flag vs sign →
   `sign_mismatch`; |total| > 100 000 → `implausible_amount`; date after today or before 2000 →
   `implausible_date`; not EUR → `foreign_currency`, 0.00 (#44 converts); low confidence on
   either step → `low_confidence`.
8. Tax year (§11): payment date, else invoice date, else the upload date (Europe/Berlin) +
   `date_missing`. Recurring categories paid 22 Dec – 10 Jan with the invoice in the other year
   → `year_boundary_recurring` (the 10-day rule is a human decision).
9. Mapping: `params/{year}.yaml` → `mapping`; `irrelevant` → NULL; a year without params →
   `unsupported_year`, NULL.

### Attention reasons

`AttentionReason` (`app/domain/enums.py`, German labels in `LABELS_DE`) in priority order:
`classification_failed`, `extraction_failed`, `unreadable`, `multiple_documents`,
`doc_type_not_supported`, `possible_duplicate`, `foreign_currency`, `implausible_amount`,
`implausible_date`, `sum_mismatch`, `sign_mismatch`, `credit_note_35a`, `labour_share_missing`,
`payment_method_unknown_35a`, `multiple_categories`, `asset_depreciation`, `date_missing`,
`unsupported_year`, `year_boundary_recurring`, `low_confidence`. The first that applies is
stored in `document.attention_reason` (CHECK: `needs_attention` ⇒ a reason); all of them go to
the `pipeline.document` log line and `belegbot.pipeline.attention`.

## Handler, errors, resume

| What happened | Handler | `document` ends |
|---|---|---|
| Transient LLM error after the router gave up | write the calls, re-raise | `queued`; #6 retries with backoff, then `failed` |
| Output error (`LLMSchemaValidationError`, `LLMTruncated`, `LLMRefusal`, `LLMContentFiltered`) | write the calls | `needs_attention` / `classification_failed` or `extraction_failed` (doc type kept) |
| Other permanent LLM error | write the calls, `PermanentJobError(error_kind=<class>)` | `failed` |
| Undecodable file | `PermanentJobError` subclass (above) | `failed` |
| Missing file / checksum | `FileMissing` / `ChecksumMismatch` (#6) | `failed` |

- `extraction` is an append-only call log: one row per `LLMCallRecord` (failed attempts
  included), written in its own transaction right after the router call. `raw_json` (the
  validated output, encrypted) only on the successful row.
- Resume: a step with a successful row for this document and prompt version is not called
  again; its `raw_json` is re-validated and reused. A re-run therefore costs nothing and gives
  the same item.
- Final transaction: lock the document; if any of its items is `overridden_by_user`, nothing
  is deleted, changed or added (`pipeline.overrides_kept`); else the document's items are
  replaced by the new one (`extraction_id` = the extract row, else the classify row). Then
  `doc_type`, `page_count`, `status`, `attention_reason`. No `audit_log` rows (system writes).
- Person: recipient normalised (case-fold, umlauts, titles) against household persons (full
  name or last name + first initial); else the uploader's person; else NULL; §35a always NULL.
- Duplicate: another document's item with the same normalised vendor (legal form dropped),
  gross amount and invoice date (payment date if one is missing) → `possible_duplicate`, the
  item is booked with `is_relevant = false`, 0.00.
- After commit: `DocumentProcessed(document_id, household_id, channel, status,
  attention_reason, doc_type)`; a failing subscriber is logged by class name only.

## Observability

Spans `pipeline process_document` → `pipeline preprocess | classify | extract | rules |
persist` (#8's `llm …` spans nest under classify / extract), attributes `document.id`,
`belegbot.pipeline.step | prompt_version | reused | outcome`, `error.type`. Metrics
`belegbot.pipeline.documents{outcome,doc_type}`, `.attention{reason}`,
`.step.duration{step,outcome}`, `.document.cost{outcome}`, `.items{category_group,is_relevant}`,
`.steps_reused{step}`, `.overrides_kept`. Logs `pipeline.step`, `pipeline.document`. Ids and
codes only: never bytes, text, file names, vendors, names, amounts, dates or `str(exc)`.

## Settings

`PIPELINE_PROMPT_VERSION` (empty = highest), `PIPELINE_MAX_PAGES=20` (≤ `LLM_MAX_PDF_PAGES`),
`PIPELINE_CLASSIFY_MAX_CHARS=20000`, `JOB_TIMEOUT_SECONDS=900` (≥ 2 × `LLM_DEADLINE_SECONDS` +
120). The worker refuses to start otherwise (`check_pipeline_settings`, names the variables).

## Local dev without a key

```bash
LLM_CLASSIFY_MODEL=fake:test LLM_EXTRACT_MODEL=fake:test PORT=8000 uv run honcho start -f Procfile
```

Every upload gets the canned "Testmodus" item (`dev_fake.py`: 12.34 € `wk_arbeitsmittel`,
paid 2025-06-01) and ends `done`. Refused with `APP_ENV=production`.

## Prompt versions

- `v1`: first real run (`recordings/bills_v0/pipeline-openai-v1`) failed the gate: classify
  (gpt-4.1-mini) called insurance / Kita / donation / school / rent statements `other` +
  irrelevant or an official type, so they never reached extract (all under-claims).
- `v2` (default): classify prompt with a doc-type decision procedure, the official types
  restricted to their issuers, a list of statements that are always relevant bills, and
  "if in doubt, relevant"; extract prompt clarifies bank-statement dates and payment methods.
  Schemas unchanged.

## Evals

```bash
# offline (CI): perfect reader, must pass the gate with 1.0 on the rule-derived metrics
uv run python -m evals.run --dataset bills_v0 --predictor pipeline --provider fake --gate
# real run (paid; needs OPENAI_API_KEY exported in the shell), recorded once
uv run python -m evals.run --dataset bills_v0 --predictor pipeline --provider openai \
    --prompt-version v2 --record pipeline-openai-v2 --compare-to heuristic --gate
# accept it as the baseline (offline, from the recording), then set compare_to in thresholds.yaml
uv run python -m evals.run --dataset bills_v0 --predictor replay --recording pipeline-openai-v2 \
    --save-baseline pipeline-openai-v2
```

Perfect-reader exception by design: `b044-jahressteuerbescheinigung` has no category / gross
(only classified in v1, extraction in #19). `payment_method` / `invoice_date` are lower for
classify-only and official documents (reported, not gated). `tests/evals/test_pipeline_recording.py`
replays `recordings/bills_v0/pipeline-openai-<version>/` with `--gate` and checks its
`prompt_sha256` against `LOCK.yaml`; it is skipped until the recording exists.
