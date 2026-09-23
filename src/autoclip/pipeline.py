from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .ai import AIProvider
from .candidates import normalize_and_deduplicate, validate_candidate
from .media import FFmpegService, media_source_from_probe
from .models import ClipCandidate, MediaSource, ScoreDimensions, Transcript, TranscriptSegment, TranscriptWord
from .storage import Workspace, atomic_write_json
from .time import MediaTime
from .transcription import FasterWhisperTranscriber


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
        chunks.append({
            "start": float(group[0].start.seconds), "end": float(group[-1].end.seconds),
            "text": " ".join(word.corrected_text or word.text for word in group),
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

    def transcribe_source(self, source: MediaSource) -> Transcript:
        return self._load_or_transcribe(source)

    def run_source(self, source_path: Path, output_dir: Path, *, source_id: str = "media_001") -> tuple[MediaSource, Transcript, list[ClipCandidate]]:
        source = media_source_from_probe(source_id, source_path, self.media.inspect(source_path))
        transcript, candidates = self.run(source, output_dir)
        return source, transcript, candidates

    def _load_or_transcribe(self, source: MediaSource) -> Transcript:
        key = self.workspace.cache_key("transcription", {"fingerprint": source.fingerprint, "model": self.transcriber.model_size})
        path = self.workspace.cache_result_path("transcription", key)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            audio = self.workspace.root / "cache" / "audio" / f"{key}.wav"
            if not audio.exists():
                self.media.extract_speech_audio(Path(source.reference), audio)
            data = self.transcriber.transcribe(audio)
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
        candidates = normalize_and_deduplicate(candidates)
        for candidate in candidates:
            self.media.extract_clip(Path(source.reference), output_dir / f"{candidate.id}.mp4", candidate.source_start, candidate.source_end)
        return transcript, candidates
