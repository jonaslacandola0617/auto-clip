from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoclip.enhancements import (MAX_ENHANCEMENT_EVENTS, enhancement_cache_key, index_local_library,
                                   match_asset, plan_enhancements, relink_asset, serialize_graphics_ass,
                                   validate_enhancement_plan)
from autoclip.media import FFmpegService, MediaToolError
from autoclip.models import (AutoClipProject, BrollInsert, EnhancementAsset, EnhancementPlan, GraphicOverlay,
                             MusicBed, SoundCue)
from autoclip.storage import Workspace, load_project, save_project
from autoclip.vision import build_visual_edit_plan
from tests.helpers import sample_project
from tests.test_phase2b import sequence_fixture


def accepted_sequence():
    sequence = sequence_fixture()
    sequence.title = "IShowSpeed challenges Tyreek Hill to a 40 yard dash"
    sequence.segments[0].transcript_excerpt = "Race me in a 40 yard dash"
    return sequence


class EnhancementModelTests(unittest.TestCase):
    def test_plan_serialization_and_no_music_is_valid(self) -> None:
        sequence = accepted_sequence(); visual = build_visual_edit_plan(sequence, [])
        plan = validate_enhancement_plan(plan_enhancements(sequence, visual), sequence)
        self.assertEqual(plan.status, "ready")
        self.assertIsNone(plan.music_track)
        project = sample_project(); project.edit_sequences = [sequence]; project.visual_edit_plans = [visual]; project.enhancement_plans = [plan]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory)); save_project(workspace, project); restored = load_project(workspace)
        self.assertEqual(restored.enhancement_plans[0].graphic_items[0].text, "40-YARD DASH")

    def test_local_asset_index_fingerprint_reference_and_relink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); first = root / "race-impact.wav"; second = root / "replacement.wav"
            first.write_bytes(b"RIFF-local-one"); second.write_bytes(b"RIFF-local-two")
            assets = index_local_library([root], media=type("M", (), {"available": False})())
            self.assertEqual(len(assets), 2)
            plan = EnhancementPlan("e", "s", 1, "review", assets=[assets[0]])
            old = assets[0].fingerprint
            relink_asset(plan, assets[0].id, second)
            self.assertNotEqual(old, assets[0].fingerprint)
            self.assertEqual(Path(assets[0].reference), second)

    def test_missing_asset_is_non_destructive_and_disable_resolves_it(self) -> None:
        sequence = accepted_sequence()
        asset = EnhancementAsset("missing", "video", "Z:/missing.mp4", {"partial_sha256": "x"})
        item = BrollInsert("b", 2, 4, "missing", relevance=.9)
        plan = EnhancementPlan("e", sequence.id, 1, "review", assets=[asset], broll_items=[item])
        self.assertEqual(validate_enhancement_plan(plan, sequence).status, "needs_revision")
        item.enabled = False
        self.assertEqual(validate_enhancement_plan(plan, sequence).status, "ready")
        self.assertEqual(sequence.status, "ready")

    def test_relevance_timing_density_graphic_and_audio_safety(self) -> None:
        sequence = accepted_sequence()
        plan = EnhancementPlan("e", sequence.id, 1, "review", broll_items=[BrollInsert("b", -1, 40, "x", relevance=.1)],
                               graphic_items=[GraphicOverlay("g", "UNSUPPORTED $99,000", 1, 3)],
                               sound_cues=[SoundCue(f"s{i}", "x", i + 1, 3, -2, .8) for i in range(MAX_ENHANCEMENT_EVENTS + 1)],
                               music_track=MusicBed("x", gain_db=-4, ducking_db=-2, enabled=True))
        validate_enhancement_plan(plan, sequence)
        joined = " ".join(plan.warnings)
        self.assertIn("density", joined)
        self.assertIn("relevance", joined)
        self.assertIn("not supported", joined)
        self.assertIn("safe bounds", joined)
        self.assertIn("Music gain", joined)

    def test_planner_is_sparse_source_grounded_and_user_disable_persists(self) -> None:
        sequence = accepted_sequence(); visual = build_visual_edit_plan(sequence, [])
        first = plan_enhancements(sequence, visual)
        self.assertLessEqual(len(first.broll_items) + len(first.graphic_items) + len(first.sound_cues), MAX_ENHANCEMENT_EVENTS)
        self.assertEqual(first.graphic_items[0].text, "40-YARD DASH")
        first.graphic_items[0].enabled = False
        second = plan_enhancements(sequence, visual, previous=first)
        self.assertFalse(second.graphic_items[0].enabled)

    def test_asset_matching_cache_and_graphics_serialization(self) -> None:
        sequence = accepted_sequence(); visual = build_visual_edit_plan(sequence, [])
        asset = EnhancementAsset("race", "video", "race-sprint.mp4", {"partial_sha256": "x"}, tags=["race", "sprint"])
        match, score = match_asset("race sprint", [asset], kinds={"video"})
        self.assertEqual(match, asset); self.assertEqual(score, 1)
        plan = plan_enhancements(sequence, visual, assets=[asset])
        key = enhancement_cache_key(plan); self.assertEqual(key, enhancement_cache_key(plan))
        plan.revision += 1; self.assertNotEqual(key, enhancement_cache_key(plan))
        self.assertIn("40-YARD DASH", serialize_graphics_ass(plan))


class EnhancementRenderTests(unittest.TestCase):
    def test_multilayer_render_preserves_dialogue_and_applies_graphic_sfx_and_music_ducking(self) -> None:
        service = FFmpegService()
        if not service.available:
            self.skipTest("FFmpeg unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.mp4"; broll = root / "race.png"; sfx = root / "impact.wav"; music = root / "music.wav"; output = root / "out.mp4"; graphics = root / "graphics.ass"
            service._run([service.ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=40", "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=48000:duration=40", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)])
            service._run([service.ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=green:s=320x180", "-frames:v", "1", str(broll)])
            service._run([service.ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=120:sample_rate=48000:duration=0.5", str(sfx)])
            service._run([service.ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=240:sample_rate=48000:duration=12", str(music)])
            sequence = accepted_sequence(); visual = build_visual_edit_plan(sequence, [])
            assets = [EnhancementAsset("b", "image", str(broll), {"partial_sha256": "b"}), EnhancementAsset("s", "audio", str(sfx), {"partial_sha256": "s"}), EnhancementAsset("m", "audio", str(music), {"partial_sha256": "m"})]
            plan = EnhancementPlan("e", sequence.id, 1, "ready", assets=assets,
                                   broll_items=[BrollInsert("broll", 2, 3.5, "b", relevance=.9)],
                                   graphic_items=[GraphicOverlay("g", "40-YARD DASH", .4, 2.2, source_references=["hook"])],
                                   sound_cues=[SoundCue("hit", "s", 7.5, .5, -20, .05)],
                                   music_track=MusicBed("m", -28, -12, enabled=True))
            graphics.write_text(serialize_graphics_ass(plan), encoding="utf-8")
            command = service.plan_edit_sequence_render(source, sequence, output, width=180, height=320, visual_plan=visual, enhancement_plan=plan, graphics=graphics)
            joined = " ".join(command)
            self.assertIn("sidechaincompress", joined); self.assertIn("overlay=", joined); self.assertIn("amix=", joined)
            service.render_edit_sequence(source, sequence, output, width=180, height=320, visual_plan=visual, enhancement_plan=plan, graphics=graphics)
            energy = service.validate_audible_audio(output); probe = service.inspect(output)
            self.assertGreater(energy.mean_db, -55)
            self.assertEqual((probe["streams"][0]["width"], probe["streams"][0]["height"]), (180, 320))

    def test_audible_validation_remains_mandatory(self) -> None:
        service = FFmpegService()
        with patch.object(FFmpegService, "measure_audio_energy", return_value=type("E", (), {"audible": False, "mean_db": -80., "peak_db": -70.})()):
            with self.assertRaises(MediaToolError):
                service.validate_audible_audio(Path("silent.mp4"))


if __name__ == "__main__":
    unittest.main()
