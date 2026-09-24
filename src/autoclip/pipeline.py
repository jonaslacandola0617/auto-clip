from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from .ai import AIProvider
from .candidates import normalize_and_deduplicate, validate_candidate
from .media import FFmpegService, MediaOperationCancelled, media_source_from_probe
from .models import ClipCandidate, MediaSource, Moment, ScoreDimensions, StoryConcept, Transcript, TranscriptSegment, TranscriptWord
from .intelligent_edit import consolidate_moments
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
        key = self.workspace.cache_key("stories", {"transcript_revision": transcript.revision_id, "moments": identity, "prompt": "stories-phase2a-v1", "provider": type(self.provider).__name__, "model": getattr(self.provider, "model", "fixture"), "preset": preset})
        path = self.workspace.root / "analysis" / f"stories-{key}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))["stories"] if path.exists() else self.provider.construct_stories(identity, {"target_duration_seconds": [25, 60], "segment_count": [2, 6], "preset": preset})
        stories = [StoryConcept(
            id=item.get("id") or f"story_{index + 1}", title=item["title"], premise=item["premise"], hook=item["hook"], context=item["context"],
            development=item["development"], payoff=item["payoff"], moment_ids=list(dict.fromkeys(item["moment_ids"])),
            target_duration_seconds=float(item["target_duration_seconds"]), explanation=item["explanation"], coherence=dict(item.get("coherence", {})),
            integrity_considerations=list(item.get("integrity_considerations", [])),
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
