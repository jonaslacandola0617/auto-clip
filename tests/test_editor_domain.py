import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from autoclip.editor import (
    EditorCommand, EditorSession, associate_preview, associate_render, ensure_editor_domain,
    sequence_to_edit_sequence, sequence_to_timeline, validate_sequence,
)
from autoclip.editor_domain import (
    AudioClip, CaptionItem, GraphicItem, MediaAsset, MediaClip, MediaLibrary, Sequence, Track,
)
from autoclip.models import (
    EditSegment, EditSequence, EditorialIntegrityResult, EnhancementPlan, GraphicOverlay,
    ProductionRun, ProjectClip, VisualEditPlan,
)
from autoclip.storage import AutosaveCoordinator, Workspace, load_project, save_project
from autoclip.time import MediaTime, Rational
from tests.helpers import RATE, TIME_BASE, mt, sample_project


def command(name: str, sequence: Sequence, **payload):
    return EditorCommand(name, sequence.id, payload)


def editor_project():
    project = sample_project()
    project.clips.append(ProjectClip("clip_legacy", "media_001", mt(8), mt(20), "Legacy Clip", caption_track_id="caption_001"))
    project.edit_sequences.append(EditSequence(
        "smart_001", "story_001", "Smart", "media_001",
        [
            EditSegment("seg_1", "moment_1", "media_001", mt(10), mt(15), 0.0, "hook", "Hook", 0),
            EditSegment("seg_2", "moment_2", "media_001", mt(40), mt(46), 5.0, "payoff", "Payoff", 1),
        ], [], EditorialIntegrityResult("passed", [], []), frame_rate=RATE, status="ready",
    ))
    return ensure_editor_domain(project)


class EditorDomainTests(unittest.TestCase):
    def test_media_library_serialization_and_relink(self):
        project = editor_project()
        restored = MediaLibrary.from_dict(json.loads(json.dumps(project.media_library.to_dict())))
        self.assertEqual(restored.assets[0].id, "media_001")
        moved_fingerprint = {**restored.assets[0].fingerprint, "basename": "source.mp4", "mtime_ns": 999}
        restored.assets[0].relink("D:/moved/source.mp4", moved_fingerprint)
        self.assertEqual(restored.assets[0].filename, "source.mp4")
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            restored.assets[0].relink("D:/wrong.mp4", {"size": 99})

    def test_sequence_round_trip_track_order_and_duration(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "smart_001")
        restored = Sequence.from_dict(json.loads(json.dumps(sequence.to_dict())))
        self.assertEqual(restored.to_dict(), sequence.to_dict())
        self.assertEqual(float(restored.duration.seconds), 11.0)
        restored.tracks[1].order = restored.tracks[0].order
        with self.assertRaisesRegex(ValueError, "orders"):
            validate_sequence(project, restored)

    def test_media_and_audio_source_timeline_mapping(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "smart_001")
        video = sequence.tracks[0].items[1]
        audio = sequence.tracks[1].items[1]
        self.assertIsInstance(video, MediaClip)
        self.assertIsInstance(audio, AudioClip)
        self.assertEqual(float(video.source_in.seconds), 40.0)
        self.assertEqual(float(video.timeline_start.seconds), 5.0)
        self.assertEqual(video.linked_item_id, audio.id)

    def test_caption_and_graphic_timing_are_structured(self):
        project = editor_project()
        clip_sequence = next(item for item in project.sequences if item.id == "sequence_clip_legacy")
        caption = clip_sequence.tracks[2].items[0]
        self.assertIsInstance(caption, CaptionItem)
        self.assertEqual(caption.text, "This really works")
        graphic = GraphicItem("g1", MediaTime.from_frames(24, RATE), MediaTime.from_frames(48, RATE), clip_sequence.tracks[3].id, "Fact")
        session = EditorSession(project)
        session.execute(command("insert_item", clip_sequence, item=asdict(graphic)))
        self.assertIsInstance(session.project.sequences[-1].tracks[3].items[0], GraphicItem)

    def test_insert_remove_move_and_overlap_validation(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "smart_001")
        session = EditorSession(project)
        graphic = GraphicItem("g1", mt(1), mt(2), sequence.tracks[3].id, "Hello")
        session.execute(command("insert_item", sequence, item=asdict(graphic)))
        moved = session.execute(command("move_item", sequence, item_id="g1", timeline_start=mt(2).to_dict()))
        self.assertEqual(float(moved.item("g1")[1].timeline_start.seconds), 2.0)
        removed = session.execute(command("remove_item", moved, item_id="g1"))
        with self.assertRaises(ValueError):
            removed.item("g1")
        overlapping = MediaClip("overlap", "media_001", mt(20), mt(22), mt(1), mt(2), sequence.tracks[0].id)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            session.execute(command("insert_item", removed, item=asdict(overlapping)))

    def test_trim_start_trim_end_split_and_reorder(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "smart_001")
        session = EditorSession(project)
        value = session.execute(command("trim_start", sequence, item_id="seg_1", source_in=mt(11).to_dict()))
        self.assertEqual(float(value.item("seg_1")[1].timeline_start.seconds), 1.0)
        value = session.execute(command("trim_end", value, item_id="seg_1", source_out=mt(14).to_dict()))
        self.assertEqual(float(value.item("seg_1")[1].timeline_duration.seconds), 3.0)
        value = session.execute(command("split_clip", value, item_id="seg_2", timeline_at=mt(8).to_dict(), new_item_id="seg_2b"))
        self.assertEqual(float(value.item("seg_2b")[1].source_in.seconds), 43.0)
        value = session.execute(command("reorder_item", value, item_id="seg_2b", index=0))
        self.assertEqual(value.tracks[0].items[0].id, "seg_2b")

    def test_trim_clamps_source_derived_caption_items(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "smart_001")
        new_out = MediaTime.from_seconds("41.5", TIME_BASE)
        changed = EditorSession(project).execute(command("trim_end", sequence, item_id="seg_2", source_out=new_out.to_dict()))
        clip_end = changed.item("seg_2")[1].timeline_start.seconds + changed.item("seg_2")[1].timeline_duration.seconds
        captions = [item for track in changed.tracks if track.type == "captions" for item in track.items
                    if isinstance(item, CaptionItem) and item.metadata.get("edit_segment_id") == "seg_2"]
        self.assertTrue(captions)
        self.assertTrue(all(item.timeline_start.seconds + item.timeline_duration.seconds <= clip_end for item in captions))
        self.assertEqual(changed.duration.seconds, clip_end)

    def test_enable_caption_and_graphic_edits(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "sequence_clip_legacy")
        session = EditorSession(project)
        caption_id = sequence.tracks[2].items[0].id
        value = session.execute(command("change_caption_text", sequence, item_id=caption_id, text="Corrected"))
        value = session.execute(command("change_caption_preset", value, item_id=caption_id, preset="word_highlight"))
        value = session.execute(command("disable_item", value, item_id=caption_id))
        caption = value.item(caption_id)[1]
        self.assertEqual((caption.text, caption.style_preset, caption.enabled), ("Corrected", "word_highlight", False))
        self.assertNotIn("visual", value.dependencies.stale_dependencies)

    def test_undo_redo_grouped_transaction_and_bounded_history(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "smart_001")
        session = EditorSession(project, history_limit=2)
        session.execute_transaction([
            command("disable_item", sequence, item_id="seg_1"),
            command("disable_item", sequence, item_id="seg_1:audio"),
        ], label="Disable linked clip")
        self.assertEqual(session.undo_count, 1)
        session.undo()
        self.assertTrue(session.project.sequences[0].item("seg_1")[1].enabled)
        session.redo()
        self.assertFalse(session.project.sequences[0].item("seg_1")[1].enabled)
        current = session.project.sequences[0]
        session.execute(command("enable_item", current, item_id="seg_1"))
        current = session.project.sequences[0]
        session.execute(command("disable_item", current, item_id="seg_1"))
        self.assertEqual(session.undo_count, 2)

    def test_dirty_autosave_recovery_and_reopen_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            project = editor_project()
            sequence = project.sequences[0]
            session = EditorSession(project)
            changed = session.execute(command("disable_item", sequence, item_id="seg_1"))
            self.assertTrue(changed.dirty)
            autosave = AutosaveCoordinator(workspace, project, debounce_seconds=60)
            autosave.schedule()
            self.assertFalse(autosave.flush())
            self.assertTrue(autosave.flush(force=True))
            reopened = load_project(workspace)
            self.assertEqual(reopened.sequences[0].to_dict(), project.sequences[0].to_dict())
            save_project(workspace, project)
            workspace.project_file.write_text("{broken", encoding="utf-8")
            recovered = load_project(workspace)
            self.assertTrue(recovered.legacy_compatibility["recovered_from_previous_snapshot"])

    def test_preview_and_render_revision_association(self):
        project = editor_project()
        sequence = project.sequences[0]
        session = EditorSession(project)
        changed = session.execute(command("disable_item", sequence, item_id="seg_1"))
        associate_preview(changed, changed.revision - 1)
        self.assertIn("preview", changed.dependencies.stale_dependencies)
        associate_preview(changed, changed.revision)
        associate_render(changed, changed.revision)
        self.assertNotIn("preview", changed.dependencies.stale_dependencies)
        self.assertNotIn("render", changed.dependencies.stale_dependencies)

    def test_representative_frame_rates_remain_exact(self):
        for rate in (Rational(24000, 1001), Rational(24), Rational(25), Rational(30), Rational(30000, 1001)):
            at = MediaTime.from_frames(137, rate)
            self.assertEqual(at.to_frames(rate), 137)

    def test_legacy_visual_enhancement_and_phase3_metadata_survive(self):
        project = sample_project()
        project.edit_sequences.append(EditSequence("smart", "story", "Smart", "media_001", [], [], EditorialIntegrityResult("passed", [], [])))
        project.visual_edit_plans.append(VisualEditPlan("visual", "smart", [], [], [], [], [], []))
        project.enhancement_plans.append(EnhancementPlan("enhance", "smart", 1, "ready", graphic_items=[GraphicOverlay("g", "Label", 0, 1)]))
        project.production_runs.append(ProductionRun("run", "profile", 1, 1, "now"))
        ensure_editor_domain(project)
        sequence = project.sequences[0]
        self.assertEqual((sequence.visual_plan_id, sequence.enhancement_plan_id), ("visual", "enhance"))
        self.assertEqual(sequence.tracks[3].items[0].content, "Label")
        self.assertEqual(project.production_runs[0].id, "run")

    def test_render_and_export_adapters_use_canonical_sequence(self):
        project = editor_project()
        sequence = next(item for item in project.sequences if item.id == "smart_001")
        adapted = sequence_to_edit_sequence(project, sequence)
        timeline = sequence_to_timeline(sequence)
        self.assertEqual([float(item.timeline_start) for item in adapted.segments], [0.0, 5.0])
        self.assertEqual([item.source_in for item in timeline.clips], [mt(10), mt(40)])


if __name__ == "__main__":
    unittest.main()
