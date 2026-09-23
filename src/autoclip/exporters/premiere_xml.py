from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

from ..models import MediaSource, Timeline


def _text(parent: ET.Element, name: str, value: object) -> ET.Element:
    node = ET.SubElement(parent, name)
    node.text = str(value)
    return node


def build_premiere_xml(timeline: Timeline, sources: dict[str, MediaSource]) -> tuple[ET.ElementTree, list[str]]:
    root = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(root, "sequence", id=timeline.id)
    _text(sequence, "name", timeline.id)
    rate = ET.SubElement(sequence, "rate")
    nominal = round(float(timeline.frame_rate.as_fraction()))
    _text(rate, "timebase", nominal)
    _text(rate, "ntsc", "TRUE" if timeline.frame_rate.denominator == 1001 else "FALSE")
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sample = ET.SubElement(fmt, "samplecharacteristics")
    _text(sample, "width", timeline.canvas_width)
    _text(sample, "height", timeline.canvas_height)
    video_track = ET.SubElement(video, "track")
    audio = ET.SubElement(media, "audio")
    audio_track = ET.SubElement(audio, "track")
    timeline_cursor = 0
    warnings: list[str] = []
    for index, clip in enumerate(timeline.clips, 1):
        source = sources[clip.source_id]
        source_in = clip.source_in.to_frames(timeline.frame_rate)
        source_out = clip.source_out.to_frames(timeline.frame_rate)
        duration = source_out - source_in
        file_id = f"file-{source.id}"
        for track, media_type in ((video_track, "video"), (audio_track, "audio")):
            item = ET.SubElement(track, "clipitem", id=f"{media_type}-{clip.id}-{index}")
            _text(item, "name", clip.title)
            _text(item, "start", timeline_cursor)
            _text(item, "end", timeline_cursor + duration)
            _text(item, "in", source_in)
            _text(item, "out", source_out)
            file_node = ET.SubElement(item, "file", id=file_id)
            _text(file_node, "name", Path(source.reference).name)
            _text(file_node, "pathurl", "file://localhost/" + quote(str(Path(source.reference).absolute()).replace("\\", "/")))
            _text(file_node, "duration", source.duration.to_frames(timeline.frame_rate, exact=False))
            file_rate = ET.SubElement(file_node, "rate")
            _text(file_rate, "timebase", nominal)
            _text(file_rate, "ntsc", "TRUE" if timeline.frame_rate.denominator == 1001 else "FALSE")
        if clip.reframe_track_id:
            warnings.append(f"{clip.id}: dynamic reframe track cannot be faithfully represented by this minimal XMEML adapter")
        timeline_cursor += duration
    _text(sequence, "duration", timeline_cursor)
    return ET.ElementTree(root), warnings


def export_premiere_xml(timeline: Timeline, sources: dict[str, MediaSource], output: Path) -> list[str]:
    tree, warnings = build_premiere_xml(timeline, sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return warnings

