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
   fixtures.
3. **Build** — implement the feature against the spec; the eval runner can run
   it end to end.
4. **Eval gate** — run the eval and compare with the thresholds in the issue
   (and the current default). The PR only merges if the gate passes: a new
   prompt / model / provider becomes default only if it scores ≥ the current
   one. Results go into the PR description.
5. **Observe** — after deploy, watch the feature in Grafana: **user override
   rate** (proxy for error rate, since the flow is fully automatic), **cost**
   (€ per document / per call, tokens), latency, schema-validation failure and
   fallback rate.
6. **Iterate** — every user override becomes a new labelled example; grow the
   eval set, adjust spec / prompt / model, and go through the gate again.

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
