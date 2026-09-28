from __future__ import annotations

from pathlib import Path

from .models import CaptionCue, CaptionTrack, EditSequence, ProjectClip, Transcript
from .time import MediaTime
from .time import Rational


CAPTION_PRESETS = {"clean", "bold_social", "word_highlight"}


def build_caption_track(clip: ProjectClip, transcript: Transcript) -> CaptionTrack:
    words_by_id = {word.id: word for word in transcript.words}
    cues: list[CaptionCue] = []
    for segment in transcript.segments:
        if segment.start.seconds >= clip.source_out.seconds or segment.end.seconds <= clip.source_in.seconds:
            continue
        words = [words_by_id[word_id] for word_id in segment.word_ids if word_id in words_by_id]
        if segment.corrected_text is not None:
            cues.append(CaptionCue(
                start=max(segment.start, clip.source_in, key=lambda value: value.seconds),
                end=min(segment.end, clip.source_out, key=lambda value: value.seconds),
                text=segment.text,
                word_ids=segment.word_ids,
                editable_text_override=segment.corrected_text,
            ))
            continue
        for group in _phrase_groups(words):
            if not group:
                continue
            cues.append(CaptionCue(
                start=max(group[0].start, clip.source_in, key=lambda value: value.seconds),
                end=min(group[-1].end, clip.source_out, key=lambda value: value.seconds),
                text=" ".join(word.corrected_text or word.text for word in group),
                word_ids=[word.id for word in group],
                word_timings=[{"id": word.id, "text": word.corrected_text or word.text,
                               "start": float(word.start.seconds), "end": float(word.end.seconds)} for word in group],
            ))
    return CaptionTrack(id=f"caption_{clip.id}_r{clip.revision}", cues=cues)


def _phrase_groups(words: list, max_words: int = 5) -> list[list]:
    groups: list[list] = []
    current: list = []
    for word in words:
        current.append(word)
        punctuation_break = (word.corrected_text or word.text).rstrip().endswith((".", "?", "!", ",", ";", ":"))
        if len(current) >= max_words or punctuation_break:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def build_edit_sequence_caption_track(sequence: EditSequence, transcript: Transcript, *, words_per_group: int = 5, positions: dict[str, str] | None = None) -> CaptionTrack:
    words_by_id = {word.id: word for word in transcript.words}
    cues: list[CaptionCue] = []
    for segment in sequence.segments:
        offset = segment.timeline_start - float(segment.source_in.seconds)
        words = [word for word in transcript.words if word.start.seconds < segment.source_out.seconds and word.end.seconds > segment.source_in.seconds]
        for group in _phrase_groups(words, max(2, min(7, words_per_group))):
            if not group:
                continue
            start = max(float(group[0].start.seconds), float(segment.source_in.seconds)) + offset
            end = min(float(group[-1].end.seconds), float(segment.source_out.seconds)) + offset
            timeline_time_base = Rational(sequence.frame_rate.denominator, sequence.frame_rate.numerator)
            cues.append(CaptionCue(
                start=MediaTime.from_seconds(start, timeline_time_base, exact=False),
                end=MediaTime.from_seconds(end, timeline_time_base, exact=False),
                text=" ".join(word.corrected_text or word.text for word in group),
                word_ids=[word.id for word in group], edit_segment_id=segment.id,
                word_timings=[{"id": word.id, "text": word.corrected_text or word.text,
                               "start": max(float(word.start.seconds), float(segment.source_in.seconds)) + offset,
                               "end": min(float(word.end.seconds), float(segment.source_out.seconds)) + offset}
                              for word in group],
                position=(positions or {}).get(segment.id, "lower"),
            ))
    return CaptionTrack(id=f"caption_{sequence.id}_r{sequence.revision}", cues=cues)


def _clock(seconds: float, *, ass: bool = False) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole, milliseconds = divmod(remainder, 1000)
    if ass:
        return f"{hours}:{minutes:02d}:{whole:02d}.{milliseconds // 10:02d}"
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{milliseconds:03d}"


def serialize_srt(track: CaptionTrack, clip: ProjectClip) -> str:
    blocks: list[str] = []
    offset = float(clip.source_in.seconds)
    for index, cue in enumerate(track.cues, 1):
        text = cue.editable_text_override or cue.text
        blocks.append(f"{index}\n{_clock(float(cue.start.seconds) - offset)} --> {_clock(float(cue.end.seconds) - offset)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def serialize_ass(track: CaptionTrack, clip: ProjectClip, preset: str) -> str:
    if preset not in CAPTION_PRESETS:
        raise ValueError("unsupported caption preset")
    styles = {
        "clean": "Arial,58,&H00FFFFFF,&H000000FF,&H00101010,0,0,0,0,100,100,0,0,1,3,1,2,80,80,90,1",
        "bold_social": "Arial,72,&H00FFFFFF,&H000000FF,&H00000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,110,1",
        "word_highlight": "Arial,68,&H0000FFFF,&H000000FF,&H00000000,-1,0,0,0,100,100,0,0,1,4,2,2,70,70,110,1",
    }
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: AutoClip,{styles[preset]}", "", "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    offset = float(clip.source_in.seconds)
    for cue in track.cues:
        raw = _karaoke_text(cue) if preset == "word_highlight" else (cue.editable_text_override or cue.text)
        text = raw.replace("\n", r"\N").replace(",", r"\,")
        alignment, margin = _ass_alignment(cue.position)
        text = rf"{{\an{alignment}}}" + text
        lines.append(f"Dialogue: 0,{_clock(float(cue.start.seconds) - offset, ass=True)},{_clock(float(cue.end.seconds) - offset, ass=True)},AutoClip,,0,0,{margin},,{text}")
    return "\n".join(lines) + "\n"


def _ass_alignment(position: str) -> tuple[int, int]:
    return {"upper": (8, 170), "center": (5, 90), "lower": (2, 110)}.get(position, (2, 110))


def _karaoke_text(cue: CaptionCue) -> str:
    if not cue.word_timings:
        return cue.editable_text_override or cue.text
    parts = []
    for index, word in enumerate(cue.word_timings):
        next_start = float(cue.word_timings[index + 1]["start"]) if index + 1 < len(cue.word_timings) else float(word["end"])
        duration = max(1, round((next_start - float(word["start"])) * 100))
        parts.append(r"{\kf" + str(duration) + "}" + str(word["text"]))
    return " ".join(parts)


def serialize_edit_sequence_ass(track: CaptionTrack, preset: str = "clean") -> str:
    if preset not in CAPTION_PRESETS:
        raise ValueError("unsupported caption preset")
    styles = {"clean": "Arial,58,&H00FFFFFF,&H000000FF,&H00101010,0,0,0,0,100,100,0,0,1,3,1,2,80,80,90,1", "bold_social": "Arial,72,&H00FFFFFF,&H000000FF,&H00000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,110,1", "word_highlight": "Arial,68,&H0000FFFF,&H000000FF,&H00000000,-1,0,0,0,100,100,0,0,1,4,2,2,70,70,110,1"}
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "", "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding", f"Style: AutoClip,{styles[preset]}", "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    for cue in track.cues:
        raw = _karaoke_text(cue) if preset == "word_highlight" else (cue.editable_text_override or cue.text)
        text = raw.replace("\n", r"\N").replace(",", r"\,")
        alignment, margin = _ass_alignment(cue.position)
        text = rf"{{\an{alignment}}}" + text
        lines.append(f"Dialogue: 0,{_clock(float(cue.start.seconds), ass=True)},{_clock(float(cue.end.seconds), ass=True)},AutoClip,,0,0,{margin},,{text}")
    return "\n".join(lines) + "\n"


def export_edit_sequence_ass(track: CaptionTrack, output: Path, preset: str = "clean") -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialize_edit_sequence_ass(track, preset), encoding="utf-8")


def export_srt(track: CaptionTrack, clip: ProjectClip, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialize_srt(track, clip), encoding="utf-8")


def export_ass(track: CaptionTrack, clip: ProjectClip, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialize_ass(track, clip, clip.caption_preset), encoding="utf-8")
