from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autoclip.intelligent_edit import MAX_EDITORIAL_REBUILDS, plan_reviewed_sequence, validate_action_policy
from autoclip.media import FFmpegService, MediaToolError
from autoclip.models import (
    AutoClipProject, EditSegment, EditSequence, EditorialAction, EditorialIntegrityResult,
    EditorialReviewResult, Moment, StoryConcept, Transcript, TranscriptSegment, TranscriptWord,
)
from autoclip.storage import Workspace, load_project, save_project
from tests.helpers import mt, sample_project, sample_source


def editorial_fixture(*, topic: str = "Tyreek Hill 40-yard challenge", hook: str = "Race me 40 yards.", payoff: str = "If you beat me, I am out.", duplicate: bool = False):
    texts = [hook, "Tyreek Hill is the NFL player Speed wants to race.", "He says the winner proves who is faster.", payoff]
    if duplicate:
        texts.insert(2, texts[1])
    moments = []
    segments = []
    words = []
    cursor = 10
    for index, text in enumerate(texts):
        word_id = f"w{index}"
        segment_id = f"s{index}"
        moment_id = f"m{index}"
        words.append(TranscriptWord(word_id, mt(cursor), mt(cursor + 1), text))
        segments.append(TranscriptSegment(segment_id, mt(cursor), mt(cursor + 4), text, [word_id]))
        moments.append(Moment(moment_id, "media_001", mt(cursor), mt(cursor + 4), [segment_id], [word_id], text, ["statement"], entities=["Tyreek Hill"], topic_ids=["tyreek-race"], confidence=.9))
        cursor += 20
    transcript = Transcript("r", "en", segments, words, "fixture")
    story = StoryConcept(
        "story", topic, "Speed challenges Tyreek Hill to decide who is faster in a 40-yard race.", texts[0], texts[1], texts[-2], texts[-1],
        [item.id for item in moments], 30, "One challenge progresses from claim to stakes.",
        coherence={"planned_order": [item.id for item in moments], "entities": ["Tyreek Hill"]},
        central_topic=topic, viewer_premise="Speed challenges Tyreek Hill to decide who is faster in a 40-yard race.",
        moment_rationales={item.id: f"Required {role}" for item, role in zip(moments, ["hook", "context", "evidence", "development", "payoff"][-len(moments):], strict=True)},
    )
    return story, {item.id: item for item in moments}, transcript


class EditorialQualityTests(unittest.TestCase):
    def test_clear_story_passes_and_persists_ready_review(self) -> None:
        story, moments, transcript = editorial_fixture()
        sequence = plan_reviewed_sequence(story, moments, transcript, sample_source())
        self.assertEqual(sequence.status, "ready")
        self.assertTrue(sequence.editorial_review.self_contained)
        with tempfile.TemporaryDirectory() as directory:
            project = sample_project()
            project.moments = list(moments.values())
            project.story_concepts = [story]
            project.edit_sequences = [sequence]
            workspace = Workspace(Path(directory))
            save_project(workspace, project)
            restored = load_project(workspace)
        self.assertEqual(restored.edit_sequences[0].status, "ready")
        self.assertTrue(restored.edit_sequences[0].editorial_review.accepted)
        self.assertEqual(len(restored.candidates), 1)

    def test_unclear_topic_no_hook_and_no_payoff_are_rejected(self) -> None:
        story, moments, transcript = editorial_fixture(topic="sports")
        self.assertEqual(plan_reviewed_sequence(story, moments, transcript, sample_source()).status, "rejected")
        story, moments, transcript = editorial_fixture(hook="They talked about the event.")
        review = plan_reviewed_sequence(story, moments, transcript, sample_source()).editorial_review
        self.assertFalse(review.hook_present)
        story, moments, transcript = editorial_fixture(payoff="Then the conversation continued.")
        review = plan_reviewed_sequence(story, moments, transcript, sample_source()).editorial_review
        self.assertFalse(review.payoff_present)

    def test_redundant_segment_is_removed_with_bounded_rebuilds(self) -> None:
        story, moments, transcript = editorial_fixture(duplicate=True)
        sequence = plan_reviewed_sequence(story, moments, transcript, sample_source(), max_rebuilds=20)
        self.assertLessEqual(sequence.editorial_review.rebuild_attempts, MAX_EDITORIAL_REBUILDS)
        self.assertEqual(len(sequence.segments), 4)
        self.assertEqual(len({item.transcript_excerpt for item in sequence.segments}), 4)

    def test_cold_viewer_and_required_roles_gate_ready_state(self) -> None:
        story, moments, transcript = editorial_fixture()
        story.coherence["entities"] = []
        for moment in moments.values():
            moment.entities = []
            moment.summary = moment.summary.replace("Tyreek Hill", "him")
        sequence = plan_reviewed_sequence(story, moments, transcript, sample_source())
        self.assertEqual(sequence.status, "rejected")
        self.assertFalse(sequence.editorial_review.self_contained)
        self.assertEqual([item["role"] for item in sequence.editorial_review.segment_roles], ["hook", "context", "development", "payoff"])

    def test_sparse_punch_in_policy(self) -> None:
        actions = [EditorialAction("p1", "punch_in", 1, 2, {"scale": 1.1}, "claim"), EditorialAction("p2", "punch_in", 4, 5, {"scale": 1.1}, "reaction")]
        with self.assertRaisesRegex(ValueError, "separated"):
            validate_action_policy(actions, 45)


class AudibleRenderTests(unittest.TestCase):
    def test_multisegment_render_has_nonzero_decoded_audio_and_synced_duration(self) -> None:
        service = FFmpegService()
        if not service.available:
            self.skipTest("FFmpeg fixture runtime is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            service._run([
                service.ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=4",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source),
            ])
            review = EditorialReviewResult("fixture", "fixture premise", True, True, True, 1, [], relevant_entities=["fixture"], central_tension="fixture", resolution="fixture", accepted=True)
            sequence = EditSequence("edit", "story", "Fixture", "media_001", [
                EditSegment("a", "m1", "media_001", mt(0), mt(1), 0, "hook", "Race me", 0),
                EditSegment("b", "m2", "media_001", mt(2), mt(3), 1, "payoff", "I win", 1),
            ], [], EditorialIntegrityResult("passed", [], []), frame_rate=sample_source().frame_rate, status="ready", editorial_review=review)
            service.render_edit_sequence(source, sequence, output, width=180, height=320)
            energy = service.validate_audible_audio(output)
            duration = float(service.inspect(output)["format"]["duration"])
        self.assertGreater(energy.sample_count, 100_000)
        self.assertGreater(energy.mean_db, -55)
        self.assertAlmostEqual(duration, 2, delta=.15)

    def test_silent_smart_edit_fails_validation(self) -> None:
        service = FFmpegService()
        if not service.available:
            self.skipTest("FFmpeg fixture runtime is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            silent = Path(directory) / "silent.wav"
            service._run([service.ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", str(silent)])
            with self.assertRaises(MediaToolError):
                service.validate_audible_audio(silent)


if __name__ == "__main__":
    unittest.main()
