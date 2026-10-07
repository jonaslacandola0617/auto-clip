from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import threading
import zipfile
from fractions import Fraction
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ai import GeminiProvider
from .captions import CAPTION_PRESETS, build_caption_track, build_edit_sequence_caption_track, export_ass, export_edit_sequence_ass, export_srt
from .credentials import CredentialStore
from .enhancements import plan_enhancements, relink_asset, serialize_graphics_ass, validate_enhancement_plan
from .desktop_protocol import ProtocolError
from .exporters.otio import export_otio
from .exporters.premiere_xml import export_premiere_xml
from .jobs import JobManager
from .media import FFmpegService, media_source_from_probe
from .models import (
    ApprovedClip, AutoClipProject, CampaignProfile, EditSequence, ManualCropOverride, MediaSource,
    OutputArtifact, ProductionRun, ProjectClip, Timeline, VisualEditPlan, WorkflowProfile,
)
from .production import (
    apply_profile_policy, collision_safe_path, production_summary, render_filename,
    select_diverse_edits, validate_campaign_edit, validate_campaign_profile,
    validate_workflow_profile, write_manifest,
)
from .intelligent_edit import construct_candidate_stories, plan_reviewed_sequence, reflow_sequence, review_editorial_quality, validate_integrity
from .editorial_v2 import create_shorter_sequence_variant, quality_score, quality_from_scores
from .pipeline import CorePipeline
from .release import MODEL_APPROX_BYTES, ReleasePaths, require_free_space, sanitize_diagnostic_text
from .storage import Workspace, atomic_write_json, load_project, save_project
from .time import MediaTime
from .transcription import FasterWhisperTranscriber, TranscriptionCancelled
from .vision import DETECTOR_CONFIG, DETECTOR_VERSION, MediaPipeFaceDetector, analyze_video_clip, analyze_visual_observations, build_visual_edit_plan, crop_geometry
from .version import APP_VERSION


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _development_api_key(project_root: Path) -> bool:
    if os.environ.get("GEMINI_API_KEY"):
        return True
    env_file = project_root / ".env"
    if not env_file.exists():
        return False
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "GEMINI_API_KEY" and value.strip().strip("\"'"):
            os.environ["GEMINI_API_KEY"] = value.strip().strip("\"'")
            return True
    return False


class DesktopService:
    def __init__(self, state_root: Path | None = None, project_root: Path | None = None) -> None:
        default_root = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "AutoClip"
        self.state_root = (state_root or Path(os.environ.get("AUTOCLIP_APP_DATA", default_root))).resolve()
        self.project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        self.paths = ReleasePaths(self.state_root)
        self.paths.ensure()
        self.recents_path = self.state_root / "recent-projects.json"
        self.settings_path = self.state_root / "settings.json"
        self.profile_library_path = self.state_root / "profiles.json"
        self.credentials = CredentialStore(self.state_root / "credentials" / "gemini.dpapi")
        self.jobs = JobManager(self.state_root / "jobs.json")
        _development_api_key(self.project_root)

    def dispatch(self, command: str, payload: dict[str, Any]) -> Any:
        handlers = {
            "doctor": self.doctor,
            "create_project": self.create_project,
            "open_project": self.open_project,
            "get_project_state": self.get_project_state,
            "inspect_media": self.inspect_media,
            "relink_source": self.relink_source,
            "list_recent_projects": self.list_recent_projects,
            "remove_recent_project": self.remove_recent_project,
            "get_settings": self.get_settings,
            "update_settings": self.update_settings,
            "start_job": self.start_job,
            "cancel_job": self.cancel_job,
            "get_job": self.get_job,
            "list_jobs": self.list_jobs,
            "correct_transcript": self.correct_transcript,
            "create_manual_clip": self.create_manual_clip,
            "create_clip_from_candidate": self.create_clip_from_candidate,
            "update_clip": self.update_clip,
            "delete_clip": self.delete_clip,
            "select_clip": self.select_clip,
            "update_edit_sequence": self.update_edit_sequence,
            "update_visual_plan": self.update_visual_plan,
            "update_enhancement_plan": self.update_enhancement_plan,
            "save_workflow_profile": self.save_workflow_profile,
            "save_campaign_profile": self.save_campaign_profile,
            "create_production_run": self.create_production_run,
            "update_production_run": self.update_production_run,
            "bulk_production_action": self.bulk_production_action,
            "set_gemini_api_key": self.set_gemini_api_key,
            "clear_gemini_api_key": self.clear_gemini_api_key,
            "clear_cache": self.clear_cache,
            "export_diagnostics": self.export_diagnostics,
        }
        return handlers[command](payload)

    def _gemini_api_key(self) -> str | None:
        development = os.environ.get("GEMINI_API_KEY")
        if development:
            return development
        try:
            return self.credentials.get_gemini_key()
        except (OSError, RuntimeError):
            return None

    def _gemini_provider(self, settings: dict[str, Any]) -> GeminiProvider:
        return GeminiProvider(model=settings["gemini_model"], api_key=self._gemini_api_key())

    def _transcriber(self, model: str) -> FasterWhisperTranscriber:
        return FasterWhisperTranscriber(model, device="cpu", compute_type="int8", model_cache=self.paths.models)

    def doctor(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        media = FFmpegService()
        settings = self.get_settings()
        transcriber = self._transcriber(settings["whisper_model"])
        whisper_ready = transcriber.available
        gemini_ready = bool(self._gemini_api_key())
        storage_ready = os.access(self.state_root, os.W_OK) and os.access(self.paths.cache, os.W_OK)
        ffmpeg_version = media.version() if media.available else "unavailable"
        detector_ready = _module_available("mediapipe") and self._detector_ready()
        return {
            "runtime": {"ready": True, "summary": f"Worker {APP_VERSION}"},
            "storage": {"ready": storage_ready, "summary": "Ready" if storage_ready else "Application storage is not writable"},
            "ffmpeg": {"ready": media.available, "summary": f"{'Bundled' if media.bundled else 'Development'} FFmpeg {ffmpeg_version}" if media.available else "Bundled FFmpeg unavailable"},
            "ffprobe": {"ready": media.available, "summary": "Bundled ffprobe ready" if media.bundled else "Development ffprobe ready" if media.available else "Bundled ffprobe unavailable"},
            "whisper": {"ready": whisper_ready, "summary": "Ready - CPU processing" if whisper_ready else "Not available"},
            "model": {"ready": transcriber.model_is_cached, "summary": f"{settings['whisper_model'].title()} model ready" if transcriber.model_is_cached else f"{settings['whisper_model'].title()} model downloads on first transcription (~{MODEL_APPROX_BYTES[settings['whisper_model']] // (1024 * 1024)} MB)"},
            "opencv": {"ready": _module_available("cv2"), "summary": "Ready" if _module_available("cv2") else "Not available"},
            "mediapipe": {"ready": detector_ready, "summary": "Ready - packaged offline detector" if detector_ready else "Not available - stable framing fallback enabled"},
            "otio": {"ready": True, "summary": "Ready - built-in OTIO export"},
            "gemini": {"ready": gemini_ready, "summary": "Configured" if gemini_ready else "API key not configured"},
            "acceleration": {"ready": False, "summary": "Not available - CPU processing will be used"},
            "python": sys.version.split()[0],
            "app_version": APP_VERSION,
            "paths": {"data": str(self.state_root), "cache": str(self.paths.cache), "models": str(self.paths.models), "logs": str(self.paths.logs)},
        }

    def _detector_ready(self) -> bool:
        try:
            detector = MediaPipeFaceDetector()
            detector.close()
            return True
        except Exception:
            return False

    def _project_file(self, value: str) -> Path:
        path = Path(value).expanduser().resolve()
        return path / "project.autoclip.json" if path.is_dir() else path

    def _load_project(self, value: str) -> tuple[Workspace, AutoClipProject]:
        project_file = self._project_file(value)
        if not project_file.exists():
            raise ProtocolError("project_not_found", "We couldn't find this AutoClip project. Locate it again or remove it from Recents.")
        workspace = Workspace(project_file.parent)
        try:
            return workspace, load_project(workspace)
        except ValueError as exc:
            if "schema version" in str(exc):
                raise ProtocolError("unsupported_project_version", "This project was created by an unsupported AutoClip version.", recoverable=False) from exc
            raise

    def _inspect_source(self, path: Path, source_id: str = "media_001") -> MediaSource:
        if not path.is_file():
            raise ProtocolError("source_not_found", "We couldn't find this video. Choose another file or locate it again.")
        media = FFmpegService()
        if not media.available:
            raise ProtocolError("ffmpeg_unavailable", "AutoClip can't inspect video yet because FFmpeg is unavailable.")
        try:
            return media_source_from_probe(source_id, path, media.inspect(path))
        except Exception as exc:
            raise ProtocolError("media_unreadable", "We couldn't read this video. Choose another file or check that the file isn't damaged.", details=sanitize_diagnostic_text(str(exc))) from exc

    def _safe_project_directory(self, location: Path, name: str) -> Path:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip().rstrip(".")
        if not cleaned:
            raise ProtocolError("invalid_project_name", "Enter a project name.")
        return location.resolve() / cleaned

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        location_value = payload.get("location")
        if not location_value:
            raise ProtocolError("project_location_required", "Choose where this project should be stored.")
        location = Path(str(location_value)).expanduser()
        location.mkdir(parents=True, exist_ok=True)
        root = self._safe_project_directory(location, name)
        project_file = root / "project.autoclip.json"
        if project_file.exists():
            raise ProtocolError("project_exists", "A project with this name already exists in that location.")
        source_value = payload.get("source_path")
        sources = [self._inspect_source(Path(str(source_value)))] if source_value else []
        project = AutoClipProject(id=f"project_{int(datetime.now(UTC).timestamp() * 1000)}", name=name, sources=sources)
        workspace = Workspace(root)
        save_project(workspace, project)
        self._touch_recent(workspace.project_file, project.name)
        return self._state(workspace, project)

    def open_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = payload.get("path")
        if not path:
            raise ProtocolError("project_path_required", "Choose an AutoClip project to open.")
        workspace, project = self._load_project(str(path))
        self._touch_recent(workspace.project_file, project.name)
        return self._state(workspace, project)

    def get_project_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("path", "")))
        return self._state(workspace, project)

    def _source_summary(self, source: MediaSource, workspace: Workspace | None = None) -> dict[str, Any]:
        path = Path(source.reference)
        video = next((stream for stream in source.streams if stream.kind == "video"), None)
        proxy_path = workspace.root / "cache" / "proxies" / f"{source.id}-{source.fingerprint.get('partial_sha256', 'source')}.mp4" if workspace else None
        return {
            "id": source.id,
            "path": source.reference,
            "filename": path.name,
            "available": path.is_file(),
            "duration_seconds": float(source.duration.seconds),
            "width": source.width,
            "height": source.height,
            "frame_rate": float(source.frame_rate.as_fraction()),
            "frame_rate_label": f"{source.frame_rate.numerator}/{source.frame_rate.denominator}",
            "codec": video.codec if video else "Unknown",
            "has_audio": any(stream.kind == "audio" for stream in source.streams),
            "preview_path": str(proxy_path) if proxy_path and proxy_path.exists() else None,
        }

    def _transcript_summary(self, project: AutoClipProject) -> dict[str, Any] | None:
        if not project.transcripts:
            return None
        transcript = project.transcripts[-1]
        return {
            "revision_id": transcript.revision_id,
            "language": transcript.language,
            "model": transcript.model,
            "segments": [{
                "id": segment.id,
                "start_seconds": float(segment.start.seconds),
                "end_seconds": float(segment.end.seconds),
                "original_text": segment.text,
                "corrected_text": segment.corrected_text,
                "text": segment.corrected_text or segment.text,
            } for segment in transcript.segments],
        }

    def _candidate_summary(self, candidate: Any) -> dict[str, Any]:
        scores = candidate.scores
        score = quality_score(candidate.editorial_quality) if candidate.editorial_quality else scores.hook * .35 + scores.standalone_context * .25 + scores.payoff * .30 + scores.emotion * .10
        return {
            "id": candidate.id, "title": candidate.title,
            "start_seconds": float(candidate.source_start.seconds),
            "end_seconds": float(candidate.source_end.seconds),
            "duration_seconds": float(candidate.source_end.seconds - candidate.source_start.seconds),
            "source": "ai", "score": round(score, 1), "category": candidate.category,
            "reason": candidate.reason,
            "hook": candidate.hook, "payoff": candidate.payoff, "story_structure": candidate.story_structure,
            "quality": asdict(candidate.editorial_quality) if candidate.editorial_quality else None,
        }

    def _clip_summary(self, workspace: Workspace, project: AutoClipProject, clip: ProjectClip) -> dict[str, Any]:
        reframe = next((track for track in project.reframe_tracks if track.id == clip.reframe_track_id), None)
        captions = next((track for track in project.caption_tracks if track.id == clip.caption_track_id), None)
        return {
            "id": clip.id, "title": clip.title, "source": clip.source,
            "candidate_id": clip.candidate_id, "selected": clip.selected,
            "start_seconds": float(clip.source_in.seconds), "end_seconds": float(clip.source_out.seconds),
            "duration_seconds": float(clip.source_out.seconds - clip.source_in.seconds),
            "framing_mode": clip.framing_mode, "manual_crop": asdict(clip.manual_crop),
            "captions_enabled": clip.captions_enabled, "caption_preset": clip.caption_preset,
            "has_reframe": reframe is not None, "caption_cue_count": len(captions.cues) if captions else 0,
            "preview_path": str(workspace.root / "cache" / "previews" / f"{clip.id}-r{clip.revision}.mp4") if (workspace.root / "cache" / "previews" / f"{clip.id}-r{clip.revision}.mp4").exists() else None,
            "render_path": clip.render_path, "revision": clip.revision,
        }

    def _edit_sequence_summary(self, sequence: EditSequence, project: AutoClipProject | None = None) -> dict[str, Any]:
        plan = next((item for item in (project.visual_edit_plans if project else []) if item.sequence_id == sequence.id), None)
        enhancement = next((item for item in (project.enhancement_plans if project else []) if item.sequence_id == sequence.id), None)
        return {
            "id": sequence.id, "story_concept_id": sequence.story_concept_id, "title": sequence.title,
            "duration_seconds": sequence.duration_seconds, "segment_count": len(sequence.segments),
            "integrity": asdict(sequence.integrity), "status": sequence.status, "revision": sequence.revision,
            "editorial_review": asdict(sequence.editorial_review) if sequence.editorial_review else None,
            "preview_path": sequence.preview_path, "render_path": sequence.render_path,
            "visual_plan": ({"id": plan.id, "revision": plan.revision, "framing_mode": plan.framing_mode,
                             "visual_emphasis": plan.visual_emphasis, "caption_preset": plan.caption_preset,
                             "caption_position": plan.caption_position, "warnings": plan.warnings,
                             "actions": [asdict(action) for action in plan.visual_actions],
                             "layouts": [asdict(layout) for layout in plan.caption_layout]} if plan else None),
            "enhancement_plan": (asdict(enhancement) if enhancement else None),
            "segments": [{
                "id": segment.id, "moment_id": segment.moment_id, "source_id": segment.source_id,
                "source_in": float(segment.source_in.seconds), "source_out": float(segment.source_out.seconds),
                "timeline_start": segment.timeline_start, "duration_seconds": float(segment.source_out.seconds - segment.source_in.seconds),
                "purpose": segment.purpose, "transcript_excerpt": segment.transcript_excerpt, "order": segment.order,
                "actions": [asdict(action) for action in sequence.actions if action.id in segment.action_ids or action.timeline_start >= segment.timeline_start and action.timeline_start < segment.timeline_start + float(segment.source_out.seconds - segment.source_in.seconds)],
            } for segment in sequence.segments],
        }

    def _state(self, workspace: Workspace, project: AutoClipProject) -> dict[str, Any]:
        library_workflows, library_campaigns = self._read_profile_library()
        workflows = {item.id: item for item in library_workflows}
        workflows.update({item.id: item for item in project.workflow_profiles})
        campaigns = {item.id: item for item in library_campaigns}
        campaigns.update({item.id: item for item in project.campaign_profiles})
        source = self._source_summary(project.sources[0], workspace) if project.sources else None
        if source is None:
            status = "needs_source"
        elif not source["available"]:
            status = "source_missing"
        elif not project.transcripts:
            status = "needs_transcript"
        elif not project.candidates and not project.clips:
            status = "needs_analysis"
        else:
            status = "ready"
        return {
            "path": str(workspace.project_file), "directory": str(workspace.root), "id": project.id,
            "name": project.name, "schema_version": project.schema_version, "status": status, "source": source,
            "transcript_count": len(project.transcripts), "candidate_count": len(project.candidates),
            "timeline_count": len(project.timelines), "transcript": self._transcript_summary(project),
            "candidates": [self._candidate_summary(candidate) for candidate in project.candidates],
            "clips": [self._clip_summary(workspace, project, clip) for clip in project.clips],
            "outputs": [asdict(output) for output in project.outputs],
            "moment_count": len(project.moments),
            "story_concepts": [asdict(story) for story in project.story_concepts],
            "edit_sequences": [self._edit_sequence_summary(sequence, project) for sequence in project.edit_sequences],
            "workflow_profiles": [asdict(profile) for profile in workflows.values()],
            "campaign_profiles": [asdict(profile) for profile in campaigns.values()],
            "production_runs": [{**asdict(run), "summary": production_summary(run)} for run in project.production_runs],
            "analysis_metrics": asdict(project.analysis_runs[-1]) if project.analysis_runs else None,
            "analysis_revision": project.analysis_revision,
        }

    def inspect_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._inspect_source(Path(str(payload.get("path", ""))))
        return self._source_summary(source)

    def relink_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        source_id = project.sources[0].id if project.sources else "media_001"
        replacement = self._inspect_source(Path(str(payload.get("source_path", ""))), source_id)
        if project.sources:
            previous = project.sources[0].fingerprint
            current = replacement.fingerprint
            same_media = previous.get("size") == current.get("size") and previous.get("partial_sha256") == current.get("partial_sha256")
            if not same_media:
                raise ProtocolError(
                    "source_fingerprint_mismatch",
                    "This file does not match the project's original source. Choose the moved original video instead.",
                    details="The file size or media fingerprint is different; no project data was changed.",
                )
        if project.sources:
            project.sources[0] = replacement
        else:
            project.sources.append(replacement)
        save_project(workspace, project)
        return self._state(workspace, project)

    def correct_transcript(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        if not project.transcripts:
            raise ProtocolError("transcript_missing", "Transcribe the source before correcting text.")
        segment_id = str(payload.get("segment_id", ""))
        corrected = str(payload.get("text", "")).strip()
        transcript = project.transcripts[-1]
        segment = next((item for item in transcript.segments if item.id == segment_id), None)
        if segment is None:
            raise ProtocolError("segment_not_found", "We couldn't find that transcript segment.")
        segment.corrected_text = corrected if corrected and corrected != segment.text else None
        transcript.revision_id = workspace.cache_key("transcript_revision", {
            "model": transcript.model,
            "segments": [{"id": item.id, "original": item.text, "override": item.corrected_text} for item in transcript.segments],
        })
        removed_ai_ids = {clip.id for clip in project.clips if clip.source == "ai"}
        project.candidates.clear()
        project.candidate_windows.clear()
        project.analysis_runs.clear()
        project.moments.clear()
        project.story_concepts.clear()
        project.edit_sequences.clear()
        project.visual_edit_plans.clear()
        project.enhancement_plans.clear()
        project.production_runs.clear()
        project.analysis_revision = None
        project.clips = [clip for clip in project.clips if clip.source != "ai"]
        project.caption_tracks.clear()
        affected_clip_ids = set(removed_ai_ids)
        for clip in project.clips:
            clip.caption_track_id = None
            if clip.captions_enabled:
                clip.render_path = None
                clip.revision += 1
                affected_clip_ids.add(clip.id)
        project.outputs = [output for output in project.outputs if output.clip_id not in affected_clip_ids and output.kind not in {"srt", "ass"}]
        save_project(workspace, project)
        return self._state(workspace, project)

    def _clip_time(self, source: MediaSource, value: Any) -> MediaTime:
        try:
            return MediaTime.from_seconds(Fraction(str(value)), source.time_base, exact=False)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise ProtocolError("invalid_clip_bounds", "Choose a valid clip start and end.") from exc

    def _validate_clip_bounds(self, source: MediaSource, start: MediaTime, end: MediaTime) -> None:
        if start.seconds < 0 or end.seconds <= start.seconds or end.seconds > source.duration.seconds:
            raise ProtocolError("invalid_clip_bounds", "Clip timing must stay inside the source and end after it starts.")

    def _ensure_caption_track(self, project: AutoClipProject, clip: ProjectClip) -> None:
        if clip.caption_track_id:
            project.caption_tracks = [track for track in project.caption_tracks if track.id != clip.caption_track_id]
        clip.caption_track_id = None
        if clip.captions_enabled and project.transcripts:
            track = build_caption_track(clip, project.transcripts[-1])
            project.caption_tracks.append(track)
            clip.caption_track_id = track.id

    def create_manual_clip(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        if not project.sources or not project.transcripts:
            raise ProtocolError("transcript_missing", "Transcribe the source before creating a manual clip.")
        transcript = project.transcripts[-1]
        start_id, end_id = str(payload.get("start_segment_id", "")), str(payload.get("end_segment_id", ""))
        start_index = next((index for index, segment in enumerate(transcript.segments) if segment.id == start_id), -1)
        end_index = next((index for index, segment in enumerate(transcript.segments) if segment.id == end_id), -1)
        if start_index < 0 or end_index < start_index:
            raise ProtocolError("invalid_clip_bounds", "Choose an ending segment at or after the starting segment.")
        start, end = transcript.segments[start_index].start, transcript.segments[end_index].end
        self._validate_clip_bounds(project.sources[0], start, end)
        title = str(payload.get("title", "")).strip() or (transcript.segments[start_index].corrected_text or transcript.segments[start_index].text)[:64]
        clip = ProjectClip(
            id=f"clip_{int(datetime.now(UTC).timestamp() * 1000)}", source_id=project.sources[0].id,
            source_in=start, source_out=end, title=title, source="manual",
        )
        self._ensure_caption_track(project, clip)
        project.clips.append(clip)
        save_project(workspace, project)
        return self._state(workspace, project)

    def create_clip_from_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        candidate_id = str(payload.get("candidate_id", ""))
        candidate = next((item for item in project.candidates if item.id == candidate_id), None)
        if candidate is None or not project.sources:
            raise ProtocolError("candidate_not_found", "We couldn't find that AI clip candidate.")
        existing = next((clip for clip in project.clips if clip.candidate_id == candidate_id), None)
        if existing:
            return self._state(workspace, project)
        clip = ProjectClip(
            id=f"clip_{candidate.id}", source_id=project.sources[0].id,
            source_in=candidate.source_start, source_out=candidate.source_end,
            title=candidate.title, source="ai", candidate_id=candidate.id,
        )
        self._ensure_caption_track(project, clip)
        project.clips.append(clip)
        save_project(workspace, project)
        return self._state(workspace, project)

    def _invalidate_clip(self, project: AutoClipProject, clip: ProjectClip, *, reframe: bool, captions: bool) -> None:
        if reframe and clip.reframe_track_id:
            project.reframe_tracks = [track for track in project.reframe_tracks if track.id != clip.reframe_track_id]
            clip.reframe_track_id = None
        if captions and clip.caption_track_id:
            project.caption_tracks = [track for track in project.caption_tracks if track.id != clip.caption_track_id]
            clip.caption_track_id = None
        clip.render_path = None
        project.outputs = [output for output in project.outputs if output.clip_id != clip.id]

    def update_clip(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        clip = next((item for item in project.clips if item.id == str(payload.get("clip_id", ""))), None)
        if clip is None or not project.sources:
            raise ProtocolError("clip_not_found", "We couldn't find that clip.")
        old_times = (clip.source_in, clip.source_out)
        old_framing = (clip.framing_mode, asdict(clip.manual_crop))
        old_caption = (clip.captions_enabled, clip.caption_preset)
        if "title" in payload:
            clip.title = str(payload["title"]).strip() or clip.title
        if "start_seconds" in payload:
            clip.source_in = self._clip_time(project.sources[0], payload["start_seconds"])
        if "end_seconds" in payload:
            clip.source_out = self._clip_time(project.sources[0], payload["end_seconds"])
        self._validate_clip_bounds(project.sources[0], clip.source_in, clip.source_out)
        if "framing_mode" in payload:
            mode = str(payload["framing_mode"])
            if mode not in {"auto", "manual"}:
                raise ProtocolError("invalid_framing", "Choose Auto or Manual framing.")
            clip.framing_mode = mode
        if "manual_crop" in payload:
            crop = payload["manual_crop"]
            clip.manual_crop = ManualCropOverride(
                enabled=clip.framing_mode == "manual", crop_x=max(0.0, min(1.0, float(crop.get("crop_x", .5)))),
                crop_y=max(0.0, min(1.0, float(crop.get("crop_y", .5)))), scale=max(1.0, min(3.0, float(crop.get("scale", 1.0)))),
            )
        if "captions_enabled" in payload:
            clip.captions_enabled = bool(payload["captions_enabled"])
        if "caption_preset" in payload:
            preset = str(payload["caption_preset"])
            if preset not in CAPTION_PRESETS:
                raise ProtocolError("invalid_caption_preset", "Choose a supported caption preset.")
            clip.caption_preset = preset
        timing_changed = old_times != (clip.source_in, clip.source_out)
        framing_changed = old_framing != (clip.framing_mode, asdict(clip.manual_crop))
        caption_changed = old_caption != (clip.captions_enabled, clip.caption_preset)
        if timing_changed or framing_changed or caption_changed:
            clip.revision += 1
            self._invalidate_clip(project, clip, reframe=timing_changed or framing_changed, captions=timing_changed or caption_changed)
            self._ensure_caption_track(project, clip)
        save_project(workspace, project)
        return self._state(workspace, project)

    def delete_clip(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        clip_id = str(payload.get("clip_id", ""))
        clip = next((item for item in project.clips if item.id == clip_id), None)
        if clip is None:
            raise ProtocolError("clip_not_found", "We couldn't find that clip.")
        self._invalidate_clip(project, clip, reframe=True, captions=True)
        project.clips = [item for item in project.clips if item.id != clip_id]
        save_project(workspace, project)
        return self._state(workspace, project)

    def select_clip(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        clip = next((item for item in project.clips if item.id == str(payload.get("clip_id", ""))), None)
        if clip is None:
            raise ProtocolError("clip_not_found", "We couldn't find that clip.")
        clip.selected = bool(payload.get("selected", True))
        save_project(workspace, project)
        return self._state(workspace, project)

    def update_edit_sequence(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        sequence = next((item for item in project.edit_sequences if item.id == str(payload.get("edit_sequence_id", ""))), None)
        if sequence is None or not project.transcripts:
            raise ProtocolError("edit_sequence_not_found", "We couldn't find that Smart Edit.")
        operation = str(payload.get("operation", ""))
        if operation == "rename":
            title = str(payload.get("title", "")).strip()
            if not title:
                raise ProtocolError("invalid_title", "Enter a Smart Edit name.")
            sequence.title = title
        elif operation == "remove_segment":
            sequence.segments = [item for item in sequence.segments if item.id != str(payload.get("segment_id", ""))]
        elif operation == "reorder":
            order = [str(item) for item in payload.get("segment_ids", [])]
            if set(order) != {item.id for item in sequence.segments} or len(order) != len(sequence.segments):
                raise ProtocolError("invalid_segment_order", "Keep every Smart Edit segment exactly once when reordering.")
            by_id = {item.id: item for item in sequence.segments}
            sequence.segments = [by_id[item] for item in order]
        elif operation == "trim":
            segment = next((item for item in sequence.segments if item.id == str(payload.get("segment_id", ""))), None)
            moment = next((item for item in project.moments if segment and item.id == segment.moment_id), None)
            if segment is None or moment is None or not project.sources:
                raise ProtocolError("edit_segment_not_found", "We couldn't find that Smart Edit segment.")
            start = self._clip_time(project.sources[0], payload.get("source_in"))
            end = self._clip_time(project.sources[0], payload.get("source_out"))
            if start.seconds < moment.source_in.seconds or end.seconds > moment.source_out.seconds or end.seconds <= start.seconds:
                raise ProtocolError("invalid_segment_bounds", "Keep the trim inside its source-grounded Moment.")
            segment.source_in, segment.source_out = start, end
        elif operation == "toggle_action":
            action = next((item for item in sequence.actions if item.id == str(payload.get("action_id", ""))), None)
            if action is None:
                raise ProtocolError("editorial_action_not_found", "We couldn't find that editorial action.")
            action.enabled = bool(payload.get("enabled", True))
            action.revision += 1
        else:
            raise ProtocolError("unsupported_edit_operation", "That Smart Edit change isn't supported.")
        reflow_sequence(sequence)
        moment_map = {item.id: item for item in project.moments}
        story = next((item for item in project.story_concepts if item.id == sequence.story_concept_id), None)
        sequence.integrity = validate_integrity(sequence, project.transcripts[-1], moment_map, allow_truthful_hook_reorder=bool(story and story.coherence.get("allow_truthful_hook_reorder")))
        sequence.editorial_review = review_editorial_quality(story, sequence, moment_map) if story else None
        sequence.status = "ready" if len(sequence.segments) >= 2 and sequence.integrity.status == "passed" and sequence.editorial_review and sequence.editorial_review.accepted else "needs_revision"
        if story:
            story.status = sequence.status
            story.understandable_without_source = bool(sequence.editorial_review and sequence.editorial_review.self_contained)
        project.visual_edit_plans = [item for item in project.visual_edit_plans if item.sequence_id != sequence.id]
        project.enhancement_plans = [item for item in project.enhancement_plans if item.sequence_id != sequence.id]
        sequence.preview_path = None
        sequence.render_path = None
        save_project(workspace, project)
        return self._state(workspace, project)

    def update_visual_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        sequence_id = str(payload.get("edit_sequence_id", ""))
        sequence = next((item for item in project.edit_sequences if item.id == sequence_id), None)
        plan = next((item for item in project.visual_edit_plans if item.sequence_id == sequence_id), None)
        if sequence is None:
            raise ProtocolError("edit_sequence_not_found", "We couldn't find that Smart Edit.")
        if plan is None:
            plan = build_visual_edit_plan(sequence, [])
            project.visual_edit_plans.append(plan)
        operation = str(payload.get("operation", "settings"))
        if operation == "toggle_action":
            action = next((item for item in plan.visual_actions if item.id == str(payload.get("action_id", ""))), None)
            if action is None:
                raise ProtocolError("visual_action_not_found", "We couldn't find that visual action.")
            action.enabled = bool(payload.get("enabled", True))
            action.revision += 1
            rebuilt = build_visual_edit_plan(sequence, plan.observations, previous=plan, framing_mode=plan.framing_mode,
                                             visual_emphasis=plan.visual_emphasis, caption_preset=plan.caption_preset,
                                             caption_position=plan.caption_position)
            project.visual_edit_plans = [item for item in project.visual_edit_plans if item.sequence_id != sequence_id] + [rebuilt]
        elif operation == "settings":
            framing = str(payload.get("framing_mode", plan.framing_mode))
            emphasis = str(payload.get("visual_emphasis", plan.visual_emphasis))
            preset = str(payload.get("caption_preset", plan.caption_preset))
            position = str(payload.get("caption_position", plan.caption_position))
            if framing not in {"auto", "fixed"} or emphasis not in {"automatic", "off"} or preset not in CAPTION_PRESETS or position not in {"auto", "upper", "center", "lower"}:
                raise ProtocolError("invalid_visual_settings", "Choose supported framing, emphasis, and caption settings.")
            rebuilt = build_visual_edit_plan(sequence, plan.observations, previous=plan, framing_mode=framing, visual_emphasis=emphasis, caption_preset=preset, caption_position=position)
            project.visual_edit_plans = [item for item in project.visual_edit_plans if item.sequence_id != sequence_id] + [rebuilt]
        else:
            raise ProtocolError("unsupported_visual_operation", "That visual treatment change isn't supported.")
        current_visual = next(item for item in project.visual_edit_plans if item.sequence_id == sequence_id)
        for enhancement in project.enhancement_plans:
            if enhancement.sequence_id == sequence_id:
                enhancement.visual_plan_revision = current_visual.revision
                enhancement.revision += 1
        sequence.preview_path = None
        sequence.render_path = None
        save_project(workspace, project)
        return self._state(workspace, project)

    def update_enhancement_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        sequence_id = str(payload.get("edit_sequence_id", ""))
        sequence = next((item for item in project.edit_sequences if item.id == sequence_id), None)
        plan = next((item for item in project.enhancement_plans if item.sequence_id == sequence_id), None)
        if sequence is None or plan is None:
            raise ProtocolError("enhancement_plan_not_found", "Plan enhancements before changing them.")
        operation = str(payload.get("operation", ""))
        if operation == "toggle":
            item_id = str(payload.get("item_id", ""))
            item = next((item for item in [*plan.broll_items, *plan.graphic_items, *plan.sound_cues] if item.id == item_id), None)
            if item is None and plan.music_track and item_id == "music":
                item = plan.music_track
            if item is None:
                raise ProtocolError("enhancement_not_found", "We couldn't find that enhancement.")
            item.enabled = bool(payload.get("enabled", True))
            plan.user_overrides[item_id] = {"enabled": item.enabled}
            plan.revision += 1
        elif operation == "relink_asset":
            try:
                relink_asset(plan, str(payload.get("asset_id", "")), Path(str(payload.get("reference", ""))))
            except ValueError as exc:
                raise ProtocolError("enhancement_asset_unavailable", str(exc)) from exc
        else:
            raise ProtocolError("unsupported_enhancement_operation", "That enhancement change isn't supported.")
        validate_enhancement_plan(plan, sequence)
        sequence.preview_path = None
        sequence.render_path = None
        save_project(workspace, project)
        return self._state(workspace, project)

    def _read_profile_library(self) -> tuple[list[WorkflowProfile], list[CampaignProfile]]:
        if not self.profile_library_path.exists():
            return [], []
        raw = json.loads(self.profile_library_path.read_text(encoding="utf-8"))
        return ([WorkflowProfile.from_dict(item) for item in raw.get("workflow_profiles", [])],
                [CampaignProfile(**item) for item in raw.get("campaign_profiles", [])])

    def _save_profile_library(self, workflows: list[WorkflowProfile], campaigns: list[CampaignProfile]) -> None:
        atomic_write_json(self.profile_library_path, {"workflow_profiles": [asdict(item) for item in workflows], "campaign_profiles": [asdict(item) for item in campaigns]})

    def save_workflow_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        profile_id = str(payload.get("id") or f"workflow_{int(datetime.now(UTC).timestamp() * 1000)}")
        existing = next((item for item in project.workflow_profiles if item.id == profile_id), None)
        profile = WorkflowProfile(
            id=profile_id, name=str(payload.get("name", existing.name if existing else "Fast Shorts")),
            revision=(existing.revision + 1 if existing else 1),
            generation_mode=str(payload.get("generation_mode", existing.generation_mode if existing else "concepts_only")),
            target_platform=str(payload.get("target_platform", existing.target_platform if existing else "shorts")),
            min_duration_seconds=float(payload.get("min_duration_seconds", existing.min_duration_seconds if existing else 15)),
            max_duration_seconds=float(payload.get("max_duration_seconds", existing.max_duration_seconds if existing else 45)),
            target_duration_seconds=float(payload.get("target_duration_seconds", existing.target_duration_seconds if existing and existing.target_duration_seconds is not None else 30)),
            preferred_max_duration_seconds=float(payload.get("preferred_max_duration_seconds", existing.preferred_max_duration_seconds if existing and existing.preferred_max_duration_seconds is not None else payload.get("max_duration_seconds", 45))),
            hard_max_duration_seconds=float(payload.get("hard_max_duration_seconds", existing.hard_max_duration_seconds if existing and existing.hard_max_duration_seconds is not None else payload.get("max_duration_seconds", 45))),
            analysis_mode=str(payload.get("analysis_mode", existing.analysis_mode if existing else "balanced")),
            desired_output_count=int(payload.get("desired_output_count", existing.desired_output_count if existing else 3)),
            pacing=str(payload.get("pacing", existing.pacing if existing else "fast")),
            hook_priority=str(payload.get("hook_priority", existing.hook_priority if existing else "strong")),
            story_style=str(payload.get("story_style", existing.story_style if existing else "self-contained")),
            framing=str(payload.get("framing", existing.framing if existing else "automatic")),
            visual_emphasis=str(payload.get("visual_emphasis", existing.visual_emphasis if existing else "restrained")),
            caption_preset=str(payload.get("caption_preset", existing.caption_preset if existing else "word_highlight")),
            enhancement_policy=str(payload.get("enhancement_policy", existing.enhancement_policy if existing else "restrained")),
            music_policy=str(payload.get("music_policy", existing.music_policy if existing else "off")),
            export_defaults=list(payload.get("export_defaults", existing.export_defaults if existing else ["mp4", "srt", "otio"])),
            campaign_profile_id=(str(payload["campaign_profile_id"]) if payload.get("campaign_profile_id") else None),
        )
        try:
            validate_workflow_profile(profile)
        except ValueError as exc:
            raise ProtocolError("invalid_workflow_profile", str(exc)) from exc
        project.workflow_profiles = [item for item in project.workflow_profiles if item.id != profile.id] + [profile]
        library_workflows, library_campaigns = self._read_profile_library()
        library_workflows = [item for item in library_workflows if item.id != profile.id] + [profile]
        self._save_profile_library(library_workflows, library_campaigns)
        save_project(workspace, project)
        return self._state(workspace, project)

    def save_campaign_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        profile_id = str(payload.get("id") or f"campaign_{int(datetime.now(UTC).timestamp() * 1000)}")
        existing = next((item for item in project.campaign_profiles if item.id == profile_id), None)
        profile = CampaignProfile(
            id=profile_id, name=str(payload.get("name", existing.name if existing else "Campaign Delivery")),
            revision=(existing.revision + 1 if existing else 1), creator=str(payload.get("creator", existing.creator if existing else "")),
            target_platform=str(payload.get("target_platform", existing.target_platform if existing else "shorts")),
            min_duration_seconds=float(payload.get("min_duration_seconds", existing.min_duration_seconds if existing else 0)),
            max_duration_seconds=float(payload.get("max_duration_seconds", existing.max_duration_seconds if existing else 60)),
            required_handle=str(payload.get("required_handle", existing.required_handle if existing else "")),
            required_cta=str(payload.get("required_cta", existing.required_cta if existing else "")),
            required_text=list(payload.get("required_text", existing.required_text if existing else [])),
            hashtags=list(payload.get("hashtags", existing.hashtags if existing else [])),
            watermark_required=bool(payload.get("watermark_required", existing.watermark_required if existing else False)),
            forbidden_terms=list(payload.get("forbidden_terms", existing.forbidden_terms if existing else [])),
            content_notes=str(payload.get("content_notes", existing.content_notes if existing else "")),
            target_deliverables=int(payload.get("target_deliverables", existing.target_deliverables if existing else 1)),
            export_naming=str(payload.get("export_naming", existing.export_naming if existing else "{campaign}-{index}-{title}")),
        )
        try:
            validate_campaign_profile(profile)
        except ValueError as exc:
            raise ProtocolError("invalid_campaign_profile", str(exc)) from exc
        project.campaign_profiles = [item for item in project.campaign_profiles if item.id != profile.id] + [profile]
        library_workflows, library_campaigns = self._read_profile_library()
        library_campaigns = [item for item in library_campaigns if item.id != profile.id] + [profile]
        self._save_profile_library(library_workflows, library_campaigns)
        save_project(workspace, project)
        return self._state(workspace, project)

    def create_production_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        library_workflows, library_campaigns = self._read_profile_library()
        profile = next((item for item in [*project.workflow_profiles, *library_workflows] if item.id == str(payload.get("workflow_profile_id", ""))), None)
        if profile is None:
            raise ProtocolError("workflow_profile_not_found", "Choose a workflow profile before starting production.")
        campaign = next((item for item in [*project.campaign_profiles, *library_campaigns] if item.id == (str(payload.get("campaign_profile_id")) if payload.get("campaign_profile_id") else profile.campaign_profile_id)), None)
        if all(item.id != profile.id for item in project.workflow_profiles):
            project.workflow_profiles.append(profile)
        if campaign and all(item.id != campaign.id for item in project.campaign_profiles):
            project.campaign_profiles.append(campaign)
        run = ProductionRun(
            id=f"run_{int(datetime.now(UTC).timestamp() * 1000)}", workflow_profile_id=profile.id,
            workflow_profile_revision=profile.revision, requested_count=int(payload.get("requested_count", profile.desired_output_count)),
            created_at=_now(), campaign_profile_id=campaign.id if campaign else None,
            campaign_profile_revision=campaign.revision if campaign else None,
            workflow_profile_snapshot=asdict(profile), campaign_profile_snapshot=asdict(campaign) if campaign else {}, status="queued",
        )
        project.production_runs.append(run)
        save_project(workspace, project)
        return self._state(workspace, project)

    def update_production_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        run = next((item for item in project.production_runs if item.id == str(payload.get("production_run_id", ""))), None)
        if run is None:
            raise ProtocolError("production_run_not_found", "We couldn't find that production run.")
        edit_ids = [str(item) for item in payload.get("edit_ids", [])]
        operation = str(payload.get("operation", ""))
        if operation == "approve":
            run.accepted_edit_ids = sorted(set(run.accepted_edit_ids) | set(edit_ids))
            run.rejected_edit_ids = [item for item in run.rejected_edit_ids if item not in edit_ids]
        elif operation == "reject":
            run.rejected_edit_ids = sorted(set(run.rejected_edit_ids) | set(edit_ids))
            run.accepted_edit_ids = [item for item in run.accepted_edit_ids if item not in edit_ids]
        elif operation == "select":
            run.selected_edit_ids = edit_ids
        else:
            raise ProtocolError("unsupported_production_operation", "That production-run change isn't supported.")
        campaign = next((item for item in project.campaign_profiles if item.id == run.campaign_profile_id), None)
        if campaign:
            for edit_id in run.accepted_edit_ids:
                sequence = next((item for item in project.edit_sequences if item.id == edit_id), None)
                enhancement = next((item for item in project.enhancement_plans if item.sequence_id == edit_id), None)
                if sequence:
                    run.campaign_validations[edit_id] = validate_campaign_edit(sequence, campaign, enhancement)
        save_project(workspace, project)
        return self._state(workspace, project)

    def bulk_production_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        run = next((item for item in project.production_runs if item.id == str(payload.get("production_run_id", ""))), None)
        if run is None:
            raise ProtocolError("production_run_not_found", "We couldn't find that production run.")
        edit_ids = [str(item) for item in payload.get("edit_ids", run.selected_edit_ids)]
        operation = str(payload.get("operation", ""))
        if operation in {"approve", "reject"}:
            return self.update_production_run({**payload, "edit_ids": edit_ids})
        if operation == "caption_preset":
            preset = str(payload.get("caption_preset", "word_highlight"))
            if preset not in CAPTION_PRESETS:
                raise ProtocolError("invalid_caption_preset", "Choose a supported caption preset.")
            for visual in project.visual_edit_plans:
                if visual.sequence_id in edit_ids:
                    visual.caption_preset = preset
                    visual.revision += 1
        elif operation == "enhancement_policy":
            policy = str(payload.get("enhancement_policy", "restrained"))
            for enhancement in project.enhancement_plans:
                if enhancement.sequence_id in edit_ids and policy == "off":
                    for item in [*enhancement.broll_items, *enhancement.graphic_items, *enhancement.sound_cues]:
                        item.enabled = False
                    if enhancement.music_track:
                        enhancement.music_track.enabled = False
                    enhancement.revision += 1
        else:
            raise ProtocolError("unsupported_bulk_operation", "That bulk change isn't supported.")
        for sequence in project.edit_sequences:
            if sequence.id in edit_ids:
                sequence.preview_path = None
                sequence.render_path = None
        save_project(workspace, project)
        return self._state(workspace, project)

    def _read_recents(self) -> list[dict[str, Any]]:
        if not self.recents_path.exists():
            return []
        return json.loads(self.recents_path.read_text(encoding="utf-8")).get("projects", [])

    def _touch_recent(self, project_file: Path, name: str) -> None:
        path = str(project_file.resolve())
        projects = [item for item in self._read_recents() if item.get("path") != path]
        projects.insert(0, {"path": path, "name": name, "last_opened": _now()})
        atomic_write_json(self.recents_path, {"projects": projects[:12]})

    def list_recent_projects(self, _payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [{**item, "available": self._project_file(item["path"]).exists()} for item in self._read_recents()]

    def remove_recent_project(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        target = str(Path(str(payload.get("path", ""))).resolve())
        projects = [item for item in self._read_recents() if str(Path(item["path"]).resolve()) != target]
        atomic_write_json(self.recents_path, {"projects": projects})
        return [{**item, "available": self._project_file(item["path"]).exists()} for item in projects]

    def _default_settings(self) -> dict[str, Any]:
        home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.cwd())
        documents = home / "Documents"
        return {
            "default_project_directory": str(documents / "AutoClip Projects"),
            "default_export_directory": "",
            "theme": "dark",
            "whisper_model": "base",
            "processing_device": "cpu",
            "ai_provider": "Gemini",
            "gemini_model": "gemini-3.6-flash",
        }

    def get_settings(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = self._default_settings()
        if self.settings_path.exists():
            settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
        if settings["gemini_model"] == "gemini-2.5-flash":
            settings["gemini_model"] = "gemini-3.6-flash"
        settings["gemini_configured"] = bool(self._gemini_api_key())
        return settings

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"default_project_directory", "default_export_directory", "theme", "whisper_model", "processing_device", "gemini_model"}
        settings = self.get_settings()
        settings.pop("gemini_configured", None)
        for key in allowed:
            if key in payload:
                settings[key] = payload[key]
        if settings["whisper_model"] not in {"tiny", "base", "small", "medium"}:
            raise ProtocolError("invalid_setting", "Choose a supported Whisper model.")
        settings["processing_device"] = "cpu"
        settings["ai_provider"] = "Gemini"
        atomic_write_json(self.settings_path, settings)
        return self.get_settings()

    def set_gemini_api_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = str(payload.get("api_key", ""))
        try:
            self.credentials.set_gemini_key(value)
        except ValueError as exc:
            raise ProtocolError("invalid_credential", str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise ProtocolError("credential_storage_failed", "AutoClip could not store the API key securely for this Windows user.", details=sanitize_diagnostic_text(str(exc))) from exc
        return {"configured": True}

    def clear_gemini_api_key(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.credentials.clear_gemini_key()
        os.environ.pop("GEMINI_API_KEY", None)
        return {"configured": False}

    def clear_cache(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_value = str(payload.get("project_path", "")).strip()
        removed = 0
        targets = [self.paths.cache]
        if project_value:
            workspace, _project = self._load_project(project_value)
            targets.append(workspace.root / "cache")
        for target in targets:
            resolved = target.resolve()
            allowed_roots = [self.state_root.resolve()]
            if project_value:
                allowed_roots.append(self._project_file(project_value).parent.resolve())
            if not any(resolved == root / "cache" for root in allowed_roots):
                raise ProtocolError("unsafe_cache_path", "AutoClip refused to clean an unexpected path.", recoverable=False)
            if resolved.exists():
                removed += sum(path.stat().st_size for path in resolved.rglob("*") if path.is_file())
                shutil.rmtree(resolved)
            resolved.mkdir(parents=True, exist_ok=True)
        return {"removed_bytes": removed}

    def export_diagnostics(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.paths.diagnostics.mkdir(parents=True, exist_ok=True)
        output = self.paths.diagnostics / f"AutoClip-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        doctor = self.doctor()
        doctor["gemini"] = {"ready": doctor["gemini"]["ready"], "summary": "Configured" if doctor["gemini"]["ready"] else "Not configured"}
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("system.json", json.dumps(doctor, ensure_ascii=False, indent=2))
            for log_path in self.paths.logs.glob("*.log"):
                content = sanitize_diagnostic_text(log_path.read_text(encoding="utf-8", errors="replace"))
                archive.writestr(f"logs/{log_path.name}", content[-500_000:])
        return {"path": str(output)}

    def start_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_type = str(payload.get("type", ""))
        project_path = str(payload.get("project_path", "")) or None
        if job_type == "doctor":
            runner = lambda _id, event, update: self._doctor_job(event, update)
        elif job_type == "transcribe":
            if not project_path:
                raise ProtocolError("project_path_required", "Open a project before starting transcription.")
            runner = lambda _id, event, update: self._transcribe_job(project_path, event, update)
        elif job_type in {"proxy", "analyze", "smart_edit", "visual_analyze", "enhancement_plan", "reframe", "preview", "render", "smart_preview", "smart_render", "export", "production_generate", "batch_preview", "batch_render", "production_package"}:
            if not project_path:
                raise ProtocolError("project_path_required", "Open a project before starting this job.")
            if job_type == "proxy":
                runner = lambda _id, event, update: self._proxy_job(project_path, event, update)
            elif job_type == "analyze":
                runner = lambda _id, event, update: self._analyze_job(project_path, event, update)
            elif job_type == "smart_edit":
                runner = lambda _id, event, update: self._smart_edit_job(project_path, event, update)
            elif job_type == "visual_analyze":
                runner = lambda _id, event, update: self._visual_analyze_job(project_path, str(payload.get("edit_sequence_id", "")), event, update)
            elif job_type == "enhancement_plan":
                runner = lambda _id, event, update: self._enhancement_plan_job(project_path, str(payload.get("edit_sequence_id", "")), event, update)
            elif job_type == "reframe":
                runner = lambda _id, event, update: self._reframe_job(project_path, str(payload.get("clip_id", "")), event, update)
            elif job_type == "preview":
                runner = lambda _id, event, update: self._render_job(project_path, str(payload.get("clip_id", "")), True, event, update)
            elif job_type == "render":
                runner = lambda _id, event, update: self._render_job(project_path, str(payload.get("clip_id", "")), False, event, update)
            elif job_type in {"smart_preview", "smart_render"}:
                runner = lambda _id, event, update: self._render_edit_sequence_job(project_path, str(payload.get("edit_sequence_id", "")), job_type == "smart_preview", event, update)
            elif job_type == "production_generate":
                runner = lambda _id, event, update: self._production_generate_job(project_path, str(payload.get("production_run_id", "")), event, update)
            elif job_type in {"batch_preview", "batch_render"}:
                runner = lambda _id, event, update: self._batch_render_job(project_path, str(payload.get("production_run_id", "")), job_type == "batch_preview", event, update)
            elif job_type == "production_package":
                runner = lambda _id, event, update: self._production_package_job(project_path, str(payload.get("production_run_id", "")), event, update)
            else:
                runner = lambda _id, event, update: self._export_job(project_path, str(payload.get("format", "")), str(payload.get("clip_id", "")) or None, event, update, str(payload.get("edit_sequence_id", "")) or None)
        else:
            raise ProtocolError("unsupported_job", "This processing job is not available yet.")
        if project_path:
            self._check_job_disk_space(job_type, project_path, payload)
        return asdict(self.jobs.start(job_type, runner, project_path=project_path, cancellable=True))

    def _check_job_disk_space(self, job_type: str, project_path: str, payload: dict[str, Any]) -> None:
        workspace, project = self._load_project(project_path)
        source_size = int(project.sources[0].fingerprint.get("size", 0)) if project.sources else 0
        if job_type == "transcribe":
            settings = self.get_settings()
            transcriber = self._transcriber(settings["whisper_model"])
            if not transcriber.model_is_cached:
                require_free_space(self.paths.models, MODEL_APPROX_BYTES[settings["whisper_model"]] + 256 * 1024 * 1024, "Transcription model download")
            audio_estimate = 256 * 1024 * 1024
            if project.sources:
                audio_estimate = max(audio_estimate, int(float(project.sources[0].duration.seconds) * 32_000 * 1.25))
            require_free_space(workspace.root, audio_estimate, "Transcription preparation")
        elif job_type in {"proxy", "preview", "smart_preview"}:
            require_free_space(workspace.root, max(512 * 1024 * 1024, source_size // 3), "Preview generation")
        elif job_type in {"render", "smart_render"}:
            require_free_space(workspace.root / "exports", max(1024 * 1024 * 1024, source_size // 2), "Final render")
        elif job_type == "batch_render":
            run_id = str(payload.get("production_run_id", ""))
            run = next((item for item in project.production_runs if item.id == run_id), None)
            count = len(run.selected_edit_ids or run.accepted_edit_ids) if run else 1
            require_free_space(workspace.root / "exports", max(1024 * 1024 * 1024, count * 512 * 1024 * 1024), "Batch render")
        elif job_type == "production_package":
            require_free_space(workspace.root / "exports", max(512 * 1024 * 1024, source_size // 4), "Production package")

    def _doctor_job(self, event: threading.Event, update: Any) -> None:
        update(0.25, "Checking processing capabilities", True)
        if event.wait(0.05):
            return
        self.doctor()
        update(0.9, "Finishing diagnostics", True)

    def _transcribe_job(self, project_path: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        update(0.03, "Inspecting source", True)
        if not project.sources:
            raise RuntimeError("Select a source video before transcription.")
        if not Path(project.sources[0].reference).is_file():
            raise RuntimeError("Source media unavailable. Locate the file before transcription.")
        update(0.1, "Preparing speech audio", True)
        if event.is_set():
            return
        settings = self.get_settings()
        pipeline = CorePipeline(
            workspace, FFmpegService(),
            self._transcriber(settings["whisper_model"]),
            self._gemini_provider(settings),
        )
        try:
            transcript = pipeline.transcribe_source(
                project.sources[0],
                progress=lambda value, stage: update(0.1 + 0.8 * value, stage, True),
                cancel_event=event,
            )
        except TranscriptionCancelled:
            return
        if event.is_set():
            return
        update(0.92, "Saving transcript", True)
        project.transcripts = [item for item in project.transcripts if item.revision_id != transcript.revision_id] + [transcript]
        save_project(workspace, project)

    def _analyze_job(self, project_path: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        if not project.sources or not project.transcripts:
            raise RuntimeError("Transcribe the source before analyzing clips.")
        settings = self.get_settings()
        provider = self._gemini_provider(settings)
        if not provider.available:
            raise RuntimeError("Gemini isn't configured yet. Add GEMINI_API_KEY and try again.")
        transcript = project.transcripts[-1]
        update(0.1, "Analyzing transcript", True)
        if event.is_set():
            return
        pipeline = CorePipeline(workspace, FFmpegService(), self._transcriber(settings["whisper_model"]), provider, prompt_version="v2.1-editorial-schema-v2")
        profile = project.workflow_profiles[-1] if project.workflow_profiles else WorkflowProfile(
            "v2_default", "V2 Balanced", min_duration_seconds=12, max_duration_seconds=28,
            target_duration_seconds=20, preferred_max_duration_seconds=25, hard_max_duration_seconds=28,
            desired_output_count=5, analysis_mode="balanced",
        )
        update(0.24, "Preparing candidate windows", False)
        candidates, windows, metrics = pipeline.analyze_source_v2(
            project.sources[0], transcript, profile=profile,
            progress=lambda count, stage: update(min(.82, .42 + count * .05), stage, False),
        )
        update(0.86, "Final comparative ranking", False)
        project.candidates = candidates
        project.candidate_windows = windows
        project.analysis_runs = [*project.analysis_runs[-9:], metrics]
        project.analysis_revision = workspace.cache_key("highlight_analysis_revision", {"transcript_revision": transcript.revision_id, "prompt": pipeline.prompt_version, "provider": type(provider).__name__, "model": provider.model, "profile_revision": profile.revision, "duration_contract": asdict(profile.duration_contract)})
        save_project(workspace, project)
        update(0.96, f"{len(candidates)} clips found" if candidates else "No suitable clips were found", False)

    def _smart_edit_job(self, project_path: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        if not project.sources or not project.transcripts:
            raise RuntimeError("Transcribe the source before creating Smart Edits.")
        settings = self.get_settings()
        provider = self._gemini_provider(settings)
        has_cached_highlights = bool(project.candidates and project.analysis_revision)
        if not provider.available and not has_cached_highlights:
            raise RuntimeError("Gemini isn't configured yet. Add GEMINI_API_KEY and try again.")
        pipeline = CorePipeline(workspace, FFmpegService(), self._transcriber(settings["whisper_model"]), provider, prompt_version="phase2a1-v1")
        transcript, source = project.transcripts[-1], project.sources[0]
        if has_cached_highlights:
            update(0.08, "Reusing cached full-source analysis", True)
            moments, stories = construct_candidate_stories(project.candidates, transcript, source)
        else:
            update(0.08, "Discovering moments across the transcript", True)
            moments = pipeline.discover_moments(source, transcript)
            stories = []
        if event.is_set():
            return
        update(0.5, f"Consolidating {len(moments)} moments", True)
        if not stories and not has_cached_highlights:
            stories = pipeline.construct_story_concepts(moments, transcript)
        if event.is_set():
            return
        update(0.72, "Planning source-grounded edit sequences", False)
        moment_map = {item.id: item for item in moments}
        sequences = [plan_reviewed_sequence(story, moment_map, transcript, source) for story in stories]
        profile = project.workflow_profiles[-1] if project.workflow_profiles else WorkflowProfile(
            "v2_default", "V2 Balanced", min_duration_seconds=12, max_duration_seconds=28,
            target_duration_seconds=20, preferred_max_duration_seconds=25, hard_max_duration_seconds=28,
            desired_output_count=5, analysis_mode="balanced",
        )
        contracted: list[EditSequence] = []
        candidate_by_story = {story.id: candidate for story, candidate in zip(stories, project.candidates)}
        for sequence in sequences:
            candidate = candidate_by_story.get(sequence.story_concept_id)
            if candidate:
                sequence.editorial_quality = candidate.editorial_quality or quality_from_scores(candidate.scores)
            if sequence.duration_seconds > profile.duration_contract.hard_maximum:
                shortened = create_shorter_sequence_variant(sequence, profile.duration_contract)
                if shortened is None:
                    sequence.status = "rejected"
                else:
                    sequence = shortened
                    story = next((item for item in stories if item.id == sequence.story_concept_id), None)
                    if story:
                        sequence.integrity = validate_integrity(sequence, transcript, moment_map, allow_truthful_hook_reorder=bool(story.coherence.get("allow_truthful_hook_reorder")))
                        sequence.editorial_review = review_editorial_quality(story, sequence, moment_map)
                        sequence.status = "ready" if sequence.integrity.status == "passed" and sequence.editorial_review.accepted else "rejected"
            contracted.append(sequence)
        sequences = contracted
        project.moments, project.story_concepts, project.edit_sequences = moments, stories, sequences
        project.visual_edit_plans = []
        project.enhancement_plans = []
        project.production_runs = []
        project.analysis_revision = workspace.cache_key("smart_edit_revision", {"transcript_revision": transcript.revision_id, "moments": [item.id for item in moments], "stories": [item.id for item in stories], "prompt": "phase2a1-v1", "model": provider.model})
        save_project(workspace, project)
        ready_count = sum(item.status == "ready" for item in sequences)
        update(0.96, f"{ready_count} Smart Edits ready" if ready_count else "AutoClip couldn't build a coherent Smart Edit from these moments", False)

    def _production_generate_job(self, project_path: str, run_id: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        run = next((item for item in project.production_runs if item.id == run_id), None)
        if run is None:
            raise RuntimeError("The production run is unavailable.")
        profile = WorkflowProfile.from_dict(run.workflow_profile_snapshot)
        update(.08, "Reusing cached moments and accepted Smart Edits", True)
        selected, suppressed = select_diverse_edits(project.edit_sequences, {item.id: item for item in project.story_concepts}, profile)
        run.generated_edit_ids = [item.id for item in selected]
        run.selected_edit_ids = list(run.generated_edit_ids)
        run.edit_states = {item.id: {"state": "review", "stage": "Editorial gates passed", "error": None} for item in selected}
        run.warnings = ([f"{len(suppressed)} near-duplicate edit(s) were suppressed."] if suppressed else [])
        if len(selected) < run.requested_count:
            run.warnings.append(f"Requested {run.requested_count}; produced {len(selected)} qualified distinct edit(s).")
        run.status = "review"
        campaign = CampaignProfile(**run.campaign_profile_snapshot) if run.campaign_profile_snapshot else None
        if campaign:
            for sequence in selected:
                enhancement = next((item for item in project.enhancement_plans if item.sequence_id == sequence.id), None)
                run.campaign_validations[sequence.id] = validate_campaign_edit(sequence, campaign, enhancement)
        save_project(workspace, project)
        if profile.generation_mode == "prepare_previews":
            for index, sequence in enumerate(selected):
                if event.is_set():
                    run.status = "cancelled"
                    save_project(workspace, project)
                    return
                update(.15 + .75 * index / max(1, len(selected)), f"Preparing preview {index + 1} of {len(selected)}", True)
                try:
                    self._visual_analyze_job(project_path, sequence.id, event, lambda *_args: None)
                    self._enhancement_plan_job(project_path, sequence.id, event, lambda *_args: None)
                    workspace, project = self._load_project(project_path)
                    run = next(item for item in project.production_runs if item.id == run_id)
                    visual = next((item for item in project.visual_edit_plans if item.sequence_id == sequence.id), None)
                    enhancement = next((item for item in project.enhancement_plans if item.sequence_id == sequence.id), None)
                    apply_profile_policy(profile, visual, enhancement)
                    save_project(workspace, project)
                    self._render_edit_sequence_job(project_path, sequence.id, True, event, lambda *_args: None)
                    workspace, project = self._load_project(project_path)
                    run = next(item for item in project.production_runs if item.id == run_id)
                    run.edit_states[sequence.id] = {"state": "preview_ready", "stage": "Preview ready", "error": None}
                except Exception as exc:
                    safe_error = sanitize_diagnostic_text(str(exc))
                    run.edit_states[sequence.id] = {"state": "failed", "stage": "Preview failed", "error": safe_error}
                    run.errors.append(f"{sequence.id}: {safe_error}")
                save_project(workspace, project)
        run.status = "review"
        save_project(workspace, project)
        update(.96, f"{len(selected)} distinct Smart Edit(s) ready for review", False)

    def _batch_render_job(self, project_path: str, run_id: str, preview: bool, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        run = next((item for item in project.production_runs if item.id == run_id), None)
        if run is None:
            raise RuntimeError("The production run is unavailable.")
        edit_ids = run.selected_edit_ids or run.accepted_edit_ids or run.generated_edit_ids
        run.status = "rendering"
        save_project(workspace, project)
        for index, edit_id in enumerate(edit_ids):
            if event.is_set():
                workspace, project = self._load_project(project_path)
                run = next(item for item in project.production_runs if item.id == run_id)
                run.status = "cancelled"
                save_project(workspace, project)
                return
            update(.05 + .88 * index / max(1, len(edit_ids)), f"{'Previewing' if preview else 'Rendering'} {index + 1} of {len(edit_ids)}", True)
            workspace, project = self._load_project(project_path)
            run = next(item for item in project.production_runs if item.id == run_id)
            run.edit_states[edit_id] = {"state": "rendering", "stage": "Rendering", "error": None}
            save_project(workspace, project)
            try:
                self._render_edit_sequence_job(project_path, edit_id, preview, event, lambda *_args: None)
                workspace, project = self._load_project(project_path)
                run = next(item for item in project.production_runs if item.id == run_id)
                run.edit_states[edit_id] = {"state": "preview_ready" if preview else "rendered", "stage": "Completed", "error": None}
            except Exception as exc:
                safe_error = sanitize_diagnostic_text(str(exc))
                run.edit_states[edit_id] = {"state": "failed", "stage": "Failed", "error": safe_error}
                run.errors.append(f"{edit_id}: {safe_error}")
            save_project(workspace, project)
        run.status = "completed" if all(item.get("state") != "rendering" for item in run.edit_states.values()) else "review"
        save_project(workspace, project)
        update(.96, "Batch render complete", False)

    def _production_package_job(self, project_path: str, run_id: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        run = next((item for item in project.production_runs if item.id == run_id), None)
        if run is None:
            raise RuntimeError("The production run is unavailable.")
        profile = WorkflowProfile.from_dict(run.workflow_profile_snapshot)
        campaign = CampaignProfile(**run.campaign_profile_snapshot) if run.campaign_profile_snapshot else None
        package_stem = campaign.name if campaign else f"{project.name}-{run.id}"
        final_root = collision_safe_path(workspace.root / "exports", package_stem, "")
        package_root = final_root.with_name(final_root.name + ".partial")
        if package_root.exists():
            shutil.rmtree(package_root)
        videos, captions_dir, timelines_dir, metadata = (package_root / name for name in ("videos", "captions", "timelines", "metadata"))
        for directory in (videos, captions_dir, timelines_dir, metadata):
            directory.mkdir(parents=True, exist_ok=True)
        edit_ids = run.accepted_edit_ids or run.selected_edit_ids or run.generated_edit_ids
        deliverables: list[dict[str, object]] = []
        for index, edit_id in enumerate(edit_ids, 1):
            if event.is_set():
                shutil.rmtree(package_root, ignore_errors=True)
                run.status = "cancelled"
                save_project(workspace, project)
                return
            sequence = next((item for item in project.edit_sequences if item.id == edit_id), None)
            if sequence is None:
                continue
            naming = campaign.export_naming if campaign else "{project}-{index}-{title}"
            stem = render_filename(naming, project=project.name, campaign=campaign.name if campaign else "", creator=campaign.creator if campaign else "", index=index, title=sequence.title)
            entry: dict[str, object] = {"edit_id": edit_id, "title": sequence.title, "duration_seconds": sequence.duration_seconds,
                "source_ranges": [{"in": float(item.source_in.seconds), "out": float(item.source_out.seconds)} for item in sequence.segments], "warnings": []}
            if sequence.render_path and Path(sequence.render_path).is_file():
                video_path = collision_safe_path(videos, stem, ".mp4")
                shutil.copy2(sequence.render_path, video_path)
                entry["video"] = str(video_path.relative_to(package_root))
            else:
                entry["warnings"].append("Final render unavailable.")
            visual = next((item for item in project.visual_edit_plans if item.sequence_id == edit_id), None)
            if project.transcripts:
                track = build_edit_sequence_caption_track(sequence, project.transcripts[-1], positions={item.edit_segment_id: item.position for item in visual.caption_layout} if visual else None)
                srt_path = collision_safe_path(captions_dir, stem, ".srt")
                lines: list[str] = []
                for cue_index, cue in enumerate(track.cues, 1):
                    def clock(value: float) -> str:
                        millis = round(value * 1000); hours, rem = divmod(millis, 3600000); minutes, rem = divmod(rem, 60000); seconds, ms = divmod(rem, 1000)
                        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"
                    lines.extend((str(cue_index), f"{clock(float(cue.start.seconds))} --> {clock(float(cue.end.seconds))}", cue.editable_text_override or cue.text, ""))
                srt_path.write_text("\n".join(lines), encoding="utf-8")
                entry["captions"] = str(srt_path.relative_to(package_root))
            timeline = self._timeline_for_edit_sequence(project, edit_id)
            otio_path, xml_path = collision_safe_path(timelines_dir, stem, ".otio"), collision_safe_path(timelines_dir, stem, ".xml")
            entry["warnings"].extend(export_otio(timeline, {item.id: item for item in project.sources}, otio_path))
            entry["warnings"].extend(export_premiere_xml(timeline, {item.id: item for item in project.sources}, xml_path))
            entry["otio"], entry["premiere_xml"] = str(otio_path.relative_to(package_root)), str(xml_path.relative_to(package_root))
            validation = run.campaign_validations.get(edit_id)
            entry["campaign_validation"] = asdict(validation) if validation else None
            deliverables.append(entry)
        write_manifest(metadata / "manifest.json", run, project.name, deliverables)
        os.replace(package_root, final_root)
        run.output_package_path = str(final_root)
        run.status = "completed"
        save_project(workspace, project)
        update(.96, f"Output package created with {len(deliverables)} deliverable(s)", False)

    def _proxy_job(self, project_path: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        if not project.sources:
            raise RuntimeError("Source media unavailable.")
        source = project.sources[0]
        output = workspace.root / "cache" / "proxies" / f"{source.id}-{source.fingerprint.get('partial_sha256', 'source')}.mp4"
        if output.exists():
            update(0.95, "Using cached preview proxy", True)
            return
        update(0.15, "Preparing preview proxy", True)
        if event.is_set():
            return
        update(0.3, "Transcoding a non-destructive preview proxy", False)
        FFmpegService().create_lazy_proxy(Path(source.reference), output)

    def _job_clip(self, project: AutoClipProject, clip_id: str) -> ProjectClip:
        clip = next((item for item in project.clips if item.id == clip_id), None)
        if clip is None:
            raise RuntimeError("The selected clip no longer exists.")
        return clip

    def _reframe_job(self, project_path: str, clip_id: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        if not project.sources:
            raise RuntimeError("Source media unavailable.")
        clip = self._job_clip(project, clip_id)
        existing = next((track for track in project.reframe_tracks if track.id == clip.reframe_track_id), None)
        if existing:
            update(0.95, "Using cached framing analysis", True)
            return
        update(0.1, "Sampling source frames", True)
        if event.is_set():
            return
        update(0.25, "Detecting and tracking the subject", False)
        track_id = f"reframe_{clip.id}_r{clip.revision}"
        override = clip.manual_crop if clip.framing_mode == "manual" else None
        track = analyze_video_clip(Path(project.sources[0].reference), clip.source_in, clip.source_out, track_id, manual_override=override)
        update(0.85, "Saving canonical framing track", False)
        project.reframe_tracks = [item for item in project.reframe_tracks if item.id != track.id]
        project.reframe_tracks.append(track)
        clip.reframe_track_id = track.id
        caption_track = next((item for item in project.caption_tracks if item.id == clip.caption_track_id), None)
        detected_y = [point.subject_y for point in track.points if point.detected]
        if caption_track and detected_y:
            safe_position = "upper" if sum(detected_y) / len(detected_y) > .58 else "lower"
            for cue in caption_track.cues:
                cue.position = safe_position
        clip.render_path = None
        save_project(workspace, project)

    def _crop_for_clip(self, project: AutoClipProject, clip: ProjectClip) -> tuple[int, int, int, int]:
        source = project.sources[0]
        track = next((item for item in project.reframe_tracks if item.id == clip.reframe_track_id), None)
        if track and track.points:
            point = track.points[len(track.points) // 2]
            center_x, center_y, scale = point.crop_x, point.crop_y, point.scale
        else:
            center_x, center_y, scale = clip.manual_crop.crop_x, clip.manual_crop.crop_y, clip.manual_crop.scale
        x, y, width, height = crop_geometry(source.width, source.height, center_x, center_y)
        if scale > 1:
            scaled_width, scaled_height = max(2, round(width / scale)), max(2, round(height / scale))
            x = max(0, min(source.width - scaled_width, x + (width - scaled_width) // 2))
            y = max(0, min(source.height - scaled_height, y + (height - scaled_height) // 2))
            width, height = scaled_width, scaled_height
        return x, y, width, height

    def _available_output(self, directory: Path, stem: str, suffix: str) -> Path:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "autoclip"
        candidate = directory / f"{cleaned}{suffix}"
        index = 2
        while candidate.exists():
            candidate = directory / f"{cleaned}-{index}{suffix}"
            index += 1
        return candidate

    def _record_output(self, project: AutoClipProject, kind: str, path: Path, clip_id: str | None = None, warnings: list[str] | None = None, edit_sequence_id: str | None = None) -> None:
        project.outputs.append(OutputArtifact(
            id=f"output_{len(project.outputs) + 1}", kind=kind, path=str(path), clip_id=clip_id, edit_sequence_id=edit_sequence_id, warnings=warnings or [],
        ))

    def _render_job(self, project_path: str, clip_id: str, preview: bool, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        if not project.sources:
            raise RuntimeError("Source media unavailable.")
        clip = self._job_clip(project, clip_id)
        if clip.framing_mode == "auto" and not clip.reframe_track_id:
            raise RuntimeError("Analyze framing before rendering this clip.")
        update(0.1, "Preparing vertical composition", True)
        if event.is_set():
            return
        captions_path: Path | None = None
        track = next((item for item in project.caption_tracks if item.id == clip.caption_track_id), None)
        if clip.captions_enabled and track:
            captions_path = workspace.root / "cache" / "previews" / f"{clip.id}-r{clip.revision}.ass"
            export_ass(track, clip, captions_path)
        output_dir = workspace.root / ("cache/previews" if preview else "exports")
        output = output_dir / f"{clip.id}-r{clip.revision}.mp4" if preview else self._available_output(output_dir, f"{clip.title}-vertical", ".mp4")
        if preview and output.exists():
            update(0.95, "Using cached vertical preview", True)
            return
        x, y, width, height = self._crop_for_clip(project, clip)
        update(0.25, "Rendering vertical preview" if preview else "Rendering finished MP4", False)
        FFmpegService().render_vertical_clip(
            Path(project.sources[0].reference), output, clip.source_in, clip.source_out,
            crop_x=x, crop_y=y, crop_width=width, crop_height=height,
            width=360 if preview else 1080, height=640 if preview else 1920, captions=captions_path,
        )
        update(0.9, "Saving output reference", False)
        if not preview:
            clip.render_path = str(output)
            self._record_output(project, "mp4", output, clip.id)
            save_project(workspace, project)

    def _render_edit_sequence_job(self, project_path: str, sequence_id: str, preview: bool, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        sequence = next((item for item in project.edit_sequences if item.id == sequence_id), None)
        if sequence is None or not project.sources or not project.transcripts:
            raise RuntimeError("The selected Smart Edit is unavailable.")
        if len(sequence.segments) < 2 or sequence.status != "ready" or sequence.integrity.status != "passed" or not sequence.editorial_review or not sequence.editorial_review.accepted:
            raise RuntimeError("Only a coherent Smart Edit that passed editorial review can be rendered.")
        plan = next((item for item in project.visual_edit_plans if item.sequence_id == sequence.id and item.sequence_revision == sequence.revision and item.detector_version == DETECTOR_VERSION and item.detector_config == DETECTOR_CONFIG), None)
        if plan is None:
            update(0.08, "Analyzing visual composition", True)
            observations, warnings = analyze_visual_observations(Path(project.sources[0].reference), sequence)
            plan = build_visual_edit_plan(sequence, observations)
            plan.warnings.extend(warnings)
            project.visual_edit_plans = [item for item in project.visual_edit_plans if item.sequence_id != sequence.id] + [plan]
        enhancement = next((item for item in project.enhancement_plans if item.sequence_id == sequence.id and item.sequence_revision == sequence.revision and item.visual_plan_revision == plan.revision), None)
        if enhancement is None:
            enhancement = validate_enhancement_plan(plan_enhancements(sequence, plan), sequence)
            project.enhancement_plans = [item for item in project.enhancement_plans if item.sequence_id != sequence.id] + [enhancement]
        if enhancement.status not in {"ready", "review"}:
            raise RuntimeError("Resolve or disable missing/unsafe enhancements before rendering.")
        update(0.15, "Mapping word-timed captions", True)
        positions = {item.edit_segment_id: item.position for item in plan.caption_layout}
        caption_track = build_edit_sequence_caption_track(sequence, project.transcripts[-1], positions=positions)
        captions = workspace.root / "cache" / "previews" / f"{sequence.id}-r{sequence.revision}-v{plan.revision}-{plan.caption_preset}.ass"
        export_edit_sequence_ass(caption_track, captions, plan.caption_preset)
        graphics: Path | None = None
        if any(item.enabled for item in enhancement.graphic_items):
            graphics = workspace.root / "cache" / "previews" / f"{sequence.id}-e{enhancement.revision}-graphics.ass"
            graphics.parent.mkdir(parents=True, exist_ok=True)
            # Keep one canonical 1080x1920 ASS coordinate system. FFmpeg scales it
            # for the lower-cost preview, so typography matches the final render.
            graphics.write_text(serialize_graphics_ass(enhancement), encoding="utf-8")
        output_dir = workspace.root / ("cache/previews" if preview else "exports")
        output = output_dir / f"{sequence.id}-r{sequence.revision}-v{plan.revision}-e{enhancement.revision}-{plan.caption_preset}.mp4" if preview else self._available_output(output_dir, f"{sequence.title}-smart-edit-phase2c", ".mp4")
        if preview and output.exists():
            FFmpegService().validate_audible_audio(output)
            sequence.preview_path = str(output)
            update(0.95, "Using cached Smart Edit preview", True)
            save_project(workspace, project)
            return
        update(0.25, "Rendering approved source segments", False)
        FFmpegService().render_edit_sequence(Path(project.sources[0].reference), sequence, output, width=360 if preview else 1080, height=640 if preview else 1920, captions=captions, visual_plan=plan, enhancement_plan=enhancement, graphics=graphics, cancel_event=event)
        if preview:
            sequence.preview_path = str(output)
        else:
            sequence.render_path = str(output)
            self._record_output(project, "smart_edit_mp4", output, edit_sequence_id=sequence.id)
        save_project(workspace, project)
        update(0.95, "Smart Edit preview ready" if preview else "Smart Edit rendered", False)

    def _visual_analyze_job(self, project_path: str, sequence_id: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        sequence = next((item for item in project.edit_sequences if item.id == sequence_id), None)
        if sequence is None or not project.sources:
            raise RuntimeError("The selected Smart Edit is unavailable.")
        existing = next((item for item in project.visual_edit_plans if item.sequence_id == sequence_id and item.sequence_revision == sequence.revision and item.detector_version == DETECTOR_VERSION and item.detector_config == DETECTOR_CONFIG), None)
        if existing:
            update(.95, "Using cached visual analysis", True)
            return
        update(.12, "Sampling approved source ranges", True)
        observations, warnings = analyze_visual_observations(Path(project.sources[0].reference), sequence)
        if event.is_set():
            return
        update(.72, "Planning stable compositions", False)
        plan = build_visual_edit_plan(sequence, observations)
        plan.warnings.extend(warnings)
        project.visual_edit_plans = [item for item in project.visual_edit_plans if item.sequence_id != sequence_id] + [plan]
        sequence.preview_path = None
        sequence.render_path = None
        save_project(workspace, project)
        update(.95, "Visual treatment ready", False)

    def _enhancement_plan_job(self, project_path: str, sequence_id: str, event: threading.Event, update: Any) -> None:
        workspace, project = self._load_project(project_path)
        sequence = next((item for item in project.edit_sequences if item.id == sequence_id), None)
        visual = next((item for item in project.visual_edit_plans if item.sequence_id == sequence_id), None)
        if sequence is None or visual is None or sequence.status != "ready":
            raise RuntimeError("Approve the Smart Edit and analyze visuals before planning enhancements.")
        existing = next((item for item in project.enhancement_plans if item.sequence_id == sequence_id and item.sequence_revision == sequence.revision and item.visual_plan_revision == visual.revision), None)
        if existing:
            update(.95, "Using cached enhancement plan", True)
            return
        update(.25, "Finding restrained enhancement opportunities", True)
        if event.is_set():
            return
        plan = validate_enhancement_plan(plan_enhancements(sequence, visual), sequence)
        project.enhancement_plans = [item for item in project.enhancement_plans if item.sequence_id != sequence_id] + [plan]
        sequence.preview_path = None
        sequence.render_path = None
        save_project(workspace, project)
        update(.95, "Enhancement plan ready", False)

    def _timeline_for_clips(self, project: AutoClipProject) -> Timeline:
        clips = [clip for clip in project.clips if clip.selected] or project.clips
        if not clips or not project.sources:
            raise RuntimeError("Select at least one clip before exporting a timeline.")
        source = project.sources[0]
        return Timeline(
            id=f"timeline_{len(project.timelines) + 1}", canvas_width=1080, canvas_height=1920,
            frame_rate=source.frame_rate,
            clips=[ApprovedClip(
                id=clip.id, source_id=clip.source_id, source_in=clip.source_in, source_out=clip.source_out,
                title=clip.title, reframe_track_id=clip.reframe_track_id, caption_track_id=clip.caption_track_id,
            ) for clip in clips],
        )

    def _timeline_for_edit_sequence(self, project: AutoClipProject, sequence_id: str) -> Timeline:
        sequence = next((item for item in project.edit_sequences if item.id == sequence_id), None)
        if sequence is None:
            raise RuntimeError("The selected Smart Edit is unavailable.")
        return Timeline(
            id=f"timeline_{sequence.id}_r{sequence.revision}", canvas_width=sequence.canvas_width,
            canvas_height=sequence.canvas_height, frame_rate=sequence.frame_rate,
            clips=[ApprovedClip(segment.id, segment.source_id, segment.source_in, segment.source_out, f"{sequence.title} - {segment.purpose}") for segment in sequence.segments],
        )

    def _export_job(self, project_path: str, export_format: str, clip_id: str | None, event: threading.Event, update: Any, edit_sequence_id: str | None = None) -> None:
        if export_format not in {"srt", "ass", "otio", "premiere_xml", "project_json"}:
            raise RuntimeError("Choose a supported export format.")
        workspace, project = self._load_project(project_path)
        update(0.1, "Validating export request", True)
        if event.is_set():
            return
        output_dir = workspace.root / "exports"
        warnings: list[str] = []
        if export_format in {"srt", "ass"}:
            clip = self._job_clip(project, clip_id or "")
            track = next((item for item in project.caption_tracks if item.id == clip.caption_track_id), None)
            if track is None:
                raise RuntimeError("Enable captions on the clip before exporting captions.")
            output = self._available_output(output_dir, clip.title, f".{export_format}")
            (export_srt if export_format == "srt" else export_ass)(track, clip, output)
        elif export_format in {"otio", "premiere_xml"}:
            timeline = self._timeline_for_edit_sequence(project, edit_sequence_id) if edit_sequence_id else self._timeline_for_clips(project)
            output = self._available_output(output_dir, project.name, ".otio" if export_format == "otio" else ".xml")
            sources = {source.id: source for source in project.sources}
            warnings = (export_otio if export_format == "otio" else export_premiere_xml)(timeline, sources, output)
            if edit_sequence_id:
                enhancement = next((item for item in project.enhancement_plans if item.sequence_id == edit_sequence_id), None)
                if enhancement and any(item.enabled for item in [*enhancement.broll_items, *enhancement.graphic_items, *enhancement.sound_cues]):
                    warnings.append("Phase 2C enhancements remain canonical in AutoClip and are not fully recreated by this minimal editable export.")
                if enhancement and enhancement.music_track and enhancement.music_track.enabled:
                    warnings.append("The music bed and dialogue ducking are represented in the rendered reference, not this minimal editable export.")
            project.timelines.append(timeline)
        else:
            output = self._available_output(output_dir, project.name, ".autoclip.json")
            shutil.copy2(workspace.project_file, output)
        update(0.85, "Saving export reference", False)
        self._record_output(project, export_format, output, clip_id, warnings, edit_sequence_id)
        save_project(workspace, project)

    def cancel_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id", ""))
        try:
            return asdict(self.jobs.cancel(job_id))
        except KeyError as exc:
            raise ProtocolError("job_not_found", "We couldn't find that processing job.") from exc
        except RuntimeError as exc:
            raise ProtocolError("job_not_cancellable", str(exc)) from exc

    def get_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return asdict(self.jobs.get(str(payload.get("job_id", ""))))
        except KeyError as exc:
            raise ProtocolError("job_not_found", "We couldn't find that processing job.") from exc

    def list_jobs(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        project_path = str(payload.get("project_path", "")) or None
        return [asdict(job) for job in self.jobs.list(project_path)]
