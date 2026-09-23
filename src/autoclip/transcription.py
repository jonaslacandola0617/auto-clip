from __future__ import annotations

from pathlib import Path
from typing import Any


class FasterWhisperTranscriber:
    def __init__(self, model_size: str = "small", device: str = "cpu", compute_type: str = "int8") -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

    @property
    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed") from exc
        model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        segments, info = model.transcribe(str(audio_path), word_timestamps=True)
        result_segments: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        for segment_index, segment in enumerate(segments):
            word_ids: list[str] = []
            for word_index, word in enumerate(segment.words or []):
                word_id = f"w_{segment_index}_{word_index}"
                word_ids.append(word_id)
                words.append({"id": word_id, "start": word.start, "end": word.end, "text": word.word})
            result_segments.append({"id": f"s_{segment_index}", "start": segment.start, "end": segment.end, "text": segment.text, "word_ids": word_ids})
        return {"language": info.language, "model": self.model_size, "segments": result_segments, "words": words}

