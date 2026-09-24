import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoclip.desktop_protocol import ProtocolError
from autoclip.desktop_service import DesktopService
from autoclip.storage import Workspace, save_project
from tests.helpers import sample_project, sample_source


class DesktopServiceTests(unittest.TestCase):
    def make_service(self, root: Path) -> DesktopService:
        project_root = root / "repo"
        project_root.mkdir()
        return DesktopService(root / "app-data", project_root)

    def test_cpu_defaults_use_practical_current_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.make_service(Path(directory)).get_settings()
            self.assertEqual(settings["whisper_model"], "base")
            self.assertEqual(settings["gemini_model"], "gemini-3.6-flash")

    def test_retired_default_gemini_model_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            service.settings_path.write_text(json.dumps({"gemini_model": "gemini-2.5-flash"}), encoding="utf-8")
            self.assertEqual(service.get_settings()["gemini_model"], "gemini-3.6-flash")

    def test_project_creation_reopening_and_recents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            created = service.create_project({"name": "Episode 12", "location": str(root / "projects")})
            self.assertEqual(created["status"], "needs_source")
            reopened = service.open_project({"path": created["path"]})
            self.assertEqual(reopened["id"], created["id"])
            recents = service.list_recent_projects()
            self.assertEqual(recents[0]["name"], "Episode 12")
            service.remove_recent_project({"path": created["path"]})
            self.assertTrue(Path(created["path"]).exists())
            self.assertEqual(service.list_recent_projects(), [])

    def test_invalid_project_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            project_dir = root / "old"
            project_dir.mkdir()
            (project_dir / "project.autoclip.json").write_text(json.dumps({"schema_version": "old"}), encoding="utf-8")
            with self.assertRaises(ProtocolError) as context:
                service.open_project({"path": str(project_dir)})
            self.assertEqual(context.exception.code, "unsupported_project_version")

    def test_missing_source_and_relink_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            project = sample_project()
            project.sources[0].reference = str(root / "missing.mp4")
            workspace = Workspace(root / "project")
            save_project(workspace, project)
            opened = service.open_project({"path": str(workspace.root)})
            self.assertEqual(opened["status"], "source_missing")
            replacement = root / "replacement.mp4"
            replacement.write_bytes(b"fixture")
            inspected = sample_source(str(replacement))
            with patch.object(service, "_inspect_source", return_value=inspected):
                relinked = service.relink_source({"project_path": str(workspace.project_file), "source_path": str(replacement)})
            self.assertTrue(relinked["source"]["available"])

    def test_source_can_be_added_after_project_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            created = service.create_project({"name": "Later Source", "location": str(root / "projects")})
            replacement = root / "replacement.mp4"
            replacement.write_bytes(b"fixture")
            with patch.object(service, "_inspect_source", return_value=sample_source(str(replacement))):
                updated = service.relink_source({"project_path": created["path"], "source_path": str(replacement)})
            self.assertEqual(updated["status"], "needs_transcript")

    def test_doctor_reports_missing_gemini_as_recoverable_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            service = self.make_service(Path(directory))
            doctor = service.doctor()
            self.assertFalse(doctor["gemini"]["ready"])
            self.assertEqual(doctor["gemini"]["summary"], "API key not configured")

    def test_api_key_is_never_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GEMINI_API_KEY": "super-secret-fixture"}, clear=True):
            root = Path(directory)
            service = self.make_service(root)
            created = service.create_project({"name": "Safe Project", "location": str(root / "projects")})
            service.update_settings({"gemini_model": "gemini-fixture"})
            self.assertNotIn("super-secret-fixture", Path(created["path"]).read_text(encoding="utf-8"))
            self.assertNotIn("super-secret-fixture", service.settings_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
