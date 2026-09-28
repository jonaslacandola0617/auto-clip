from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Iterable

from .models import (
    CampaignProfile, CampaignValidation, EditSequence, EnhancementPlan, ProductionRun,
    StoryConcept, VisualEditPlan, WorkflowProfile,
)


def validate_workflow_profile(profile: WorkflowProfile) -> None:
    if not profile.name.strip():
        raise ValueError("workflow profile name is required")
    if not 1 <= profile.desired_output_count <= 20:
        raise ValueError("desired output count must be between 1 and 20")
    if profile.min_duration_seconds <= 0 or profile.max_duration_seconds < profile.min_duration_seconds:
        raise ValueError("workflow duration range is invalid")
    if profile.generation_mode not in {"concepts_only", "prepare_previews"}:
        raise ValueError("unsupported workflow generation mode")
    if profile.pacing not in {"relaxed", "balanced", "fast"}:
        raise ValueError("unsupported pacing preference")
    if profile.enhancement_policy not in {"off", "restrained", "automatic"}:
        raise ValueError("unsupported enhancement policy")


def revise_workflow_profile(profile: WorkflowProfile, **changes: object) -> WorkflowProfile:
    revised = replace(profile, **changes, revision=profile.revision + 1)
    validate_workflow_profile(revised)
    return revised


def validate_campaign_profile(profile: CampaignProfile) -> None:
    if not profile.name.strip():
        raise ValueError("campaign name is required")
    if profile.max_duration_seconds < profile.min_duration_seconds:
        raise ValueError("campaign duration range is invalid")
    if profile.target_deliverables < 1:
        raise ValueError("campaign deliverable count must be positive")


def validate_campaign_edit(
    sequence: EditSequence,
    campaign: CampaignProfile,
    enhancement: EnhancementPlan | None = None,
    *,
    output_format: str = "mp4",
) -> CampaignValidation:
    text = " ".join([sequence.title, *(item.transcript_excerpt for item in sequence.segments)]).lower()
    if enhancement:
        text += " " + " ".join(item.text for item in enhancement.graphic_items if item.enabled).lower()
    checks: list[dict[str, object]] = []

    def check(name: str, state: str, detail: str) -> None:
        checks.append({"name": name, "state": state, "detail": detail})

    duration = sequence.duration_seconds
    check("duration", "passed" if campaign.min_duration_seconds <= duration <= campaign.max_duration_seconds else "failed", f"{duration:.2f}s")
    for value, label in ((campaign.required_handle, "required_handle"), (campaign.required_cta, "required_cta")):
        if value:
            check(label, "passed" if value.lower() in text else "failed", value)
    for required in campaign.required_text:
        check("required_text", "passed" if required.lower() in text else "failed", required)
    for forbidden in campaign.forbidden_terms:
        check("forbidden_term", "failed" if forbidden.lower() in text else "passed", forbidden)
    if campaign.watermark_required:
        check("watermark", "needs_review", "Watermark presence requires visual review.")
    check("output_format", "passed" if output_format.lower() == "mp4" else "needs_review", output_format)
    states = {str(item["state"]) for item in checks}
    state = "failed_checks" if "failed" in states else "needs_review" if "needs_review" in states else "passed_checks"
    warnings = [str(item["detail"]) for item in checks if item["state"] != "passed"]
    return CampaignValidation(state, checks, warnings)


def source_overlap(left: EditSequence, right: EditSequence) -> float:
    left_ranges = [(float(item.source_in.seconds), float(item.source_out.seconds)) for item in left.segments]
    right_ranges = [(float(item.source_in.seconds), float(item.source_out.seconds)) for item in right.segments]
    intersection = sum(max(0.0, min(a1, b1) - max(a0, b0)) for a0, a1 in left_ranges for b0, b1 in right_ranges)
    left_duration = sum(end - start for start, end in left_ranges)
    right_duration = sum(end - start for start, end in right_ranges)
    return min(1.0, intersection / max(0.001, min(left_duration, right_duration)))


def _tokens(value: str) -> set[str]:
    return {item for item in re.findall(r"[a-z0-9]+", value.lower()) if len(item) > 2}


def topic_overlap(left: StoryConcept | None, right: StoryConcept | None) -> float:
    if not left or not right:
        return 0.0
    a = _tokens(" ".join((left.central_topic, left.premise, left.hook)))
    b = _tokens(" ".join((right.central_topic, right.premise, right.hook)))
    return len(a & b) / max(1, len(a | b))


def select_diverse_edits(
    sequences: Iterable[EditSequence],
    stories: dict[str, StoryConcept],
    profile: WorkflowProfile,
) -> tuple[list[EditSequence], list[str]]:
    validate_workflow_profile(profile)
    qualified = [item for item in sequences if (
        item.status == "ready" and item.integrity.status == "passed" and item.editorial_review
        and item.editorial_review.accepted and profile.min_duration_seconds <= item.duration_seconds <= profile.max_duration_seconds
    )]
    qualified.sort(key=lambda item: (-(item.editorial_review.coherence_score if item.editorial_review else 0), item.id))
    selected: list[EditSequence] = []
    suppressed: list[str] = []
    for candidate in qualified:
        duplicate = False
        candidate_moments = {segment.moment_id for segment in candidate.segments}
        for chosen in selected:
            chosen_moments = {segment.moment_id for segment in chosen.segments}
            moment_overlap = len(candidate_moments & chosen_moments) / max(1, min(len(candidate_moments), len(chosen_moments)))
            if moment_overlap >= .5 or source_overlap(candidate, chosen) >= .65 or topic_overlap(stories.get(candidate.story_concept_id), stories.get(chosen.story_concept_id)) >= .72:
                duplicate = True
                break
        if duplicate:
            suppressed.append(candidate.id)
            continue
        selected.append(candidate)
        if len(selected) >= profile.desired_output_count:
            break
    return selected, suppressed


def apply_profile_policy(profile: WorkflowProfile, visual: VisualEditPlan | None, enhancement: EnhancementPlan | None) -> None:
    if visual:
        visual.framing_mode = "fixed" if profile.framing == "fixed" else "auto"
        visual.visual_emphasis = "off" if profile.visual_emphasis == "off" else "automatic"
        visual.caption_preset = profile.caption_preset
        visual.revision += 1
    if enhancement:
        if profile.enhancement_policy == "off":
            for item in [*enhancement.broll_items, *enhancement.graphic_items, *enhancement.sound_cues]:
                item.enabled = False
            if enhancement.music_track:
                enhancement.music_track.enabled = False
        if profile.music_policy == "off" and enhancement.music_track:
            enhancement.music_track.enabled = False
        enhancement.revision += 1


def sanitize_filename(value: str, *, fallback: str = "autoclip", max_length: int = 120) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    cleaned = re.sub(r"-+", "-", cleaned)[:max_length].rstrip(". ")
    if not cleaned or cleaned.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        return fallback
    return cleaned


def render_filename(template: str, *, project: str, campaign: str, creator: str, index: int, title: str) -> str:
    values = {"project": project, "campaign": campaign or project, "creator": creator or project, "index": f"{index:02d}", "title": title}
    try:
        return sanitize_filename(template.format_map(values))
    except (KeyError, ValueError):
        return sanitize_filename(f"{project}-{index:02d}-{title}")


def collision_safe_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{sanitize_filename(stem)}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{sanitize_filename(stem)}-{index}{suffix}"
        index += 1
    return candidate


def write_manifest(path: Path, run: ProductionRun, project_name: str, deliverables: list[dict[str, object]]) -> None:
    payload = {
        "version": "phase3a-v1",
        "production_run_id": run.id,
        "source_project": project_name,
        "workflow_profile": {"id": run.workflow_profile_id, "revision": run.workflow_profile_revision},
        "campaign_profile": ({"id": run.campaign_profile_id, "revision": run.campaign_profile_revision} if run.campaign_profile_id else None),
        "deliverables": deliverables,
        "warnings": run.warnings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def production_summary(run: ProductionRun) -> dict[str, int]:
    states = [item.get("state") for item in run.edit_states.values()]
    return {
        "requested": run.requested_count,
        "produced": len(run.generated_edit_ids),
        "approved": len(run.accepted_edit_ids),
        "rendered": sum(state == "rendered" for state in states),
        "failed": sum(state == "failed" for state in states),
        "needs_review": sum(item.state == "needs_review" for item in run.campaign_validations.values()),
    }
