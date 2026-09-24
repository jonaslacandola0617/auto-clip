import tempfile
import unittest
from pathlib import Path

from autoclip.ai import FixtureProvider
from autoclip.pipeline import CorePipeline
from autoclip.storage import Workspace
from tests.helpers import sample_source


PAYLOAD = {"candidates": [{
    "start": 8, "end": 28, "title": "Useful moment", "hook": "This works",
    "category": "education", "reason": "Complete thought",
    "scores": {"hook": 90, "standalone_context": 85, "payoff": 80},
}]}


class FakeMedia:
    def __init__(self) -> None:
        self.audio_calls = 0
        self.clip_calls = 0

    def extract_speech_audio(self, source: Path, output: Path, *, cancel_event=None) -> None:
        self.audio_calls += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fixture")

    def extract_clip(self, source: Path, output: Path, start, end) -> None:
        self.clip_calls += 1


class FakeTranscriber:
    model_size = "fixture"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio: Path, **kwargs):
        self.calls += 1
        return {
            "language": "en", "model": "fixture",
            "words": [{"id": "w1", "start": 10, "end": 11, "text": "This"}, {"id": "w2", "start": 11, "end": 12, "text": "works"}],
            "segments": [{"id": "s1", "start": 10, "end": 12, "text": "This works", "word_ids": ["w1", "w2"]}],
        }


class CountingProvider(FixtureProvider):
    def __init__(self):
        super().__init__(PAYLOAD)
        self.calls = 0

    def analyze_transcript(self, chunks, metadata):
        self.calls += 1
        return super().analyze_transcript(chunks, metadata)


class PipelineCacheTests(unittest.TestCase):
    def test_transcription_and_analysis_are_reused_but_clips_can_be_reextracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            media = FakeMedia()
            transcriber = FakeTranscriber()
            provider = CountingProvider()
            pipeline = CorePipeline(Workspace(Path(directory)), media, transcriber, provider)
            pipeline.run(sample_source(), Path(directory) / "exports")
            pipeline.run(sample_source(), Path(directory) / "exports")
            self.assertEqual(transcriber.calls, 1)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(media.audio_calls, 1)
            self.assertEqual(media.clip_calls, 2)


if __name__ == "__main__":
    unittest.main()
