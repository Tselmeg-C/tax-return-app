# ADLC — lifecycle for LLM features

Any feature where an LLM makes or influences a decision (classify, extract,
categorise, bot replies, …) follows this lifecycle. Evals are first-class from
M1: an LLM feature is not done until it has an eval set and passes its gate.

The issue for such a task uses the **llm-feature** template and fills in its
`ADLC` section (Spec, Eval, Gate, Observe).

## Steps

1. **Spec** — write down what the LLM must produce before writing code: the
   pydantic output schema, field list with types and allowed values, the
   prompt (versioned in `backend/app/pipeline/prompts/*.md`), which provider /
   model the router uses, and the failure behaviour (retry, fallback,
   `needs_attention`).
2. **Eval set** — build the labelled dataset the feature is measured on, in
   `backend/evals/` (anonymised inputs + expected output). Pick the metrics
   (e.g. relevance precision/recall, category accuracy, amount exact-match,
   € error) and record a baseline. No real document contents or Steuer-IDs in
   fixtures. The repo is public, so committed datasets are synthetic (generated
   from a spec by `evals/synth`); real samples stay in the git-ignored
   `evals/datasets_private/` (#39). Commands (from `backend/`, see
   `backend/evals/README.md`):

   ```bash
   uv run python -m evals.synth --dataset bills_v0            # render from the spec
   uv run python -m evals.validate --dataset bills_v0         # labels, coverage, privacy
   uv run python -m evals.run --dataset bills_v0 --predictor heuristic \
       --save-baseline heuristic                               # trivial baseline
   ```
3. **Build** — implement the feature against the spec; the eval runner can run
   it end to end.
4. **Eval gate** — run the eval and compare with the thresholds in the issue
   (and the current default). The PR only merges if the gate passes: a new
   prompt / model / provider becomes default only if it scores ≥ the current
   one. Results go into the PR description.

   ```bash
   uv run python -m evals.run --dataset bills_v0 --predictor pipeline \
       --provider openai --model <m> --prompt-version <v> --record <name> --gate
   uv run python -m evals.run --dataset bills_v0 --predictor replay \
       --recording <name> --gate                               # offline, in CI
   ```

   - Thresholds live in `backend/evals/thresholds.yaml` per dataset
     (`status: proposed | confirmed`); `compare_to` names the baseline
     (`evals/baselines/<dataset>/<name>.json`) the candidate must not fall
     below on the `regression.metrics` (± `tolerance`). `--compare-to` overrides
     it for one run.
   - Exit codes: `0` pass (or skipped: the predictor's key is not set), `1`
     gate failed, `2` usage / config / dataset error.
   - Real runs are recorded once (`--record`, by the user or #27) and replayed
     in CI (`--predictor replay`); recordings hold prediction fields only, no
     prompts, raw LLM output or document text.
   - The PR description gets the run's `report.md`.
5. **Observe** — after deploy, watch the feature in Grafana: **user override
   rate** (proxy for error rate, since the flow is fully automatic), **cost**
   (€ per document / per call, tokens), latency, schema-validation failure and
   fallback rate.
6. **Iterate** — every user override becomes a new labelled example; grow the
   eval set, adjust spec / prompt / model, and go through the gate again.

## Worked example: #9 (bill pipeline v1)

1. **Spec**: `ClassifyOutput` / `GenericBillExtraction` (`backend/app/pipeline/schemas.py`),
   prompt set `prompts/v1/` pinned in `prompts/LOCK.yaml`, routing `classify` → gpt-4.1-mini,
   `extract` → gpt-4.1 (fallback gpt-4.1-mini), failure behaviour in `_docs/pipeline.md`.
2. **Eval set**: `bills_v0` (#7). Offline proof of the rules: the perfect reader
   (`--predictor pipeline --provider fake --gate`, in CI) scores 1.0 on every rule-derived
   metric except one documented by-design case.
3. **Gate**: thresholds `status: confirmed` by the user; one real run, recorded:
   `--predictor pipeline --provider openai --prompt-version v1 --record pipeline-openai-v1
   --compare-to heuristic --gate`; its `report.md` goes into the PR.
4. **Baseline**: the accepted run is saved (`--predictor replay --recording pipeline-openai-v1
   --save-baseline pipeline-openai-v1`) and becomes `compare_to` in `thresholds.yaml`; the
   recording is replayed in CI (`tests/evals/test_pipeline_recording.py`, which also fails when
   the prompt hash no longer matches). Every later prompt / model / schema change (a new
   `prompts/vN`) needs a new recording that is ≥ this baseline.

## Labels

- **`adlc:spec`** — add when the issue defines or changes an LLM spec: a new
  or changed output schema, field list or prompt, or a new LLM step. The spec
  and the eval set must exist before building starts.
- **`adlc:eval-gate`** — add when merging the issue's PR depends on an eval
  run passing: any change to a prompt, schema, model, provider or routing that
  affects LLM output. The gate thresholds are listed in the issue's ADLC
  section.

Most new LLM features carry both labels. Tasks that do not touch LLM
behaviour (plain backend, tax engine, frontend, infra) carry neither and use
the feature or bug template.
