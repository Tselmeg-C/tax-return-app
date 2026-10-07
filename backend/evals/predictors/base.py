"""The predictor interface owned by the eval harness (#7). The LLM provider (#8) is not
imported here; #9 registers a `pipeline` predictor built on top of it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from evals.dataset import EvalCase
from evals.paths import EvalPaths
from evals.schema import Prediction


@runtime_checkable
class Predictor(Protocol):
    name: str

    def describe(self) -> dict[str, str]:
        """provider, model, prompt_version and other ids. Never secrets."""
        ...

    async def predict(self, case: EvalCase) -> Prediction: ...


@dataclass(frozen=True)
class PredictorOptions:
    """CLI options for a predictor. The first four are the user-facing ones; the rest are
    filled by the runner so offline predictors can find their inputs."""

    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    recording: str | None = None
    dataset: str | None = None
    paths: EvalPaths | None = None


def _no_env(_: PredictorOptions) -> tuple[str, ...]:
    return ()


@dataclass(frozen=True)
class PredictorSpec:
    name: str
    factory: Callable[[PredictorOptions], Predictor]
    requires_env: Callable[[PredictorOptions], tuple[str, ...]] = _no_env
    default_provider: str | None = None
