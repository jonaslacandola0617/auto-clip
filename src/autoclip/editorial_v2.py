from __future__ import annotations

import re
import time
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
from typing import Any, Callable, Iterable, TypeVar

from .models import (
    AnalysisRunMetrics, AnalysisStageMetric, CandidateWindow, ClipCandidate,
    DurationContract, EditorialQuality, EditSequence, MediaSource, ScoreDimensions,
    Transcript, WorkflowProfile,
)
from .time import MediaTime


PREPROCESSING_VERSION = "v2.1-window-v1"
QUALITY_POLICY_VERSION = "v2.1-quality-v1"
PROMPT_SCHEMA_VERSION = "v2.1-editorial-schema-v2"

_HOOK_CUES = {"how", "why", "never", "secret", "actually", "challenge", "biggest", "best", "worst"}
_PAYOFF_CUES = {"because", "therefore", "result", "answer", "won", "lost", "finally", "so"}
_ENERGY_CUES = {"wow", "crazy", "amazing", "race", "challenge", "beat", "win", "no", "yes"}
_FILLER = {"um", "uh", "like", "you know", "i mean", "okay so", "hello everyone", "hey guys"}

QUALITY_WEIGHTS: dict[str, dict[str, float]] = {
    "high_energy": {"hook": .20, "curiosity": .10, "conflict_tension": .14, "payoff": .15, "standalone_clarity": .08, "novelty": .07, "energy": .12, "dead_space_density": -.06, "entertainment": .20},
    "educational": {"hook": .13, "curiosity": .11, "conflict_tension": .04, "payoff": .15, "standalone_clarity": .21, "novelty": .16, "energy": .05, "dead_space_density": -.07, "entertainment": .12},
    "conversational": {"hook": .13, "curiosity": .11, "conflict_tension": .09, "payoff": .15, "standalone_clarity": .18, "novelty": .10, "energy": .07, "dead_space_density": -.06, "entertainment": .13},
    "default": {"hook": .16, "curiosity": .11, "conflict_tension": .09, "payoff": .16, "standalone_clarity": .16, "novelty": .10, "energy": .08, "dead_space_density": -.06, "entertainment": .16},
}


def duration_contract_for(profile: WorkflowProfile | None) -> DurationContract:
    return profile.duration_contract if profile else DurationContract(12, 20, 25, 28)


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", value.lower())


def create_candidate_windows(
    source: MediaSource,
    transcript: Transcript,
    contract: DurationContract,
    *,
    max_windows: int = 120,
) -> list[CandidateWindow]:
    """Build deterministic, overlapping, source-grounded windows without an LLM."""
    segments = sorted(transcript.segments, key=lambda item: item.start.seconds)
    if not segments:
        return []
    windows: list[CandidateWindow] = []
    stride = max(1, int(round(max(1.0, contract.target / 8))))
    for start_index in range(0, len(segments), stride):
        selected = []
        for segment in segments[start_index:]:
            if not selected and float(segment.end.seconds - segment.start.seconds) > contract.hard_maximum:
                break
            proposed = float(segment.end.seconds - segments[start_index].start.seconds)
            if proposed > contract.hard_maximum:
                break
            selected.append(segment)
            text = segment.corrected_text or segment.text
            if proposed >= contract.target and re.search(r"[.!?][\"']?$", text.strip()):
                break
        if not selected:
            continue
        duration = float(selected[-1].end.seconds - selected[0].start.seconds)
        if duration < contract.minimum:
            continue
        text = " ".join(item.corrected_text or item.text for item in selected).strip()
        tokens = _words(text)
        if not tokens:
            continue
        word_ids = [word_id for item in selected for word_id in item.word_ids]
        filler_count = sum(1 for cue in _FILLER if cue in text.lower())
        pause_seconds = sum(max(0.0, float(right.start.seconds - left.end.seconds)) for left, right in zip(selected, selected[1:]))
        lexical_hook = len(set(tokens[: min(35, len(tokens))]) & _HOOK_CUES)
        lexical_payoff = len(set(tokens[-min(45, len(tokens)):]) & _PAYOFF_CUES)
        energy = len(set(tokens) & _ENERGY_CUES)
        local_score = 50 + lexical_hook * 8 + lexical_payoff * 7 + energy * 4 - filler_count * 5 - min(20, pause_seconds * 3)
        windows.append(CandidateWindow(
            id=f"window_{start_index + 1}", source_id=source.id,
            source_start=selected[0].start, source_end=selected[-1].end,
            transcript_segment_ids=[item.id for item in selected], transcript_word_ids=word_ids,
            text_summary=" ".join(tokens[:24]),
            local_signals={
                "local_score": round(max(0.0, min(100.0, local_score)), 3),
                "speech_density": round(min(1.0, len(tokens) / max(1.0, duration * 2.7)), 4),
                "dead_space_density": round(min(1.0, pause_seconds / max(.001, duration)), 4),
                "hook_cues": float(lexical_hook), "payoff_cues": float(lexical_payoff), "energy_cues": float(energy),
            },
            provenance={"transcript_revision": transcript.revision_id, "source_id": source.id},
        ))
    # Stable sort makes preprocessing and cache identities repeatable.
    windows.sort(key=lambda item: (-item.local_signals["local_score"], float(item.source_start.seconds), item.id))
    retained: list[CandidateWindow] = []
    for window in windows:
        if any(window_overlap(window, other) >= .82 for other in retained):
            continue
        retained.append(window)
        if len(retained) >= max_windows:
            break
    return retained


def window_overlap(left: CandidateWindow, right: CandidateWindow) -> float:
    start = max(float(left.source_start.seconds), float(right.source_start.seconds))
    end = min(float(left.source_end.seconds), float(right.source_end.seconds))
    return max(0.0, end - start) / max(.001, min(left.duration_seconds, right.duration_seconds))


def prefilter_windows(windows: Iterable[CandidateWindow], *, mode: str = "balanced") -> list[CandidateWindow]:
    limits = {"fast": 12, "balanced": 24, "best_quality": 36}
    threshold = {"fast": 58.0, "balanced": 50.0, "best_quality": 44.0}
    if mode not in limits:
        raise ValueError("analysis mode must be fast, balanced, or best_quality")
    ranked = sorted(windows, key=lambda item: (-item.local_signals.get("local_score", 0), float(item.source_start.seconds), item.id))
    return [item for item in ranked if item.local_signals.get("local_score", 0) >= threshold[mode]][:limits[mode]]


def quality_score(quality: EditorialQuality, story_style: str = "default") -> float:
    normalized = story_style.lower().replace("-", "_").replace(" ", "_")
    weights = QUALITY_WEIGHTS.get(normalized, QUALITY_WEIGHTS["default"])
    return round(sum(float(getattr(quality, key)) * weight for key, weight in weights.items()), 3)


def comparative_rank(candidates: Iterable[ClipCandidate], *, story_style: str = "default", limit: int | None = None) -> tuple[list[ClipCandidate], list[str]]:
    ranked = sorted(candidates, key=lambda item: (-quality_score(item.editorial_quality or quality_from_scores(item.scores), story_style), item.id))
    selected: list[ClipCandidate] = []
    suppressed: list[str] = []
    for candidate in ranked:
        if any(candidate_overlap(candidate, prior) >= .65 or _title_similarity(candidate.title, prior.title) >= .72 for prior in selected):
            suppressed.append(candidate.id)
            continue
        selected.append(candidate)
        if limit is not None and len(selected) >= limit:
            break
    return selected, suppressed


def quality_from_scores(scores: ScoreDimensions) -> EditorialQuality:
    return EditorialQuality(
        hook=scores.hook, curiosity=scores.hook, conflict_tension=scores.emotion,
        payoff=scores.payoff, standalone_clarity=scores.standalone_context,
        novelty=scores.hook, energy=scores.emotion, dead_space_density=0,
        entertainment=round((scores.hook + scores.payoff + scores.emotion) / 3),
    )


def passes_editorial_gates(candidate: ClipCandidate) -> bool:
    """Deterministic minimums preserve hook, cold-viewer clarity, and an ending payoff."""
    quality = candidate.editorial_quality or quality_from_scores(candidate.scores)
    return bool(candidate.hook.strip()) and quality.hook >= 50 and quality.standalone_clarity >= 50 and quality.payoff >= 50


def candidate_overlap(left: ClipCandidate, right: ClipCandidate) -> float:
    start = max(float(left.source_start.seconds), float(right.source_start.seconds))
    end = min(float(left.source_end.seconds), float(right.source_end.seconds))
    shortest = min(float(left.source_end.seconds - left.source_start.seconds), float(right.source_end.seconds - right.source_start.seconds))
    return max(0.0, end - start) / max(.001, shortest)


def _title_similarity(left: str, right: str) -> float:
    a, b = set(_words(left)), set(_words(right))
    return len(a & b) / max(1, len(a | b))


def refine_candidate_boundaries(candidate: ClipCandidate, transcript: Transcript, source: MediaSource) -> ClipCandidate:
    """Snap to complete word boundaries and remove only leading/trailing filler or dead air."""
    words = [word for word in transcript.words if word.end.seconds > candidate.source_start.seconds and word.start.seconds < candidate.source_end.seconds]
    if not words:
        return candidate
    while len(words) > 2 and (words[0].corrected_text or words[0].text).lower().strip(" ,.!?") in {"um", "uh"}:
        words.pop(0)
    start = words[0].start
    end = words[-1].end
    if end.seconds <= start.seconds:
        return candidate
    candidate.source_start = MediaTime.from_seconds(start.seconds, source.time_base, exact=False)
    candidate.source_end = MediaTime.from_seconds(end.seconds, source.time_base, exact=False)
    return candidate


def align_candidate_hook(candidate: ClipCandidate, contract: DurationContract, transcript: Transcript, source: MediaSource) -> ClipCandidate:
    hook_tokens = {token for token in _words(candidate.hook) if token not in {"a", "an", "and", "are", "i", "is", "of", "on", "the", "to", "was", "you"}}
    if not hook_tokens:
        return candidate
    for segment in transcript.segments:
        if segment.end.seconds <= candidate.source_start.seconds or segment.start.seconds >= candidate.source_end.seconds:
            continue
        segment_tokens = set(_words(segment.corrected_text or segment.text))
        shared = hook_tokens & segment_tokens
        if len(shared) < min(3, len(hook_tokens)) and len(shared) / max(1, len(hook_tokens)) < .5:
            continue
        runway = float(segment.start.seconds - candidate.source_start.seconds)
        remaining = float(candidate.source_end.seconds - segment.start.seconds)
        if runway > 3 and remaining >= contract.minimum:
            candidate.source_start = MediaTime.from_seconds(segment.start.seconds, source.time_base, exact=False)
        break
    return candidate


def enforce_candidate_duration(candidate: ClipCandidate, contract: DurationContract, transcript: Transcript, source: MediaSource) -> ClipCandidate | None:
    candidate = refine_candidate_boundaries(candidate, transcript, source)
    candidate = align_candidate_hook(candidate, contract, transcript, source)
    if float(candidate.source_end.seconds - candidate.source_start.seconds) <= contract.hard_maximum:
        return candidate
    # One bounded shortening attempt. End only at a transcript segment boundary.
    viable = [segment for segment in transcript.segments if segment.start.seconds >= candidate.source_start.seconds and segment.end.seconds <= candidate.source_end.seconds and float(segment.end.seconds - candidate.source_start.seconds) <= contract.hard_maximum]
    if not viable:
        return None
    last = viable[-1]
    text = (last.corrected_text or last.text).strip()
    if not re.search(r"[.!?][\"']?$", text) or not any(cue in set(_words(text)) for cue in _PAYOFF_CUES):
        return None
    candidate.source_end = MediaTime.from_seconds(last.end.seconds, source.time_base, exact=False)
    return candidate if float(candidate.source_end.seconds - candidate.source_start.seconds) >= contract.minimum else None


def sequence_within_contract(sequence: EditSequence, contract: DurationContract) -> bool:
    return contract.minimum <= sequence.duration_seconds <= contract.hard_maximum


def create_shorter_sequence_variant(sequence: EditSequence, contract: DurationContract) -> EditSequence | None:
    """Create a non-destructive variant by removing optional development ranges once."""
    if sequence.duration_seconds <= contract.hard_maximum:
        variant = deepcopy(sequence)
        variant.id = f"{sequence.id}_shorter"
        variant.title = f"{sequence.title} (Shorter)"
        variant.revision = 1
        variant.preview_path = None
        variant.render_path = None
        return variant
    variant = deepcopy(sequence)
    removable = [item for item in variant.segments if item.purpose not in {"hook", "context", "payoff"}]
    if not removable:
        return None
    remove_id = max(removable, key=lambda item: float(item.source_out.seconds - item.source_in.seconds)).id
    variant.segments = [item for item in variant.segments if item.id != remove_id]
    cursor = 0.0
    for index, segment in enumerate(variant.segments):
        segment.order = index
        segment.timeline_start = cursor
        cursor += float(segment.source_out.seconds - segment.source_in.seconds)
    variant.actions = []
    if not sequence_within_contract(variant, contract):
        return None
    variant.id = f"{sequence.id}_shorter"
    variant.title = f"{sequence.title} (Shorter)"
    variant.revision = 1
    variant.preview_path = None
    variant.render_path = None
    return variant


T = TypeVar("T")


class ProviderFailure(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def classify_provider_failure(exc: Exception) -> str:
    text = str(exc).lower()
    if any(token in text for token in ("429", "quota", "rate limit")):
        return "quota_or_rate_limit"
    if any(token in text for token in ("401", "403", "credential", "api key")):
        return "invalid_credentials"
    if any(token in text for token in ("malformed", "schema", "structured output")):
        return "malformed_output"
    return "temporary_provider_failure"


def bounded_provider_call(call: Callable[[], T], *, max_retries: int = 1, on_retry: Callable[[int], None] | None = None) -> T:
    retries = 0
    while True:
        try:
            return call()
        except Exception as exc:
            kind = classify_provider_failure(exc)
            if retries >= max_retries or kind in {"invalid_credentials", "malformed_output"}:
                raise ProviderFailure(kind, str(exc)) from exc
            retries += 1
            if on_retry:
                on_retry(retries)


def cache_identity(
    transcript: Transcript, profile: WorkflowProfile | None, provider_name: str, model: str,
    *, campaign_revision: int | None = None,
) -> dict[str, Any]:
    contract = duration_contract_for(profile)
    return {
        "transcript_revision": transcript.revision_id,
        "preprocessing_revision": PREPROCESSING_VERSION,
        "duration_contract": asdict(contract),
        "profile_revision": profile.revision if profile else 0,
        "analysis_mode": profile.analysis_mode if profile else "balanced",
        "prompt_schema_revision": PROMPT_SCHEMA_VERSION,
        "provider": provider_name, "model_tier": model,
        "campaign_revision": campaign_revision,
        "quality_policy_revision": QUALITY_POLICY_VERSION,
    }


def finish_metrics(metrics: AnalysisRunMetrics, started: float) -> None:
    metrics.total_duration_seconds = round(time.perf_counter() - started, 6)
    metrics.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metrics.ai_request_count = sum(item.ai_request_count for item in metrics.stages)
    metrics.provider_retries = sum(item.provider_retries for item in metrics.stages)
    cacheable = [item for item in metrics.stages if item.stage not in {"deterministic_validation", "comparative_ranking"}]
    metrics.cache_hits = sum(item.cache_hit for item in cacheable)
    metrics.cache_misses = sum(not item.cache_hit for item in cacheable)
