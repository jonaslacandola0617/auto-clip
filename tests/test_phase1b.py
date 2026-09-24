import json
import tempfile
import threading
import unittest
from pathlib import Path

from autoclip.ai import FixtureProvider
from autoclip.captions import build_caption_track, serialize_ass, serialize_srt
from autoclip.desktop_protocol import ProtocolError
from autoclip.desktop_service import DesktopService
from autoclip.models import OutputArtifact
from autoclip.pipeline import CorePipeline
from autoclip.storage import Workspace, load_project, save_project
from tests.helpers import sample_project


class CountingProvider(FixtureProvider):
    calls = 0

    def analyze_transcript(self, chunks, metadata):
        type(self).calls += 1
        return super().analyze_transcript(chunks, metadata)


class Phase1BTests(unittest.TestCase):
    def setUp(self) -> None:
        CountingProvider.calls = 0

    def make_project(self, root: Path) -> tuple[DesktopService, Workspace]:
        repo = root / "repo"
        repo.mkdir()
        workspace = Workspace(root / "project")
        save_project(workspace, sample_project())
        return DesktopService(root / "app-data", repo), workspace

    def create_manual_clip(self, service: DesktopService, workspace: Workspace) -> str:
        state = service.create_manual_clip({
            "project_path": str(workspace.project_file),
            "start_segment_id": "s1",
            "end_segment_id": "s2",
            "title": "Manual selection",
        })
        return state["clips"][0]["id"]

    def test_transcript_correction_preserves_original_and_invalidates_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, workspace = self.make_project(Path(directory))
            service.create_clip_from_candidate({"project_path": str(workspace.project_file), "candidate_id": "candidate_001"})
            before = load_project(workspace).transcripts[-1].revision_id
            state = service.correct_transcript({"project_path": str(workspace.project_file), "segment_id": "s1", "text": "This definitely works"})
            reopened = load_project(workspace)
            segment = reopened.transcripts[-1].segments[0]
            self.assertEqual(segment.text, "This works")
            self.assertEqual(segment.corrected_text, "This definitely works")
            self.assertNotEqual(reopened.transcripts[-1].revision_id, before)
            self.assertEqual(state["candidates"], [])
            self.assertFalse(any(clip.source == "ai" for clip in reopened.clips))
            self.assertEqual(reopened.caption_tracks, [])

    def test_manual_clip_creation_bounds_and_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, workspace = self.make_project(Path(directory))
            clip_id = self.create_manual_clip(service, workspace)
            reopened = service.open_project({"path": str(workspace.project_file)})
            self.assertEqual(reopened["clips"][0]["id"], clip_id)
            self.assertEqual(reopened["clips"][0]["source"], "manual")
            with self.assertRaises(ProtocolError) as context:
                service.create_manual_clip({
                    "project_path": str(workspace.project_file),
                    "start_segment_id": "s2", "end_segment_id": "s1",
                })
            self.assertEqual(context.exception.code, "invalid_clip_bounds")

    def test_clip_edit_invalidation_is_dependency_aware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, workspace = self.make_project(Path(directory))
            clip_id = self.create_manual_clip(service, workspace)
            project = load_project(workspace)
            clip = project.clips[0]
            clip.reframe_track_id = project.reframe_tracks[0].id
            clip.render_path = "render.mp4"
            project.outputs.append(OutputArtifact("out1", "mp4", "render.mp4", clip_id))
            save_project(workspace, project)
            preview = workspace.root / "cache" / "previews" / f"{clip.id}-r{clip.revision}.mp4"
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes(b"preview-cache")
            state = service.update_clip({
                "project_path": str(workspace.project_file), "clip_id": clip_id,
                "caption_preset": "bold_social",
            })
            edited = load_project(workspace).clips[0]
            self.assertIsNotNone(edited.reframe_track_id)
            self.assertIsNone(state["clips"][0]["preview_path"])
            self.assertIsNone(edited.render_path)
            service.update_clip({
                "project_path": str(workspace.project_file), "clip_id": clip_id,
                "start_seconds": 11, "end_seconds": 41,
            })
            self.assertIsNone(load_project(workspace).clips[0].reframe_track_id)

    def test_caption_tracks_serialize_to_srt_and_ass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, workspace = self.make_project(Path(directory))
            self.create_manual_clip(service, workspace)
            project = load_project(workspace)
            clip = project.clips[0]
            track = build_caption_track(clip, project.transcripts[-1])
            self.assertIn("-->", serialize_srt(track, clip))
            self.assertIn("[Events]", serialize_ass(track, clip, "word_highlight"))
            self.assertGreater(len(track.cues), 0)

    def test_ai_candidates_are_deduplicated_persisted_and_cache_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / "project")
            project = sample_project()
            payload = json.loads((Path(__file__).parent / "fixtures" / "valid_ai_response.json").read_text(encoding="utf-8"))
            provider = CountingProvider(payload)
            pipeline = CorePipeline(workspace, object(), object(), provider, prompt_version="phase1b-test")
            first = pipeline.analyze_source(project.sources[0], project.transcripts[-1])
            second = pipeline.analyze_source(project.sources[0], project.transcripts[-1])
            self.assertEqual(CountingProvider.calls, 1)
            self.assertEqual([item.id for item in first], [item.id for item in second])
            project.candidates = first
            save_project(workspace, project)
            self.assertEqual(len(load_project(workspace).candidates), len(first))

    def test_invalid_export_and_failed_render_preserve_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, workspace = self.make_project(Path(directory))
            clip_id = self.create_manual_clip(service, workspace)
            before = workspace.project_file.read_bytes()
            with self.assertRaises(RuntimeError):
                service._export_job(str(workspace.project_file), "unknown", None, threading.Event(), lambda *_: None)
            with self.assertRaises(RuntimeError):
                service._render_job(str(workspace.project_file), clip_id, False, threading.Event(), lambda *_: None)
            self.assertEqual(workspace.project_file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
