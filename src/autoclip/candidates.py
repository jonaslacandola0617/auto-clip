from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Iterable

from .models import ClipCandidate, Transcript
from .time import MediaTime


class CandidateValidationError(ValueError):
    pass


def validate_candidate(candidate: ClipCandidate, source_duration: MediaTime, transcript: Transcript, *, min_seconds: int = 5, max_seconds: int = 180) -> None:
    start, end, duration = candidate.source_start.seconds, candidate.source_end.seconds, source_duration.seconds
    if start < 0:
        raise CandidateValidationError("candidate start is negative")
    if end > duration:
        raise CandidateValidationError("candidate end exceeds source duration")
    if start >= end:
        raise CandidateValidationError("candidate range is empty or reversed")
    length = end - start
    if length < min_seconds or length > max_seconds:
        raise CandidateValidationError("candidate duration violates configured constraints")
    if not any(segment.start.seconds < end and segment.end.seconds > start for segment in transcript.segments):
        raise CandidateValidationError("candidate does not reference an existing transcript range")


def normalize_and_deduplicate(candidates: Iterable[ClipCandidate], overlap_threshold: Fraction = Fraction(4, 5)) -> list[ClipCandidate]:
    ranked = sorted(candidates, key=ranking_score, reverse=True)
    result: list[ClipCandidate] = []
    for candidate in ranked:
        duplicate = False
        for kept in result:
            overlap = max(Fraction(0), min(candidate.source_end.seconds, kept.source_end.seconds) - max(candidate.source_start.seconds, kept.source_start.seconds))
            shorter = min(candidate.source_end.seconds - candidate.source_start.seconds, kept.source_end.seconds - kept.source_start.seconds)
            if shorter and overlap / shorter >= overlap_threshold:
                duplicate = True
                break
        if not duplicate:
            result.append(replace(candidate, title=candidate.title.strip(), hook=candidate.hook.strip(), reason=candidate.reason.strip()))
    return result


def ranking_score(candidate: ClipCandidate) -> float:
    s = candidate.scores
    return s.hook * 0.35 + s.standalone_context * 0.25 + s.payoff * 0.30 + s.emotion * 0.10

