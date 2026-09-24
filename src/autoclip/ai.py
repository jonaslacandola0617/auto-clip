from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any


CANDIDATE_REQUIRED_FIELDS = {"start", "end", "title", "hook", "category", "reason", "scores"}
SCORE_REQUIRED_FIELDS = {"hook", "standalone_context", "payoff"}


class AIResponseError(ValueError):
    pass


class AIProvider(ABC):
    @abstractmethod
    def analyze_transcript(self, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def find_candidates(self, analysis: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def rank_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def discover_moments(self, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def construct_stories(self, moments: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError


def validate_candidate_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"candidates"} or not isinstance(payload["candidates"], list):
        raise AIResponseError("response must be an object containing only a candidates array")
    validated: list[dict[str, Any]] = []
    for item in payload["candidates"]:
        if not isinstance(item, dict) or not CANDIDATE_REQUIRED_FIELDS.issubset(item):
            raise AIResponseError("candidate is missing required fields")
        if set(item) != CANDIDATE_REQUIRED_FIELDS:
            raise AIResponseError("candidate contains unsupported fields")
        if not isinstance(item["start"], (int, float)) or not isinstance(item["end"], (int, float)):
            raise AIResponseError("candidate timestamps must be numeric")
        if any(not isinstance(item[field], str) or not item[field].strip() for field in ("title", "hook", "category", "reason")):
            raise AIResponseError("candidate text fields must be non-empty strings")
        scores = item["scores"]
        if not isinstance(scores, dict) or not SCORE_REQUIRED_FIELDS.issubset(scores) or not set(scores).issubset(SCORE_REQUIRED_FIELDS | {"emotion"}):
            raise AIResponseError("candidate scores do not match schema")
        if any(not isinstance(value, int) or not 0 <= value <= 100 for value in scores.values()):
            raise AIResponseError("candidate scores must be integers from 0 to 100")
        validated.append(item)
    return validated


class GeminiProvider(AIProvider):
    """Gemini REST adapter. Only bounded transcript chunks and explicit metadata are sent."""

    def __init__(self, model: str = "gemini-3.6-flash", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def analyze_transcript(self, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        safe_metadata = {key: metadata[key] for key in ("duration_seconds", "min_clip_seconds", "max_clip_seconds", "language") if key in metadata}
        request_body = {
            "contents": [{"parts": [{"text": json.dumps({"transcript_chunks": chunks, "metadata": safe_metadata}, separators=(",", ":"))}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "required": ["candidates"],
                    "properties": {"candidates": {"type": "ARRAY", "items": {
                        "type": "OBJECT", "required": sorted(CANDIDATE_REQUIRED_FIELDS),
                        "properties": {
                            "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
                            "title": {"type": "STRING"}, "hook": {"type": "STRING"},
                            "category": {"type": "STRING"}, "reason": {"type": "STRING"},
                            "scores": {"type": "OBJECT", "required": sorted(SCORE_REQUIRED_FIELDS), "properties": {
                                "hook": {"type": "INTEGER", "minimum": 0, "maximum": 100},
                                "standalone_context": {"type": "INTEGER", "minimum": 0, "maximum": 100},
                                "payoff": {"type": "INTEGER", "minimum": 0, "maximum": 100},
                                "emotion": {"type": "INTEGER", "minimum": 0, "maximum": 100},
                            }},
                        },
                    }}},
                },
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self._api_key}"
        request = urllib.request.Request(url, data=json.dumps(request_body).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            message = "request rejected"
            try:
                error_payload = json.loads(exc.read())
                message = str(error_payload.get("error", {}).get("message") or message)
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
            raise RuntimeError(f"Gemini request rejected ({exc.code}): {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Gemini request failed") from exc
        try:
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            payload = json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AIResponseError("Gemini returned malformed structured output") from exc
        validate_candidate_payload(payload)
        return payload

    def _generate_structured(self, prompt: dict[str, Any], response_schema: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        body = {"contents": [{"parts": [{"text": json.dumps(prompt, separators=(",", ":"))}]}], "generationConfig": {"responseMimeType": "application/json", "responseSchema": response_schema}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self._api_key}"
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = json.loads(response.read())
            return json.loads(raw["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Gemini request rejected ({exc.code})") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Gemini request failed") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AIResponseError("Gemini returned malformed structured output") from exc

    def discover_moments(self, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        item = {"type": "OBJECT", "required": ["start", "end", "summary", "types", "segment_ids", "word_ids", "topics", "entities", "characteristics", "confidence", "reasoning"], "properties": {
            "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"}, "summary": {"type": "STRING"},
            "types": {"type": "ARRAY", "items": {"type": "STRING"}}, "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "word_ids": {"type": "ARRAY", "items": {"type": "STRING"}}, "topics": {"type": "ARRAY", "items": {"type": "STRING"}},
            "entities": {"type": "ARRAY", "items": {"type": "STRING"}}, "characteristics": {"type": "OBJECT"},
            "confidence": {"type": "NUMBER"}, "reasoning": {"type": "STRING"},
        }}
        payload = self._generate_structured({"task": "Discover source-grounded notable moments. Do not create clips or invent timestamps.", "transcript_chunks": chunks, "metadata": metadata}, {"type": "OBJECT", "required": ["moments"], "properties": {"moments": {"type": "ARRAY", "items": item}}})
        moments = payload.get("moments")
        if not isinstance(moments, list):
            raise AIResponseError("moment response is missing moments")
        return moments

    def construct_stories(self, moments: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        item = {"type": "OBJECT", "required": ["title", "premise", "hook", "context", "development", "payoff", "moment_ids", "target_duration_seconds", "explanation", "coherence", "integrity_considerations"], "properties": {
            "title": {"type": "STRING"}, "premise": {"type": "STRING"}, "hook": {"type": "STRING"}, "context": {"type": "STRING"},
            "development": {"type": "STRING"}, "payoff": {"type": "STRING"}, "moment_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "target_duration_seconds": {"type": "NUMBER"}, "explanation": {"type": "STRING"}, "coherence": {"type": "OBJECT"},
            "integrity_considerations": {"type": "ARRAY", "items": {"type": "STRING"}},
        }}
        payload = self._generate_structured({"task": "Build only coherent truthful multi-moment short-form stories. Return none when evidence is insufficient.", "moments": moments, "metadata": metadata}, {"type": "OBJECT", "required": ["stories"], "properties": {"stories": {"type": "ARRAY", "items": item}}})
        stories = payload.get("stories")
        if not isinstance(stories, list):
            raise AIResponseError("story response is missing stories")
        return stories

    def find_candidates(self, analysis: dict[str, Any]) -> list[dict[str, Any]]:
        return validate_candidate_payload(analysis)

    def rank_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(candidates, key=lambda c: c["scores"]["hook"] * .35 + c["scores"]["standalone_context"] * .25 + c["scores"]["payoff"] * .30 + c["scores"].get("emotion", 0) * .10, reverse=True)


class FixtureProvider(AIProvider):
    def __init__(self, payload: dict[str, Any], *, moments: list[dict[str, Any]] | None = None, stories: list[dict[str, Any]] | None = None) -> None:
        self.payload = payload
        self.moments = moments or []
        self.stories = stories or []

    def analyze_transcript(self, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
        validate_candidate_payload(self.payload)
        return self.payload

    def find_candidates(self, analysis: dict[str, Any]) -> list[dict[str, Any]]:
        return validate_candidate_payload(analysis)

    def rank_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return GeminiProvider(api_key="fixture").rank_candidates(candidates)

    def discover_moments(self, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return self.moments

    def construct_stories(self, moments: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return self.stories
