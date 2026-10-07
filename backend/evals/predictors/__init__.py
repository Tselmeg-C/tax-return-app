"""Predictor registry. #9 adds `pipeline` with `register(PredictorSpec(...))`."""

from __future__ import annotations

from evals.predictors.base import Predictor, PredictorOptions, PredictorSpec
from evals.predictors.heuristic import make_heuristic
from evals.predictors.oracle import make_oracle
from evals.predictors.replay import make_replay

REGISTRY: dict[str, PredictorSpec] = {}


def register(spec: PredictorSpec) -> PredictorSpec:
    REGISTRY[spec.name] = spec
    return spec


register(PredictorSpec(name="oracle", factory=make_oracle))
register(PredictorSpec(name="heuristic", factory=make_heuristic))
register(PredictorSpec(name="replay", factory=make_replay))

__all__ = ["REGISTRY", "Predictor", "PredictorOptions", "PredictorSpec", "register"]
