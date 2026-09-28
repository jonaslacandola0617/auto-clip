from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from threading import Event
from unittest.mock import patch

from autoclip.models import CampaignProfile, ProductionRun, WorkflowProfile
from autoclip.production import (
    apply_profile_policy, collision_safe_path, production_summary, render_filename,
    revise_workflow_profile, sanitize_filename, select_diverse_edits, source_overlap,
    validate_campaign_edit, write_manifest,
)
from autoclip.storage import Workspace, load_project, save_project
from autoclip.desktop_service import DesktopService
from autoclip.vision import build_visual_edit_plan
from tests.helpers import mt, sample_project
from tests.test_phase2c import accepted_sequence


def story_for(sequence, *, story_id: str, topic: str):
    from autoclip.models import StoryConcept
    sequence.story_concept_id = story_id
    return StoryConcept(story_id, topic, topic, f"Hook: {topic}", "context", "development", "payoff", [item.moment_id for item in sequence.segments], sequence.duration_seconds, "qualified", central_topic=topic, understandable_without_source=True)


class Phase3AModelTests(unittest.TestCase):
    def test_workflow_campaign_and_production_run_serialization(self) -> None:
        project = sample_project(); sequence = accepted_sequence(); project.edit_sequences = [sequence]
        workflow = WorkflowProfile("fast", "Fast Shorts", desired_output_count=3)
        campaign = CampaignProfile("launch", "Launch", required_text=["40 yard"])
        run = ProductionRun("run", workflow.id, workflow.revision, 3, "2026-01-01T00:00:00Z", campaign.id, campaign.revision,
                            workflow_profile_snapshot=workflow.__dict__ if hasattr(workflow, "__dict__") else {}, status="review")
        project.workflow_profiles = [workflow]; project.campaign_profiles = [campaign]; project.production_runs = [run]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory)); save_project(workspace, project); restored = load_project(workspace)
        self.assertEqual(restored.workflow_profiles[0].desired_output_count, 3)
        self.assertEqual(restored.campaign_profiles[0].required_text, ["40 yard"])
        self.assertEqual(restored.production_runs[0].campaign_profile_revision, 1)

    def test_profile_versioning_does_not_mutate_existing_run_revision(self) -> None:
        original = WorkflowProfile("fast", "Fast Shorts", desired_output_count=3)
        run = ProductionRun("run", original.id, original.revision, 3, "now")
        revised = revise_workflow_profile(original, desired_output_count=5, caption_preset="clean")
        self.assertEqual((original.revision, revised.revision, run.workflow_profile_revision), (1, 2, 1))
        self.assertEqual(revised.desired_output_count, 5)

    def test_backward_compatible_phase2c_project_defaults(self) -> None:
        project = sample_project(); raw = project.to_dict(); raw["schema_version"] = "0.3-phase2c"
        raw.pop("workflow_profiles", None); raw.pop("campaign_profiles", None); raw.pop("production_runs", None)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory)); workspace.ensure(); workspace.project_file.write_text(json.dumps(raw), encoding="utf-8")
            restored = load_project(workspace)
        self.assertEqual(restored.workflow_profiles, [])
        self.assertEqual(restored.production_runs, [])

    def test_campaign_deterministic_pass_fail_and_needs_review(self) -> None:
        sequence = accepted_sequence()
        passed = validate_campaign_edit(sequence, CampaignProfile("c", "Race", max_duration_seconds=30, required_text=["40 yard"], forbidden_terms=["lottery"]))
        self.assertEqual(passed.state, "passed_checks")
        failed = validate_campaign_edit(sequence, CampaignProfile("c", "Race", max_duration_seconds=10, required_cta="follow now"))
        self.assertEqual(failed.state, "failed_checks")
        review = validate_campaign_edit(sequence, CampaignProfile("c", "Race", watermark_required=True))
        self.assertEqual(review.state, "needs_review")


class Phase3ADiversityTests(unittest.TestCase):
    def test_desired_count_and_fewer_strong_results(self) -> None:
        first = accepted_sequence(); first.id = "one"; story1 = story_for(first, story_id="story1", topic="race challenge")
        second = deepcopy(first); second.id = "two"; second.story_concept_id = "story2"
        for index, segment in enumerate(second.segments):
            segment.moment_id = f"other_{index}"; segment.source_in = mt(80 + index * 5); segment.source_out = mt(84 + index * 5)
        story2 = story_for(second, story_id="story2", topic="training argument")
        rejected = deepcopy(second); rejected.id = "three"; rejected.status = "rejected"
        selected, _ = select_diverse_edits([first, second, rejected], {story1.id: story1, story2.id: story2}, WorkflowProfile("p", "P", min_duration_seconds=10, desired_output_count=5))
        self.assertEqual([item.id for item in selected], ["one", "two"])

    def test_duplicate_moments_and_source_overlap_are_suppressed(self) -> None:
        first = accepted_sequence(); first.id = "one"; story1 = story_for(first, story_id="s1", topic="race challenge")
        duplicate = deepcopy(first); duplicate.id = "duplicate"; story2 = story_for(duplicate, story_id="s2", topic="race challenge rematch")
        selected, suppressed = select_diverse_edits([first, duplicate], {story1.id: story1, story2.id: story2}, WorkflowProfile("p", "P", min_duration_seconds=10, desired_output_count=3))
        self.assertEqual(len(selected), 1); self.assertEqual(len(suppressed), 1)
        self.assertGreaterEqual(source_overlap(first, duplicate), .99)

    def test_profile_policy_updates_plans_without_story_changes(self) -> None:
        sequence = accepted_sequence(); visual = build_visual_edit_plan(sequence, [])
        from autoclip.enhancements import plan_enhancements
        enhancement = plan_enhancements(sequence, visual)
        story_id = sequence.story_concept_id
        apply_profile_policy(WorkflowProfile("p", "P", min_duration_seconds=10, caption_preset="clean", enhancement_policy="off"), visual, enhancement)
        self.assertEqual(visual.caption_preset, "clean")
        self.assertTrue(all(not item.enabled for item in enhancement.graphic_items))
        self.assertEqual(sequence.story_concept_id, story_id)


class Phase3AOutputTests(unittest.TestCase):
    def test_filename_sanitization_template_and_collision(self) -> None:
        self.assertEqual(sanitize_filename('Bad:<Name>?'), "Bad-Name-")
        self.assertEqual(render_filename("{project}-{index}-{title}", project="Show", campaign="", creator="", index=2, title="A/B"), "Show-02-A-B")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "clip.mp4").write_bytes(b"one")
            self.assertEqual(collision_safe_path(root, "clip", ".mp4").name, "clip-2.mp4")

    def test_manifest_is_machine_readable_and_contains_no_transcript_or_secrets(self) -> None:
        run = ProductionRun("run", "profile", 2, 2, "now", generated_edit_ids=["edit1"], warnings=["one strong result"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.json"
            write_manifest(output, run, "Project", [{"edit_id": "edit1", "title": "Title", "source_ranges": [{"in": 1, "out": 2}]}])
            raw = output.read_text(encoding="utf-8"); data = json.loads(raw)
        self.assertEqual(data["production_run_id"], "run")
        self.assertNotIn("transcript", raw.lower()); self.assertNotIn("api_key", raw.lower())

    def test_production_summary_counts_partial_failure(self) -> None:
        run = ProductionRun("run", "p", 1, 3, "now", generated_edit_ids=["a", "b", "c"], accepted_edit_ids=["a", "b"],
                            edit_states={"a": {"state": "rendered"}, "b": {"state": "failed"}, "c": {"state": "review"}})
        self.assertEqual(production_summary(run), {"requested": 3, "produced": 3, "approved": 2, "rendered": 1, "failed": 1, "needs_review": 0})


class Phase3AOrchestrationTests(unittest.TestCase):
    def project_with_run(self, root: Path, count: int = 2):
        project = sample_project(); first = accepted_sequence(); first.id = "edit1"; story1 = story_for(first, story_id="story1", topic="race challenge")
        sequences = [first]; stories = [story1]
        if count > 1:
            second = deepcopy(first); second.id = "edit2"
            for index, segment in enumerate(second.segments):
                segment.moment_id = f"different_{index}"; segment.source_in = mt(80 + index * 5); segment.source_out = mt(84 + index * 5)
            stories.append(story_for(second, story_id="story2", topic="training argument")); sequences.append(second)
        profile = WorkflowProfile("profile", "Fast Shorts", min_duration_seconds=10, desired_output_count=count)
        run = ProductionRun("run", profile.id, profile.revision, count, "now", workflow_profile_snapshot=asdict(profile), generated_edit_ids=[item.id for item in sequences], selected_edit_ids=[item.id for item in sequences], edit_states={item.id: {"state": "review"} for item in sequences})
        project.story_concepts = stories; project.edit_sequences = sequences; project.workflow_profiles = [profile]; project.production_runs = [run]
        workspace = Workspace(root); save_project(workspace, project)
        return workspace

    def test_generation_reuses_cached_edits_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); workspace = self.project_with_run(root, 1); service = DesktopService(root / "state", root)
            with patch("autoclip.desktop_service.GeminiProvider") as provider:
                service._production_generate_job(str(workspace.project_file), "run", Event(), lambda *_args: None)
            provider.assert_not_called()
            restored = load_project(workspace)
        self.assertEqual(restored.production_runs[0].generated_edit_ids, ["edit1"])

    def test_profile_library_is_reusable_across_projects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state_root = root / "state"; first = Workspace(root / "one"); second = Workspace(root / "two")
            save_project(first, sample_project()); other = sample_project(); other.id = "other"; other.name = "Other"; save_project(second, other)
            service = DesktopService(state_root, root)
            service.save_workflow_profile({"project_path": str(first.project_file), "id": "shared", "name": "Shared", "min_duration_seconds": 10})
            state = service.get_project_state({"path": str(second.project_file)})
            self.assertIn("shared", [item["id"] for item in state["workflow_profiles"]])
            created = service.create_production_run({"project_path": str(second.project_file), "workflow_profile_id": "shared"})
        self.assertEqual(created["production_runs"][0]["workflow_profile_id"], "shared")

    def test_batch_render_isolates_failure_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); workspace = self.project_with_run(root); service = DesktopService(root / "state", root)
            def render(_project_path, edit_id, _preview, _event, _update):
                if edit_id == "edit1":
                    raise RuntimeError("fixture failure")
            with patch.object(service, "_render_edit_sequence_job", side_effect=render):
                service._batch_render_job(str(workspace.project_file), "run", False, Event(), lambda *_args: None)
            run = load_project(workspace).production_runs[0]
        self.assertEqual(run.edit_states["edit1"]["state"], "failed")
        self.assertEqual(run.edit_states["edit2"]["state"], "rendered")

    def test_batch_cancellation_preserves_completed_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); workspace = self.project_with_run(root); project = load_project(workspace)
            project.production_runs[0].edit_states["edit1"] = {"state": "rendered", "stage": "Completed", "error": None}; save_project(workspace, project)
            service = DesktopService(root / "state", root); event = Event(); event.set()
            service._batch_render_job(str(workspace.project_file), "run", False, event, lambda *_args: None)
            run = load_project(workspace).production_runs[0]
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.edit_states["edit1"]["state"], "rendered")


if __name__ == "__main__":
    unittest.main()
