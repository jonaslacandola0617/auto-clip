import json
import tempfile
import unittest
from pathlib import Path

from autoclip.models import AutoClipProject
from autoclip.storage import Workspace, load_project, save_project
from tests.helpers import sample_project


class ModelStorageTests(unittest.TestCase):
    def test_project_round_trip(self) -> None:
        project = sample_project()
        restored = AutoClipProject.from_dict(json.loads(json.dumps(project.to_dict())))
        self.assertEqual(restored.to_dict(), project.to_dict())

    def test_atomic_save_and_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            project = sample_project()
            save_project(workspace, project)
            project.name = "Updated"
            save_project(workspace, project)
            self.assertEqual(load_project(workspace).name, "Updated")
            previous = workspace.project_file.with_suffix(".json.previous")
            self.assertTrue(previous.exists())
            self.assertEqual(json.loads(previous.read_text(encoding="utf-8"))["name"], "Fixture Project")

    def test_cache_identity_is_stable_and_config_sensitive(self) -> None:
        workspace = Workspace(Path("fixture"))
        first = workspace.cache_key("analysis", {"b": 2, "a": 1})
        self.assertEqual(first, workspace.cache_key("analysis", {"a": 1, "b": 2}))
        self.assertNotEqual(first, workspace.cache_key("analysis", {"a": 1, "b": 3}))

    def test_cache_paths_are_separate_from_canonical_project(self) -> None:
        workspace = Workspace(Path("project"))
        cache = workspace.root / "cache" / "proxies" / "proxy.mp4"
        self.assertNotIn(cache, workspace.canonical_paths())
        self.assertEqual(workspace.project_file.name, "project.autoclip.json")


if __name__ == "__main__":
    unittest.main()

