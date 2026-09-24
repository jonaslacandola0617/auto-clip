from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Any

from .models import (
    ClipCandidate, EditSegment, EditSequence, EditorialAction, EditorialIntegrityResult, MediaSource,
    Moment, StoryConcept, Transcript,
)
from .time import MediaTime


ALLOWED_ACTIONS = {"hard_cut", "jump_cut", "trim_silence", "punch_in", "framing_change", "caption_emphasis", "short_hold"}
NEGATIONS = {"never", "not", "no", "without", "cannot", "can't", "dont", "don't", "didn't", "isn't", "wasn't"}
COMPETITION_WORDS = {"race", "racing", "challenge", "challenges", "dash", "sparring", "wager", "versus", "vs", "olympic", "debate"}


def _overlap(left: Moment, right: Moment) -> float:
    common = max(Fraction(0), min(left.source_out.seconds, right.source_out.seconds) - max(left.source_in.seconds, right.source_in.seconds))
    shortest = min(left.source_out.seconds - left.source_in.seconds, right.source_out.seconds - right.source_in.seconds)
    return float(common / shortest) if shortest > 0 else 0.0


def consolidate_moments(moments: list[Moment]) -> list[Moment]:
    consolidated: list[Moment] = []
    for moment in sorted(moments, key=lambda item: (item.source_id, item.source_in.seconds, item.source_out.seconds)):
        duplicate = next((item for item in consolidated if item.source_id == moment.source_id and (_overlap(item, moment) >= 0.7 or (set(item.transcript_word_ids) & set(moment.transcript_word_ids) and item.summary.casefold() == moment.summary.casefold()))), None)
        if duplicate is None:
            consolidated.append(moment)
            continue
        duplicate.source_in = min(duplicate.source_in, moment.source_in, key=lambda value: value.seconds)
        duplicate.source_out = max(duplicate.source_out, moment.source_out, key=lambda value: value.seconds)
        duplicate.transcript_segment_ids = list(dict.fromkeys(duplicate.transcript_segment_ids + moment.transcript_segment_ids))
        duplicate.transcript_word_ids = list(dict.fromkeys(duplicate.transcript_word_ids + moment.transcript_word_ids))
        duplicate.types = list(dict.fromkeys(duplicate.types + moment.types))
        duplicate.topic_ids = list(dict.fromkeys(duplicate.topic_ids + moment.topic_ids))
        duplicate.entities = list(dict.fromkeys(duplicate.entities + moment.entities))
        duplicate.confidence = max(duplicate.confidence, moment.confidence)
    return consolidated


def moments_from_candidates(candidates: list[ClipCandidate], transcript: Transcript, source: MediaSource, *, max_seconds: float = 14.0) -> list[Moment]:
    moments: list[Moment] = []
    for index, candidate in enumerate(candidates):
        overlapping = [segment for segment in transcript.segments if segment.start.seconds < candidate.source_end.seconds and segment.end.seconds > candidate.source_start.seconds]
        if not overlapping:
            continue
        chosen = [overlapping[0]]
        for segment in overlapping[1:]:
            if float(segment.end.seconds - chosen[0].start.seconds) > max_seconds:
                break
            chosen.append(segment)
        start, end = chosen[0].start, chosen[-1].end
        word_ids = [word_id for segment in chosen for word_id in segment.word_ids]
        title_words = {word.strip(".,!?():").casefold() for word in candidate.title.split()}
        topics = [candidate.category.casefold()]
        if title_words & COMPETITION_WORDS:
            topics.append("competition")
        entities = [word.strip(".,!?():") for word in candidate.title.split() if word[:1].isupper() and len(word) > 2]
        moments.append(Moment(
            id=f"moment_candidate_{index + 1}", source_id=source.id, source_in=start, source_out=end,
            transcript_segment_ids=[segment.id for segment in chosen], transcript_word_ids=word_ids,
            summary=" ".join(segment.corrected_text or segment.text for segment in chosen),
            types=["hook" if index == 0 else "statement", "payoff" if candidate.scores.payoff >= 80 else "context"],
            entities=list(dict.fromkeys(entities)), topic_ids=list(dict.fromkeys(topics)),
            characteristics={"hook": candidate.scores.hook / 100, "payoff": candidate.scores.payoff / 100, "context": candidate.scores.standalone_context / 100},
            provider_provenance={**candidate.provider_provenance, "derived_from": candidate.id, "derivation": "phase2a-candidate-cache-v1"},
            confidence=(candidate.scores.hook + candidate.scores.standalone_context + candidate.scores.payoff) / 300,
            reasoning=candidate.reason,
        ))
    return consolidate_moments(moments)


def construct_story_concepts_locally(moments: list[Moment], *, max_segments: int = 4) -> list[StoryConcept]:
    topic_groups: dict[str, list[Moment]] = {}
    for moment in moments:
        for topic in moment.topic_ids:
            topic_groups.setdefault(topic, []).append(moment)
    eligible = [(topic, group) for topic, group in topic_groups.items() if len(group) >= 2]
    if not eligible:
        return []
    topic, group = max(eligible, key=lambda item: (len(item[1]), item[0] == "competition"))
    selected = sorted(group, key=lambda item: item.source_in.seconds)[:max_segments]
    total = sum(float(item.source_out.seconds - item.source_in.seconds) for item in selected)
    if total < 8:
        return []
    return [StoryConcept(
        id=f"story_{topic.replace(' ', '_')}", title=f"{topic.title()} story", premise=f"Related moments form one {topic} thread.",
        hook=selected[0].summary, context=selected[1].summary if len(selected) > 1 else "",
        development=selected[-2].summary if len(selected) > 2 else "", payoff=selected[-1].summary,
        moment_ids=[item.id for item in selected], target_duration_seconds=min(60, max(25, total)),
        explanation=f"These moments share the {topic} thread and remain in source chronology.",
        coherence={"shared_topic": topic, "chronology": "source_order"},
        integrity_considerations=["Preserve each complete transcript segment and its original source timestamp."],
    )]


def validate_action(action: EditorialAction, duration_seconds: float) -> None:
    if action.type not in ALLOWED_ACTIONS:
        raise ValueError("unsupported editorial action")
    if not 0 <= action.timeline_start < action.timeline_end <= duration_seconds:
        raise ValueError("editorial action is outside the sequence timeline")
    if action.type == "punch_in" and not 1.0 <= float(action.parameters.get("scale", 1.0)) <= 1.25:
        raise ValueError("punch-in scale must be between 1.0 and 1.25")
    if action.type == "short_hold" and float(action.parameters.get("duration", 0)) > 1.0:
        raise ValueError("short hold may not exceed one second")


def validate_integrity(sequence: EditSequence, transcript: Transcript, moments: dict[str, Moment]) -> EditorialIntegrityResult:
    words = {word.id: word for word in transcript.words}
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    passed = True
    previous: EditSegment | None = None
    for segment in sequence.segments:
        moment = moments.get(segment.moment_id)
        covered_ids = set(moment.transcript_word_ids if moment else [])
        retained = {word_id for word_id in covered_ids if word_id in words and words[word_id].start.seconds >= segment.source_in.seconds and words[word_id].end.seconds <= segment.source_out.seconds}
        removed_negations = [words[word_id].text for word_id in covered_ids - retained if word_id in words and words[word_id].text.strip(".,!?\"'").casefold() in NEGATIONS]
        safe_negation = not removed_negations
        checks.append({"type": "negation_retention", "segment_id": segment.id, "passed": safe_negation, "evidence": removed_negations})
        passed &= safe_negation
        if previous and segment.purpose == "reaction":
            left = set(moments.get(previous.moment_id).topic_ids if moments.get(previous.moment_id) else [])
            right = set(moment.topic_ids if moment else [])
            related = not left or not right or bool(left & right)
            checks.append({"type": "reaction_association", "segment_id": segment.id, "passed": related, "evidence": sorted(left & right)})
            passed &= related
        previous = segment
    if any(sequence.segments[index].source_in.seconds > sequence.segments[index + 1].source_in.seconds for index in range(len(sequence.segments) - 1)):
        warnings.append("Sequence reorders source chronology and requires human review.")
    status = "passed" if passed and not warnings else "review_required" if passed else "failed"
    return EditorialIntegrityResult(status, checks, [segment.moment_id for segment in sequence.segments], warnings, status != "passed")


def plan_sequence(story: StoryConcept, moments: dict[str, Moment], transcript: Transcript, source: MediaSource) -> EditSequence:
    selected = [moments[moment_id] for moment_id in story.moment_ids if moment_id in moments]
    selected.sort(key=lambda item: item.source_in.seconds)
    cursor = 0.0
    segments: list[EditSegment] = []
    purposes = ["hook", "context", "development", "payoff"]
    for index, moment in enumerate(selected):
        purpose = purposes[min(index, len(purposes) - 1)]
        if index == len(selected) - 1 and len(selected) > 1:
            purpose = "payoff"
        segments.append(EditSegment(
            id=f"{story.id}_segment_{index + 1}", moment_id=moment.id, source_id=source.id,
            source_in=moment.source_in, source_out=moment.source_out, timeline_start=cursor,
            purpose=purpose, transcript_excerpt=moment.summary, order=index,
            provenance={"planner": "phase2a-v1"},
        ))
        cursor += float(moment.source_out.seconds - moment.source_in.seconds)
    actions = [EditorialAction(f"{story.id}_cut_{index}", "hard_cut", max(0, segment.timeline_start - 0.01), segment.timeline_start + 0.01, {}, "Join approved source segments", provenance={"planner": "phase2a-v1"}) for index, segment in enumerate(segments[1:], 1)]
    placeholder = EditorialIntegrityResult("review_required", [], [], required_review=True)
    sequence = EditSequence(f"edit_{story.id}", story.id, story.title, source.id, segments, actions, placeholder, frame_rate=source.frame_rate)
    sequence.integrity = validate_integrity(sequence, transcript, moments)
    sequence.status = "ready_to_preview" if sequence.integrity.status == "passed" and len(segments) >= 2 else "review_required"
    for action in actions:
        validate_action(action, sequence.duration_seconds)
    return sequence


def reflow_sequence(sequence: EditSequence) -> EditSequence:
    cursor = 0.0
    for index, segment in enumerate(sequence.segments):
        segment.order = index
        segment.timeline_start = cursor
        cursor += float(segment.source_out.seconds - segment.source_in.seconds)
    sequence.revision += 1
    sequence.preview_path = None
    sequence.render_path = None
    return sequence
