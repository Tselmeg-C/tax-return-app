"""Offline LLM eval harness (`_docs/adlc.md`): datasets, predictors, metrics, reports, gate.

Never imports `app.llm`: predictors that call a real provider are plugged in through the
registry in `evals.predictors` (#9).
"""
