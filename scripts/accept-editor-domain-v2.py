from __future__ import annotations

import argparse
import json
from pathlib import Path
from threading import Event

from autoclip.desktop_service import DesktopService
from autoclip.editor import EditorCommand, EditorSession, sequence_to_timeline
from autoclip.editor_domain import CaptionItem, MediaClip
from autoclip.exporters.otio import export_otio
from autoclip.exporters.premiere_xml import export_premiere_xml
from autoclip.media import FFmpegService
from autoclip.storage import Workspace, load_project, save_project
from autoclip.time import MediaTime


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-destructive V2.2 editor-domain acceptance using cached project state.")
    parser.add_argument("source_project", type=Path)
    parser.add_argument("output_project", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    source_workspace = Workspace(args.source_project.parent if args.source_project.is_file() else args.source_project)
    project = load_project(source_workspace)
    project.id = "v2_2_editor_acceptance"
    project.name = "AutoClip V2.2 Editor Acceptance"
    project.outputs = []
    accepted = next(value for value in project.edit_sequences if value.status == "ready" and value.integrity.status == "passed")
    project.active_sequence_id = accepted.id
    sequence = next(value for value in project.sequences if value.id == accepted.id)
    session = EditorSession(project)

    video_items = sorted(
        (item for track in sequence.tracks if track.type == "video" for item in track.items if isinstance(item, MediaClip)),
        key=lambda item: item.timeline_start.seconds,
    )
    target = video_items[-1]
    original_source_out = target.source_out
    trimmed_source_out = MediaTime.from_seconds(target.source_out.seconds - MediaTime.from_seconds("0.2", target.source_out.time_base, exact=False).seconds,
                                                target.source_out.time_base, exact=False)
    sequence = session.execute(EditorCommand("trim_end", sequence.id, {"item_id": target.id, "source_out": trimmed_source_out.to_dict()}))
    trimmed_revision = sequence.revision
    undone = session.undo()
    undo_restored = undone.item(target.id)[1].source_out == original_source_out
    redone = session.redo()
    redo_restored = redone.item(target.id)[1].source_out == trimmed_source_out

    caption = next(
        item for track in redone.tracks if track.type == "captions" for item in track.items
        if isinstance(item, CaptionItem) and item.enabled
    )
    caption_text = caption.text
    edited = session.execute(EditorCommand("change_caption_text", redone.id, {"item_id": caption.id, "text": caption_text.upper()}))

    workspace = Workspace(args.output_project)
    save_project(workspace, project)
    reopened = load_project(workspace)
    reopened_sequence = next(value for value in reopened.sequences if value.id == edited.id)
    reopened_caption = reopened_sequence.item(caption.id)[1]

    timeline = sequence_to_timeline(reopened_sequence)
    sources = {value.id: value for value in reopened.sources}
    otio_path = workspace.root / "exports" / "v2-2-acceptance.otio"
    xml_path = workspace.root / "exports" / "v2-2-acceptance.xml"
    otio_warnings = export_otio(timeline, sources, otio_path)
    xml_warnings = export_premiere_xml(timeline, sources, xml_path)

    service = DesktopService(project_root=Path.cwd())
    service._render_edit_sequence_job(str(workspace.project_file), reopened_sequence.id, True, Event(), lambda *_args: None)
    final_project = load_project(workspace)
    final_sequence = next(value for value in final_project.sequences if value.id == reopened_sequence.id)
    preview_candidates = sorted((workspace.root / "cache" / "previews").glob(f"{final_sequence.id}-r{final_sequence.revision}-*.mp4"))
    if not preview_candidates:
        raise RuntimeError("V2.2 preview was not created")
    preview_path = preview_candidates[-1]
    FFmpegService().validate_audible_audio(preview_path)

    report = {
        "phase": "V2.2",
        "source_project": str(source_workspace.root),
        "acceptance_project": str(workspace.root),
        "provider_calls": 0,
        "transcription_runs": 0,
        "legacy_edit_sequence_id": accepted.id,
        "sequence_revision": final_sequence.revision,
        "trimmed_revision": trimmed_revision,
        "undo_restored_source_time": undo_restored,
        "redo_restored_trim": redo_restored,
        "caption_edit_persisted": isinstance(reopened_caption, CaptionItem) and reopened_caption.text == caption_text.upper(),
        "legacy_ai_metadata_preserved": bool(final_sequence.ai_generation_metadata.get("story_concept_id")),
        "source_ranges": [{"source_in": float(item.source_in.seconds), "source_out": float(item.source_out.seconds)}
                          for track in final_sequence.tracks if track.type == "video" for item in track.items if isinstance(item, MediaClip)],
        "preview": str(preview_path),
        "preview_revision": final_sequence.dependencies.preview_revision,
        "preview_current": final_sequence.dependencies.preview_revision == final_sequence.revision,
        "audio_validation": "passed",
        "otio": str(otio_path),
        "premiere_xml": str(xml_path),
        "export_warnings": otio_warnings + xml_warnings,
    }
    report_path = args.report or workspace.root / "v2-2-acceptance.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
