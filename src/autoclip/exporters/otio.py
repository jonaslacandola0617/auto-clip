from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import MediaSource, Timeline


def _rational_time(value: int, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "value": float(value), "rate": rate}


def _time_range(start: int, duration: int, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rational_time(start, rate), "duration": _rational_time(duration, rate)}


def build_otio(timeline: Timeline, sources: dict[str, MediaSource]) -> tuple[dict[str, Any], list[str]]:
    rate = float(timeline.frame_rate.as_fraction())
    video_children: list[dict[str, Any]] = []
    audio_children: list[dict[str, Any]] = []
    warnings: list[str] = []
    for clip in timeline.clips:
        source = sources[clip.source_id]
        start = clip.source_in.to_frames(timeline.frame_rate, exact=False)
        end = clip.source_out.to_frames(timeline.frame_rate, exact=False)
        source_range = _time_range(start, end - start, rate)
        reference = {
            "OTIO_SCHEMA": "ExternalReference.1", "name": source.id,
            "target_url": Path(source.reference).absolute().as_uri(), "available_range": None,
            "available_image_bounds": None, "metadata": {"autoclip:fingerprint": source.fingerprint},
        }
        base = {
            "OTIO_SCHEMA": "Clip.2", "name": clip.title, "source_range": source_range,
            "media_references": {"DEFAULT_MEDIA": reference}, "active_media_reference_key": "DEFAULT_MEDIA",
            "metadata": {"autoclip:clip_id": clip.id}, "effects": [], "markers": [],
            "enabled": True, "color": None,
        }
        video_children.append(base)
        audio_children.append({**base, "metadata": {**base["metadata"], "autoclip:media_kind": "audio"}})
        if clip.reframe_track_id:
            warnings.append(f"{clip.id}: dynamic reframe track is stored in AutoClip data but not recreated as an OTIO effect")
    tracks = [
        {"OTIO_SCHEMA": "Track.1", "name": "V1", "kind": "Video", "source_range": None, "effects": [], "markers": [], "enabled": True, "color": None, "children": video_children},
        {"OTIO_SCHEMA": "Track.1", "name": "A1", "kind": "Audio", "source_range": None, "effects": [], "markers": [], "enabled": True, "color": None, "children": audio_children},
    ]
    payload = {"OTIO_SCHEMA": "Timeline.1", "name": timeline.id, "global_start_time": _rational_time(0, rate), "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "source_range": None, "effects": [], "markers": [], "enabled": True, "color": None, "children": tracks}, "metadata": {"autoclip:canvas": {"width": timeline.canvas_width, "height": timeline.canvas_height}}}
    return payload, warnings


def export_otio(timeline: Timeline, sources: dict[str, MediaSource], output: Path) -> list[str]:
    payload, warnings = build_otio(timeline, sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return warnings
