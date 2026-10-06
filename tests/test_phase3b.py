import json
import os
import tempfile
import tomllib
import unittest
import zipfile
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from autoclip.credentials import CredentialStore
from autoclip.desktop_protocol import COMMANDS, PROTOCOL_VERSION, ProtocolError, parse_request
from autoclip.desktop_service import DesktopService
from autoclip.jobs import JobManager
from autoclip.media import FFmpegService
from autoclip.release import InsufficientDiskSpaceError, ReleasePaths, require_free_space, sanitize_diagnostic_text
from autoclip.storage import Workspace, load_project, save_project
from autoclip.transcription import FasterWhisperTranscriber
from autoclip.version import APP_VERSION
from tests.helpers import sample_project, sample_source


RELEASE_COMMANDS = {
    "update_visual_plan", "update_enhancement_plan", "save_workflow_profile", "save_campaign_profile",
    "create_production_run", "update_production_run", "bulk_production_action", "set_gemini_api_key",
    "clear_gemini_api_key", "clear_cache", "export_diagnostics",
}


class Phase3BReleaseTests(unittest.TestCase):
    def make_service(self, root: Path) -> DesktopService:
        repository = root / "repo"
        repository.mkdir()
        return DesktopService(root / "app-data", repository)

    def test_release_commands_are_protocol_allowlisted(self) -> None:
        self.assertTrue(RELEASE_COMMANDS.issubset(COMMANDS))
        for command in RELEASE_COMMANDS:
            self.assertEqual(parse_request({"version": PROTOCOL_VERSION, "id": command, "command": command, "payload": {}}).command, command)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is release-platform specific")
    def test_dpapi_credential_round_trip_does_not_store_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gemini.dpapi"
            store = CredentialStore(path)
            secret = "fixture-secret-åß"
            store.set_gemini_key(secret)
            self.assertEqual(store.get_gemini_key(), secret)
            self.assertNotIn(secret.encode("utf-8"), path.read_bytes())
            store.clear_gemini_key()
            self.assertFalse(path.exists())

    def test_release_paths_support_unicode_and_dotted_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = ReleasePaths(Path(directory) / "Creator's project (v1.2)" / "媒体")
            paths.ensure()
            self.assertTrue(paths.models.is_dir())
            self.assertTrue(paths.logs.is_dir())

    def test_diagnostics_sanitize_secret_assignments_and_urls(self) -> None:
        value = "api_key=top-secret token: second https://example.test/?key=third&mode=x"
        sanitized = sanitize_diagnostic_text(value)
        self.assertNotIn("top-secret", sanitized)
        self.assertNotIn("second", sanitized)
        self.assertNotIn("third", sanitized)
        self.assertIn("[redacted]", sanitized)

    def test_disk_preflight_fails_with_actionable_message(self) -> None:
        usage = namedtuple("usage", "total used free")(1000, 900, 100)
        with tempfile.TemporaryDirectory() as directory, patch("autoclip.release.shutil.disk_usage", return_value=usage):
            with self.assertRaises(InsufficientDiskSpaceError) as context:
                require_free_space(Path(directory), 200, "Rendering")
            self.assertIn("Free disk space", str(context.exception))

    def test_stale_waiting_and_cancelling_jobs_recover_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "jobs.json"
            jobs = []
            for state in ("waiting", "cancelling"):
                jobs.append({
                    "id": state, "type": "transcribe", "state": state, "progress": 0.2,
                    "current_stage": state.title(), "created_at": "2026-01-01T00:00:00+00:00",
                    "started_at": None, "completed_at": None, "error": None, "cancellable": True,
                    "project_path": None,
                })
            store.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            manager = JobManager(store)
            for state in ("waiting", "cancelling"):
                recovered = manager.get(state)
                self.assertEqual(recovered.state, "failed")
                self.assertEqual(recovered.error["code"], "interrupted")

    def test_release_media_runtime_never_falls_back_to_path(self) -> None:
        with patch.dict(os.environ, {"AUTOCLIP_RELEASE": "1"}, clear=True), \
             patch("autoclip.media._managed_media_binary", return_value=None), \
             patch("autoclip.media.shutil.which", return_value="C:/unmanaged/ffmpeg.exe"):
            service = FFmpegService()
        self.assertEqual(service.ffmpeg, "")
        self.assertEqual(service.ffprobe, "")
        self.assertFalse(service.available)

    def test_transcription_model_cache_is_app_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "models"
            model = cache / "models--Systran--faster-whisper-base" / "snapshots" / "revision" / "model.bin"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"fixture")
            transcriber = FasterWhisperTranscriber("base", model_cache=cache)
            self.assertTrue(transcriber.model_is_cached)
            self.assertEqual(transcriber.model_cache, cache.resolve())

    def test_relink_same_media_preserves_editorial_state_and_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            workspace = Workspace(root / "project")
            project = sample_project()
            project.sources[0].reference = str(root / "missing source.mp4")
            save_project(workspace, project)
            moved = root / "moved source (final.v2).mp4"
            moved.write_bytes(b"fixture")
            with patch.object(service, "_inspect_source", return_value=sample_source(str(moved))):
                state = service.relink_source({"project_path": str(workspace.project_file), "source_path": str(moved)})
            self.assertEqual(state["candidate_count"], 1)
            self.assertEqual(state["transcript_count"], 1)
            different = sample_source(str(root / "different.mp4"))
            different.fingerprint = {"size": 999, "partial_sha256": "different"}
            with patch.object(service, "_inspect_source", return_value=different), self.assertRaises(ProtocolError) as context:
                service.relink_source({"project_path": str(workspace.project_file), "source_path": str(root / "different.mp4")})
            self.assertEqual(context.exception.code, "source_fingerprint_mismatch")
            self.assertEqual(load_project(workspace).sources[0].reference, str(moved))

    def test_cache_cleanup_preserves_project_and_completed_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            workspace = Workspace(root / "Creator's Project (v1.2)")
            save_project(workspace, sample_project())
            (service.paths.cache / "temporary.bin").write_bytes(b"app-cache")
            (workspace.root / "cache").mkdir(exist_ok=True)
            (workspace.root / "cache" / "preview.bin").write_bytes(b"preview-cache")
            export = workspace.root / "exports" / "finished.video.mp4"
            export.parent.mkdir(exist_ok=True)
            export.write_bytes(b"finished")
            result = service.clear_cache({"project_path": str(workspace.project_file)})
            self.assertGreater(result["removed_bytes"], 0)
            self.assertTrue(workspace.project_file.exists())
            self.assertTrue(export.exists())
            self.assertEqual(list((workspace.root / "cache").iterdir()), [])

    def test_diagnostic_bundle_redacts_logs_and_excludes_project_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            service = self.make_service(Path(directory))
            secret = "diagnostic-secret-fixture"
            (service.paths.logs / "worker.log").write_text(f"api_key={secret}\nnormal line", encoding="utf-8")
            output = Path(service.export_diagnostics()["path"])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                combined = b"".join(archive.read(name) for name in names)
            self.assertIn("system.json", names)
            self.assertIn("logs/worker.log", names)
            self.assertNotIn(secret.encode(), combined)
            self.assertFalse(any("project.autoclip.json" in name for name in names))

    def test_application_versions_are_synchronized(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        package_version = json.loads((repository / "package.json").read_text(encoding="utf-8"))["version"]
        cargo_version = tomllib.loads((repository / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
        python_version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(APP_VERSION, package_version)
        self.assertEqual(APP_VERSION, cargo_version)
        self.assertEqual(APP_VERSION.replace("-rc.", "rc"), python_version)


if __name__ == "__main__":
    unittest.main()
