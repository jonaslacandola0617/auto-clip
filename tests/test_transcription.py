import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autoclip.transcription import FasterWhisperTranscriber, TranscriptionCancelled


class FakeWhisperModel:
    def __init__(self, *_args, **_kwargs):
        pass

    def transcribe(self, _path, **_kwargs):
        word = SimpleNamespace(start=0.0, end=1.0, word=" hello")
        segments = [
            SimpleNamespace(start=0.0, end=1.0, text="hello", words=[word]),
            SimpleNamespace(start=1.0, end=2.0, text="again", words=[word]),
        ]
        return segments, SimpleNamespace(language="en")


class TranscriptionControlTests(unittest.TestCase):
    def test_progress_reports_model_and_media_position(self) -> None:
        reports = []
        with tempfile.TemporaryDirectory() as directory:
            transcriber = FasterWhisperTranscriber(directory)
            module = SimpleNamespace(WhisperModel=FakeWhisperModel)
            with patch.dict(sys.modules, {"faster_whisper": module}):
                result = transcriber.transcribe(
                    Path(directory) / "audio.wav",
                    duration_seconds=2,
                    progress=lambda value, stage: reports.append((value, stage)),
                )
        self.assertEqual(result["language"], "en")
        self.assertTrue(any("Loading cached" in stage for _, stage in reports))
        self.assertTrue(any("min processed" in stage for _, stage in reports))
        self.assertEqual(reports[-1][1], "Finalizing transcript words")

    def test_cancellation_is_honored_between_segments(self) -> None:
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 4

        with tempfile.TemporaryDirectory() as directory:
            transcriber = FasterWhisperTranscriber(directory)
            module = SimpleNamespace(WhisperModel=FakeWhisperModel)
            with patch.dict(sys.modules, {"faster_whisper": module}):
                with self.assertRaises(TranscriptionCancelled):
                    transcriber.transcribe(Path(directory) / "audio.wav", cancelled=cancelled)


if __name__ == "__main__":
    unittest.main()
