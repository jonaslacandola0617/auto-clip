from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from .editor_domain import (
    AudioClip, CaptionItem, GraphicItem, MediaAsset, MediaClip, MediaLibrary, Sequence,
    TimelineItem, Track, timeline_item_from_dict,
)
from .models import (
    ApprovedClip, AutoClipProject, CaptionCue, CaptionTrack, EditSegment, EditSequence,
    EditorialIntegrityResult, EnhancementPlan, GraphicOverlay, MediaSource, Timeline,
)
from .time import MediaTime, Rational


TRACK_ITEM_TYPES: dict[str, tuple[type[Any], ...]] = {
    "video": (MediaClip,), "audio": (AudioClip,), "captions": (CaptionItem,), "graphics": (GraphicItem,),
}


def _mt(value: MediaTime | dict[str, Any]) -> MediaTime:
    return value if isinstance(value, MediaTime) else MediaTime.from_dict(value)


def _source_asset(source: MediaSource) -> MediaAsset:
    audio = next((stream for stream in source.streams if stream.kind == "audio"), None)
    return MediaAsset(
        id=source.id, reference=source.reference, filename=Path(source.reference).name,
        media_type="video", duration=source.duration, width=source.width, height=source.height,
        frame_rate=source.frame_rate, time_base=source.time_base, fingerprint=source.fingerprint,
        audio_channels=audio.channels if audio else None,
        audio_sample_rate=audio.sample_rate if audio else None,
        provenance={"legacy_source_id": source.id},
    )


def _at(seconds: Fraction | float, time_base: Rational) -> MediaTime:
    return MediaTime.from_seconds(seconds, time_base, exact=False)


def _base_tracks(sequence_id: str) -> list[Track]:
    return [
        Track(f"{sequence_id}:video:1", "video", "Video 1", 0),
        Track(f"{sequence_id}:audio:1", "audio", "Dialogue", 1),
        Track(f"{sequence_id}:captions:1", "captions", "Captions", 2),
        Track(f"{sequence_id}:graphics:1", "graphics", "Graphics", 3),
    ]


def _legacy_edit_sequence(project: AutoClipProject, legacy: EditSequence) -> Sequence:
    tracks = _base_tracks(legacy.id)
    video, audio = tracks[0], tracks[1]
    source = next((value for value in project.sources if value.id == legacy.source_id), None)
    time_base = source.time_base if source else Rational(legacy.frame_rate.denominator, legacy.frame_rate.numerator)
    for segment in sorted(legacy.segments, key=lambda value: (value.timeline_start, value.order)):
        start = _at(segment.timeline_start, time_base)
        duration = _at(segment.source_out.seconds - segment.source_in.seconds, time_base)
        common = dict(
            media_asset_id=segment.source_id, source_in=segment.source_in, source_out=segment.source_out,
            timeline_start=start, timeline_duration=duration,
            metadata={"legacy_segment_id": segment.id, "moment_id": segment.moment_id, "purpose": segment.purpose,
                      "provenance": segment.provenance},
        )
        video.items.append(MediaClip(id=segment.id, track_id=video.id, linked_item_id=f"{segment.id}:audio", **common))
        audio.items.append(AudioClip(id=f"{segment.id}:audio", track_id=audio.id, linked_item_id=segment.id, **common))
    visual = next((plan for plan in project.visual_edit_plans if plan.sequence_id == legacy.id), None)
    enhancement = next((plan for plan in project.enhancement_plans if plan.sequence_id == legacy.id), None)
    sequence = Sequence(
        id=legacy.id, name=legacy.title, canvas_width=legacy.canvas_width, canvas_height=legacy.canvas_height,
        frame_rate=legacy.frame_rate, time_base=time_base, tracks=tracks, lifecycle="migrated_from_v1",
        visual_plan_id=visual.id if visual else None, enhancement_plan_id=enhancement.id if enhancement else None,
        revision=max(1, legacy.revision), saved_revision=max(1, legacy.revision),
        metadata={"legacy_edit_sequence_id": legacy.id, "story_concept_id": legacy.story_concept_id,
                  "source_id": legacy.source_id, "legacy_status": legacy.status},
        ai_generation_metadata={"story_concept_id": legacy.story_concept_id,
                                "editorial_quality": asdict(legacy.editorial_quality) if legacy.editorial_quality else None},
    )
    if project.transcripts:
        from .captions import build_edit_sequence_caption_track

        preset = visual.caption_preset if visual else "word_highlight"
        for index, cue in enumerate(build_edit_sequence_caption_track(legacy, project.transcripts[-1]).cues):
            tracks[2].items.append(CaptionItem(
                id=f"{legacy.id}:caption:{index}", timeline_start=cue.start,
                timeline_duration=_at(cue.end.seconds - cue.start.seconds, time_base), track_id=tracks[2].id,
                text=cue.editable_text_override or cue.text, word_timings=deepcopy(cue.word_timings),
                style_preset=preset, position=cue.position,
                metadata={"word_ids": cue.word_ids, "edit_segment_id": cue.edit_segment_id},
            ))
    _materialize_enhancements(project, sequence)
    return sequence


def _legacy_clip_sequence(project: AutoClipProject, clip: Any) -> Sequence:
    source = next(source for source in project.sources if source.id == clip.source_id)
    sequence_id = f"sequence_{clip.id}"
    time_base = source.time_base
    tracks = _base_tracks(sequence_id)
    duration = _at(clip.source_out.seconds - clip.source_in.seconds, time_base)
    video_id = f"{clip.id}:video"
    tracks[0].items.append(MediaClip(video_id, clip.source_id, clip.source_in, clip.source_out, _at(0, time_base), duration, tracks[0].id,
                                    transform_reference=clip.reframe_track_id, linked_item_id=f"{clip.id}:audio",
                                    metadata={"legacy_clip_id": clip.id, "candidate_id": clip.candidate_id}))
    tracks[1].items.append(AudioClip(f"{clip.id}:audio", clip.source_id, clip.source_in, clip.source_out, _at(0, time_base), duration,
                                    tracks[1].id, linked_item_id=video_id, metadata={"legacy_clip_id": clip.id}))
    caption_track = next((track for track in project.caption_tracks if track.id == clip.caption_track_id), None)
    if caption_track:
        for index, cue in enumerate(caption_track.cues):
            cue_start = max(Fraction(0), cue.start.seconds - clip.source_in.seconds)
            cue_end = min(duration.seconds, cue.end.seconds - clip.source_in.seconds)
            if cue_end > cue_start:
                tracks[2].items.append(CaptionItem(
                    f"{clip.id}:caption:{index}", _at(cue_start, time_base), _at(cue_end - cue_start, time_base),
                    tracks[2].id, cue.editable_text_override or cue.text, cue.word_timings, clip.caption_preset,
                    cue.position, metadata={"word_ids": cue.word_ids, "legacy_caption_track_id": caption_track.id},
                ))
    return Sequence(
        sequence_id, clip.title, 1080, 1920, source.frame_rate, time_base, tracks,
        metadata={"legacy_clip_id": clip.id, "source_id": clip.source_id, "candidate_id": clip.candidate_id},
        lifecycle="migrated_from_v1", revision=max(1, clip.revision), saved_revision=max(1, clip.revision),
    )


def _materialize_enhancements(project: AutoClipProject, sequence: Sequence) -> None:
    plan = next((value for value in project.enhancement_plans if value.sequence_id == sequence.id), None)
    if not plan:
        return
    graphics = next(track for track in sequence.tracks if track.type == "graphics")
    broll_track = Track(f"{sequence.id}:video:broll", "video", "B-roll", len(sequence.tracks), metadata={"allow_overlap": False})
    sfx_track = Track(f"{sequence.id}:audio:sfx", "audio", "Sound effects", len(sequence.tracks) + 1)
    for item in plan.graphic_items:
        graphics.items.append(GraphicItem(
            item.id, _at(item.timeline_start, sequence.time_base), _at(item.timeline_end - item.timeline_start, sequence.time_base),
            graphics.id, item.text, item.style_preset, item.placement, item.enabled,
            {"enhancement_plan_id": plan.id, "source_references": item.source_references}, {"reason": item.reason},
        ))
    for item in plan.broll_items:
        if item.asset_id not in {asset.id for asset in project.media_library.assets} or not item.enabled:
            continue
        asset = project.media_library.asset(item.asset_id)
        duration = _at(item.timeline_end - item.timeline_start, sequence.time_base)
        asset_base = asset.time_base or sequence.time_base
        broll_track.items.append(MediaClip(
            item.id, item.asset_id, _at(0, asset_base), _at(duration.seconds, asset_base),
            _at(item.timeline_start, sequence.time_base), duration, broll_track.id,
            metadata={"enhancement_plan_id": plan.id, "fit": item.fit, "motion": item.motion, "reason": item.reason},
        ))
    for cue in plan.sound_cues:
        if not any(asset.id == cue.asset_id for asset in project.media_library.assets):
            continue
        asset = project.media_library.asset(cue.asset_id)
        available = float(asset.duration.seconds)
        source_out = _at(min(cue.duration, available) if available > 0 else cue.duration, asset.time_base or sequence.time_base)
        sfx_track.items.append(AudioClip(
            cue.id, cue.asset_id, _at(0, asset.time_base or sequence.time_base), source_out,
            _at(cue.timeline_start, sequence.time_base), _at(cue.duration, sequence.time_base), sfx_track.id,
            role="sfx", gain_db=cue.gain_db, enabled=cue.enabled, metadata={"enhancement_plan_id": plan.id},
        ))
    if broll_track.items:
        sequence.tracks.append(broll_track)
    if sfx_track.items:
        sequence.tracks.append(sfx_track)


def ensure_editor_domain(project: AutoClipProject) -> AutoClipProject:
    """Create the v2.2 canonical domain in memory without deleting legacy metadata."""
    known = {asset.id for asset in project.media_library.assets}
    project.media_library.assets.extend(_source_asset(source) for source in project.sources if source.id not in known)
    for plan in project.enhancement_plans:
        for asset in plan.assets:
            if asset.id not in {value.id for value in project.media_library.assets}:
                asset_base = project.sources[0].time_base if project.sources else Rational(1, 1000)
                duration = MediaTime.from_seconds(str(asset.source_metadata.get("duration_seconds", 0)), asset_base, exact=False)
                project.media_library.assets.append(MediaAsset(
                    asset.id, asset.reference, Path(asset.reference).name, asset.kind,
                    duration, asset.fingerprint, availability="available",
                    provenance={"enhancement_plan_id": plan.id, **asset.source_metadata},
                ))
    existing = {sequence.id for sequence in project.sequences}
    project.sequences.extend(_legacy_edit_sequence(project, value) for value in project.edit_sequences if value.id not in existing)
    existing = {sequence.id for sequence in project.sequences}
    project.sequences.extend(_legacy_clip_sequence(project, value) for value in project.clips if f"sequence_{value.id}" not in existing)
    if project.active_sequence_id is None and project.sequences:
        preferred = next((value for value in project.sequences if value.metadata.get("legacy_status") == "ready"), project.sequences[0])
        project.active_sequence_id = preferred.id
    project.legacy_compatibility.setdefault("migrated_from_schema", project.schema_version)
    project.legacy_compatibility["legacy_metadata_preserved"] = True
    return project


def accept_validated_ai_plan(project: AutoClipProject, legacy: EditSequence) -> Sequence:
    """Schema-bound AI materialization through one editor command transaction."""
    if legacy.integrity.status != "passed" or legacy.status != "ready":
        raise ValueError("only validated, ready AI plans may be materialized")
    project.sequences = [value for value in project.sequences if value.id != legacy.id]
    planned = _legacy_edit_sequence(project, legacy)
    items = [deepcopy(item) for track in planned.tracks for item in track.items]
    planned.tracks = [Track(track.id, track.type, track.name, track.order, enabled=track.enabled,
                            locked=track.locked, metadata=deepcopy(track.metadata)) for track in planned.tracks]
    planned.lifecycle = "generated_from_ai"
    planned.revision = 0
    planned.saved_revision = 0
    project.sequences.append(planned)
    session = EditorSession(project)
    result = session.execute_transaction(
        [EditorCommand("insert_item", planned.id, {"item": asdict(item)}) for item in items],
        label="Accept AI edit plan",
    )
    result.lifecycle = "generated_from_ai"
    return result


def validate_sequence(project: AutoClipProject, sequence: Sequence) -> None:
    orders = [track.order for track in sequence.tracks]
    if len(orders) != len(set(orders)):
        raise ValueError("track orders must be unique")
    ids: set[str] = set()
    assets = {asset.id for asset in project.media_library.assets}
    for track in sequence.tracks:
        if track.type not in TRACK_ITEM_TYPES:
            raise ValueError(f"unsupported track type: {track.type}")
        ranges: list[tuple[Fraction, Fraction]] = []
        for item in track.items:
            if item.id in ids:
                raise ValueError(f"duplicate timeline item id: {item.id}")
            ids.add(item.id)
            if item.track_id != track.id or not isinstance(item, TRACK_ITEM_TYPES[track.type]):
                raise ValueError(f"item {item.id} is incompatible with track {track.id}")
            if item.timeline_start.seconds < 0 or item.timeline_duration.seconds <= 0:
                raise ValueError("timeline placement must be non-negative with positive duration")
            if isinstance(item, (MediaClip, AudioClip)):
                if item.media_asset_id not in assets:
                    raise ValueError(f"unknown media asset: {item.media_asset_id}")
                if item.source_in.seconds < 0 or item.source_out.seconds <= item.source_in.seconds:
                    raise ValueError("source range must be positive")
                if item.speed <= 0 if isinstance(item, MediaClip) else False:
                    raise ValueError("clip speed must be positive")
            if track.type in {"video", "audio"} and not track.metadata.get("allow_overlap") and item.enabled:
                start, end = item.timeline_start.seconds, item.timeline_start.seconds + item.timeline_duration.seconds
                if any(start < old_end and end > old_start for old_start, old_end in ranges):
                    raise ValueError(f"overlapping items are not allowed on track {track.id}")
                ranges.append((start, end))


@dataclass(frozen=True, slots=True)
class EditorCommand:
    name: str
    sequence_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _HistoryEntry:
    sequence_id: str
    before: Sequence
    after: Sequence
    before_editor_revision: int
    after_editor_revision: int
    label: str


def _shift_after(sequence: Sequence, track: Track, threshold: Fraction, delta: Fraction, excluded: set[str]) -> None:
    for item in track.items:
        if item.id not in excluded and item.timeline_start.seconds >= threshold:
            item.timeline_start = _at(item.timeline_start.seconds + delta, sequence.time_base)


def _linked(sequence: Sequence, item: TimelineItem) -> tuple[Track, TimelineItem] | None:
    linked_id = getattr(item, "linked_item_id", None)
    if not linked_id:
        return None
    try:
        return sequence.item(linked_id)
    except ValueError:
        return None


def _dependent_captions(sequence: Sequence, item: TimelineItem) -> list[tuple[Track, CaptionItem]]:
    segment_id = getattr(item, "metadata", {}).get("legacy_segment_id")
    if not segment_id:
        return []
    return [
        (track, caption) for track in sequence.tracks if track.type == "captions"
        for caption in track.items if isinstance(caption, CaptionItem)
        and caption.metadata.get("edit_segment_id") == segment_id
    ]


def _clamp_dependent_captions(sequence: Sequence, item: TimelineItem) -> None:
    clip_start = item.timeline_start.seconds
    clip_end = clip_start + item.timeline_duration.seconds
    for track, caption in list(_dependent_captions(sequence, item)):
        start = max(caption.timeline_start.seconds, clip_start)
        end = min(caption.timeline_start.seconds + caption.timeline_duration.seconds, clip_end)
        if end <= start:
            track.items.remove(caption)
            continue
        caption.timeline_start = _at(start, sequence.time_base)
        caption.timeline_duration = _at(end - start, sequence.time_base)


def _apply(sequence: Sequence, command: EditorCommand) -> set[str]:
    payload = command.payload
    stale = {"preview", "render"}
    if command.name == "insert_item":
        item = timeline_item_from_dict(payload["item"])
        sequence.track(item.track_id).items.append(item)
        stale.add("captions" if isinstance(item, CaptionItem) else "enhancements" if isinstance(item, GraphicItem) else "timeline")
    elif command.name == "remove_item":
        track, item = sequence.item(payload["item_id"])
        linked = _linked(sequence, item) if payload.get("preserve_link", True) else None
        for caption_track, caption in _dependent_captions(sequence, item):
            caption_track.items.remove(caption)
        track.items.remove(item)
        if linked:
            linked[0].items.remove(linked[1])
        if payload.get("ripple", False):
            _shift_after(sequence, track, item.timeline_start.seconds + item.timeline_duration.seconds, -item.timeline_duration.seconds, set())
        stale.add("captions" if isinstance(item, CaptionItem) else "timeline")
    elif command.name == "move_item":
        old_track, item = sequence.item(payload["item_id"])
        linked = _linked(sequence, item) if payload.get("preserve_link", True) else None
        old_start = item.timeline_start.seconds
        captions = _dependent_captions(sequence, item)
        destination = sequence.track(payload.get("track_id", old_track.id))
        new_start = _mt(payload["timeline_start"])
        if new_start.seconds < 0:
            raise ValueError("timeline start must not be negative")
        old_track.items.remove(item)
        item.track_id = destination.id
        item.timeline_start = new_start.rescale(sequence.time_base, exact=False)
        destination.items.append(item)
        delta = item.timeline_start.seconds - old_start
        for _, caption in captions:
            caption.timeline_start = _at(caption.timeline_start.seconds + delta, sequence.time_base)
        if linked:
            linked[1].timeline_start = _at(linked[1].timeline_start.seconds + delta, sequence.time_base)
        stale.add("timeline")
    elif command.name in {"trim_start", "trim_end"}:
        track, item = sequence.item(payload["item_id"])
        if not isinstance(item, (MediaClip, AudioClip)):
            raise ValueError("only source-backed clips can be trimmed")
        before_end = item.timeline_start.seconds + item.timeline_duration.seconds
        linked = _linked(sequence, item) if payload.get("preserve_link", True) else None
        if command.name == "trim_start":
            new_source = _mt(payload["source_in"])
            delta = new_source.seconds - item.source_in.seconds
            if new_source.seconds >= item.source_out.seconds:
                raise ValueError("trim start must be before source out")
            item.source_in = new_source
            item.timeline_start = _at(item.timeline_start.seconds + delta, sequence.time_base)
            item.timeline_duration = _at(item.timeline_duration.seconds - delta, sequence.time_base)
            if linked and isinstance(linked[1], (MediaClip, AudioClip)):
                linked[1].source_in = _at(linked[1].source_in.seconds + delta, linked[1].source_in.time_base)
                linked[1].timeline_start = _at(linked[1].timeline_start.seconds + delta, sequence.time_base)
                linked[1].timeline_duration = _at(linked[1].timeline_duration.seconds - delta, sequence.time_base)
        else:
            new_source = _mt(payload["source_out"])
            if new_source.seconds <= item.source_in.seconds:
                raise ValueError("trim end must be after source in")
            item.source_out = new_source
            item.timeline_duration = _at(new_source.seconds - item.source_in.seconds, sequence.time_base)
            if linked and isinstance(linked[1], (MediaClip, AudioClip)):
                linked[1].source_out = _at(linked[1].source_in.seconds + item.timeline_duration.seconds, linked[1].source_out.time_base)
                linked[1].timeline_duration = item.timeline_duration
        if payload.get("ripple", False):
            delta = (item.timeline_start.seconds + item.timeline_duration.seconds) - before_end
            _shift_after(sequence, track, before_end, delta, {item.id})
        _clamp_dependent_captions(sequence, item)
        stale.update({"timeline", "captions", "visual"})
    elif command.name == "split_clip":
        track, item = sequence.item(payload["item_id"])
        if not isinstance(item, (MediaClip, AudioClip)):
            raise ValueError("only source-backed clips can be split")
        at = _mt(payload["timeline_at"]).rescale(sequence.time_base, exact=False)
        offset = at.seconds - item.timeline_start.seconds
        if offset <= 0 or offset >= item.timeline_duration.seconds:
            raise ValueError("split point must be inside the clip")
        second = deepcopy(item)
        second.id = payload["new_item_id"]
        second.metadata = deepcopy(second.metadata)
        second.metadata["legacy_segment_id"] = second.id
        second.timeline_start = at
        second.timeline_duration = _at(item.timeline_duration.seconds - offset, sequence.time_base)
        second.source_in = _at(item.source_in.seconds + offset, item.source_in.time_base)
        item.timeline_duration = _at(offset, sequence.time_base)
        item.source_out = second.source_in
        track.items.append(second)
        for _, caption in _dependent_captions(sequence, item):
            if caption.timeline_start.seconds >= at.seconds:
                caption.metadata["edit_segment_id"] = second.metadata.get("legacy_segment_id", second.id)
            elif caption.timeline_start.seconds + caption.timeline_duration.seconds > at.seconds:
                caption.timeline_duration = _at(at.seconds - caption.timeline_start.seconds, sequence.time_base)
        linked = _linked(sequence, item) if payload.get("preserve_link", True) else None
        if linked and isinstance(linked[1], (MediaClip, AudioClip)):
            linked_second = deepcopy(linked[1])
            linked_second.id = payload.get("new_linked_item_id", f"{payload['new_item_id']}:linked")
            linked_second.timeline_start = at
            linked_second.timeline_duration = second.timeline_duration
            linked_second.source_in = _at(linked[1].source_in.seconds + offset, linked[1].source_in.time_base)
            linked[1].timeline_duration = item.timeline_duration
            linked[1].source_out = linked_second.source_in
            item.linked_item_id = linked[1].id
            linked[1].linked_item_id = item.id
            second.linked_item_id = linked_second.id
            linked_second.linked_item_id = second.id
            linked[0].items.append(linked_second)
        stale.update({"timeline", "captions", "visual"})
    elif command.name == "reorder_item":
        track, item = sequence.item(payload["item_id"])
        track.items.remove(item)
        index = int(payload["index"])
        if index < 0 or index > len(track.items):
            raise ValueError("reorder index is outside the track")
        track.items.insert(index, item)
        stale.add("timeline")
    elif command.name in {"enable_item", "disable_item"}:
        _, item = sequence.item(payload["item_id"])
        item.enabled = command.name == "enable_item"
        linked = _linked(sequence, item) if payload.get("preserve_link", True) else None
        if linked:
            linked[1].enabled = item.enabled
        stale.add("captions" if isinstance(item, CaptionItem) else "timeline")
    elif command.name == "change_caption_text":
        _, item = sequence.item(payload["item_id"])
        if not isinstance(item, CaptionItem):
            raise ValueError("item is not a caption")
        item.text = str(payload["text"])
        item.metadata["manual_override"] = True
        stale = {"captions", "preview", "render"}
    elif command.name == "change_caption_preset":
        _, item = sequence.item(payload["item_id"])
        if not isinstance(item, CaptionItem):
            raise ValueError("item is not a caption")
        item.style_preset = str(payload["preset"])
        stale = {"captions", "preview", "render"}
    elif command.name == "change_graphic_text":
        _, item = sequence.item(payload["item_id"])
        if not isinstance(item, GraphicItem):
            raise ValueError("item is not a graphic")
        item.content = str(payload["text"])
        item.metadata["manual_override"] = True
        stale = {"enhancements", "preview", "render"}
    else:
        raise ValueError(f"unsupported editor command: {command.name}")
    return stale


class EditorSession:
    def __init__(self, project: AutoClipProject, *, history_limit: int = 100) -> None:
        self.project = ensure_editor_domain(project)
        self.history_limit = max(1, history_limit)
        self._undo: list[_HistoryEntry] = []
        self._redo: list[_HistoryEntry] = []

    def _sequence(self, sequence_id: str) -> Sequence:
        try:
            return next(value for value in self.project.sequences if value.id == sequence_id)
        except StopIteration as exc:
            raise ValueError(f"unknown sequence: {sequence_id}") from exc

    def execute(self, command: EditorCommand) -> Sequence:
        return self.execute_transaction([command], label=command.name)

    def execute_transaction(self, commands: Iterable[EditorCommand], *, label: str = "Edit") -> Sequence:
        commands = list(commands)
        if not commands:
            raise ValueError("transaction must contain at least one command")
        if len({value.sequence_id for value in commands}) != 1:
            raise ValueError("a transaction may only edit one sequence")
        sequence = self._sequence(commands[0].sequence_id)
        before = deepcopy(sequence)
        working = deepcopy(sequence)
        stale: set[str] = set()
        for command in commands:
            stale.update(_apply(working, command))
        validate_sequence(self.project, working)
        working.revision = sequence.revision + 1
        if "captions" in stale:
            working.dependencies.captions_revision += 1
        if "visual" in stale:
            working.dependencies.visual_revision += 1
        working.dependencies.stale_dependencies = sorted(set(working.dependencies.stale_dependencies) | stale)
        working.lifecycle = "editing"
        before_editor_revision = self.project.editor_revision
        self.project.sequences[self.project.sequences.index(sequence)] = working
        self.project.editor_revision += 1
        self._undo.append(_HistoryEntry(working.id, before, deepcopy(working), before_editor_revision, self.project.editor_revision, label))
        self._undo = self._undo[-self.history_limit:]
        self._redo.clear()
        return working

    def undo(self) -> Sequence:
        if not self._undo:
            raise ValueError("nothing to undo")
        entry = self._undo.pop()
        current = self._sequence(entry.sequence_id)
        self.project.sequences[self.project.sequences.index(current)] = deepcopy(entry.before)
        self.project.editor_revision += 1
        self._redo.append(entry)
        return self._sequence(entry.sequence_id)

    def redo(self) -> Sequence:
        if not self._redo:
            raise ValueError("nothing to redo")
        entry = self._redo.pop()
        current = self._sequence(entry.sequence_id)
        redone = deepcopy(entry.after)
        redone.revision = current.revision + 1
        self.project.sequences[self.project.sequences.index(current)] = redone
        self.project.editor_revision += 1
        self._undo.append(entry)
        return redone

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)


def sequence_to_edit_sequence(project: AutoClipProject, sequence: Sequence) -> EditSequence:
    legacy = next((value for value in project.edit_sequences if value.id == sequence.metadata.get("legacy_edit_sequence_id")), None)
    items = sorted(
        (item for track in sequence.tracks if track.type == "video" for item in track.items if isinstance(item, MediaClip) and item.enabled),
        key=lambda item: (item.timeline_start.seconds, item.id),
    )
    segments = [EditSegment(
        id=item.metadata.get("legacy_segment_id", item.id), moment_id=item.metadata.get("moment_id", ""),
        source_id=item.media_asset_id, source_in=item.source_in, source_out=item.source_out,
        timeline_start=float(item.timeline_start.seconds), purpose=item.metadata.get("purpose", "timeline edit"),
        transcript_excerpt=item.metadata.get("transcript_excerpt", ""), order=index,
        provenance={"editor_sequence_id": sequence.id, "editor_revision": str(sequence.revision)},
    ) for index, item in enumerate(items)]
    return EditSequence(
        id=sequence.id, story_concept_id=sequence.metadata.get("story_concept_id", ""), title=sequence.name,
        source_id=sequence.metadata.get("source_id", items[0].media_asset_id if items else ""), segments=segments,
        actions=deepcopy(legacy.actions) if legacy else [],
        integrity=deepcopy(legacy.integrity) if legacy else EditorialIntegrityResult("passed", [], [], validator_version="v2.2-sequence-adapter"),
        canvas_width=sequence.canvas_width, canvas_height=sequence.canvas_height, frame_rate=sequence.frame_rate,
        revision=sequence.revision, status="ready", editorial_review=deepcopy(legacy.editorial_review) if legacy else None,
        editorial_quality=deepcopy(legacy.editorial_quality) if legacy else None,
    )


def sequence_to_timeline(sequence: Sequence) -> Timeline:
    clips = [ApprovedClip(
        item.id, item.media_asset_id, item.source_in, item.source_out, sequence.name,
        reframe_track_id=item.transform_reference,
    ) for track in sequence.tracks if track.type == "video" for item in track.items if isinstance(item, MediaClip) and item.enabled]
    clips.sort(key=lambda value: next(item.timeline_start.seconds for track in sequence.tracks for item in track.items if item.id == value.id))
    return Timeline(sequence.id, sequence.canvas_width, sequence.canvas_height, sequence.frame_rate, clips)


def sequence_to_caption_track(sequence: Sequence) -> CaptionTrack:
    cues = [CaptionCue(
        start=item.timeline_start, end=_at(item.timeline_start.seconds + item.timeline_duration.seconds, sequence.time_base),
        text=item.text, word_ids=list(item.metadata.get("word_ids", [])), word_timings=deepcopy(item.word_timings),
        position=item.position, edit_segment_id=item.metadata.get("edit_segment_id"),
    ) for track in sequence.tracks if track.type == "captions" for item in track.items
            if isinstance(item, CaptionItem) and item.enabled]
    cues.sort(key=lambda cue: cue.start.seconds)
    return CaptionTrack(f"caption_{sequence.id}_r{sequence.revision}", cues)


def sequence_to_enhancement_plan(project: AutoClipProject, sequence: Sequence, fallback: EnhancementPlan) -> EnhancementPlan:
    plan = deepcopy(fallback)
    plan.sequence_revision = sequence.revision
    plan.graphic_items = [GraphicOverlay(
        item.id, item.content, float(item.timeline_start.seconds),
        float(item.timeline_start.seconds + item.timeline_duration.seconds), item.placement,
        item.style_preset, source_references=list(item.provenance.get("source_references", [])), enabled=item.enabled,
    ) for track in sequence.tracks if track.type == "graphics" for item in track.items if isinstance(item, GraphicItem)]
    return plan


def associate_preview(sequence: Sequence, revision: int) -> None:
    sequence.dependencies.preview_revision = revision
    if revision == sequence.revision and "preview" in sequence.dependencies.stale_dependencies:
        sequence.dependencies.stale_dependencies.remove("preview")


def associate_render(sequence: Sequence, revision: int) -> None:
    sequence.dependencies.render_revision = revision
    if revision == sequence.revision and "render" in sequence.dependencies.stale_dependencies:
        sequence.dependencies.stale_dependencies.remove("render")

