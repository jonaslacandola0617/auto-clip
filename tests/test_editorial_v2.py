from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from autoclip.ai import FixtureProvider
from autoclip.editorial_v2 import (
    ProviderFailure, bounded_provider_call, cache_identity, comparative_rank,
    create_candidate_windows, create_shorter_sequence_variant, duration_contract_for,
    enforce_candidate_duration, passes_editorial_gates, prefilter_windows, quality_score,
)
from autoclip.media import FFmpegService
from autoclip.models import (
    AutoClipProject, ClipCandidate, DurationContract, EditorialQuality, ScoreDimensions,
    Transcript, TranscriptSegment, TranscriptWord, WorkflowProfile,
)
from autoclip.pipeline import CorePipeline
from autoclip.storage import Workspace, load_project, save_project
from tests.helpers import mt, sample_project, sample_source
from tests.test_phase2c import accepted_sequence


def long_transcript() -> Transcript:
    texts = [
        "Why this challenge actually matters.",
        "The fastest player said nobody could beat him.",
        "They agreed to race because the argument would not stop.",
        "The start was close and the tension kept building.",
        "Finally the answer came and he won the race.",
        "That result surprised everyone.",
    ]
    words: list[TranscriptWord] = []
    segments: list[TranscriptSegment] = []
    for index, text in enumerate(texts):
        start = index * 5
        ids = []
        for offset, token in enumerate(text.split()):
            identifier = f"w{index}_{offset}"
            ids.append(identifier)
            words.append(TranscriptWord(identifier, mt(start + min(offset, 3)), mt(start + min(offset, 3) + 1), token))
        segments.append(TranscriptSegment(f"s{index}", mt(start), mt(start + 5), text, ids))
    return Transcript("long-v1", "en", segments, words, "fixture")


PAYLOAD = {"candidates": [{
    "start": 0, "end": 25, "title": "The race challenge", "hook": "Why this race matters",
    "category": "entertainment", "reason": "A complete challenge with a result",
    "scores": {"hook": 90, "standalone_context": 85, "payoff": 88, "emotion": 80},
}]}


class CountingV2Provider(FixtureProvider):
    def __init__(self) -> None:
        super().__init__(PAYLOAD)
        self.fast_calls = 0
        self.strong_calls = 0

    def rank_candidate_windows(self, windows, metadata):
        self.fast_calls += 1
        return super().rank_candidate_windows(windows, metadata)

    def plan_edits(self, windows, metadata):
        self.strong_calls += 1
        return super().plan_edits(windows, metadata)


class V2WindowTests(unittest.TestCase):
    def test_candidate_windows_are_deterministic_and_source_grounded(self) -> None:
        source, transcript = sample_source(), long_transcript()
        contract = DurationContract(12, 20, 25, 28)
        first = create_candidate_windows(source, transcript, contract)
        second = create_candidate_windows(source, transcript, contract)
        self.assertEqual([asdict(item) for item in first], [asdict(item) for item in second])
        self.assertTrue(first)
        self.assertEqual(first[0].provenance["transcript_revision"], transcript.revision_id)
        self.assertTrue(first[0].transcript_segment_ids)
        self.assertLessEqual(first[0].duration_seconds, contract.hard_maximum)

    def test_local_prefilter_mode_is_deterministic_and_bounded(self) -> None:
        windows = create_candidate_windows(sample_source(), long_transcript(), DurationContract(10, 15, 20, 25))
        self.assertEqual([item.id for item in prefilter_windows(windows)], [item.id for item in prefilter_windows(windows)])
        self.assertLessEqual(len(prefilter_windows(windows, mode="fast")), 12)


class V2DurationTests(unittest.TestCase):
    def test_duration_contract_serializes_and_legacy_profile_migrates(self) -> None:
        contract = DurationContract(12, 20, 25, 28)
        self.assertEqual(json.loads(json.dumps(asdict(contract)))["hard_maximum"], 28)
        migrated = WorkflowProfile.from_dict({"id": "old", "name": "Old", "min_duration_seconds": 15, "max_duration_seconds": 45})
        self.assertEqual(asdict(migrated.duration_contract), {"minimum": 15.0, "target": 45.0, "preferred_maximum": 45.0, "hard_maximum": 45.0})

    def test_hard_maximum_shortens_only_at_complete_payoff_boundary(self) -> None:
        candidate = ClipCandidate("c", mt(0), mt(30), "Race", "Why", "entertainment", "reason", ScoreDimensions(90, 90, 90, 80), {})
        result = enforce_candidate_duration(candidate, DurationContract(12, 20, 25, 25), long_transcript(), sample_source())
        self.assertIsNotNone(result)
        self.assertLessEqual(float(result.source_end.seconds - result.source_start.seconds), 25)

    def test_overlong_candidate_without_payoff_is_rejected(self) -> None:
        transcript = long_transcript()
        transcript.segments[4].text = "The conversation continued"
        candidate = ClipCandidate("c", mt(0), mt(30), "Race", "Why", "talk", "reason", ScoreDimensions(90, 90, 90, 80), {})
        self.assertIsNone(enforce_candidate_duration(candidate, DurationContract(12, 20, 25, 25), transcript, sample_source()))

    def test_shorter_sequence_variant_is_non_destructive_and_bounded(self) -> None:
        sequence = accepted_sequence()
        original_count = len(sequence.segments)
        contract = DurationContract(5, 8, 9, 10)
        variant = create_shorter_sequence_variant(sequence, contract)
        self.assertEqual(len(sequence.segments), original_count)
        if variant is not None:
            self.assertNotEqual(variant.id, sequence.id)
            self.assertLessEqual(variant.duration_seconds, 10)


class V2QualityTests(unittest.TestCase):
    def candidate(self, identifier: str, start: int, quality: EditorialQuality) -> ClipCandidate:
        return ClipCandidate(identifier, mt(start), mt(start + 15), identifier, "hook", "test", "reason", ScoreDimensions(quality.hook, quality.standalone_clarity, quality.payoff, quality.energy), {}, quality)

    def test_quality_schema_and_profile_specific_weighting(self) -> None:
        energetic = EditorialQuality(85, 80, 90, 85, 60, 60, 95, 2, 92)
        clear = EditorialQuality(70, 75, 20, 85, 98, 95, 35, 2, 70)
        self.assertGreater(quality_score(energetic, "high_energy"), quality_score(clear, "high_energy"))
        self.assertGreater(quality_score(clear, "educational"), quality_score(energetic, "educational"))

    def test_hook_cold_viewer_and_payoff_gates(self) -> None:
        good = self.candidate("good", 0, EditorialQuality(80, 70, 60, 80, 80, 70, 70, 2, 80))
        weak = self.candidate("weak", 30, EditorialQuality(40, 70, 60, 80, 80, 70, 70, 2, 80))
        self.assertTrue(passes_editorial_gates(good))
        self.assertFalse(passes_editorial_gates(weak))

    def test_comparative_ranking_prefers_entertaining_and_suppresses_duplicates(self) -> None:
        strong = self.candidate("strong", 0, EditorialQuality(90, 90, 85, 90, 80, 80, 90, 1, 95))
        duplicate = self.candidate("duplicate", 2, EditorialQuality(70, 70, 50, 75, 85, 60, 50, 2, 60))
        distinct = self.candidate("distinct", 40, EditorialQuality(75, 75, 65, 80, 85, 75, 65, 2, 78))
        selected, suppressed = comparative_rank([duplicate, distinct, strong], story_style="high_energy", limit=5)
        self.assertEqual(selected[0].id, "strong")
        self.assertIn("duplicate", suppressed)
        self.assertEqual(len(selected), 2)  # requested count remains a maximum, not filler


class V2ReliabilityTests(unittest.TestCase):
    def test_cache_identity_changes_for_relevant_revisions_only(self) -> None:
        transcript = long_transcript()
        first = cache_identity(transcript, WorkflowProfile("p", "P", revision=1), "Fixture", "fast")
        second = cache_identity(transcript, WorkflowProfile("p", "P", revision=2), "Fixture", "fast")
        self.assertNotEqual(first, second)
        self.assertNotIn("caption_preset", first)
        self.assertNotIn("export_defaults", first)

    def test_bounded_retry_and_failure_classification(self) -> None:
        calls = 0
        def flaky():
            nonlocal calls
            calls += 1
            raise RuntimeError("429 quota reached")
        with self.assertRaises(ProviderFailure) as raised:
            bounded_provider_call(flaky, max_retries=1)
        self.assertEqual(calls, 2)
        self.assertEqual(raised.exception.kind, "quota_or_rate_limit")

    def test_provider_failure_preserves_prior_project_and_window_cache(self) -> None:
        class FailingProvider(CountingV2Provider):
            def rank_candidate_windows(self, windows, metadata):
                raise RuntimeError("429 quota reached")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            project = sample_project()
            project.transcripts = [long_transcript()]
            save_project(workspace, project)
            pipeline = CorePipeline(workspace, FFmpegService(), object(), FailingProvider())
            with self.assertRaises(ProviderFailure):
                pipeline.analyze_source_v2(project.sources[0], project.transcripts[-1], profile=WorkflowProfile("p", "P"))
            restored = load_project(workspace)
            window_cache = list((workspace.root / "analysis").glob("v2-windows-*.json"))
        self.assertEqual([item.id for item in restored.candidates], [item.id for item in project.candidates])
        self.assertEqual(len(window_cache), 1)

    def test_pipeline_progress_metrics_and_cache_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = CountingV2Provider()
            pipeline = CorePipeline(Workspace(Path(directory)), FFmpegService(), object(), provider)
            profile = WorkflowProfile("p", "P", min_duration_seconds=12, max_duration_seconds=28, target_duration_seconds=20, preferred_max_duration_seconds=25, hard_max_duration_seconds=28, desired_output_count=3)
            progress: list[int] = []
            first, windows, metrics = pipeline.analyze_source_v2(sample_source(), long_transcript(), profile=profile, progress=lambda count, _stage: progress.append(count))
            second, _, cached = pipeline.analyze_source_v2(sample_source(), long_transcript(), profile=profile)
        self.assertTrue(windows)
        self.assertEqual(len(first), 1)
        self.assertEqual([item.id for item in first], [item.id for item in second])
        self.assertEqual(progress, [1])
        self.assertEqual((provider.fast_calls, provider.strong_calls), (1, 1))
        self.assertEqual(metrics.ai_request_count, 2)
        self.assertGreaterEqual(cached.cache_hits, 2)
        self.assertIsNotNone(metrics.time_to_first_candidate_seconds)
        self.assertEqual(json.loads(json.dumps(asdict(metrics)))["pipeline_version"], "v2.1-hybrid-v1")

    def test_existing_project_round_trip_preserves_v2_diagnostics(self) -> None:
        project = sample_project()
        raw = project.to_dict()
        restored = AutoClipProject.from_dict(raw)
        self.assertEqual(restored.id, project.id)
        self.assertEqual(duration_contract_for(WorkflowProfile("p", "P")).hard_maximum, 45)


if __name__ == "__main__":
    unittest.main()
