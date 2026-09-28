from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoclip.captions import build_caption_track, build_edit_sequence_caption_track, serialize_ass, serialize_edit_sequence_ass
from autoclip.models import (AutoClipProject, BoundingBox, EditSegment, EditSequence, EditorialIntegrityResult,
                             EditorialReviewResult, ProjectClip, Transcript, TranscriptSegment, TranscriptWord,
                             VisualDetection, VisualObservation)
from autoclip.storage import Workspace, load_project, save_project
from autoclip.vision import (DETECTOR_VERSION, Detection, analyze_video_clip, build_subject_tracks,
                             build_visual_edit_plan, detector_asset_path, select_layout, visual_cache_key)
from tests.helpers import mt, sample_project, sample_source


def sequence_fixture() -> EditSequence:
    review = EditorialReviewResult("race", "Speed races Tyreek", True, True, True, 1, [], accepted=True)
    return EditSequence("edit", "story", "Race", "media_001", [
        EditSegment("hook", "m1", "media_001", mt(10), mt(14), 0, "hook", "Race me", 0),
        EditSegment("context", "m2", "media_001", mt(20), mt(24), 4, "context", "Forty yards", 1),
        EditSegment("payoff", "m3", "media_001", mt(30), mt(34), 8, "payoff", "Deal", 2),
    ], [], EditorialIntegrityResult("passed", [], []), status="ready", editorial_review=review)


def observation(at: float, boxes: list[tuple[float, float, float, float]], scene: str = "hook") -> VisualObservation:
    return VisualObservation(f"o{at}", "media_001", mt(at), scene,
                             [VisualDetection("", "face", BoundingBox(*box), .9) for box in boxes])


class Phase2BVisualTests(unittest.TestCase):
    def test_packaged_detector_asset_is_discoverable_and_validated(self) -> None:
        asset = detector_asset_path()
        self.assertTrue(asset.is_file())
        self.assertGreater(asset.stat().st_size, 200_000)

    def test_detector_failure_falls_back_to_stable_center(self) -> None:
        with patch("autoclip.vision.OpenCVFaceDetector", side_effect=RuntimeError("missing")):
            track = analyze_video_clip(Path("missing.mp4"), mt(0), mt(1), "r")
        self.assertEqual([(point.crop_x, point.crop_y) for point in track.points], [(.5, .5), (.5, .5)])

    def test_subject_identity_persists_across_temporary_loss_and_reacquisition(self) -> None:
        observations = [observation(10, [(.2, .2, .2, .2)]), observation(10.5, []), observation(11, [(.23, .2, .2, .2)])]
        tracks = build_subject_tracks(observations, max_gap_samples=2)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].points), 2)
        self.assertGreaterEqual(tracks[0].lost_samples, 1)

    def test_multi_person_layout_and_split_screen_are_deterministic(self) -> None:
        close = [VisualDetection("a", "face", BoundingBox(.2, .2, .18, .2), .9), VisualDetection("b", "face", BoundingBox(.48, .2, .18, .2), .9)]
        far = [VisualDetection("a", "face", BoundingBox(.02, .2, .15, .2), .9), VisualDetection("b", "face", BoundingBox(.82, .2, .15, .2), .9)]
        self.assertEqual(select_layout(close)[0], "two_person_wide")
        layout, ids, panels, _, _ = select_layout(far)
        self.assertEqual((layout, ids), ("split_screen", ["a", "b"]))
        self.assertEqual(len(panels), 2)
        self.assertEqual(select_layout(far, active_subject_id="b")[0], "speaker_focus")

    def test_plan_has_no_continuous_pan_and_sparse_bounded_punch_ins(self) -> None:
        sequence = sequence_fixture()
        observations = [observation(10, [(.7, .2, .2, .2)]), observation(11, [(.72, .2, .2, .2)]), observation(30, [(.2, .2, .2, .2)], "payoff")]
        plan = build_visual_edit_plan(sequence, observations)
        self.assertLessEqual(len(plan.visual_actions), 2)
        self.assertTrue(all(1.0 <= float(action.parameters["scale"]) <= 1.2 for action in plan.visual_actions))
        for segment in sequence.segments:
            centers = {(decision.crop_x, decision.crop_y) for decision in plan.reframe_decisions if decision.edit_segment_id == segment.id}
            self.assertLessEqual(len(centers), 1)

    def test_caption_safe_zone_and_user_disabled_action_persist(self) -> None:
        sequence = sequence_fixture()
        low_face = [observation(10, [(.4, .65, .2, .2)])]
        first = build_visual_edit_plan(sequence, low_face)
        self.assertEqual(first.caption_layout[0].position, "upper")
        first.visual_actions[0].enabled = False
        rebuilt = build_visual_edit_plan(sequence, low_face, previous=first)
        self.assertFalse(next(action for action in rebuilt.visual_actions if action.id == first.visual_actions[0].id).enabled)

    def test_word_timing_phrase_grouping_and_karaoke_are_source_aligned(self) -> None:
        sequence = sequence_fixture()
        words = [TranscriptWord("w1", mt(10), mt(10.4), "Race"), TranscriptWord("w2", mt(10.6), mt(11), "me,"),
                 TranscriptWord("w3", mt(11.2), mt(11.6), "right"), TranscriptWord("w4", mt(11.8), mt(12.2), "now!")]
        transcript = Transcript("r", "en", [TranscriptSegment("s", mt(10), mt(12.2), "Race me, right now!", [word.id for word in words])], words, "fixture")
        track = build_edit_sequence_caption_track(sequence, transcript, words_per_group=5, positions={"hook": "upper"})
        self.assertEqual(len(track.cues), 2)  # punctuation-aware break at “me,”
        self.assertAlmostEqual(track.cues[0].word_timings[1]["start"], .6)
        ass = serialize_edit_sequence_ass(track, "word_highlight")
        self.assertIn(r"{\kf60}Race", ass)
        self.assertIn(r"{\an8}", ass)

    def test_highlight_clip_uses_the_same_word_timed_caption_engine(self) -> None:
        words = [TranscriptWord("w1", mt(10), mt(10.4), "Race"), TranscriptWord("w2", mt(10.5), mt(11), "now!")]
        transcript = Transcript("r", "en", [TranscriptSegment("s", mt(10), mt(11), "Race now!", ["w1", "w2"])], words, "fixture")
        clip = ProjectClip("clip", "media_001", mt(10), mt(11), "Race", caption_preset="word_highlight")
        track = build_caption_track(clip, transcript)
        track.cues[0].position = "upper"
        ass = serialize_ass(track, clip, "word_highlight")
        self.assertIn(r"{\kf50}Race", ass)
        self.assertIn(r"{\an8}", ass)

    def test_visual_plan_round_trip_cache_reuse_and_invalidation(self) -> None:
        sequence = sequence_fixture()
        project = sample_project()
        project.edit_sequences = [sequence]
        project.visual_edit_plans = [build_visual_edit_plan(sequence, [observation(10, [(.4, .2, .2, .2)])])]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory)); save_project(workspace, project); restored = load_project(workspace)
        self.assertEqual(restored.visual_edit_plans[0].reframe_decisions[0].layout, "single_subject")
        key = visual_cache_key(sequence, sample_source().fingerprint)
        self.assertEqual(key, visual_cache_key(sequence, sample_source().fingerprint))
        sequence.revision += 1
        self.assertNotEqual(key, visual_cache_key(sequence, sample_source().fingerprint))
        self.assertNotEqual(key, visual_cache_key(sequence_fixture(), sample_source().fingerprint, detector_version=DETECTOR_VERSION + "-new"))


if __name__ == "__main__":
    unittest.main()
