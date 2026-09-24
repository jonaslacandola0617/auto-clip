from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import threading
from fractions import Fraction
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ai import GeminiProvider
from .captions import CAPTION_PRESETS, build_caption_track, export_ass, export_srt
from .desktop_protocol import ProtocolError
from .exporters.otio import export_otio
from .exporters.premiere_xml import export_premiere_xml
from .jobs import JobManager
from .media import FFmpegService, media_source_from_probe
from .models import (
    ApprovedClip, AutoClipProject, ManualCropOverride, MediaSource, OutputArtifact,
    ProjectClip, Timeline,
)
from .pipeline import CorePipeline
from .storage import Workspace, atomic_write_json, load_project, save_project
from .time import MediaTime
from .transcription import FasterWhisperTranscriber, TranscriptionCancelled
from .vision import analyze_video_clip, crop_geometry


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
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.recents_path = self.state_root / "recent-projects.json"
        self.settings_path = self.state_root / "settings.json"
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
        }
        return handlers[command](payload)

    def doctor(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        media = FFmpegService()
        whisper_ready = FasterWhisperTranscriber().available
        gemini_ready = _development_api_key(self.project_root)
        return {
            "ffmpeg": {"ready": media.available, "summary": "Ready" if media.available else "Not available"},
            "ffprobe": {"ready": media.available, "summary": "Ready" if media.available else "Not available"},
            "whisper": {"ready": whisper_ready, "summary": "Ready - CPU processing" if whisper_ready else "Not available"},
            "opencv": {"ready": _module_available("cv2"), "summary": "Ready" if _module_available("cv2") else "Not available"},
            "mediapipe": {"ready": _module_available("mediapipe"), "summary": "Ready" if _module_available("mediapipe") else "Not available"},
            "otio": {"ready": _module_available("opentimelineio"), "summary": "Ready" if _module_available("opentimelineio") else "Not available"},
            "gemini": {"ready": gemini_ready, "summary": "Configured" if gemini_ready else "API key not configured"},
            "acceleration": {"ready": False, "summary": "Not available - CPU processing will be used"},
            "python": sys.version.split()[0],
        }

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
            raise ProtocolError("media_unreadable", "We couldn't read this video. Choose another file or check that the file isn't damaged.", details=str(exc)) from exc

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
        score = scores.hook * .35 + scores.standalone_context * .25 + scores.payoff * .30 + scores.emotion * .10
        return {
            "id": candidate.id, "title": candidate.title,
            "start_seconds": float(candidate.source_start.seconds),
            "end_seconds": float(candidate.source_end.seconds),
            "duration_seconds": float(candidate.source_end.seconds - candidate.source_start.seconds),
            "source": "ai", "score": round(score, 1), "category": candidate.category,
            "reason": candidate.reason,
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

    def _state(self, workspace: Workspace, project: AutoClipProject) -> dict[str, Any]:
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
        }

    def inspect_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._inspect_source(Path(str(payload.get("path", ""))))
        return self._source_summary(source)

    def relink_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        source_id = project.sources[0].id if project.sources else "media_001"
        replacement = self._inspect_source(Path(str(payload.get("source_path", ""))), source_id)
        source_changed = not project.sources or project.sources[0].fingerprint != replacement.fingerprint
        if project.sources:
            project.sources[0] = replacement
        else:
            project.sources.append(replacement)
        if source_changed:
            project.transcripts.clear()
            project.candidates.clear()
            project.clips.clear()
            project.timelines.clear()
            project.reframe_tracks.clear()
            project.caption_tracks.clear()
            project.outputs.clear()
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
        settings["gemini_configured"] = _development_api_key(self.project_root)
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

    def start_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_type = str(payload.get("type", ""))
        project_path = str(payload.get("project_path", "")) or None
        if job_type == "doctor":
            runner = lambda _id, event, update: self._doctor_job(event, update)
        elif job_type == "transcribe":
            if not project_path:
                raise ProtocolError("project_path_required", "Open a project before starting transcription.")
            runner = lambda _id, event, update: self._transcribe_job(project_path, event, update)
        elif job_type in {"proxy", "analyze", "reframe", "preview", "render", "export"}:
            if not project_path:
                raise ProtocolError("project_path_required", "Open a project before starting this job.")
            if job_type == "proxy":
                runner = lambda _id, event, update: self._proxy_job(project_path, event, update)
            elif job_type == "analyze":
                runner = lambda _id, event, update: self._analyze_job(project_path, event, update)
            elif job_type == "reframe":
                runner = lambda _id, event, update: self._reframe_job(project_path, str(payload.get("clip_id", "")), event, update)
            elif job_type == "preview":
                runner = lambda _id, event, update: self._render_job(project_path, str(payload.get("clip_id", "")), True, event, update)
            elif job_type == "render":
                runner = lambda _id, event, update: self._render_job(project_path, str(payload.get("clip_id", "")), False, event, update)
            else:
                runner = lambda _id, event, update: self._export_job(project_path, str(payload.get("format", "")), str(payload.get("clip_id", "")) or None, event, update)
        else:
            raise ProtocolError("unsupported_job", "This processing job is not available yet.")
        return asdict(self.jobs.start(job_type, runner, project_path=project_path, cancellable=True))

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
            FasterWhisperTranscriber(settings["whisper_model"], device="cpu", compute_type="int8"),
            GeminiProvider(model=settings["gemini_model"]),
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
        provider = GeminiProvider(model=settings["gemini_model"])
        if not provider.available:
            raise RuntimeError("Gemini isn't configured yet. Add GEMINI_API_KEY and try again.")
        update(0.1, "Preparing transcript chunks", True)
        if event.is_set():
            return
        pipeline = CorePipeline(workspace, FFmpegService(), FasterWhisperTranscriber(settings["whisper_model"]), provider, prompt_version="phase1b-v1")
        update(0.3, "Analyzing clips with Gemini", False)
        candidates = pipeline.analyze_source(project.sources[0], project.transcripts[-1])
        update(0.85, "Ranking and saving candidates", False)
        project.candidates = candidates
        save_project(workspace, project)

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

    def _record_output(self, project: AutoClipProject, kind: str, path: Path, clip_id: str | None = None, warnings: list[str] | None = None) -> None:
        project.outputs.append(OutputArtifact(
            id=f"output_{len(project.outputs) + 1}", kind=kind, path=str(path), clip_id=clip_id, warnings=warnings or [],
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

    def _export_job(self, project_path: str, export_format: str, clip_id: str | None, event: threading.Event, update: Any) -> None:
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
            timeline = self._timeline_for_clips(project)
            output = self._available_output(output_dir, project.name, ".otio" if export_format == "otio" else ".xml")
            sources = {source.id: source for source in project.sources}
            warnings = (export_otio if export_format == "otio" else export_premiere_xml)(timeline, sources, output)
            project.timelines.append(timeline)
        else:
            output = self._available_output(output_dir, project.name, ".autoclip.json")
            shutil.copy2(workspace.project_file, output)
        update(0.85, "Saving export reference", False)
        self._record_output(project, export_format, output, clip_id, warnings)
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
