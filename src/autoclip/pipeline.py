from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from .ai import AIProvider
from .candidates import normalize_and_deduplicate, validate_candidate
from .media import FFmpegService, MediaOperationCancelled, media_source_from_probe
from .models import AnalysisRunMetrics, AnalysisStageMetric, CandidateWindow, ClipCandidate, EditorialQuality, MediaSource, Moment, ScoreDimensions, StoryConcept, Transcript, TranscriptSegment, TranscriptWord, WorkflowProfile
from .intelligent_edit import consolidate_moments
from .editorial_v2 import (
    bounded_provider_call, cache_identity, comparative_rank, create_candidate_windows,
    duration_contract_for, enforce_candidate_duration, finish_metrics, passes_editorial_gates, prefilter_windows,
)
from .storage import Workspace, atomic_write_json
from .time import MediaTime
from .transcription import FasterWhisperTranscriber, TranscriptionCancelled


def chunk_transcript(transcript: Transcript, *, max_words: int = 800, overlap_words: int = 80) -> list[dict[str, Any]]:
    if max_words <= overlap_words:
        raise ValueError("max_words must exceed overlap_words")
    words = transcript.words
    chunks: list[dict[str, Any]] = []
    step = max_words - overlap_words
    for start in range(0, len(words), step):
        group = words[start:start + max_words]
        if not group:
            break
        word_ids = {word.id for word in group}
        chunks.append({
            "start": float(group[0].start.seconds), "end": float(group[-1].end.seconds),
            "text": " ".join(word.corrected_text or word.text for word in group),
            "word_ids": [word.id for word in group],
            "segment_ids": [segment.id for segment in transcript.segments if word_ids.intersection(segment.word_ids)],
        })
        if start + max_words >= len(words):
            break
    return chunks


@dataclass(slots=True)
class CorePipeline:
    workspace: Workspace
    media: FFmpegService
    transcriber: FasterWhisperTranscriber
    provider: AIProvider
    prompt_version: str = "phase0-v1"

    def transcribe_source(
        self,
        source: MediaSource,
        *,
        progress: Callable[[float, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Transcript:
        return self._load_or_transcribe(source, progress=progress, cancel_event=cancel_event)

    def analyze_source(self, source: MediaSource, transcript: Transcript) -> list[ClipCandidate]:
        analysis = self._load_or_analyze(source, transcript)
        raw_candidates = self.provider.rank_candidates(self.provider.find_candidates(analysis))
        candidates: list[ClipCandidate] = []
        for index, raw in enumerate(raw_candidates):
            candidate = ClipCandidate(
                id=f"candidate_{index + 1}",
                source_start=MediaTime.from_seconds(Fraction(str(raw["start"])), source.time_base, exact=False),
                source_end=MediaTime.from_seconds(Fraction(str(raw["end"])), source.time_base, exact=False),
                title=raw["title"], hook=raw["hook"], category=raw["category"], reason=raw["reason"],
                scores=ScoreDimensions(**{**{"emotion": 0}, **raw["scores"]}),
                provider_provenance={"provider": type(self.provider).__name__, "prompt_version": self.prompt_version},
            )
            validate_candidate(candidate, source.duration, transcript)
            candidates.append(candidate)
        return normalize_and_deduplicate(candidates)

    def analyze_source_v2(
        self,
        source: MediaSource,
        transcript: Transcript,
        *,
        profile: WorkflowProfile | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> tuple[list[ClipCandidate], list[CandidateWindow], AnalysisRunMetrics]:
        """V2 hybrid funnel: deterministic windows, FAST shortlist, STRONG batch plan, local gates."""
        started = time.perf_counter()
        metrics = AnalysisRunMetrics(
            id=f"analysis_{time.time_ns()}", pipeline_version="v2.1-hybrid-v1",
            transcript_revision=transcript.revision_id,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        contract = duration_contract_for(profile)
        mode = profile.analysis_mode if profile else "balanced"

        stage_started = time.perf_counter()
        windows = create_candidate_windows(source, transcript, contract)
        shortlist = prefilter_windows(windows, mode=mode)
        window_key = self.workspace.cache_key("v2_windows", {
            "transcript_revision": transcript.revision_id, "preprocessing_revision": "v2.1-window-v1",
            "duration_contract": asdict(contract),
        })
        window_path = self.workspace.root / "analysis" / f"v2-windows-{window_key}.json"
        window_cache_hit = window_path.exists()
        if not window_cache_hit:
            atomic_write_json(window_path, {"windows": [asdict(item) for item in windows]})
        metrics.candidate_counts.update({"local_windows": len(windows), "local_shortlist": len(shortlist)})
        metrics.stages.append(AnalysisStageMetric(
            "local_preprocessing", round(time.perf_counter() - stage_started, 6),
            input_count=len(transcript.segments), output_count=len(shortlist),
            approximate_context_words=len(transcript.words),
            cache_hit=window_cache_hit,
        ))
        if not shortlist:
            finish_metrics(metrics, started)
            return [], windows, metrics

        segment_by_id = {segment.id: segment for segment in transcript.segments}
        provider_windows = [{
            "id": item.id, "start": float(item.source_start.seconds), "end": float(item.source_end.seconds),
            "text_summary": item.text_summary,
            "text": " ".join((segment_by_id[segment_id].corrected_text or segment_by_id[segment_id].text) for segment_id in item.transcript_segment_ids if segment_id in segment_by_id),
            "word_ids": item.transcript_word_ids, "segment_ids": item.transcript_segment_ids,
            "local_signals": item.local_signals,
        } for item in shortlist]
        identity = cache_identity(transcript, profile, type(self.provider).__name__, getattr(self.provider, "model", "fixture"))

        fast_key = self.workspace.cache_key("v2_fast_ranking", identity | {"window_ids": [item.id for item in shortlist]})
        fast_path = self.workspace.root / "analysis" / f"v2-fast-{fast_key}.json"
        stage_started = time.perf_counter()
        fast_retry_count = [0]
        if fast_path.exists():
            rankings = json.loads(fast_path.read_text(encoding="utf-8"))["rankings"]
            fast_hit = True
        else:
            rankings = bounded_provider_call(
                lambda: self.provider.rank_candidate_windows(provider_windows, {"tier": "fast", "mode": mode, "duration_contract": identity["duration_contract"]}),
                max_retries=0 if mode == "fast" else 1,
                on_retry=lambda value: fast_retry_count.__setitem__(0, value),
            )
            atomic_write_json(fast_path, {"rankings": rankings})
            fast_hit = False
        rank_by_id = {str(item.get("window_id")): item for item in rankings if isinstance(item, dict)}
        retained = [item for item in provider_windows if rank_by_id.get(item["id"], {}).get("retain", False)]
        retained.sort(key=lambda item: (-int(rank_by_id[item["id"]].get("rank_score", 0)), item["id"]))
        strong_limits = {"fast": 4, "balanced": 8, "best_quality": 12}
        retained = retained[:strong_limits[mode]]
        metrics.candidate_counts["fast_retained"] = len(retained)
        metrics.stages.append(AnalysisStageMetric(
            "fast_ranking", round(time.perf_counter() - stage_started, 6),
            ai_request_count=0 if fast_hit else 1 + fast_retry_count[0], provider_retries=fast_retry_count[0],
            input_count=len(shortlist), output_count=len(retained),
            approximate_context_words=sum(len(item["text"].split()) for item in provider_windows), cache_hit=fast_hit,
        ))
        if not retained:
            finish_metrics(metrics, started)
            return [], windows, metrics
        if fast_retry_count[0]:
            metrics.provider_errors.append("fast_ranking: transient provider failure recovered by bounded retry")

        plan_key = self.workspace.cache_key("v2_strong_planning", identity | {"window_ids": [item["id"] for item in retained]})
        plan_path = self.workspace.root / "analysis" / f"v2-plans-{plan_key}.json"
        stage_started = time.perf_counter()
        strong_retry_count = [0]
        if plan_path.exists():
            raw_candidates = json.loads(plan_path.read_text(encoding="utf-8"))["candidates"]
            plan_hit = True
        else:
            raw_candidates = bounded_provider_call(
                lambda: self.provider.plan_edits(retained, {
                    "tier": "strong", "mode": mode, "duration_seconds": float(source.duration.seconds),
                    "min_clip_seconds": contract.minimum, "target_clip_seconds": contract.target,
                    "max_clip_seconds": contract.hard_maximum, "language": transcript.language,
                }),
                max_retries=0 if mode == "fast" else 1,
                on_retry=lambda value: strong_retry_count.__setitem__(0, value),
            )
            atomic_write_json(plan_path, {"candidates": raw_candidates})
            plan_hit = False
        metrics.stages.append(AnalysisStageMetric(
            "strong_planning", round(time.perf_counter() - stage_started, 6),
            ai_request_count=0 if plan_hit else 1 + strong_retry_count[0], provider_retries=strong_retry_count[0],
            input_count=len(retained), output_count=len(raw_candidates),
            approximate_context_words=sum(len(item["text"].split()) for item in retained), cache_hit=plan_hit,
        ))
        if strong_retry_count[0]:
            metrics.provider_errors.append("strong_planning: transient provider failure recovered by bounded retry")

        validation_started = time.perf_counter()
        candidates: list[ClipCandidate] = []
        retained_by_id = {item["id"]: item for item in retained}
        for index, raw in enumerate(raw_candidates):
            planned_window = retained_by_id.get(str(raw.get("window_id", "")))
            if raw.get("window_id") and (planned_window is None or float(raw["start"]) < float(planned_window["start"]) or float(raw["end"]) > float(planned_window["end"])):
                metrics.rejection_reasons["outside_candidate_window"] = metrics.rejection_reasons.get("outside_candidate_window", 0) + 1
                continue
            scores = ScoreDimensions(**{**{"emotion": 0}, **raw["scores"]})
            candidate = ClipCandidate(
                id=f"candidate_v2_{index + 1}",
                source_start=MediaTime.from_seconds(Fraction(str(raw["start"])), source.time_base, exact=False),
                source_end=MediaTime.from_seconds(Fraction(str(raw["end"])), source.time_base, exact=False),
                title=raw["title"], hook=raw["hook"], category=raw["category"], reason=raw["reason"], scores=scores,
                provider_provenance={"provider": type(self.provider).__name__, "prompt_version": "v2.1-editorial-schema-v2", "tier": "strong"},
                editorial_quality=EditorialQuality(**raw["quality"]) if raw.get("quality") else EditorialQuality(
                    hook=scores.hook, curiosity=scores.hook, conflict_tension=scores.emotion,
                    payoff=scores.payoff, standalone_clarity=scores.standalone_context,
                    novelty=round((scores.hook + scores.standalone_context) / 2), energy=scores.emotion,
                    dead_space_density=0, entertainment=round((scores.hook + scores.payoff + scores.emotion) / 3),
                ), candidate_window_id=str(raw.get("window_id")) if raw.get("window_id") else None,
                payoff=str(raw.get("payoff", "")), story_structure={str(key): str(value) for key, value in raw.get("story_structure", {}).items()},
            )
            try:
                validate_candidate(candidate, source.duration, transcript, min_seconds=contract.minimum, max_seconds=max(180, contract.hard_maximum))
            except ValueError:
                metrics.rejection_reasons["invalid_source_range_or_duration"] = metrics.rejection_reasons.get("invalid_source_range_or_duration", 0) + 1
                continue
            candidate = enforce_candidate_duration(candidate, contract, transcript, source)
            if candidate is None:
                metrics.rejection_reasons["cannot_fit_hard_maximum_with_payoff"] = metrics.rejection_reasons.get("cannot_fit_hard_maximum_with_payoff", 0) + 1
                continue
            if not passes_editorial_gates(candidate):
                metrics.rejection_reasons["hook_clarity_or_payoff_gate"] = metrics.rejection_reasons.get("hook_clarity_or_payoff_gate", 0) + 1
                continue
            candidates.append(candidate)
            if metrics.time_to_first_candidate_seconds is None:
                metrics.time_to_first_candidate_seconds = round(time.perf_counter() - started, 6)
            if progress:
                progress(len(candidates), f"{len(candidates)} candidate{'s' if len(candidates) != 1 else ''} ready")
        metrics.stages.append(AnalysisStageMetric(
            "deterministic_validation", round(time.perf_counter() - validation_started, 6),
            input_count=len(raw_candidates), output_count=len(candidates),
        ))
        comparison_started = time.perf_counter()
        ranked, suppressed = comparative_rank(candidates, story_style=profile.story_style if profile else "default", limit=profile.desired_output_count if profile else None)
        metrics.candidate_counts.update({"planned": len(raw_candidates), "validated": len(candidates), "qualified": len(ranked), "duplicates_suppressed": len(suppressed)})
        metrics.stages.append(AnalysisStageMetric(
            "comparative_ranking", round(time.perf_counter() - comparison_started, 6),
            input_count=len(candidates), output_count=len(ranked),
        ))
        finish_metrics(metrics, started)
        return ranked, windows, metrics

    def discover_moments(self, source: MediaSource, transcript: Transcript, *, preset: str = "default") -> list[Moment]:
        key = self.workspace.cache_key("moments", {"transcript_revision": transcript.revision_id, "prompt": "moments-phase2a-v1", "provider": type(self.provider).__name__, "model": getattr(self.provider, "model", "fixture"), "preset": preset})
        path = self.workspace.root / "analysis" / f"moments-{key}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))["moments"] if path.exists() else self.provider.discover_moments(chunk_transcript(transcript), {"source_id": source.id, "duration_seconds": float(source.duration.seconds), "transcript_revision": transcript.revision_id, "preset": preset})
        moments = []
        for index, item in enumerate(raw):
            start_seconds, end_seconds = Fraction(str(item["start"])), Fraction(str(item["end"]))
            if start_seconds < 0 or end_seconds <= start_seconds or end_seconds > source.duration.seconds:
                continue
            segment_ids = [segment.id for segment in transcript.segments if segment.start.seconds < end_seconds and segment.end.seconds > start_seconds]
            word_ids = [word.id for word in transcript.words if word.start.seconds < end_seconds and word.end.seconds > start_seconds]
            moments.append(Moment(
                id=item.get("id") or f"moment_{index + 1}", source_id=source.id,
                source_in=MediaTime.from_seconds(start_seconds, source.time_base, exact=False),
                source_out=MediaTime.from_seconds(end_seconds, source.time_base, exact=False),
                transcript_segment_ids=segment_ids, transcript_word_ids=word_ids,
                summary=str(item["summary"]), types=list(item.get("types", [])), entities=list(item.get("entities", [])),
                topic_ids=list(item.get("topics", [])), characteristics=dict(item.get("characteristics", {})),
                provider_provenance={"provider": type(self.provider).__name__, "model": getattr(self.provider, "model", "fixture"), "prompt_version": "moments-phase2a-v1"},
                confidence=float(item.get("confidence", 0)), reasoning=str(item.get("reasoning", "")),
            ))
        moments = consolidate_moments(moments)
        if not path.exists():
            atomic_write_json(path, {"moments": [{
                "id": moment.id, "start": float(moment.source_in.seconds), "end": float(moment.source_out.seconds),
                "summary": moment.summary, "types": moment.types, "segment_ids": moment.transcript_segment_ids,
                "word_ids": moment.transcript_word_ids, "topics": moment.topic_ids, "entities": moment.entities,
                "characteristics": moment.characteristics, "confidence": moment.confidence, "reasoning": moment.reasoning,
            } for moment in moments]})
        return moments

    def construct_story_concepts(self, moments: list[Moment], transcript: Transcript, *, preset: str = "default") -> list[StoryConcept]:
        identity = [{"id": item.id, "summary": item.summary, "types": item.types, "topics": item.topic_ids, "entities": item.entities, "start": float(item.source_in.seconds), "end": float(item.source_out.seconds)} for item in moments]
        key = self.workspace.cache_key("stories", {"transcript_revision": transcript.revision_id, "moments": identity, "prompt": "stories-phase2a1-v1", "provider": type(self.provider).__name__, "model": getattr(self.provider, "model", "fixture"), "preset": preset})
        path = self.workspace.root / "analysis" / f"stories-{key}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))["stories"] if path.exists() else self.provider.construct_stories(identity, {"target_duration_seconds": [25, 60], "segment_count": [2, 6], "preset": preset})
        stories = [StoryConcept(
            id=item.get("id") or f"story_{index + 1}", title=item["title"], premise=item["premise"], hook=item["hook"], context=item["context"],
            development=item["development"], payoff=item["payoff"], moment_ids=list(dict.fromkeys(item["moment_ids"])),
            target_duration_seconds=float(item["target_duration_seconds"]), explanation=item["explanation"], coherence=dict(item.get("coherence", {})),
            integrity_considerations=list(item.get("integrity_considerations", [])),
            central_topic=str(item.get("central_topic", "")), viewer_premise=str(item.get("viewer_premise", item.get("premise", ""))),
            moment_rationales={str(key): str(value) for key, value in item.get("moment_rationales", {}).items()},
            understandable_without_source=bool(item.get("understandable_without_source", False)),
        ) for index, item in enumerate(raw) if len(set(item.get("moment_ids", []))) >= 2]
        if not path.exists():
            atomic_write_json(path, {"stories": [{**item, "id": story.id} for item, story in zip(raw, stories)]})
        return stories

    def run_source(self, source_path: Path, output_dir: Path, *, source_id: str = "media_001") -> tuple[MediaSource, Transcript, list[ClipCandidate]]:
        source = media_source_from_probe(source_id, source_path, self.media.inspect(source_path))
        transcript, candidates = self.run(source, output_dir)
        return source, transcript, candidates

    def _load_or_transcribe(
        self,
        source: MediaSource,
        *,
        progress: Callable[[float, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Transcript:
        def report(value: float, stage: str) -> None:
            if progress:
                progress(value, stage)

        def cancelled() -> bool:
            return bool(cancel_event and cancel_event.is_set())

        key = self.workspace.cache_key(
            "transcription",
            {
                "fingerprint": source.fingerprint,
                "transcriber": getattr(self.transcriber, "cache_identity", {"model": self.transcriber.model_size}),
            },
        )
        path = self.workspace.cache_result_path("transcription", key)
        if path.exists():
            report(0.95, "Using cached transcript")
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            audio = self.workspace.root / "cache" / "audio" / f"{key}.wav"
            if not audio.exists():
                report(0.05, "Extracting speech audio")
                try:
                    self.media.extract_speech_audio(Path(source.reference), audio, cancel_event=cancel_event)
                except MediaOperationCancelled as exc:
                    raise TranscriptionCancelled("Audio extraction cancelled safely.") from exc
            else:
                report(0.15, "Using cached speech audio")
            if cancelled():
                raise TranscriptionCancelled("Transcription cancelled before inference.")
            data = self.transcriber.transcribe(
                audio,
                duration_seconds=float(source.duration.seconds),
                progress=lambda value, stage: report(0.18 + 0.7 * value, stage),
                cancelled=cancelled,
            )
            if cancelled():
                raise TranscriptionCancelled("Transcription cancelled before cache write.")
            report(0.9, "Saving transcript cache")
            atomic_write_json(path, data)
        time_base = source.time_base
        words = [TranscriptWord(
            id=w["id"], start=MediaTime.from_seconds(Fraction(str(w["start"])), time_base, exact=False),
            end=MediaTime.from_seconds(Fraction(str(w["end"])), time_base, exact=False), text=w["text"],
        ) for w in data["words"]]
        segments = [TranscriptSegment(
            id=s["id"], start=MediaTime.from_seconds(Fraction(str(s["start"])), time_base, exact=False),
            end=MediaTime.from_seconds(Fraction(str(s["end"])), time_base, exact=False), text=s["text"], word_ids=s["word_ids"],
        ) for s in data["segments"]]
        return Transcript(revision_id=key, language=data["language"], segments=segments, words=words, model=data["model"])

    def _load_or_analyze(self, source: MediaSource, transcript: Transcript) -> dict[str, Any]:
        key = self.workspace.cache_key("analysis", {"transcript_revision": transcript.revision_id, "prompt": self.prompt_version, "provider": type(self.provider).__name__})
        path = self.workspace.cache_result_path("analysis", key)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        analysis = self.provider.analyze_transcript(chunk_transcript(transcript), {
            "duration_seconds": float(source.duration.seconds), "min_clip_seconds": 5,
            "max_clip_seconds": 180, "language": transcript.language,
        })
        atomic_write_json(path, analysis)
        return analysis

    def run(self, source: MediaSource, output_dir: Path) -> tuple[Transcript, list[ClipCandidate]]:
        transcript = self._load_or_transcribe(source)
        candidates = self.analyze_source(source, transcript)
        for candidate in candidates:
            self.media.extract_clip(Path(source.reference), output_dir / f"{candidate.id}.mp4", candidate.source_start, candidate.source_end)
        return transcript, candidates
