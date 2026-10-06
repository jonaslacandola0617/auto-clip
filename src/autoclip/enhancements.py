from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from .media import FFmpegService, fingerprint_file
from .models import (BrollInsert, EditSequence, EnhancementAsset, EnhancementOpportunity,
                     EnhancementPlan, GraphicOverlay, MusicBed, SoundCue, VisualEditPlan)

MAX_ENHANCEMENT_EVENTS = 3
VISUAL_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 or token.isdigit()}


def index_local_library(folders: Iterable[Path], media: FFmpegService | None = None) -> list[EnhancementAsset]:
    service = media or FFmpegService()
    assets: list[EnhancementAsset] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(item for item in folder.rglob("*") if item.is_file() and item.suffix.lower() in VISUAL_EXTENSIONS | AUDIO_EXTENSIONS):
            kind = "audio" if path.suffix.lower() in AUDIO_EXTENSIONS else ("image" if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else "video")
            metadata: dict[str, object] = {"extension": path.suffix.lower()}
            if kind != "image" and service.available:
                try:
                    probe = service.inspect(path)
                    metadata["duration"] = float(probe.get("format", {}).get("duration", 0) or 0)
                except Exception:
                    metadata["probe_warning"] = "unreadable media metadata"
            fingerprint = fingerprint_file(path)
            assets.append(EnhancementAsset(
                id=f"asset_{fingerprint['partial_sha256'][:16]}", kind=kind, reference=str(path.resolve()),
                fingerprint=fingerprint, source_metadata=metadata,
                tags=sorted(_tokens(path.stem.replace("_", " ").replace("-", " "))),
            ))
    return assets


def match_asset(concept: str, assets: list[EnhancementAsset], *, kinds: set[str] | None = None) -> tuple[EnhancementAsset | None, float]:
    wanted = _tokens(concept)
    best: tuple[EnhancementAsset | None, float] = (None, 0.0)
    for asset in assets:
        if kinds and asset.kind not in kinds:
            continue
        available = _tokens(Path(asset.reference).stem) | set(asset.tags)
        score = len(wanted & available) / max(1, len(wanted))
        if score > best[1]:
            best = (asset, score)
    return best


def plan_enhancements(sequence: EditSequence, visual_plan: VisualEditPlan, *, assets: list[EnhancementAsset] | None = None, previous: EnhancementPlan | None = None) -> EnhancementPlan:
    context = " ".join([sequence.title, *(segment.transcript_excerpt for segment in sequence.segments)])
    opportunities: list[EnhancementOpportunity] = []
    graphics: list[GraphicOverlay] = []
    source_refs = [segment.id for segment in sequence.segments]
    if {"40", "yard", "dash"}.issubset(_tokens(context)) or {"40", "yard", "race"}.issubset(_tokens(context)):
        opportunities.append(EnhancementOpportunity("op_fact_40_yard", .45, 2.6, context[:280], "clarifies the central challenge", "graphic", .95, .98))
        graphics.append(GraphicOverlay("graphic_40_yard_dash", "40-YARD DASH", .45, 2.6, "upper", "fact", "source-grounded challenge label", source_refs))
    all_assets = assets or []
    broll: list[BrollInsert] = []
    match, relevance = match_asset("40 yard race running sprint", all_assets, kinds={"video", "image"})
    if match and relevance >= .35 and len(graphics) < MAX_ENHANCEMENT_EVENTS:
        start = min(5.0, max(2.8, sequence.duration_seconds * .3))
        broll.append(BrollInsert("broll_race_context", start, min(sequence.duration_seconds, start + 2.5), match.id, reason="illustrates the race context", provenance={"source": "local_match"}, relevance=relevance))
    prior = {item.id: item.enabled for item in (previous.graphic_items if previous else [])}
    for item in graphics:
        item.enabled = prior.get(item.id, item.enabled)
    revision = previous.revision + 1 if previous else 1
    return EnhancementPlan(
        f"enhancement_{sequence.id}", sequence.id, revision, "review", all_assets, opportunities,
        broll, graphics, [], None, provenance={"planner": "phase2c-deterministic-v1"},
        generation_metadata={"density_budget": MAX_ENHANCEMENT_EVENTS, "ai_used": False},
        sequence_revision=sequence.revision, visual_plan_revision=visual_plan.revision,
    )


def validate_enhancement_plan(plan: EnhancementPlan, sequence: EditSequence) -> EnhancementPlan:
    duration = sequence.duration_seconds
    assets = {asset.id: asset for asset in plan.assets}
    warnings: list[str] = []
    enabled_count = sum(item.enabled for item in plan.broll_items + plan.graphic_items + plan.sound_cues) + int(bool(plan.music_track and plan.music_track.enabled))
    if enabled_count > MAX_ENHANCEMENT_EVENTS:
        warnings.append(f"Enhancement density exceeds the {MAX_ENHANCEMENT_EVENTS}-event budget.")
    for item in plan.broll_items:
        if not item.enabled:
            continue
        asset = assets.get(item.asset_id)
        if item.timeline_start < 0 or item.timeline_end <= item.timeline_start or item.timeline_end > duration:
            warnings.append(f"{item.id}: invalid B-roll timing.")
        if item.relevance < .35:
            warnings.append(f"{item.id}: B-roll relevance is too weak.")
        if asset is None or not Path(asset.reference).is_file():
            warnings.append(f"{item.id}: missing enhancement asset.")
    supported_text = _tokens(" ".join([sequence.title, *(segment.transcript_excerpt for segment in sequence.segments)]))
    for item in plan.graphic_items:
        if not item.enabled:
            continue
        if item.timeline_start < 0 or item.timeline_end <= item.timeline_start or item.timeline_end > duration:
            warnings.append(f"{item.id}: invalid graphic timing.")
        if not _tokens(item.text).issubset(supported_text):
            warnings.append(f"{item.id}: graphic wording is not supported by approved source text.")
    for cue in plan.sound_cues:
        if not cue.enabled:
            continue
        asset = assets.get(cue.asset_id)
        if not -30 <= cue.gain_db <= -8 or not 0 < cue.duration <= 2 or not 0 <= cue.fade_seconds <= .3:
            warnings.append(f"{cue.id}: sound cue parameters are outside safe bounds.")
        if cue.timeline_start < 0 or cue.timeline_start + cue.duration > duration:
            warnings.append(f"{cue.id}: sound cue timing is outside the sequence.")
        if asset is None or not Path(asset.reference).is_file():
            warnings.append(f"{cue.id}: missing enhancement asset.")
    if plan.music_track and plan.music_track.enabled:
        asset = assets.get(plan.music_track.asset_id)
        if not -36 <= plan.music_track.gain_db <= -16 or not -18 <= plan.music_track.ducking_db <= -6:
            warnings.append("Music gain or ducking is outside safe bounds.")
        if asset is None or not Path(asset.reference).is_file():
            warnings.append("Missing music asset.")
    plan.warnings = warnings
    plan.status = "ready" if not warnings else "needs_revision"
    return plan


def relink_asset(plan: EnhancementPlan, asset_id: str, replacement: Path) -> None:
    if not replacement.is_file():
        raise ValueError("replacement enhancement asset is unavailable")
    asset = next((item for item in plan.assets if item.id == asset_id), None)
    if asset is None:
        raise ValueError("enhancement asset was not found")
    asset.reference = str(replacement.resolve())
    asset.fingerprint = fingerprint_file(replacement)
    plan.revision += 1


def enhancement_cache_key(plan: EnhancementPlan) -> str:
    payload = {"sequence": plan.sequence_id, "sequence_revision": plan.sequence_revision,
               "visual_revision": plan.visual_plan_revision, "plan_revision": plan.revision,
               "assets": [(item.id, item.fingerprint) for item in plan.assets]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def serialize_graphics_ass(plan: EnhancementPlan, *, width: int = 1080, height: int = 1920) -> str:
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "",
             "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
             "Style: Fact,Arial,76,&H00FFFFFF,&H000000FF,&H00101010,-1,0,0,0,100,100,1,0,1,5,2,8,70,70,180,1", "", "[Events]",
             "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    def clock(value: float) -> str:
        centis = round(max(0, value) * 100); hours, rem = divmod(centis, 360000); minutes, rem = divmod(rem, 6000); seconds, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"
    for item in plan.graphic_items:
        if not item.enabled:
            continue
        alignment = {"upper": 8, "center": 5, "lower": 2}.get(item.placement, 8)
        text = item.text.replace("\n", r"\N").replace(",", r"\,").replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 1,{clock(item.timeline_start)},{clock(item.timeline_end)},Fact,,0,0,0,,{{\\an{alignment}\\fad(120,120)}}{text}")
    return "\n".join(lines) + "\n"
