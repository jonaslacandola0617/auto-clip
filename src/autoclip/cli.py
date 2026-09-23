from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .ai import GeminiProvider
from .exporters import export_otio, export_premiere_xml
from .media import FFmpegService
from .models import AutoClipProject
from .pipeline import CorePipeline
from .storage import Workspace, load_project
from .transcription import FasterWhisperTranscriber


def doctor() -> int:
    ffmpeg = FFmpegService()
    transcriber = FasterWhisperTranscriber()
    status = {
        "python": sys.version.split()[0],
        "ffmpeg": ffmpeg.ffmpeg if ffmpeg.available else None,
        "ffprobe": ffmpeg.ffprobe if ffmpeg.available else None,
        "faster_whisper": transcriber.available,
        "gemini_api_key": bool(os.environ.get("GEMINI_API_KEY")),
        "opencv": _module_available("cv2"),
        "mediapipe": _module_available("mediapipe"),
        "opentimelineio": _module_available("opentimelineio"),
    }
    print(json.dumps(status, indent=2))
    return 0 if ffmpeg.available and transcriber.available else 2


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def export(project_path: Path, timeline_id: str, output_dir: Path) -> int:
    project = AutoClipProject.from_dict(json.loads(project_path.read_text(encoding="utf-8")))
    timeline = next(item for item in project.timelines if item.id == timeline_id)
    sources = {item.id: item for item in project.sources}
    warnings = {
        "otio": export_otio(timeline, sources, output_dir / f"{timeline.id}.otio"),
        "premiere_xml": export_premiere_xml(timeline, sources, output_dir / f"{timeline.id}.xml"),
    }
    print(json.dumps(warnings, indent=2))
    return 0


def run_pipeline(source: Path, workspace_path: Path) -> int:
    workspace = Workspace(workspace_path)
    workspace.ensure()
    pipeline = CorePipeline(workspace, FFmpegService(), FasterWhisperTranscriber(), GeminiProvider())
    media_source, transcript, candidates = pipeline.run_source(source, workspace.root / "exports")
    project = AutoClipProject(id="phase0", name=source.stem, sources=[media_source], transcripts=[transcript], candidates=candidates)
    from .storage import save_project
    save_project(workspace, project)
    print(json.dumps({"project": str(workspace.project_file), "candidate_count": len(candidates)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoclip-phase0")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    export_parser = sub.add_parser("export")
    export_parser.add_argument("project", type=Path)
    export_parser.add_argument("timeline_id")
    export_parser.add_argument("output_dir", type=Path)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("source", type=Path)
    run_parser.add_argument("workspace", type=Path)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "run":
        return run_pipeline(args.source, args.workspace)
    return export(args.project, args.timeline_id, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
