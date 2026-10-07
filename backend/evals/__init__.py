"""Offline LLM eval harness (`_docs/adlc.md`): datasets, predictors, metrics, reports, gate.

Never imports `app.llm` directly: the `pipeline` predictor (#9) reaches it only through
`app.pipeline`, registered in `evals.predictors`.
"""
