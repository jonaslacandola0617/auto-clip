from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import SCHEMA_VERSION
from .editor_domain import MediaLibrary, Sequence
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
class DurationContract:
    minimum: float
    target: float
    preferred_maximum: float
    hard_maximum: float

    def __post_init__(self) -> None:
        values = (self.minimum, self.target, self.preferred_maximum, self.hard_maximum)
        if self.minimum <= 0 or tuple(sorted(values)) != values:
            raise ValueError("duration contract must satisfy 0 < minimum <= target <= preferred maximum <= hard maximum")


@dataclass(slots=True)
class EditorialQuality:
    hook: int = 0
    curiosity: int = 0
    conflict_tension: int = 0
    payoff: int = 0
    standalone_clarity: int = 0
    novelty: int = 0
    energy: int = 0
    dead_space_density: int = 0
    entertainment: int = 0
    policy_version: str = "v2.1-quality-v1"


@dataclass(slots=True)
class CandidateWindow:
    id: str
    source_id: str
    source_start: MediaTime
    source_end: MediaTime
    transcript_segment_ids: list[str]
    transcript_word_ids: list[str]
    text_summary: str
    local_signals: dict[str, float] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    preprocessing_version: str = "v2.1-window-v1"

    @property
    def duration_seconds(self) -> float:
        return float(self.source_end.seconds - self.source_start.seconds)


@dataclass(slots=True)
class AnalysisStageMetric:
    stage: str
    duration_seconds: float
    ai_request_count: int = 0
    provider_retries: int = 0
    input_count: int = 0
    output_count: int = 0
    approximate_context_words: int = 0
    cache_hit: bool = False


@dataclass(slots=True)
class AnalysisRunMetrics:
    id: str
    pipeline_version: str
    transcript_revision: str
    started_at: str
    completed_at: str | None = None
    total_duration_seconds: float = 0.0
    time_to_first_candidate_seconds: float | None = None
    ai_request_count: int = 0
    provider_retries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    candidate_counts: dict[str, int] = field(default_factory=dict)
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    provider_errors: list[str] = field(default_factory=list)
    stages: list[AnalysisStageMetric] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    editorial_quality: EditorialQuality | None = None
    candidate_window_id: str | None = None
    payoff: str = ""
    story_structure: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Moment:
    id: str
    source_id: str
    source_in: MediaTime
    source_out: MediaTime
    transcript_segment_ids: list[str]
    transcript_word_ids: list[str]
    summary: str
    types: list[str]
    speakers: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)
    characteristics: dict[str, float] = field(default_factory=dict)
    local_metadata: dict[str, Any] = field(default_factory=dict)
    provider_provenance: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    reasoning: str = ""


@dataclass(slots=True)
class StoryConcept:
    id: str
    title: str
    premise: str
    hook: str
    context: str
    development: str
    payoff: str
    moment_ids: list[str]
    target_duration_seconds: float
    explanation: str
    coherence: dict[str, Any] = field(default_factory=dict)
    integrity_considerations: list[str] = field(default_factory=list)
    status: str = "draft"
    central_topic: str = ""
    viewer_premise: str = ""
    moment_rationales: dict[str, str] = field(default_factory=dict)
    understandable_without_source: bool = False


@dataclass(slots=True)
class EditorialAction:
    id: str
    type: str
    timeline_start: float
    timeline_end: float
    parameters: dict[str, Any]
    reason: str
    enabled: bool = True
    provenance: dict[str, str] = field(default_factory=dict)
    revision: int = 1


@dataclass(slots=True)
class EditSegment:
    id: str
    moment_id: str
    source_id: str
    source_in: MediaTime
    source_out: MediaTime
    timeline_start: float
    purpose: str
    transcript_excerpt: str
    order: int
    action_ids: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class EditorialIntegrityResult:
    status: str
    checks: list[dict[str, Any]]
    evidence_references: list[str]
    warnings: list[str] = field(default_factory=list)
    required_review: bool = False
    validator_version: str = "phase2a-v1"


@dataclass(slots=True)
class EditorialReviewResult:
    topic: str
    viewer_premise: str
    hook_present: bool
    self_contained: bool
    payoff_present: bool
    coherence_score: float
    segment_roles: list[dict[str, Any]]
    relevant_entities: list[str] = field(default_factory=list)
    central_tension: str = ""
    resolution: str = ""
    problems: list[str] = field(default_factory=list)
    accepted: bool = False
    rebuild_attempts: int = 0
    reviewer_version: str = "phase2a1-editorial-v1"


@dataclass(slots=True)
class EditSequence:
    id: str
    story_concept_id: str
    title: str
    source_id: str
    segments: list[EditSegment]
    actions: list[EditorialAction]
    integrity: EditorialIntegrityResult
    canvas_width: int = 1080
    canvas_height: int = 1920
    frame_rate: Rational = field(default_factory=lambda: Rational(30000, 1001))
    revision: int = 1
    status: str = "review_required"
    preview_path: str | None = None
    render_path: str | None = None
    editorial_review: EditorialReviewResult | None = None
    editorial_quality: EditorialQuality | None = None

    @property
    def duration_seconds(self) -> float:
        return sum(float(segment.source_out.seconds - segment.source_in.seconds) for segment in self.segments)


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
class BoundingBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(slots=True)
class VisualDetection:
    subject_id: str
    kind: str
    box: BoundingBox
    confidence: float


@dataclass(slots=True)
class VisualObservation:
    id: str
    source_id: str
    at: MediaTime
    scene_id: str
    detections: list[VisualDetection]


@dataclass(slots=True)
class SubjectTrackPoint:
    at: MediaTime
    box: BoundingBox
    confidence: float
    detected: bool = True


@dataclass(slots=True)
class SubjectTrack:
    id: str
    source_id: str
    points: list[SubjectTrackPoint]
    confidence: float
    lost_samples: int = 0


@dataclass(slots=True)
class ReframeDecision:
    id: str
    edit_segment_id: str
    timeline_start: float
    timeline_end: float
    layout: str
    crop_x: float = 0.5
    crop_y: float = 0.5
    scale: float = 1.0
    subject_ids: list[str] = field(default_factory=list)
    panels: list[BoundingBox] = field(default_factory=list)
    reason: str = "stable composition"


@dataclass(slots=True)
class VisualShot:
    id: str
    edit_segment_id: str
    source_in: MediaTime
    source_out: MediaTime
    timeline_start: float
    reframe_decision_id: str
    action_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CaptionLayout:
    edit_segment_id: str
    position: str = "lower"
    margin_vertical: int = 110
    reason: str = "default safe region"


@dataclass(slots=True)
class VisualEditPlan:
    id: str
    sequence_id: str
    shots: list[VisualShot]
    observations: list[VisualObservation]
    subject_tracks: list[SubjectTrack]
    reframe_decisions: list[ReframeDecision]
    caption_layout: list[CaptionLayout]
    visual_actions: list[EditorialAction]
    warnings: list[str] = field(default_factory=list)
    version: str = "phase2b-v1"
    revision: int = 1
    sequence_revision: int = 1
    detector_version: str = "mediapipe-blazeface-short-range-v1"
    detector_config: dict[str, Any] = field(default_factory=lambda: {"sample_interval": .5, "confidence": .55})
    framing_mode: str = "auto"
    visual_emphasis: str = "automatic"
    caption_preset: str = "word_highlight"
    caption_position: str = "auto"


@dataclass(slots=True)
class EnhancementAsset:
    id: str
    kind: str
    reference: str
    fingerprint: dict[str, Any]
    provider: str = "local_library"
    provider_asset_id: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    license_metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EnhancementOpportunity:
    id: str
    timeline_start: float
    timeline_end: float
    context: str
    reason: str
    type: str
    priority: float
    confidence: float


@dataclass(slots=True)
class BrollInsert:
    id: str
    timeline_start: float
    timeline_end: float
    asset_id: str
    fit: str = "cover"
    motion: str = "none"
    reason: str = "supporting visual context"
    provenance: dict[str, str] = field(default_factory=dict)
    relevance: float = 0.0
    enabled: bool = True


@dataclass(slots=True)
class GraphicOverlay:
    id: str
    text: str
    timeline_start: float
    timeline_end: float
    placement: str = "upper"
    style_preset: str = "fact"
    reason: str = "source-grounded emphasis"
    source_references: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass(slots=True)
class SoundCue:
    id: str
    asset_id: str
    timeline_start: float
    duration: float
    gain_db: float = -18.0
    fade_seconds: float = 0.08
    purpose: str = "restrained emphasis"
    enabled: bool = True


@dataclass(slots=True)
class MusicBed:
    asset_id: str
    gain_db: float = -24.0
    ducking_db: float = -10.0
    fade_in: float = 0.5
    fade_out: float = 0.8
    loop: bool = True
    reason: str = "optional background bed"
    enabled: bool = False


@dataclass(slots=True)
class EnhancementPlan:
    id: str
    sequence_id: str
    revision: int
    status: str
    assets: list[EnhancementAsset] = field(default_factory=list)
    opportunities: list[EnhancementOpportunity] = field(default_factory=list)
    broll_items: list[BrollInsert] = field(default_factory=list)
    graphic_items: list[GraphicOverlay] = field(default_factory=list)
    sound_cues: list[SoundCue] = field(default_factory=list)
    music_track: MusicBed | None = None
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    generation_metadata: dict[str, Any] = field(default_factory=dict)
    user_overrides: dict[str, Any] = field(default_factory=dict)
    sequence_revision: int = 1
    visual_plan_revision: int = 1
    version: str = "phase2c-v1"


@dataclass(slots=True)
class CaptionCue:
    start: MediaTime
    end: MediaTime
    text: str
    word_ids: list[str]
    editable_text_override: str | None = None
    edit_segment_id: str | None = None
    word_timings: list[dict[str, Any]] = field(default_factory=list)
    position: str = "lower"


@dataclass(slots=True)
class CaptionTrack:
    id: str
    cues: list[CaptionCue]


@dataclass(slots=True)
class ProjectClip:
    id: str
    source_id: str
    source_in: MediaTime
    source_out: MediaTime
    title: str
    source: str = "manual"
    candidate_id: str | None = None
    selected: bool = False
    framing_mode: str = "auto"
    manual_crop: ManualCropOverride = field(default_factory=ManualCropOverride)
    captions_enabled: bool = True
    caption_preset: str = "clean"
    reframe_track_id: str | None = None
    caption_track_id: str | None = None
    render_path: str | None = None
    revision: int = 1


@dataclass(slots=True)
class OutputArtifact:
    id: str
    kind: str
    path: str
    clip_id: str | None = None
    edit_sequence_id: str | None = None
    sequence_revision: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowProfile:
    id: str
    name: str
    revision: int = 1
    generation_mode: str = "concepts_only"
    target_platform: str = "shorts"
    min_duration_seconds: float = 20.0
    max_duration_seconds: float = 45.0
    target_duration_seconds: float | None = 30.0
    preferred_max_duration_seconds: float | None = None
    hard_max_duration_seconds: float | None = None
    analysis_mode: str = "balanced"
    desired_output_count: int = 3
    pacing: str = "balanced"
    hook_priority: str = "strong"
    story_style: str = "self-contained"
    framing: str = "automatic"
    visual_emphasis: str = "restrained"
    caption_preset: str = "word_highlight"
    enhancement_policy: str = "restrained"
    music_policy: str = "off"
    export_defaults: list[str] = field(default_factory=lambda: ["mp4", "srt"])
    campaign_profile_id: str | None = None
    version: str = "v2.1-profile-v1"

    @property
    def duration_contract(self) -> DurationContract:
        maximum = float(self.max_duration_seconds)
        target = float(self.target_duration_seconds if self.target_duration_seconds is not None else maximum)
        preferred = float(self.preferred_max_duration_seconds if self.preferred_max_duration_seconds is not None else maximum)
        hard = float(self.hard_max_duration_seconds if self.hard_max_duration_seconds is not None else maximum)
        target = max(float(self.min_duration_seconds), min(target, hard))
        preferred = max(target, min(preferred, hard))
        return DurationContract(float(self.min_duration_seconds), target, preferred, hard)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkflowProfile":
        migrated = dict(value)
        legacy_maximum = float(migrated.get("max_duration_seconds", 45.0))
        if "target_duration_seconds" not in migrated:
            migrated["target_duration_seconds"] = legacy_maximum
        if "preferred_max_duration_seconds" not in migrated:
            migrated["preferred_max_duration_seconds"] = legacy_maximum
        if "hard_max_duration_seconds" not in migrated:
            migrated["hard_max_duration_seconds"] = legacy_maximum
        if "analysis_mode" not in migrated:
            migrated["analysis_mode"] = "balanced"
        return cls(**migrated)


@dataclass(slots=True)
class CampaignProfile:
    id: str
    name: str
    revision: int = 1
    creator: str = ""
    target_platform: str = "shorts"
    min_duration_seconds: float = 0.0
    max_duration_seconds: float = 60.0
    required_handle: str = ""
    required_cta: str = ""
    required_text: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    watermark_required: bool = False
    forbidden_terms: list[str] = field(default_factory=list)
    content_notes: str = ""
    target_deliverables: int = 1
    export_naming: str = "{campaign}-{index}-{title}"
    version: str = "phase3a-v1"


@dataclass(slots=True)
class CampaignValidation:
    state: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validator_version: str = "phase3a-campaign-v1"


@dataclass(slots=True)
class ProductionRun:
    id: str
    workflow_profile_id: str
    workflow_profile_revision: int
    requested_count: int
    created_at: str
    campaign_profile_id: str | None = None
    campaign_profile_revision: int | None = None
    workflow_profile_snapshot: dict[str, Any] = field(default_factory=dict)
    campaign_profile_snapshot: dict[str, Any] = field(default_factory=dict)
    generated_edit_ids: list[str] = field(default_factory=list)
    accepted_edit_ids: list[str] = field(default_factory=list)
    rejected_edit_ids: list[str] = field(default_factory=list)
    selected_edit_ids: list[str] = field(default_factory=list)
    edit_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    campaign_validations: dict[str, CampaignValidation] = field(default_factory=dict)
    status: str = "draft"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    output_package_path: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    version: str = "phase3a-v1"


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
    clips: list[ProjectClip] = field(default_factory=list)
    outputs: list[OutputArtifact] = field(default_factory=list)
    moments: list[Moment] = field(default_factory=list)
    story_concepts: list[StoryConcept] = field(default_factory=list)
    edit_sequences: list[EditSequence] = field(default_factory=list)
    visual_edit_plans: list[VisualEditPlan] = field(default_factory=list)
    enhancement_plans: list[EnhancementPlan] = field(default_factory=list)
    workflow_profiles: list[WorkflowProfile] = field(default_factory=list)
    campaign_profiles: list[CampaignProfile] = field(default_factory=list)
    production_runs: list[ProductionRun] = field(default_factory=list)
    candidate_windows: list[CandidateWindow] = field(default_factory=list)
    analysis_runs: list[AnalysisRunMetrics] = field(default_factory=list)
    analysis_revision: str | None = None
    media_library: MediaLibrary = field(default_factory=MediaLibrary)
    sequences: list[Sequence] = field(default_factory=list)
    active_sequence_id: str | None = None
    editor_revision: int = 0
    legacy_compatibility: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoClipProject":
        schema_version = data.get("schema_version", "0.2-phase0")
        if schema_version not in {"0.2-phase0", "0.3-phase2a", "0.3-phase2a1", "0.3-phase2b", "0.3-phase2c", "0.4-phase3a", "2.0", "2.1", SCHEMA_VERSION}:
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
            "editorial_quality": EditorialQuality(**c["editorial_quality"]) if c.get("editorial_quality") else None,
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
        clips = [ProjectClip(**{
            **c, "source_in": _mt(c["source_in"]), "source_out": _mt(c["source_out"]),
            "manual_crop": ManualCropOverride(**c.get("manual_crop", {})),
        }) for c in data.get("clips", [])]
        outputs = [OutputArtifact(**o) for o in data.get("outputs", [])]
        moments = [Moment(**{
            **m, "source_in": _mt(m["source_in"]), "source_out": _mt(m["source_out"]),
        }) for m in data.get("moments", [])]
        stories = [StoryConcept(**story) for story in data.get("story_concepts", [])]
        sequences = [EditSequence(
            **{**sequence,
               "frame_rate": Rational.parse(sequence["frame_rate"]),
               "segments": [EditSegment(**{**segment, "source_in": _mt(segment["source_in"]), "source_out": _mt(segment["source_out"])}) for segment in sequence.get("segments", [])],
               "actions": [EditorialAction(**action) for action in sequence.get("actions", [])],
               "integrity": EditorialIntegrityResult(**sequence["integrity"]),
               "editorial_review": EditorialReviewResult(**sequence["editorial_review"]) if sequence.get("editorial_review") else None,
               "editorial_quality": EditorialQuality(**sequence["editorial_quality"]) if sequence.get("editorial_quality") else None},
        ) for sequence in data.get("edit_sequences", [])]
        visual_plans = [VisualEditPlan(
            **{**plan,
               "shots": [VisualShot(**{**shot, "source_in": _mt(shot["source_in"]), "source_out": _mt(shot["source_out"])}) for shot in plan.get("shots", [])],
               "observations": [VisualObservation(**{**observation, "at": _mt(observation["at"]), "detections": [VisualDetection(**{**detection, "box": BoundingBox(**detection["box"])}) for detection in observation.get("detections", [])]}) for observation in plan.get("observations", [])],
               "subject_tracks": [SubjectTrack(**{**track, "points": [SubjectTrackPoint(**{**point, "at": _mt(point["at"]), "box": BoundingBox(**point["box"])}) for point in track.get("points", [])]}) for track in plan.get("subject_tracks", [])],
               "reframe_decisions": [ReframeDecision(**{**decision, "panels": [BoundingBox(**box) for box in decision.get("panels", [])]}) for decision in plan.get("reframe_decisions", [])],
               "caption_layout": [CaptionLayout(**layout) for layout in plan.get("caption_layout", [])],
               "visual_actions": [EditorialAction(**action) for action in plan.get("visual_actions", [])]},
        ) for plan in data.get("visual_edit_plans", [])]
        enhancement_plans = [EnhancementPlan(
            **{**plan,
               "assets": [EnhancementAsset(**asset) for asset in plan.get("assets", [])],
               "opportunities": [EnhancementOpportunity(**item) for item in plan.get("opportunities", [])],
               "broll_items": [BrollInsert(**item) for item in plan.get("broll_items", [])],
               "graphic_items": [GraphicOverlay(**item) for item in plan.get("graphic_items", [])],
               "sound_cues": [SoundCue(**item) for item in plan.get("sound_cues", [])],
               "music_track": MusicBed(**plan["music_track"]) if plan.get("music_track") else None},
        ) for plan in data.get("enhancement_plans", [])]
        workflow_profiles = [WorkflowProfile.from_dict(profile) for profile in data.get("workflow_profiles", [])]
        campaign_profiles = [CampaignProfile(**profile) for profile in data.get("campaign_profiles", [])]
        production_runs = [ProductionRun(**{
            **run,
            "campaign_validations": {key: CampaignValidation(**value) for key, value in run.get("campaign_validations", {}).items()},
        }) for run in data.get("production_runs", [])]
        candidate_windows = [CandidateWindow(**{
            **window, "source_start": _mt(window["source_start"]), "source_end": _mt(window["source_end"]),
        }) for window in data.get("candidate_windows", [])]
        analysis_runs = [AnalysisRunMetrics(**{
            **run, "stages": [AnalysisStageMetric(**stage) for stage in run.get("stages", [])],
        }) for run in data.get("analysis_runs", [])]
        media_library = MediaLibrary.from_dict(data.get("media_library", {}))
        editor_sequences = [Sequence.from_dict(sequence) for sequence in data.get("sequences", [])]
        return cls(
            id=data["id"], name=data["name"], sources=sources, transcripts=transcripts,
            candidates=candidates, timelines=timelines, reframe_tracks=reframes,
            caption_tracks=captions, clips=clips, outputs=outputs, moments=moments,
            story_concepts=stories, edit_sequences=sequences, visual_edit_plans=visual_plans,
            enhancement_plans=enhancement_plans, workflow_profiles=workflow_profiles,
            campaign_profiles=campaign_profiles, production_runs=production_runs,
            candidate_windows=candidate_windows, analysis_runs=analysis_runs,
            analysis_revision=data.get("analysis_revision"), media_library=media_library,
            sequences=editor_sequences, active_sequence_id=data.get("active_sequence_id"),
            editor_revision=int(data.get("editor_revision", 0)),
            legacy_compatibility=dict(data.get("legacy_compatibility", {})), schema_version=SCHEMA_VERSION,
        )
