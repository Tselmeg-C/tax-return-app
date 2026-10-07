"""`replay`: score a recorded run offline (no network). Usage and latency come from the
recording. A case missing from the recording gets `error_kind: MissingRecording`."""

from __future__ import annotations

from evals.dataset import EvalCase
from evals.paths import EvalPaths
from evals.predictors.base import PredictorOptions
from evals.recording import Recording, RecordingError, load_recording, recording_dir
from evals.schema import Prediction


class ReplayPredictor:
    name = "replay"

    def __init__(self, recording: Recording, current_dataset_hash: str | None = None) -> None:
        self._recording = recording
        self.recording_stale = False
        if current_dataset_hash is not None:
            self.check_dataset_hash(current_dataset_hash)

    def check_dataset_hash(self, current: str) -> None:
        self.recording_stale = self._recording.meta.get("dataset_hash") != current

    def describe(self) -> dict[str, str]:
        recorded = self._recording.meta.get("predictor", {})
        out = {
            "name": self.name,
            "recording": self._recording.name,
            "usage": "recorded",
        }
        for key in ("name", "provider", "model", "prompt_version"):
            if isinstance(recorded, dict) and key in recorded:
                out[f"recorded_{key}" if key == "name" else key] = str(recorded[key])
        out.setdefault("provider", "none")
        out.setdefault("model", "-")
        out.setdefault("prompt_version", "-")
        return out

    async def predict(self, case: EvalCase) -> Prediction:
        pred = self._recording.predictions.get(case.id)
        if pred is None:
            return Prediction(error_kind="MissingRecording")
        return pred.model_copy(deep=True)


def make_replay(options: PredictorOptions) -> ReplayPredictor:
    if not options.recording:
        raise RecordingError("replay needs --recording NAME")
    if options.dataset is None:
        raise RecordingError("replay needs a dataset")
    paths = options.paths or EvalPaths()
    return ReplayPredictor(
        load_recording(recording_dir(paths.recordings, options.dataset, options.recording))
    )
