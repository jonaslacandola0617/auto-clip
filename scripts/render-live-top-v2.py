from __future__ import annotations

import argparse
import json
from pathlib import Path
from threading import Event

from autoclip.captions import build_caption_track
from autoclip.desktop_service import DesktopService
from autoclip.models import ProjectClip
from autoclip.storage import Workspace, load_project, save_project
from autoclip.time import MediaTime


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the top sanitized V2.1 live benchmark candidate.")
    parser.add_argument("project", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("output_project", type=Path)
    args = parser.parse_args()
    source_workspace = Workspace(args.project.parent if args.project.is_file() else args.project)
    project = load_project(source_workspace)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    top = report["live"]["candidates"][0]
    source = project.sources[0]
    source_range = top["source_ranges"][0]
    clip = ProjectClip(
        id="v2_live_top", source_id=source.id,
        source_in=MediaTime.from_seconds(str(source_range["start"]), source.time_base, exact=False),
        source_out=MediaTime.from_seconds(str(source_range["end"]), source.time_base, exact=False),
        title=top["title"], source="ai", candidate_id=top["id"],
        framing_mode="fixed", captions_enabled=True, caption_preset="word_highlight",
    )
    track = build_caption_track(clip, project.transcripts[-1])
    clip.caption_track_id = track.id
    project.id = "v2_1_live_acceptance"
    project.name = "AutoClip V2.1 Live Acceptance"
    project.clips = [clip]
    project.caption_tracks = [track]
    project.outputs = []
    workspace = Workspace(args.output_project)
    save_project(workspace, project)
    service = DesktopService(project_root=Path.cwd())
    service._render_job(str(workspace.project_file), clip.id, True, Event(), lambda *_args: None)
    output = workspace.root / "cache" / "previews" / f"{clip.id}-r{clip.revision}.mp4"
    report["preview"] = {
        "path": str(output), "candidate_id": top["id"], "title": top["title"],
        "framing": "fixed center; visual analysis intentionally not rerun",
        "caption_preset": clip.caption_preset,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"preview": str(output), "exists": output.is_file(), "bytes": output.stat().st_size if output.exists() else 0}, indent=2))
    return 0 if output.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
