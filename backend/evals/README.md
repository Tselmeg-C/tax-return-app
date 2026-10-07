# evals — offline eval harness for the bill pipeline

Defines "correct" for the bill pipeline (`plan.md` §7, §12; `_docs/adlc.md`) and measures any
predictor against it. Everything runs offline: no network, no API key, no DB. The harness never
imports `app.llm` directly; the `pipeline` predictor (#9) runs the production pipeline through
`app.pipeline`.

All commands run from `backend/`.

## Layout

```
evals/
  run.py              # CLI: python -m evals.run
  validate.py         # CLI: python -m evals.validate
  synth/              # generator: __main__.py, generate.py, raster.py, writer.py,
                      #   templates/*.py, specs/bills_v0.yaml (single source of truth)
  schema.py           # ExpectedLabel, Prediction, CallUsage, CaseResult, Report (pydantic)
  dataset.py          # load + hash a dataset, EvalCase (has no label)
  predictors/         # registry (__init__), Protocol (base), oracle, heuristic, replay, pipeline (#9)
  fakes/              # perfect_reader.py: flawless classify / extract replies per bills_v0 case
  recording.py        # record / load recordings
  metrics.py  report.py  gate.py  privacy.py  paths.py
  datasets/bills_v0/  # manifest.yaml + cases/<id>/{document.*, label.yaml}  (generated, committed)
  recordings/bills_v0/fixture-noisy/   # hand-made recording with deliberate mistakes
  baselines/bills_v0/heuristic.{json,md}
  thresholds.yaml     # gate thresholds (status: proposed)
  LABELLING.md        # field definitions and labelling rules
```

Git-ignored: `evals/reports/` (run output) and `evals/datasets_private/` (local real samples, #39).

## Commands

```bash
uv run python -m evals.validate --dataset bills_v0                 # labels, coverage, sizes, privacy
uv run python -m evals.run --dataset bills_v0 --predictor oracle --gate      # self-test: exit 0
uv run python -m evals.run --dataset bills_v0 --predictor heuristic          # trivial baseline
uv run python -m evals.run --dataset bills_v0 --predictor heuristic --gate   # exit 1 (by design)
uv run python -m evals.run --dataset bills_v0 --predictor replay --recording fixture-noisy
uv run python -m evals.synth --dataset bills_v0            # regenerate every case from the spec
uv run python -m evals.synth --dataset bills_v0 --case b012-steuerberatung-split
uv run python -m evals.synth --dataset bills_v0 --check    # compare a fresh render with git
```

Runner options: `--provider P --model M --prompt-version V` (passed to the predictor),
`--recording NAME` (replay), `--record NAME [--force]`, `--gate`, `--thresholds FILE`,
`--compare-to BASELINE`, `--save-baseline NAME [--force]`, `--out DIR`, `--concurrency 4`,
`--case-timeout 120`, `--cases ID,ID` or `--tags TAG,TAG` (subset; not with `--gate` /
`--save-baseline`).

Output: `evals/reports/<UTC yyyymmddThhmmssZ>-<dataset>-<predictor>[-<model>][-<prompt_version>]/`
with `report.json` (stable sorted keys, codes and numbers only) and `report.md` (paste it into
the PR description). stdout shows case ids, counts and the markdown report; never document
text, vendor, person hint, exception messages or env var values.

### Exit codes

| Code | `evals.run` | `evals.validate` | `evals.synth --check` |
|---|---|---|---|
| 0 | ok, gate passed, or skipped (key not set) | dataset valid | committed files match |
| 1 | `--gate` failed | problems found | a case differs (ids listed) |
| 2 | usage / config / dataset error (one line) | unknown dataset | spec error |

### No key, no failure

A predictor declares the env vars it needs (`requires_env`). If one is unset or empty, the
runner does not build the predictor, prints exactly one line

```
SKIPPED: predictor '<name>' (provider '<provider>') needs <VAR>, which is not set. No real-provider eval was run.
```

writes a report with `"status": "skipped"` and exits 0 (even with `--gate`). Only the variable
name is printed. CI never sets a key, so CI never calls a paid API.

## Metrics

A failed case (`error_kind`) or a `None` field counts as wrong. Ratios have 4 decimals; €
amounts are decimal strings with 2 decimals, cost metrics with 4 (a document costs fractions of
a cent); a metric with a zero denominator is `null` (`n/a`). Money is `Decimal`, never float.

`n_cases`, `n_errors`, `error_rate`; `relevance_precision` / `_recall` / `_f1` (positive =
`expected.tax_relevant`; `None` is FN for a positive and FP for a negative case);
`doc_type_accuracy`; `category_accuracy`, `category_group_accuracy`, `per_group`,
`top_confusions`; `gross_exact_match`; `deductible_exact_match` (a `tax_relevant: false`
prediction counts as `0.00`); `deductible_abs_error_eur_mean` / `_max` / `_sum` (`None` → 0);
`overclaim_eur_sum`, `underclaim_eur_sum` (they add up to the abs error sum);
`labour_share_35a_abs_error_eur_mean`; `tax_year_accuracy`, `payment_method_accuracy`,
`invoice_date_exact_match`; `vendor_match`, `person_hint_match` (report only, never gated);
`cost_eur_total`, `cost_eur_per_doc_mean`, `cost_eur_per_doc_p95`, `input_tokens_total`,
`output_tokens_total` (`null` without calls); `latency_ms_p50`, `latency_ms_p95` (per case the
sum of `calls[].latency_ms`, else the measured wall time; nearest rank).

## Gate and baselines

`--gate` checks `thresholds.yaml` for the dataset: every threshold (`min` / `max`; a
`required` metric that is `n/a` fails), and, when `compare_to` (or `--compare-to`) names a
baseline that is not stale, the regression rule: each `regression.metrics` entry must not be
worse than the baseline by more than `tolerance` (`per_metric` overrides). `status: proposed`
prints a note and does not change the exit code. A dataset without an entry → exit 2.

`--save-baseline NAME` writes `baselines/<dataset>/NAME.{json,md}` (the report). It refuses to
overwrite without `--force` and refuses subset or stale-recording runs (exit 2). On a skipped
run (key not set) nothing is saved but the exit code stays 0, as Decision 4 ("no key, no
failure") requires; one line says the baseline was not written. The same holds for `--record`. A baseline
whose dataset hash differs from the current dataset is stale: the report says so, the
regression rule is skipped (one warning), absolute thresholds still apply.

The heuristic baseline's metrics are deterministic except `latency_ms_*` (wall time), so tests
compare everything but latency.

## The `pipeline` predictor (#9)

Runs `app.pipeline.core.run_pipeline` (the code the worker runs) on the case bytes, without DB
steps (person matching and dedupe are tested with pytest). Options: `--provider openai`
(default; `requires_env` = `OPENAI_API_KEY`) or `fake`; `--model provider:model` (both steps)
or `classify=…,extract=…`; `--prompt-version vN` (default: highest). `describe()` adds both
effective models, `prompt_sha256` (from `app/pipeline/prompts/LOCK.yaml`), `routing_version`
and `pricing_version`. `tax_relevant` is the final item's `is_relevant` (after the rules);
documents without an item (official ones) predict `deductible_amount` 0.00 and the
`certificate_year` as `tax_year`.

`--provider fake` answers with the **perfect reader** (`evals/fakes/perfect_reader.py`): what a
flawless reader returns, built from the values the spec prints (never the computed label
fields). It must pass the gate with 1.0 on the rule-derived metrics; by design
`b044-jahressteuerbescheinigung` misses category / gross (only classified in v1, #19). It is a
test fixture, never a baseline.

Recording names: `pipeline-<provider>-<prompt_version>` (e.g. `pipeline-openai-v1`).

## Record and replay a real run

```bash
# once, with the key exported in the shell (by the user or the manual workflow #27):
uv run python -m evals.run --dataset bills_v0 --predictor pipeline --provider openai \
    --prompt-version v1 --record pipeline-openai-v1 --compare-to heuristic --gate
# offline, anywhere (CI):
uv run python -m evals.run --dataset bills_v0 --predictor replay --recording pipeline-openai-v1 --gate
```

A recording is `recordings/<dataset>/<name>/meta.json` (`describe()`, dataset name + hash, git
sha, UTC timestamp, label schema version) + `predictions.jsonl` (one `{"case_id",
"prediction"}` per line, sorted). Prediction fields only: never prompts, raw LLM output or
document text. `--record` refuses to overwrite without `--force` and refuses private datasets.
On replay a missing case gets `error_kind: MissingRecording`; a recording with another
`dataset_hash` sets `"recording_stale": true` with one warning.

## Add a case

1. Add it to `synth/specs/bills_v0.yaml`: `id`, `template`, `variant`, `tags`, `doc` (values
   printed on the document) and `expected` (the label; see `LABELLING.md`). Fictional names
   only ("Muster" / "Beispiel"), masked identifiers, no 11-digit numbers.
2. `uv run python -m evals.synth --dataset bills_v0 --case <id>` and look at the document.
3. `uv run python -m evals.validate --dataset bills_v0`.
4. The dataset hash changes: re-save the baselines (`--save-baseline heuristic --force`),
   update `fixture-noisy` (its expected metrics are asserted in `tests/evals/test_run.py`).

## Add a predictor

Implement `Predictor` (`name`, `describe()`, `async predict(case) -> Prediction`) and register
it in `evals/predictors/__init__.py`:

```python
register(
    PredictorSpec(
        name="pipeline",
        factory=make_pipeline,  # PredictorOptions -> Predictor
        requires_env=lambda o: ("OPENAI_API_KEY",) if (o.provider or "openai") == "openai" else (),
        default_provider="openai",
    )
)
```

`EvalCase` (`id`, `path`, `mime_type`, `variant`, `tags`, `read_bytes()`) has no label. Copy
one `CallUsage` per LLM call (#8's `LLMResult` usage fields). Exceptions and timeouts become
`error_kind` (class name only).

## Generator determinism (`--check`)

Documents are rendered with reportlab (`invariant=1`, standard PDF fonts, fixed metadata),
rasterised with pypdfium2 and transformed with Pillow using per-case seeds; versions are locked
in `uv.lock`. `--check` renders into a temp dir and compares sha256 per file. If raster bytes
ever differ across machines while `label.yaml` is identical, the case passes via a fallback
(same PDF text layer and page count for PDFs, same dimensions for images) and is listed as
such. On the dev container (linux x86_64) the bytes are identical.
