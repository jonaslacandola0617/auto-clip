from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import SCHEMA_VERSION
from .time import MediaTime, Rational


def _mt(value: MediaTime | dict[str, Any]) -> MediaTime:
    return value if isinstance(value, MediaTime) else MediaTime.from_dict(value)


@dataclass(slots=True)
class StreamInfo:
    index: int
    codec: str
    kind: str
    channels: int | None = None
    sample_rate: int | None = None


@dataclass(slots=True)
class MediaSource:
    id: str
    reference: str
    fingerprint: dict[str, Any]
    duration: MediaTime
    width: int
    height: int
    frame_rate: Rational
    time_base: Rational
    streams: list[StreamInfo]
    rotation: int = 0
    variable_frame_rate: bool = False
    vfr_fallback: str | None = None


@dataclass(slots=True)
class TranscriptWord:
    id: str
    start: MediaTime
    end: MediaTime
    text: str
    corrected_text: str | None = None


@dataclass(slots=True)
class TranscriptSegment:
    id: str
    start: MediaTime
    end: MediaTime
    text: str
    word_ids: list[str]
    corrected_text: str | None = None


@dataclass(slots=True)
class Transcript:
    revision_id: str
    language: str
    segments: list[TranscriptSegment]
    words: list[TranscriptWord]
    model: str


@dataclass(slots=True)
class ScoreDimensions:
    hook: int
    standalone_context: int
    payoff: int
    emotion: int = 0


@dataclass(slots=True)
class ClipCandidate:
    id: str
    source_start: MediaTime
    source_end: MediaTime
    title: str
    hook: str
    category: str
    reason: str
    scores: ScoreDimensions
    provider_provenance: dict[str, str]


@dataclass(slots=True)
class ReframePoint:
    at: MediaTime
    subject_x: float
    subject_y: float
    crop_x: float
    crop_y: float
    scale: float = 1.0
    confidence: float | None = None
    detected: bool = True


@dataclass(slots=True)
class ManualCropOverride:
    enabled: bool = False
    crop_x: float = 0.5
    crop_y: float = 0.5
    scale: float = 1.0


@dataclass(slots=True)
class ReframeTrack:
    id: str
    points: list[ReframePoint]
    smoothing: dict[str, Any]
    manual_override: ManualCropOverride = field(default_factory=ManualCropOverride)


@dataclass(slots=True)
class CaptionCue:
    start: MediaTime
    end: MediaTime
    text: str
    word_ids: list[str]
    editable_text_override: str | None = None


@dataclass(slots=True)
class CaptionTrack:
    id: str
    cues: list[CaptionCue]


@dataclass(slots=True)
class Marker:
    at: MediaTime
    label: str


@dataclass(slots=True)
class ApprovedClip:
    id: str
    source_id: str
    source_in: MediaTime
    source_out: MediaTime
    title: str
    reframe_track_id: str | None = None
    caption_track_id: str | None = None
    markers: list[Marker] = field(default_factory=list)


@dataclass(slots=True)
class Timeline:
    id: str
    canvas_width: int
    canvas_height: int
    frame_rate: Rational
    clips: list[ApprovedClip]


@dataclass(slots=True)
class AutoClipProject:
    id: str
    name: str
    sources: list[MediaSource]
    transcripts: list[Transcript] = field(default_factory=list)
    candidates: list[ClipCandidate] = field(default_factory=list)
    timelines: list[Timeline] = field(default_factory=list)
    reframe_tracks: list[ReframeTrack] = field(default_factory=list)
    caption_tracks: list[CaptionTrack] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoClipProject":
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {data.get('schema_version')}")
        sources = [MediaSource(
            **{**s, "duration": _mt(s["duration"]), "frame_rate": Rational.parse(s["frame_rate"]),
               "time_base": Rational.parse(s["time_base"]), "streams": [StreamInfo(**x) for x in s["streams"]]}
        ) for s in data.get("sources", [])]
        transcripts = [Transcript(
            revision_id=t["revision_id"], language=t["language"], model=t["model"],
            segments=[TranscriptSegment(**{**x, "start": _mt(x["start"]), "end": _mt(x["end"])}) for x in t["segments"]],
            words=[TranscriptWord(**{**x, "start": _mt(x["start"]), "end": _mt(x["end"])}) for x in t["words"]],
        ) for t in data.get("transcripts", [])]
        candidates = [ClipCandidate(**{
            **c, "source_start": _mt(c["source_start"]), "source_end": _mt(c["source_end"]),
            "scores": ScoreDimensions(**c["scores"]),
        }) for c in data.get("candidates", [])]
        timelines = [Timeline(
            id=t["id"], canvas_width=t["canvas_width"], canvas_height=t["canvas_height"],
            frame_rate=Rational.parse(t["frame_rate"]), clips=[ApprovedClip(**{
                **c, "source_in": _mt(c["source_in"]), "source_out": _mt(c["source_out"]),
                "markers": [Marker(**{**m, "at": _mt(m["at"])}) for m in c.get("markers", [])],
            }) for c in t["clips"]],
        ) for t in data.get("timelines", [])]
        reframes = [ReframeTrack(
            id=r["id"], smoothing=r["smoothing"], manual_override=ManualCropOverride(**r.get("manual_override", {})),
            points=[ReframePoint(**{**p, "at": _mt(p["at"])}) for p in r["points"]],
        ) for r in data.get("reframe_tracks", [])]
        captions = [CaptionTrack(
            id=c["id"], cues=[CaptionCue(**{**q, "start": _mt(q["start"]), "end": _mt(q["end"])}) for q in c["cues"]],
        ) for c in data.get("caption_tracks", [])]
        return cls(
            id=data["id"], name=data["name"], sources=sources, transcripts=transcripts,
            candidates=candidates, timelines=timelines, reframe_tracks=reframes,
            caption_tracks=captions, schema_version=data["schema_version"],
        )

