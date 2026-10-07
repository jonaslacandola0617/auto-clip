from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, TypeAlias

from .time import MediaTime, Rational


EDITOR_SCHEMA_VERSION = "2.2"


def _mt(value: MediaTime | dict[str, Any]) -> MediaTime:
    return value if isinstance(value, MediaTime) else MediaTime.from_dict(value)


@dataclass(slots=True)
class MediaAsset:
    id: str
    reference: str
    filename: str
    media_type: str
    duration: MediaTime
    fingerprint: dict[str, Any]
    width: int | None = None
    height: int | None = None
    frame_rate: Rational | None = None
    time_base: Rational | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    availability: str = "available"
    provenance: dict[str, Any] = field(default_factory=dict)

    def relink(self, reference: str, fingerprint: dict[str, Any]) -> None:
        identity_keys = ("size", "partial_sha256")
        comparable = all(key in self.fingerprint and key in fingerprint for key in identity_keys)
        matches = all(self.fingerprint[key] == fingerprint[key] for key in identity_keys) if comparable else fingerprint == self.fingerprint
        if self.fingerprint and not matches:
            raise ValueError("relinked media fingerprint does not match the canonical asset")
        self.reference = reference
        self.filename = Path(reference).name
        self.availability = "available"


@dataclass(slots=True)
class MediaLibrary:
    assets: list[MediaAsset] = field(default_factory=list)
    version: str = EDITOR_SCHEMA_VERSION

    def asset(self, asset_id: str) -> MediaAsset:
        try:
            return next(asset for asset in self.assets if asset.id == asset_id)
        except StopIteration as exc:
            raise ValueError(f"unknown media asset: {asset_id}") from exc

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaLibrary":
        return cls(
            version=data.get("version", EDITOR_SCHEMA_VERSION),
            assets=[MediaAsset(**{
                **asset,
                "duration": _mt(asset["duration"]),
                "frame_rate": Rational.parse(asset["frame_rate"]) if asset.get("frame_rate") else None,
                "time_base": Rational.parse(asset["time_base"]) if asset.get("time_base") else None,
            }) for asset in data.get("assets", [])],
        )


@dataclass(slots=True)
class MediaClip:
    id: str
    media_asset_id: str
    source_in: MediaTime
    source_out: MediaTime
    timeline_start: MediaTime
    timeline_duration: MediaTime
    track_id: str
    enabled: bool = True
    transform_reference: str | None = None
    linked_item_id: str | None = None
    speed: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: Literal["media_clip"] = "media_clip"


@dataclass(slots=True)
class AudioClip:
    id: str
    media_asset_id: str
    source_in: MediaTime
    source_out: MediaTime
    timeline_start: MediaTime
    timeline_duration: MediaTime
    track_id: str
    role: str = "dialogue"
    enabled: bool = True
    linked_item_id: str | None = None
    gain_db: float = 0.0
    ducking: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: Literal["audio_clip"] = "audio_clip"


@dataclass(slots=True)
class CaptionItem:
    id: str
    timeline_start: MediaTime
    timeline_duration: MediaTime
    track_id: str
    text: str
    word_timings: list[dict[str, Any]] = field(default_factory=list)
    style_preset: str = "clean"
    position: str = "lower"
    emphasis: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: Literal["caption_item"] = "caption_item"


@dataclass(slots=True)
class GraphicItem:
    id: str
    timeline_start: MediaTime
    timeline_duration: MediaTime
    track_id: str
    content: str
    style_preset: str = "fact"
    placement: str = "upper"
    enabled: bool = True
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: Literal["graphic_item"] = "graphic_item"


TimelineItem: TypeAlias = MediaClip | AudioClip | CaptionItem | GraphicItem


def timeline_item_from_dict(data: dict[str, Any]) -> TimelineItem:
    value = dict(data)
    kind = value.pop("kind")
    for key in ("source_in", "source_out", "timeline_start", "timeline_duration"):
        if key in value:
            value[key] = _mt(value[key])
    types = {
        "media_clip": MediaClip,
        "audio_clip": AudioClip,
        "caption_item": CaptionItem,
        "graphic_item": GraphicItem,
    }
    try:
        return types[kind](**value)
    except KeyError as exc:
        raise ValueError(f"unsupported timeline item kind: {kind}") from exc


@dataclass(slots=True)
class Track:
    id: str
    type: str
    name: str
    order: int
    items: list[TimelineItem] = field(default_factory=list)
    enabled: bool = True
    locked: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SequenceDependencyState:
    captions_revision: int = 1
    visual_revision: int = 1
    preview_revision: int | None = None
    render_revision: int | None = None
    stale_dependencies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Sequence:
    id: str
    name: str
    canvas_width: int
    canvas_height: int
    frame_rate: Rational
    time_base: Rational
    tracks: list[Track] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    ai_generation_metadata: dict[str, Any] = field(default_factory=dict)
    visual_plan_id: str | None = None
    enhancement_plan_id: str | None = None
    revision: int = 1
    saved_revision: int = 1
    lifecycle: str = "created_manual"
    dependencies: SequenceDependencyState = field(default_factory=SequenceDependencyState)
    version: str = EDITOR_SCHEMA_VERSION

    @property
    def duration(self) -> MediaTime:
        end = max(
            (item.timeline_start.seconds + item.timeline_duration.seconds for track in self.tracks for item in track.items if item.enabled),
            default=Fraction(0),
        )
        return MediaTime.from_seconds(end, self.time_base)

    @property
    def dirty(self) -> bool:
        return self.revision != self.saved_revision

    def track(self, track_id: str) -> Track:
        try:
            return next(track for track in self.tracks if track.id == track_id)
        except StopIteration as exc:
            raise ValueError(f"unknown track: {track_id}") from exc

    def item(self, item_id: str) -> tuple[Track, TimelineItem]:
        for track in self.tracks:
            for item in track.items:
                if item.id == item_id:
                    return track, item
        raise ValueError(f"unknown timeline item: {item_id}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sequence":
        return cls(**{
            **data,
            "frame_rate": Rational.parse(data["frame_rate"]),
            "time_base": Rational.parse(data["time_base"]),
            "tracks": [Track(**{**track, "items": [timeline_item_from_dict(item) for item in track.get("items", [])]}) for track in data.get("tracks", [])],
            "dependencies": SequenceDependencyState(**data.get("dependencies", {})),
        })

