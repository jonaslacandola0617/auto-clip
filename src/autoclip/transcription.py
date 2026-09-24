from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable


ProgressCallback = Callable[[float, str], None]


class TranscriptionCancelled(RuntimeError):
    """Raised after a transcription reaches a safe cancellation checkpoint."""


class FasterWhisperTranscriber:
    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 1,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size

    @property
    def cache_identity(self) -> dict[str, Any]:
        return {
            "model": self.model_size,
            "compute_type": self.compute_type,
            "beam_size": self.beam_size,
            "word_timestamps": True,
        }

    @property
    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def model_is_cached(self) -> bool:
        model_path = Path(self.model_size).expanduser()
        if model_path.is_dir():
            return True
        cache_root = os.environ.get("HF_HUB_CACHE")
        if cache_root:
            hub = Path(cache_root)
        else:
            hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
            hub = hf_home / "hub"
        repository = hub / f"models--Systran--faster-whisper-{self.model_size}"
        return any((repository / "snapshots").glob("*/model.bin"))

    def transcribe(
        self,
        audio_path: Path,
        *,
        duration_seconds: float | None = None,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        def check_cancelled() -> None:
            if cancelled and cancelled():
                raise TranscriptionCancelled("Transcription cancelled at a safe checkpoint.")

        def report(value: float, stage: str) -> None:
            if progress:
                progress(max(0.0, min(1.0, value)), stage)

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed") from exc

        check_cancelled()
        report(0.02, "Loading cached transcription model" if self.model_is_cached else "Downloading transcription model")
        model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        check_cancelled()
        report(0.08, "Starting local transcription")
        segments, info = model.transcribe(
            str(audio_path),
            beam_size=self.beam_size,
            best_of=self.beam_size,
            word_timestamps=True,
        )
        result_segments: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        last_reported_progress = 0.08
        last_reported_at = 0.0
        for segment_index, segment in enumerate(segments):
            check_cancelled()
            word_ids: list[str] = []
            for word_index, word in enumerate(segment.words or []):
                word_id = f"w_{segment_index}_{word_index}"
                word_ids.append(word_id)
                words.append({"id": word_id, "start": word.start, "end": word.end, "text": word.word})
            result_segments.append({"id": f"s_{segment_index}", "start": segment.start, "end": segment.end, "text": segment.text, "word_ids": word_ids})
            if duration_seconds and duration_seconds > 0:
                segment_progress = 0.1 + 0.88 * min(float(segment.end) / duration_seconds, 1.0)
            else:
                segment_progress = min(0.1 + segment_index * 0.002, 0.96)
            now = time.monotonic()
            if segment_progress - last_reported_progress >= 0.005 or now - last_reported_at >= 1.0:
                report(segment_progress, f"Transcribing locally · {float(segment.end) / 60:.1f} min processed")
                last_reported_progress = segment_progress
                last_reported_at = now
        check_cancelled()
        report(0.99, "Finalizing transcript words")
        return {"language": info.language, "model": self.model_size, "segments": result_segments, "words": words}
