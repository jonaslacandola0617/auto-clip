from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import re
from typing import Any

from .models import (
    ClipCandidate, EditSegment, EditSequence, EditorialAction, EditorialIntegrityResult, EditorialReviewResult, MediaSource,
    Moment, StoryConcept, Transcript,
)
from .time import MediaTime


ALLOWED_ACTIONS = {"hard_cut", "jump_cut", "trim_silence", "punch_in", "framing_change", "caption_emphasis", "short_hold"}
NEGATIONS = {"never", "not", "no", "without", "cannot", "can't", "dont", "don't", "didn't", "isn't", "wasn't"}
COMPETITION_WORDS = {"race", "racing", "challenge", "challenges", "dash", "sparring", "wager", "versus", "vs", "olympic", "debate"}
HOOK_WORDS = {"scared", "never", "challenge", "challenged", "race", "beat", "winner", "risk", "bet", "quit", "leave", "fired", "secret", "why", "how"}
PAYOFF_WORDS = {"beat", "winner", "won", "lost", "left", "quit", "answer", "result", "therefore", "finally", "line", "deal"}
GENERIC_TOPICS = {"sports", "entertainment", "competition", "story", "interesting moments"}
STOP_WORDS = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "his", "her", "how", "why", "what", "from", "by", "is", "was", "it", "this", "that", "i", "me", "my", "you", "your"}
MAX_EDITORIAL_REBUILDS = 2


def _tokens(text: str) -> set[str]:
    values: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9]+", text.casefold()):
        token = raw
        for suffix in ("ing", "ed", "es", "s"):
            if token.endswith(suffix) and len(token) > len(suffix) + 3:
                token = token[:-len(suffix)]
                break
        if token not in STOP_WORDS and len(token) > 1:
            values.add(token)
    return values


def _jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _approximately_present(reference: str, text: str) -> bool:
    text_tokens = _tokens(text)
    return any(left == right or (len(left) >= 4 and len(right) >= 4 and left[:4] == right[:4]) for left in _tokens(reference) for right in text_tokens)


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


def _transcript_windows(transcript: Transcript, start: Fraction, end: Fraction, *, max_seconds: float) -> list[tuple[list[Any], str]]:
    segments = [item for item in transcript.segments if item.start.seconds < end and item.end.seconds > start]
    windows: list[tuple[list[Any], str]] = []
    for index in range(len(segments)):
        chosen: list[Any] = []
        for segment in segments[index:]:
            duration = float(segment.end.seconds - segments[index].start.seconds)
            if duration > max_seconds:
                break
            chosen.append(segment)
            if duration >= 3:
                windows.append((chosen.copy(), " ".join(item.corrected_text or item.text for item in chosen)))
    return windows


def _window_score(text: str, reference: str, cues: set[str], *, question_bonus: bool = False) -> float:
    words, reference_words = _tokens(text), _tokens(reference)
    overlap = len(words & reference_words) / max(1, len(reference_words))
    cue_score = min(1.0, len(words & cues) / 2)
    return overlap * 0.65 + cue_score * 0.3 + (0.15 if question_bonus and "?" in text else 0.0)


def _moment_from_window(candidate: ClipCandidate, role: str, index: int, window: tuple[list[Any], str], source: MediaSource) -> Moment:
    segments, text = window
    return Moment(
        id=f"moment_{candidate.id}_{role}_{index}", source_id=source.id,
        source_in=segments[0].start, source_out=segments[-1].end,
        transcript_segment_ids=[item.id for item in segments],
        transcript_word_ids=[word_id for item in segments for word_id in item.word_ids],
        summary=text, types=[role],
        entities=[word for word in re.findall(r"\b[A-Z][A-Za-z0-9]+\b", candidate.title) if word not in {"How"}],
        topic_ids=[candidate.id],
        characteristics={"hook": 1.0 if role == "hook" else 0.0, "payoff": 1.0 if role == "payoff" else 0.0, "context": 1.0 if role == "context" else 0.0},
        provider_provenance={**candidate.provider_provenance, "derived_from": candidate.id, "derivation": "phase2a1-editorial-refinement-v1"},
        confidence=0.9, reasoning=f"Selected as the {role} for {candidate.title}",
    )


def construct_candidate_stories(candidates: list[ClipCandidate], transcript: Transcript, source: MediaSource) -> tuple[list[Moment], list[StoryConcept]]:
    """Build conservative, single-premise stories from cached candidates and nearby transcript context."""
    moments: list[Moment] = []
    stories: list[StoryConcept] = []
    for candidate in candidates:
        start = max(Fraction(0), candidate.source_start.seconds - 20)
        end = min(source.duration.seconds, candidate.source_end.seconds + 5)
        short_windows = _transcript_windows(transcript, start, end, max_seconds=10)
        long_windows = _transcript_windows(transcript, start, end, max_seconds=20)
        if not short_windows or not long_windows:
            continue
        hook = max(short_windows, key=lambda item: (
            _window_score(item[1], candidate.hook, HOOK_WORDS) - max(0.0, float(item[0][-1].end.seconds - item[0][0].start.seconds) - 5.0) * 0.04,
            -float(item[0][-1].end.seconds - item[0][0].start.seconds),
        ))
        hook_start, hook_end = hook[0][0].start.seconds, hook[0][-1].end.seconds

        def disjoint(window: tuple[list[Any], str]) -> bool:
            return window[0][-1].end.seconds <= hook_start or window[0][0].start.seconds >= hook_end

        context_pool = [item for item in short_windows if disjoint(item) and item[0][0].start.seconds < hook_start]
        if not context_pool:
            continue
        context = max(context_pool, key=lambda item: (_window_score(item[1], candidate.title + " " + candidate.reason, {"question", "race", "why", "how"}, question_bonus=True), -float(item[0][-1].end.seconds - item[0][0].start.seconds)))
        occupied = [(context[0][0].start.seconds, context[0][-1].end.seconds), (hook_start, hook_end)]

        def available(window: tuple[list[Any], str]) -> bool:
            left, right = window[0][0].start.seconds, window[0][-1].end.seconds
            return all(right <= start_at or left >= end_at for start_at, end_at in occupied)

        development_pool = [item for item in short_windows if available(item) and item[0][0].start.seconds > context[0][-1].end.seconds]
        if not development_pool:
            continue
        development = max(development_pool, key=lambda item: (_window_score(item[1], candidate.reason, {"because", "but", "so", "explain"}), -float(item[0][-1].end.seconds - item[0][0].start.seconds)))
        occupied.append((development[0][0].start.seconds, development[0][-1].end.seconds))
        payoff_pool = [item for item in long_windows if available(item) and item[0][0].start.seconds >= hook_end]
        if not payoff_pool:
            continue
        payoff = max(payoff_pool, key=lambda item: (_window_score(item[1], candidate.hook + " " + candidate.reason, PAYOFF_WORDS), -float(item[0][-1].end.seconds - item[0][0].start.seconds)))
        chosen = [("hook", hook), ("context", context), ("development", development), ("payoff", payoff)]
        candidate_moments = [_moment_from_window(candidate, role, index, window, source) for index, (role, window) in enumerate(chosen, 1)]
        if len({(item.source_in.seconds, item.source_out.seconds) for item in candidate_moments}) != len(candidate_moments):
            continue
        moments.extend(candidate_moments)
        topic = candidate.title
        rationales = {item.id: f"Provides the {role} for the single premise: {candidate.reason}" for (role, _), item in zip(chosen, candidate_moments, strict=True)}
        stories.append(StoryConcept(
            id=f"story_{candidate.id}", title=candidate.title, premise=candidate.reason,
            hook=candidate_moments[0].summary, context=candidate_moments[1].summary,
            development=candidate_moments[2].summary, payoff=candidate_moments[3].summary,
            moment_ids=[item.id for item in candidate_moments], target_duration_seconds=min(60, sum(float(item.source_out.seconds - item.source_in.seconds) for item in candidate_moments)),
            explanation=f"A source-grounded hook, context, development, and payoff all address: {candidate.reason}",
            coherence={"single_candidate": candidate.id, "planned_order": [item.id for item in candidate_moments], "allow_truthful_hook_reorder": True, "entities": candidate_moments[0].entities},
            integrity_considerations=["The hook is moved forward without changing who said it or what it refers to."],
            central_topic=topic, viewer_premise=candidate.reason, moment_rationales=rationales,
        ))
    return moments, stories


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


def validate_action_policy(actions: list[EditorialAction], duration_seconds: float) -> None:
    enabled = [action for action in actions if action.enabled]
    for action in enabled:
        validate_action(action, duration_seconds)
    punch_ins = sorted((action for action in enabled if action.type == "punch_in"), key=lambda item: item.timeline_start)
    if len(punch_ins) > max(1, int(duration_seconds // 20) + 1):
        raise ValueError("punch-ins must remain sparse")
    if any(right.timeline_start - left.timeline_end < 8 for left, right in zip(punch_ins, punch_ins[1:])):
        raise ValueError("punch-ins must be separated by at least eight seconds")
    if punch_ins and any(action.type == "framing_change" and action.parameters.get("continuous", False) for action in enabled):
        raise ValueError("punch-ins cannot be combined with continuous crop motion")


def validate_integrity(sequence: EditSequence, transcript: Transcript, moments: dict[str, Moment], *, allow_truthful_hook_reorder: bool = False) -> EditorialIntegrityResult:
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
    reordered = any(sequence.segments[index].source_in.seconds > sequence.segments[index + 1].source_in.seconds for index in range(len(sequence.segments) - 1))
    truthful_hook_reorder = reordered and allow_truthful_hook_reorder and sequence.segments[0].purpose == "hook" and all(
        sequence.segments[index].source_in.seconds <= sequence.segments[index + 1].source_in.seconds
        for index in range(1, len(sequence.segments) - 1)
    )
    checks.append({"type": "chronology", "passed": not reordered or truthful_hook_reorder, "evidence": "truthful_hook_reorder" if truthful_hook_reorder else "source_order" if not reordered else "unsupported_reorder"})
    if reordered and not truthful_hook_reorder:
        warnings.append("Sequence reorders source chronology and requires human review.")
        passed = False
    status = "passed" if passed and not warnings else "review_required" if passed else "failed"
    return EditorialIntegrityResult(status, checks, [segment.moment_id for segment in sequence.segments], warnings, status != "passed")


def review_editorial_quality(story: StoryConcept, sequence: EditSequence, moments: dict[str, Moment], *, rebuild_attempts: int = 0) -> EditorialReviewResult:
    problems: list[str] = []
    topic = story.central_topic.strip() or str(story.coherence.get("shared_topic", "")).strip()
    if not topic or topic.casefold() in GENERIC_TOPICS:
        problems.append("No specific central topic was established.")
    premise = story.viewer_premise.strip() or story.premise.strip()
    if len(_tokens(premise)) < 5:
        problems.append("The viewer premise is not specific enough.")
    roles = [segment.purpose for segment in sequence.segments]
    hook_text = sequence.segments[0].transcript_excerpt if sequence.segments else ""
    hook_present = bool(sequence.segments and roles[0] == "hook" and (_tokens(hook_text) & HOOK_WORDS or "?" in hook_text))
    if not hook_present:
        problems.append("The opening does not contain a source-grounded hook.")
    payoff_text = sequence.segments[-1].transcript_excerpt if sequence.segments else ""
    payoff_present = bool(sequence.segments and roles[-1] == "payoff" and (_tokens(payoff_text) & PAYOFF_WORDS) and payoff_text != hook_text)
    if not payoff_present:
        problems.append("The edit has no recognizable payoff.")
    if "context" not in roles and "setup" not in roles:
        problems.append("The edit lacks necessary context.")
    entities = list(dict.fromkeys(str(item) for item in story.coherence.get("entities", []) if str(item).strip()))
    if not entities:
        entities = list(dict.fromkeys(entity for segment in sequence.segments for entity in (moments.get(segment.moment_id).entities if moments.get(segment.moment_id) else [])))
    combined_text = " ".join(segment.transcript_excerpt for segment in sequence.segments)
    evidenced_entities = [entity for entity in entities if _approximately_present(entity, combined_text)]
    if not evidenced_entities:
        problems.append("The selected source lines do not identify a relevant person or entity.")
    reference_text = " ".join((topic, premise, story.hook, story.payoff))
    segment_roles: list[dict[str, Any]] = []
    for segment in sequence.segments:
        rationale = story.moment_rationales.get(segment.moment_id, "").strip()
        if not rationale:
            problems.append(f"Segment {segment.id} has no editorial necessity rationale.")
        relevant = _approximately_present(reference_text, segment.transcript_excerpt)
        if not relevant:
            problems.append(f"Segment {segment.id} is tangential or contextless.")
        segment_roles.append({"segment_id": segment.id, "moment_id": segment.moment_id, "role": segment.purpose, "why_it_belongs": rationale, "relevant": relevant})
    redundant: set[str] = set()
    for index, segment in enumerate(sequence.segments):
        for other in sequence.segments[index + 1:]:
            if _jaccard(segment.transcript_excerpt, other.transcript_excerpt) >= 0.72:
                redundant.add(other.id)
    if redundant:
        problems.append("The edit contains redundant segments: " + ", ".join(sorted(redundant)))
    self_contained = bool(topic and premise and evidenced_entities and hook_present and payoff_present and ("context" in roles or "setup" in roles) and all(item["relevant"] for item in segment_roles))
    if not self_contained:
        problems.append("A cold viewer could not reliably identify the subject, tension, and resolution.")
    checks = [bool(topic and topic.casefold() not in GENERIC_TOPICS), bool(premise), hook_present, payoff_present, self_contained, not redundant, all(item["why_it_belongs"] and item["relevant"] for item in segment_roles)]
    score = sum(checks) / len(checks)
    return EditorialReviewResult(
        topic=topic, viewer_premise=premise, hook_present=hook_present, self_contained=self_contained,
        payoff_present=payoff_present, coherence_score=round(score, 3), segment_roles=segment_roles,
        relevant_entities=evidenced_entities, central_tension=premise, resolution=story.payoff,
        problems=list(dict.fromkeys(problems)), accepted=not problems and score >= 0.85,
        rebuild_attempts=rebuild_attempts,
    )


def plan_sequence(story: StoryConcept, moments: dict[str, Moment], transcript: Transcript, source: MediaSource) -> EditSequence:
    selected = [moments[moment_id] for moment_id in story.moment_ids if moment_id in moments]
    if not story.coherence.get("planned_order"):
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
    sequence = EditSequence(f"edit_{story.id}", story.id, story.title, source.id, segments, actions, placeholder, frame_rate=source.frame_rate, status="editorial_review")
    sequence.integrity = validate_integrity(sequence, transcript, moments, allow_truthful_hook_reorder=bool(story.coherence.get("allow_truthful_hook_reorder")))
    sequence.editorial_review = review_editorial_quality(story, sequence, moments)
    sequence.status = "ready" if sequence.integrity.status == "passed" and sequence.editorial_review.accepted and 2 <= len(segments) <= 4 else "rejected"
    story.status = sequence.status
    story.understandable_without_source = sequence.editorial_review.self_contained
    validate_action_policy(actions, sequence.duration_seconds)
    return sequence


def plan_reviewed_sequence(story: StoryConcept, moments: dict[str, Moment], transcript: Transcript, source: MediaSource, *, max_rebuilds: int = MAX_EDITORIAL_REBUILDS) -> EditSequence:
    max_rebuilds = max(0, min(max_rebuilds, MAX_EDITORIAL_REBUILDS))
    working = story
    sequence = plan_sequence(working, moments, transcript, source)
    attempts = 0
    while not sequence.editorial_review.accepted and attempts < max_rebuilds and len(working.moment_ids) > 2:
        duplicate_id: str | None = None
        chosen = [moments[item] for item in working.moment_ids if item in moments]
        for index, item in enumerate(chosen):
            duplicate = next((other for other in chosen[index + 1:] if _jaccard(item.summary, other.summary) >= 0.72), None)
            if duplicate:
                duplicate_id = duplicate.id
                break
        if duplicate_id is None:
            break
        attempts += 1
        working = replace(working, moment_ids=[item for item in working.moment_ids if item != duplicate_id], moment_rationales={key: value for key, value in working.moment_rationales.items() if key != duplicate_id})
        sequence = plan_sequence(working, moments, transcript, source)
    if sequence.editorial_review:
        sequence.editorial_review.rebuild_attempts = attempts
    story.status = sequence.status
    story.understandable_without_source = bool(sequence.editorial_review and sequence.editorial_review.self_contained)
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
