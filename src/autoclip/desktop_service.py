from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ai import GeminiProvider
from .desktop_protocol import ProtocolError
from .jobs import JobManager
from .media import FFmpegService, media_source_from_probe
from .models import AutoClipProject, MediaSource
from .pipeline import CorePipeline
from .storage import Workspace, atomic_write_json, load_project, save_project
from .transcription import FasterWhisperTranscriber


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

    def _source_summary(self, source: MediaSource) -> dict[str, Any]:
        path = Path(source.reference)
        video = next((stream for stream in source.streams if stream.kind == "video"), None)
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
        }

    def _state(self, workspace: Workspace, project: AutoClipProject) -> dict[str, Any]:
        source = self._source_summary(project.sources[0]) if project.sources else None
        if source is None:
            status = "needs_source"
        elif not source["available"]:
            status = "source_missing"
        elif not project.transcripts:
            status = "needs_transcript"
        elif not project.candidates:
            status = "needs_analysis"
        else:
            status = "ready"
        return {
            "path": str(workspace.project_file), "directory": str(workspace.root), "id": project.id,
            "name": project.name, "schema_version": project.schema_version, "status": status, "source": source,
            "transcript_count": len(project.transcripts), "candidate_count": len(project.candidates),
            "timeline_count": len(project.timelines),
        }

    def inspect_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._inspect_source(Path(str(payload.get("path", ""))))
        return self._source_summary(source)

    def relink_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace, project = self._load_project(str(payload.get("project_path", "")))
        source_id = project.sources[0].id if project.sources else "media_001"
        replacement = self._inspect_source(Path(str(payload.get("source_path", ""))), source_id)
        if project.sources:
            project.sources[0] = replacement
        else:
            project.sources.append(replacement)
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
            "whisper_model": "small",
            "processing_device": "cpu",
            "ai_provider": "Gemini",
            "gemini_model": "gemini-2.5-flash",
        }

    def get_settings(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = self._default_settings()
        if self.settings_path.exists():
            settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
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
        update(0.25, "Transcribing locally", False)
        transcript = pipeline.transcribe_source(project.sources[0])
        update(0.9, "Saving transcript", False)
        project.transcripts = [item for item in project.transcripts if item.revision_id != transcript.revision_id] + [transcript]
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
