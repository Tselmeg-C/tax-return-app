"""`oracle`: returns the expected label. Proves the scoring (all quality metrics 1.0)."""

from __future__ import annotations

from evals.dataset import EvalCase, load_dataset
from evals.predictors.base import PredictorOptions
from evals.schema import ExpectedLabel, Prediction


class OraclePredictor:
    name = "oracle"

    def __init__(self, labels: dict[str, ExpectedLabel]) -> None:
        # The labels come through the constructor, never through `EvalCase`.
        self._labels = labels

    def describe(self) -> dict[str, str]:
        return {"name": self.name, "provider": "none", "model": "oracle", "prompt_version": "-"}

    async def predict(self, case: EvalCase) -> Prediction:
        return Prediction.from_expected(self._labels[case.id].expected)


def make_oracle(options: PredictorOptions) -> OraclePredictor:
    if options.dataset is None:
        raise ValueError("oracle needs the dataset")
    return OraclePredictor(load_dataset(options.dataset, options.paths).labels)
