from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from autoclip import SCHEMA_VERSION
from autoclip.ai import FixtureProvider
from autoclip.captions import build_edit_sequence_caption_track
from autoclip.intelligent_edit import consolidate_moments, moments_from_candidates, plan_sequence, reflow_sequence, validate_action, validate_integrity
from autoclip.desktop_service import DesktopService
from autoclip.media import FFmpegService
from autoclip.models import (
    ApprovedClip, AutoClipProject, EditSegment, EditSequence, EditorialAction, EditorialIntegrityResult,
    Moment, StoryConcept, Transcript, TranscriptSegment, TranscriptWord,
    Timeline,
)
from autoclip.exporters.otio import build_otio
from autoclip.exporters.premiere_xml import build_premiere_xml
from autoclip.time import MediaTime
from autoclip.pipeline import CorePipeline
from autoclip.storage import Workspace, load_project, save_project
from tests.helpers import mt, sample_project, sample_source, sample_transcript


def moment(identifier: str, start: int, end: int, *, topics: list[str] | None = None, types: list[str] | None = None, words: list[str] | None = None) -> Moment:
    return Moment(identifier, "media_001", mt(start), mt(end), [f"s_{identifier}"], words or [], identifier, types or ["statement"], topic_ids=topics or ["topic"], confidence=.9)


def story(moment_ids: list[str]) -> StoryConcept:
    return StoryConcept("story_1", "A coherent story", "premise", "hook", "context", "development", "payoff", moment_ids, 30, "Related evidence", {"topic": "topic"})


class CountingProvider(FixtureProvider):
    def __init__(self, moments, stories):
        super().__init__({"candidates": []}, moments=moments, stories=stories)
        self.moment_calls = 0
        self.story_calls = 0

    def discover_moments(self, chunks, metadata):
        self.moment_calls += 1
        return super().discover_moments(chunks, metadata)

    def construct_stories(self, moments, metadata):
        self.story_calls += 1
        return super().construct_stories(moments, metadata)


class Phase2ATests(unittest.TestCase):
    def test_models_round_trip_and_phase1_schema_migrates(self) -> None:
        project = sample_project()
        moments = [moment("m1", 10, 14), moment("m2", 40, 45)]
        sequence = plan_sequence(story(["m1", "m2"]), {item.id: item for item in moments}, sample_transcript(), sample_source())
        project.moments, project.story_concepts, project.edit_sequences = moments, [story(["m1", "m2"])], [sequence]
        restored = AutoClipProject.from_dict(project.to_dict())
        self.assertEqual(restored.edit_sequences[0].segments[1].source_in, mt(40))
        legacy = sample_project().to_dict()
        legacy["schema_version"] = "0.2-phase0"
        legacy.pop("moments", None)
        self.assertEqual(AutoClipProject.from_dict(legacy).schema_version, SCHEMA_VERSION)

    def test_duplicate_moments_consolidate_without_losing_topics(self) -> None:
        first = moment("m1", 10, 20, topics=["claim"])
        second = moment("m2", 12, 19, topics=["reaction"])
        result = consolidate_moments([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].topic_ids, ["claim", "reaction"])

    def test_candidate_cache_derives_distinct_source_grounded_moments(self) -> None:
        project = sample_project()
        moments = moments_from_candidates(project.candidates, project.transcripts[-1], project.sources[0])
        self.assertEqual(len(moments), 1)
        self.assertNotEqual(moments[0].id, project.candidates[0].id)
        self.assertEqual(moments[0].provider_provenance["derived_from"], project.candidates[0].id)
        self.assertTrue(moments[0].transcript_word_ids)

    def test_no_story_is_forced_when_provider_finds_insufficient_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = CorePipeline(Workspace(Path(directory)), FFmpegService(), object(), CountingProvider([], []))
            self.assertEqual(pipeline.construct_story_concepts([moment("m1", 10, 12)], sample_transcript()), [])

    def test_non_contiguous_sequence_reflows_and_persists_manual_order(self) -> None:
        transcript = sample_transcript()
        moments = [moment("m1", 10, 12), moment("m2", 40, 42)]
        sequence = plan_sequence(story(["m1", "m2"]), {item.id: item for item in moments}, transcript, sample_source())
        sequence.segments.reverse()
        reflow_sequence(sequence)
        self.assertEqual([item.source_in for item in sequence.segments], [mt(40), mt(10)])
        self.assertEqual([item.timeline_start for item in sequence.segments], [0, 2])
        with tempfile.TemporaryDirectory() as directory:
            project = sample_project()
            project.moments, project.story_concepts, project.edit_sequences = moments, [story(["m1", "m2"])], [sequence]
            workspace = Workspace(Path(directory))
            save_project(workspace, project)
            self.assertEqual(load_project(workspace).edit_sequences[0].segments[0].moment_id, "m2")

    def test_trim_removal_and_transcript_invalidation_persist(self) -> None:
        moments = [moment("m1", 10, 12, words=["w1", "w2"]), moment("m2", 40, 42, words=["w3", "w4"])]
        sequence = plan_sequence(story(["m1", "m2"]), {item.id: item for item in moments}, sample_transcript(), sample_source())
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory) / "project")
            project = sample_project()
            project.moments, project.story_concepts, project.edit_sequences, project.analysis_revision = moments, [story(["m1", "m2"])], [sequence], "analysis"
            save_project(workspace, project)
            service = DesktopService(Path(directory) / "state", Path(directory))
            service.update_edit_sequence({"project_path": str(workspace.project_file), "edit_sequence_id": sequence.id, "operation": "trim", "segment_id": sequence.segments[0].id, "source_in": 10.25, "source_out": 11.75})
            trimmed = load_project(workspace).edit_sequences[0]
            self.assertAlmostEqual(float(trimmed.segments[0].source_in.seconds), 10.25, places=3)
            service.update_edit_sequence({"project_path": str(workspace.project_file), "edit_sequence_id": sequence.id, "operation": "remove_segment", "segment_id": sequence.segments[1].id})
            self.assertEqual(len(load_project(workspace).edit_sequences[0].segments), 1)
            service.correct_transcript({"project_path": str(workspace.project_file), "segment_id": "s1", "text": "Corrected"})
            invalidated = load_project(workspace)
            self.assertEqual((invalidated.moments, invalidated.story_concepts, invalidated.edit_sequences), ([], [], []))
            self.assertIsNone(invalidated.analysis_revision)

    def test_integrity_rejects_removed_negation_and_false_reaction(self) -> None:
        transcript = Transcript("r", "en", [TranscriptSegment("s", mt(0), mt(5), "I would never call that a scam", ["w1", "w2", "w3"])], [TranscriptWord("w1", mt(1), mt(2), "never"), TranscriptWord("w2", mt(2), mt(3), "call"), TranscriptWord("w3", mt(3), mt(4), "scam")], "fixture")
        setup = moment("setup", 0, 4, topics=["claim"], words=["w1", "w2", "w3"])
        reaction = moment("reaction", 20, 22, topics=["other"], types=["reaction"])
        segments = [EditSegment("a", "setup", "media_001", mt(2), mt(4), 0, "statement", "call scam", 0), EditSegment("b", "reaction", "media_001", mt(20), mt(22), 2, "reaction", "wow", 1)]
        sequence = EditSequence("e", "s", "unsafe", "media_001", segments, [], EditorialIntegrityResult("review_required", [], []))
        result = validate_integrity(sequence, transcript, {"setup": setup, "reaction": reaction})
        self.assertEqual(result.status, "failed")
        self.assertFalse(next(item for item in result.checks if item["type"] == "negation_retention")["passed"])
        self.assertFalse(next(item for item in result.checks if item["type"] == "reaction_association")["passed"])

    def test_editorial_action_parameters_are_bounded(self) -> None:
        validate_action(EditorialAction("a", "punch_in", 1, 2, {"scale": 1.12}, "emphasis"), 5)
        with self.assertRaises(ValueError):
            validate_action(EditorialAction("a", "punch_in", 1, 2, {"scale": 2}, "emphasis"), 5)

    def test_moment_and_story_cache_reuse_provider_results(self) -> None:
        raw_moments = [{"id": "m1", "start": 10, "end": 12, "summary": "one", "types": ["hook"], "segment_ids": ["s1"], "word_ids": ["w1"], "topics": ["topic"], "entities": [], "characteristics": {}, "confidence": .9, "reasoning": "hook"}, {"id": "m2", "start": 40, "end": 42, "summary": "two", "types": ["payoff"], "segment_ids": ["s2"], "word_ids": ["w3"], "topics": ["topic"], "entities": [], "characteristics": {}, "confidence": .9, "reasoning": "payoff"}]
        raw_stories = [{"id": "s1", "title": "story", "premise": "p", "hook": "h", "context": "c", "development": "d", "payoff": "p", "moment_ids": ["m1", "m2"], "target_duration_seconds": 30, "explanation": "related", "coherence": {}, "integrity_considerations": []}]
        with tempfile.TemporaryDirectory() as directory:
            provider = CountingProvider(raw_moments, raw_stories)
            pipeline = CorePipeline(Workspace(Path(directory)), FFmpegService(), object(), provider)
            first = pipeline.discover_moments(sample_source(), sample_transcript())
            pipeline.discover_moments(sample_source(), sample_transcript())
            pipeline.construct_story_concepts(first, sample_transcript())
            pipeline.construct_story_concepts(first, sample_transcript())
            self.assertEqual((provider.moment_calls, provider.story_calls), (1, 1))

    def test_render_plan_and_captions_follow_segment_timeline(self) -> None:
        moments = [moment("m1", 10, 12), moment("m2", 40, 42)]
        sequence = plan_sequence(story(["m1", "m2"]), {item.id: item for item in moments}, sample_transcript(), sample_source())
        arguments = FFmpegService("ffmpeg", "ffprobe").plan_edit_sequence_render(Path("source.mp4"), sequence, Path("out.mp4"))
        self.assertEqual(arguments.count("-i"), 2)
        self.assertIn("concat=n=2:v=1:a=1", arguments[arguments.index("-filter_complex") + 1])
        captions = build_edit_sequence_caption_track(sequence, sample_transcript())
        self.assertEqual([cue.edit_segment_id for cue in captions.cues], [sequence.segments[0].id, sequence.segments[1].id])
        self.assertLess(float(captions.cues[0].start.seconds), float(captions.cues[1].start.seconds))

    def test_editable_exports_snap_valid_source_times_to_frames(self) -> None:
        source = sample_source()
        clips = [
            ApprovedClip("segment-1", source.id, MediaTime.from_seconds(149.4, source.time_base, exact=False), MediaTime.from_seconds(161.96, source.time_base, exact=False), "Hook"),
            ApprovedClip("segment-2", source.id, MediaTime.from_seconds(658.02, source.time_base, exact=False), MediaTime.from_seconds(671.48, source.time_base, exact=False), "Context"),
        ]
        timeline = Timeline("smart", 1080, 1920, source.frame_rate, clips)
        otio, _ = build_otio(timeline, {source.id: source})
        xml, _ = build_premiere_xml(timeline, {source.id: source})
        self.assertEqual(len(otio["tracks"]["children"][0]["children"]), 2)
        self.assertEqual(len(otio["tracks"]["children"][1]["children"]), 2)
        self.assertEqual(len(xml.getroot().findall("./sequence/media/video/track/clipitem")), 2)
        self.assertEqual(len(xml.getroot().findall("./sequence/media/audio/track/clipitem")), 2)
        self.assertGreater(int(xml.getroot().findtext("./sequence/duration") or 0), 0)


if __name__ == "__main__":
    unittest.main()
