"""Bill pipeline (#9): preprocess → classify → extract → rules → persist.

`core.run_pipeline` is shared by the job handler (`handler.py`) and the eval predictor
(`evals/predictors/pipeline.py`). LLM calls go through `app.llm.get_router()` only. See
`_docs/pipeline.md`.
"""
